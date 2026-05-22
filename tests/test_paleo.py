"""Unit tests for paleo. Run from repo root: `python3 -m unittest discover -s tests`."""
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

# Make `paleo` importable when running from repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import paleo


class TestPolicyMatch(unittest.TestCase):
    def test_tool_prefix(self):
        p = paleo._compile_policy({"id": "x", "match": {"tool_prefix": "mcp__claude_ai_"}, "severity": "block", "reason": ""})
        self.assertTrue(p["_match"]("mcp__claude_ai_Tavily__tavily_search", {}))
        self.assertFalse(p["_match"]("Bash", {}))
        self.assertFalse(p["_match"]("mcp__supabase__query", {}))

    def test_tool_exact(self):
        p = paleo._compile_policy({"id": "y", "match": {"tool_exact": "Bash"}, "severity": "warn", "reason": ""})
        self.assertTrue(p["_match"]("Bash", {}))
        self.assertFalse(p["_match"]("BashFoo", {}))

    def test_tool_regex(self):
        p = paleo._compile_policy({"id": "z", "match": {"tool_regex": r"^mcp__.*Tavily.*"}, "severity": "block", "reason": ""})
        self.assertTrue(p["_match"]("mcp__claude_ai_Tavily__tavily_search", {}))
        self.assertFalse(p["_match"]("mcp__supabase__query", {}))

    def test_empty_match_never_fires(self):
        p = paleo._compile_policy({"id": "noop", "match": {}, "severity": "warn", "reason": ""})
        self.assertFalse(p["_match"]("anything", {}))


class TestExtractPaths(unittest.TestCase):
    def test_finds_tilde_and_absolute(self):
        text = "logs live at `~/.claude/foo.log` and `/home/alice/scripts/bar.py` — see also /home/alice/proj/log"
        out = paleo._extract_paths(text)
        self.assertIn("~/.claude/foo.log", out)
        self.assertIn("/home/alice/scripts/bar.py", out)
        self.assertIn("/home/alice/proj/log", out)

    def test_filters_too_short(self):
        out = paleo._extract_paths("see `/x` or `~/y`")
        self.assertEqual(out, set())

    def test_strips_trailing_punctuation(self):
        text = "the path is `/home/alice/scripts/bar.py`."
        out = paleo._extract_paths(text)
        self.assertIn("/home/alice/scripts/bar.py", out)

    def test_backtick_paths_can_contain_spaces(self):
        text = "keystore at `/home/alice/My Project/release/signing.keystore`"
        out = paleo._extract_paths(text)
        self.assertIn("/home/alice/My Project/release/signing.keystore", out)

    def test_double_quoted_paths_can_contain_spaces(self):
        text = '--signing.store.file="/home/alice/My Project/release/signing.keystore" \\'
        out = paleo._extract_paths(text)
        self.assertIn("/home/alice/My Project/release/signing.keystore", out)
        # bare-path regex must not have captured the truncated prefix
        self.assertNotIn("/home/alice/My", out)

    def test_paths_with_plus_in_version(self):
        text = "JDK at `/home/alice/.jdk/jdk-17.0.13+11`"
        out = paleo._extract_paths(text)
        self.assertIn("/home/alice/.jdk/jdk-17.0.13+11", out)

    def test_skips_template_placeholders(self):
        text = (
            "Copy into `/home/alice/proj/Release X.X/` "
            "and also `/home/alice/${USER}/foo` "
            "and `/home/alice/*.log` "
            "but keep `/home/alice/real/path`"
        )
        out = paleo._extract_paths(text)
        self.assertNotIn("/home/alice/proj/Release X.X/", out)
        self.assertNotIn("/home/alice/${USER}/foo", out)
        self.assertNotIn("/home/alice/*.log", out)
        self.assertIn("/home/alice/real/path", out)

    def test_skips_paths_in_lines_marked_absent(self):
        text = (
            "previously at `/home/alice/old-game/` — this path does not exist anymore.\n"
            "but `/home/alice/scripts/bar.py` is still valid.\n"
        )
        out = paleo._extract_paths(text)
        self.assertNotIn("/home/alice/old-game/", out)
        self.assertIn("/home/alice/scripts/bar.py", out)


class TestExpand(unittest.TestCase):
    def test_tilde_expansion(self):
        out = paleo._expand("~/foo")
        self.assertEqual(out, pathlib.Path.home() / "foo")

    def test_absolute_passthrough(self):
        out = paleo._expand("/etc/hosts")
        self.assertEqual(out, pathlib.Path("/etc/hosts"))


class TestWalkMemory(unittest.TestCase):
    def test_follows_relative_md_link_skips_http(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "INDEX.md").write_text(
                "- [link a](a.md) — note\n"
                "- [link b](b.md)\n"
                "- [external](https://example.com)\n"
            )
            (root / "a.md").write_text("- [self loop](INDEX.md)\n- [missing](nonexistent.md)\n")
            (root / "b.md").write_text("end of chain\n")
            out = {p.name for p in paleo._walk_memory(root / "INDEX.md")}
            self.assertEqual(out, {"INDEX.md", "a.md", "b.md"})


class TestExpectedInterval(unittest.TestCase):
    def test_daily(self):
        self.assertAlmostEqual(paleo._expected_interval_hours("0 21 * * *"), 24.0)

    def test_weekly(self):
        self.assertAlmostEqual(paleo._expected_interval_hours("30 22 * * 6"), 24.0 * 7)

    def test_every_n_minutes(self):
        # */15 in the minute field → 15min interval → 0.25h
        self.assertAlmostEqual(paleo._expected_interval_hours("*/15 * * * *"), 0.25)

    def test_malformed_falls_back(self):
        self.assertAlmostEqual(paleo._expected_interval_hours("totally not a cron"), 24.0)


class TestIngestRecord(unittest.TestCase):
    def test_counts_tool_use_skill_agent_mcp(self):
        usage = paleo.Usage()
        rec = {
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                    {"type": "tool_use", "name": "Skill", "input": {"skill": "gsd-progress"}},
                    {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "Explore"}},
                    {"type": "tool_use", "name": "mcp__supabase__query", "input": {}},
                    {"type": "text", "text": "hi"},
                ]
            }
        }
        paleo._ingest_record(rec, usage)
        self.assertEqual(usage.tool_uses["Bash"], 1)
        self.assertEqual(usage.skill_uses["gsd-progress"], 1)
        self.assertEqual(usage.agent_uses["Explore"], 1)
        self.assertEqual(usage.mcp_uses["supabase"], 1)

    def test_ignores_non_dict_message(self):
        usage = paleo.Usage()
        paleo._ingest_record({"message": None}, usage)
        paleo._ingest_record({"message": "string"}, usage)
        paleo._ingest_record({}, usage)
        self.assertEqual(sum(usage.tool_uses.values()), 0)


class TestAgeFormatting(unittest.TestCase):
    def test_minutes_hours_days(self):
        self.assertEqual(paleo._fmt_age(30 * 60), "30m")
        self.assertEqual(paleo._fmt_age(5 * 3600), "5h")
        self.assertEqual(paleo._fmt_age(3 * 86400), "3d")


class TestCronLogRegex(unittest.TestCase):
    def test_captures_redirect_target(self):
        cmd = "/usr/bin/python3 /home/x/foo.py >> /home/x/foo.log 2>&1"
        m = paleo.CRON_LOG_RE.search(cmd)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "/home/x/foo.log")


class TestPolicyOutcome(unittest.TestCase):
    def test_distinguishes_blocked_from_succeeded(self):
        """tool_use with is_error=True tool_result → blocked; without → succeeded."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            session = root / "session.jsonl"
            lines = [
                # blocked call
                {"message": {"content": [{
                    "type": "tool_use", "id": "tu_A",
                    "name": "mcp__claude_ai_Tavily__tavily_search", "input": {},
                }]}, "timestamp": "2026-05-18T00:00:00Z"},
                {"message": {"content": [{
                    "type": "tool_result", "tool_use_id": "tu_A",
                    "is_error": True, "content": "BLOCKED by hook",
                }]}},
                # succeeded call
                {"message": {"content": [{
                    "type": "tool_use", "id": "tu_B",
                    "name": "mcp__claude_ai_Linear__list_projects", "input": {},
                }]}, "timestamp": "2026-05-12T00:00:00Z"},
                {"message": {"content": [{
                    "type": "tool_result", "tool_use_id": "tu_B",
                    "content": "[{...real data...}]",
                }]}},
                # non-matching tool_use — should not be counted
                {"message": {"content": [{
                    "type": "tool_use", "id": "tu_C",
                    "name": "Bash", "input": {"command": "ls"},
                }]}},
            ]
            session.write_text("\n".join(json.dumps(L) for L in lines) + "\n")

            policies = [{
                "id": "p", "match": {"tool_prefix": "mcp__claude_ai_"},
                "severity": "block", "reason": "test",
            }]
            hits, _ = paleo.collect_policy_hits(root, since_seconds=None, policies=policies)
            ph = hits["p"]
            self.assertEqual(len(ph), 2)
            blocked = [h for h in ph if h["blocked"] is True]
            succeeded = [h for h in ph if h["blocked"] is False]
            self.assertEqual(len(blocked), 1)
            self.assertEqual(len(succeeded), 1)
            self.assertEqual(blocked[0]["tool_name"], "mcp__claude_ai_Tavily__tavily_search")
            self.assertEqual(succeeded[0]["tool_name"], "mcp__claude_ai_Linear__list_projects")


class TestPolicySince(unittest.TestCase):
    def test_since_drops_pre_cutoff_hits(self):
        """--since filters out hits older than the cutoff but keeps newer ones."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            session = root / "session.jsonl"
            lines = [
                # OLD hit — should be dropped by --since 2026-05-21
                {"message": {"content": [{
                    "type": "tool_use", "id": "tu_old",
                    "name": "mcp__claude_ai_Linear__list_projects", "input": {},
                }]}, "timestamp": "2026-05-12T14:53:00Z"},
                {"message": {"content": [{
                    "type": "tool_result", "tool_use_id": "tu_old", "content": "ok",
                }]}},
                # NEW hit — should be kept
                {"message": {"content": [{
                    "type": "tool_use", "id": "tu_new",
                    "name": "mcp__claude_ai_Tavily__tavily_search", "input": {},
                }]}, "timestamp": "2026-05-21T11:34:00Z"},
                {"message": {"content": [{
                    "type": "tool_result", "tool_use_id": "tu_new",
                    "is_error": True, "content": "BLOCKED",
                }]}},
            ]
            session.write_text("\n".join(json.dumps(L) for L in lines) + "\n")

            policies = [{
                "id": "p", "match": {"tool_prefix": "mcp__claude_ai_"},
                "severity": "block", "reason": "test",
            }]
            since_dt = paleo._parse_since("2026-05-21")
            hits, _ = paleo.collect_policy_hits(root, None, policies, since_dt=since_dt)
            self.assertEqual(len(hits["p"]), 1)
            self.assertEqual(hits["p"][0]["tool_name"], "mcp__claude_ai_Tavily__tavily_search")

    def test_parse_since_accepts_date_and_iso(self):
        self.assertIsNotNone(paleo._parse_since("2026-05-21"))
        self.assertIsNotNone(paleo._parse_since("2026-05-21T11:04:00Z"))
        self.assertIsNotNone(paleo._parse_since("2026-05-21T11:04:00+00:00"))
        self.assertIsNone(paleo._parse_since(None))
        self.assertIsNone(paleo._parse_since(""))

    def test_parse_since_rejects_garbage(self):
        with self.assertRaises(ValueError):
            paleo._parse_since("not-a-timestamp")
        with self.assertRaises(ValueError):
            paleo._parse_since("2026/05/21")

    def test_hits_without_timestamp_are_kept(self):
        """Defensive: a hit with no timestamp shouldn't be silently dropped."""
        since_dt = paleo._parse_since("2026-05-21")
        self.assertTrue(paleo._hit_after({"timestamp": None}, since_dt))
        self.assertTrue(paleo._hit_after({}, since_dt))


class TestHooksDiscovery(unittest.TestCase):
    def _make_settings(self, root: pathlib.Path, hooks_dict: dict) -> None:
        """Write a Claude-style settings.json with the given hooks block."""
        (root / "settings.json").write_text(json.dumps({"hooks": hooks_dict}))

    def test_read_hooks_file_handles_missing(self):
        self.assertEqual(paleo._read_hooks_file(pathlib.Path("/no/such/file")), [])

    def test_read_hooks_file_parses_nested_shape(self):
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "settings.json"
            f.write_text(json.dumps({
                "hooks": {
                    "Stop": [{"matcher": "", "hooks": [
                        {"type": "command", "command": "bash /tmp/a.sh"},
                        {"type": "command", "command": "bash /tmp/b.sh"},
                    ]}],
                    "PreToolUse": [{"matcher": "Bash", "hooks": [
                        {"type": "command", "command": "bash /tmp/c.sh"},
                    ]}],
                }
            }))
            out = paleo._read_hooks_file(f)
            self.assertEqual(len(out), 3)
            events = [h["event"] for h in out]
            self.assertEqual(events.count("Stop"), 2)
            self.assertEqual(events.count("PreToolUse"), 1)
            pre = next(h for h in out if h["event"] == "PreToolUse")
            self.assertEqual(pre["matcher"], "Bash")

    def test_read_hooks_file_tolerates_malformed(self):
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "settings.json"
            # Missing 'command' key, non-dict entries — should be skipped, not raised
            f.write_text(json.dumps({
                "hooks": {
                    "Stop": [
                        {"matcher": "", "hooks": [
                            {"type": "command"},  # no command
                            "not-a-dict",
                            {"type": "command", "command": "bash /tmp/ok.sh"},
                        ]},
                        "not-a-dict",
                    ]
                }
            }))
            out = paleo._read_hooks_file(f)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["command"], "bash /tmp/ok.sh")


class TestHookFires(unittest.TestCase):
    def test_collect_hook_fires_only_stop_hook_summary(self):
        """Only system records with subtype=stop_hook_summary contribute fires."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            session = root / "session.jsonl"
            lines = [
                # Stop hook summary — counted
                {"type": "system", "subtype": "stop_hook_summary",
                 "timestamp": "2026-05-21T12:00:00Z",
                 "hookInfos": [
                     {"command": "bash /tmp/a.sh", "durationMs": 100},
                     {"command": "bash /tmp/b.sh", "durationMs": 200},
                 ]},
                # Another stop_hook_summary — accumulates
                {"type": "system", "subtype": "stop_hook_summary",
                 "timestamp": "2026-05-21T12:05:00Z",
                 "hookInfos": [
                     {"command": "bash /tmp/a.sh", "durationMs": 300},
                 ]},
                # Different subtype — IGNORED
                {"type": "system", "subtype": "turn_duration",
                 "hookInfos": [
                     {"command": "bash /tmp/never-counted.sh", "durationMs": 999},
                 ]},
                # Non-system record with content blocks — IGNORED
                {"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Bash"}
                ]}},
            ]
            session.write_text("\n".join(json.dumps(L) for L in lines) + "\n")
            fires = paleo.collect_hook_fires(root, None)
            self.assertEqual(set(fires.keys()), {"bash /tmp/a.sh", "bash /tmp/b.sh"})
            self.assertEqual(fires["bash /tmp/a.sh"]["fires"], 2)
            self.assertEqual(fires["bash /tmp/a.sh"]["max_ms"], 300)
            self.assertEqual(fires["bash /tmp/a.sh"]["total_ms"], 400)
            self.assertEqual(fires["bash /tmp/a.sh"]["last_ts"], "2026-05-21T12:05:00Z")
            self.assertEqual(fires["bash /tmp/b.sh"]["fires"], 1)

    def test_event_classification(self):
        """Stop is observable; other events are config-only."""
        self.assertIn("Stop", paleo.HOOK_EVENTS_OBSERVABLE)
        for ev in ["PreToolUse", "PostToolUse", "SessionStart",
                   "UserPromptSubmit", "Notification", "PostCompact"]:
            self.assertIn(ev, paleo.HOOK_EVENTS_CONFIG_ONLY)
            self.assertNotIn(ev, paleo.HOOK_EVENTS_OBSERVABLE)


class TestProjectAttribution(unittest.TestCase):
    def test_assigns_session_to_dominant_cwd(self):
        """A session is attributed to the cwd appearing on the most records,
        even when some records wander into other directories."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            session = root / "s1.jsonl"
            lines = [
                {"cwd": "/proj/alpha", "message": {"content": [
                    {"type": "tool_use", "name": "Bash"}]}},
                {"cwd": "/proj/alpha", "message": {"content": [
                    {"type": "tool_use", "name": "Read"}]}},
                {"cwd": "/proj/alpha", "message": {"content": [
                    {"type": "tool_use", "name": "Skill",
                     "input": {"skill": "deploy"}}]}},
                # one stray record in another dir — should NOT flip attribution
                {"cwd": "/proj/beta", "message": {"content": [
                    {"type": "tool_use", "name": "Edit"}]}},
            ]
            session.write_text("\n".join(json.dumps(L) for L in lines) + "\n")
            projects = paleo.collect_projects(root, None)
            self.assertEqual(set(projects.keys()), {"/proj/alpha"})
            p = projects["/proj/alpha"]
            self.assertEqual(p["sessions"], 1)
            self.assertEqual(p["tool_calls"], 4)  # all tool_uses count
            self.assertEqual(p["tools"]["Bash"], 1)
            self.assertEqual(p["tools"]["Edit"], 1)
            self.assertEqual(p["skills"]["deploy"], 1)

    def test_aggregates_multiple_sessions_per_project(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            for i in range(3):
                s = root / f"s{i}.jsonl"
                s.write_text(json.dumps({
                    "cwd": "/proj/alpha",
                    "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
                }) + "\n")
            projects = paleo.collect_projects(root, None)
            self.assertEqual(projects["/proj/alpha"]["sessions"], 3)
            self.assertEqual(projects["/proj/alpha"]["tool_calls"], 3)

    def test_normalize_rolls_up_agent_worktrees(self):
        n = paleo._normalize_project_cwd
        self.assertEqual(n("/home/u/myapp/.claude/worktrees/agent-abc"), "/home/u/myapp")
        self.assertEqual(n("/home/u/myapp/.claude/worktrees/agent-abc/src-tauri"), "/home/u/myapp")
        self.assertEqual(n("/home/u/myapp/src-tauri"), "/home/u/myapp/src-tauri")
        self.assertEqual(n("/home/u/myapp"), "/home/u/myapp")

    def test_worktree_activity_attributed_to_parent(self):
        """A session that ran in an agent worktree counts toward the parent."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            s = root / "s.jsonl"
            s.write_text(json.dumps({
                "cwd": "/proj/alpha/.claude/worktrees/agent-xyz",
                "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
            }) + "\n")
            projects = paleo.collect_projects(root, None)
            self.assertEqual(set(projects.keys()), {"/proj/alpha"})
            self.assertEqual(projects["/proj/alpha"]["sessions"], 1)

    def test_session_with_no_cwd_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            s = root / "s.jsonl"
            s.write_text(json.dumps({
                "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
            }) + "\n")
            projects = paleo.collect_projects(root, None)
            self.assertEqual(projects, {})


class TestGhosts(unittest.TestCase):
    def _write_agent_result(self, root, *, status, tool_count, tokens, content):
        """Write a one-line JSONL session with a single Agent toolUseResult."""
        session = root / f"s_{abs(hash((status, tool_count, tokens, content)))}.jsonl"
        rec = {
            "timestamp": "2026-05-22T10:00:00Z",
            "toolUseResult": {
                "agentType": "general-purpose",
                "status": status,
                "totalToolUseCount": tool_count,
                "totalTokens": tokens,
                "content": [{"type": "text", "text": content}],
            },
        }
        session.write_text(json.dumps(rec) + "\n")
        return session

    def test_phantom_with_action_claim_is_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write_agent_result(
                root, status="completed", tool_count=0, tokens=21300,
                content="I've created the integration tests in tests/auth.test.ts.",
            )
            ghosts = paleo.collect_ghosts(root, None)
            self.assertEqual(len(ghosts), 1)
            self.assertEqual(ghosts[0]["tool_calls"], 0)
            self.assertIn("created", ghosts[0]["claim"])

    def test_prose_agent_with_zero_tools_is_not_flagged(self):
        """The key false-positive guard: a content agent that legitimately
        returns text with zero tool calls must NOT be flagged."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write_agent_result(
                root, status="completed", tool_count=0, tokens=34000,
                content="**1. LinkedIn Post**\nMost startups don't fail at hiring...",
            )
            self.assertEqual(paleo.collect_ghosts(root, None), [])

    def test_zero_tokens_excluded(self):
        """Async-launch artifacts (0 tool calls, 0 tokens) are not ghosts."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write_agent_result(
                root, status="completed", tool_count=0, tokens=0,
                content="I created the file foo.py.",
            )
            self.assertEqual(paleo.collect_ghosts(root, None), [])

    def test_agent_that_used_tools_is_not_a_ghost(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write_agent_result(
                root, status="completed", tool_count=12, tokens=50000,
                content="I created the file foo.py.",
            )
            self.assertEqual(paleo.collect_ghosts(root, None), [])

    def test_min_tokens_floor_respected(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write_agent_result(
                root, status="completed", tool_count=0, tokens=500,
                content="I wrote the config to settings.json.",
            )
            self.assertEqual(paleo.collect_ghosts(root, None, min_tokens=1000), [])
            self.assertEqual(len(paleo.collect_ghosts(root, None, min_tokens=100)), 1)

    def test_failed_agent_not_a_ghost(self):
        """An agent that reported failure isn't lying — not a ghost."""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write_agent_result(
                root, status="error", tool_count=0, tokens=21300,
                content="I tried to create the file but hit an error.",
            )
            self.assertEqual(paleo.collect_ghosts(root, None), [])


class TestMalformedInputDefensive(unittest.TestCase):
    """paleo's contract is to never crash on malformed input. These feed
    pathological-but-valid-JSON records to every collector and assert no raise."""

    def _write(self, root, name, lines):
        (root / name).write_text("\n".join(lines) + "\n")

    def test_non_dict_jsonl_lines_do_not_crash_collectors(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            # valid JSON, but not objects: array, string, int, bool, null
            self._write(root, "x.jsonl", ['["a","b"]', '"bare"', "42", "true", "null"])
            # every collector must tolerate these
            self.assertEqual(paleo.collect_usage(root, None).sessions_seen, 1)
            self.assertEqual(paleo.collect_ghosts(root, None), [])
            self.assertEqual(paleo.collect_projects(root, None), {})
            self.assertEqual(paleo.collect_hook_fires(root, None), {})
            hits, _ = paleo.collect_policy_hits(root, None, paleo.DEFAULT_POLICIES)
            self.assertTrue(all(v == [] for v in hits.values()))

    def test_non_numeric_tokens_does_not_crash_ghosts(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write(root, "g.jsonl", [json.dumps({
                "toolUseResult": {
                    "status": "completed", "totalToolUseCount": 0,
                    "totalTokens": "not-a-number",
                    "content": "I created foo.py", "agentType": "x",
                },
            })])
            # malformed tokens → treated as 0 → below floor → not flagged, no raise
            self.assertEqual(paleo.collect_ghosts(root, None), [])

    def test_non_numeric_duration_does_not_crash_hook_fires(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write(root, "h.jsonl", [json.dumps({
                "type": "system", "subtype": "stop_hook_summary",
                "timestamp": "2026-05-22T00:00:00Z",
                "hookInfos": [{"command": "bash x.sh", "durationMs": "bad"}],
            })])
            fires = paleo.collect_hook_fires(root, None)
            self.assertEqual(fires["bash x.sh"]["fires"], 1)
            self.assertEqual(fires["bash x.sh"]["max_ms"], 0)  # bad duration → 0

    def test_non_string_cwd_does_not_crash_projects(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write(root, "p.jsonl", [json.dumps({
                "cwd": 12345,
                "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
            })])
            # non-string cwd is ignored, not crashed on
            self.assertEqual(paleo.collect_projects(root, None), {})


class TestCliDefaults(unittest.TestCase):
    def test_bare_invocation_defaults_to_health(self):
        """`paleo` with no subcommand should parse cmd as None so main() can
        route it to health."""
        args = paleo.build_parser().parse_args([])
        self.assertIsNone(args.cmd)

    def test_explicit_subcommand_preserved(self):
        args = paleo.build_parser().parse_args(["dead"])
        self.assertEqual(args.cmd, "dead")

    def test_version_string_exists(self):
        self.assertRegex(paleo.__version__, r"^\d+\.\d+")


class TestHealthSummary(unittest.TestCase):
    def test_crons_summary_row_shape(self):
        rows = paleo._crons_summary()
        for r in rows:
            self.assertIn("status", r)
            self.assertIn(r["status"], {"ok", "stale", "missing-log", "no-log-redirect", "stat-error"})


class TestCollectUsageWindow(unittest.TestCase):
    def test_skips_files_older_than_window(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            fresh = root / "fresh.jsonl"
            old = root / "old.jsonl"
            fresh.write_text(json.dumps({
                "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}
            }) + "\n")
            old.write_text(json.dumps({
                "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}
            }) + "\n")
            # backdate `old` to 60 days ago
            sixty_days_ago = time.time() - 60 * 86400
            os.utime(old, (sixty_days_ago, sixty_days_ago))

            usage = paleo.collect_usage(root, since_seconds=7 * 86400)
            self.assertEqual(usage.tool_uses["Bash"], 1)
            self.assertEqual(usage.tool_uses["Read"], 0)
            self.assertEqual(usage.sessions_seen, 1)


if __name__ == "__main__":
    unittest.main()
