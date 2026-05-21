#!/usr/bin/env python3
"""paleo — agent workspace archeology.

Map what Claude Code thinks you have installed (skills, MCP servers, subagents)
against what your session JSONL logs prove you actually invoked. Surface the
dead-capability surface so it can be pruned, promoted, or remembered.

Stdlib only. Reads from ~/.claude/ by default. No mutation.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable

HOME = pathlib.Path.home()
CLAUDE = HOME / ".claude"
PROJECTS_DIR = CLAUDE / "projects"
SKILLS_DIR = CLAUDE / "skills"
PLUGINS_DIR = CLAUDE / "plugins"
AGENTS_DIR = CLAUDE / "agents"
MCP_FILE = CLAUDE / ".mcp.json"


# ----- discovery: what's installed -------------------------------------------

FRONTMATTER_NAME = re.compile(r"^name:\s*(\S+)", re.MULTILINE)


def _read_skill_name(md_path: pathlib.Path) -> str | None:
    try:
        head = md_path.read_text(errors="replace")[:2000]
    except OSError:
        return None
    m = FRONTMATTER_NAME.search(head)
    if m:
        return m.group(1).strip()
    return None


def discover_skills() -> dict[str, pathlib.Path]:
    """Return {skill_name: install_path}. Covers user + plugin skills."""
    found: dict[str, pathlib.Path] = {}

    # User-level: ~/.claude/skills/<name>/SKILL.md OR ~/.claude/skills/<name>.md
    if SKILLS_DIR.exists():
        for entry in SKILLS_DIR.iterdir():
            if entry.is_dir():
                skill_md = entry / "SKILL.md"
                if skill_md.exists():
                    name = _read_skill_name(skill_md) or entry.name
                    found.setdefault(name, skill_md)
            elif entry.suffix == ".md":
                name = _read_skill_name(entry) or entry.stem
                found.setdefault(name, entry)

    # Plugin skills: ~/.claude/plugins/**/skills/<name>/SKILL.md
    # Skip ~/.claude/plugins/cache/... — those are duplicate copies, not separate skills.
    if PLUGINS_DIR.exists():
        for skill_md in PLUGINS_DIR.rglob("skills/*/SKILL.md"):
            if "/cache/" in str(skill_md):
                continue
            name = _read_skill_name(skill_md) or skill_md.parent.name
            plugin_namespace = _infer_plugin_namespace(skill_md)
            if plugin_namespace and ":" not in name:
                ns_name = f"{plugin_namespace}:{name}"
                found.setdefault(ns_name, skill_md)
            found.setdefault(name, skill_md)

    return found


def _infer_plugin_namespace(skill_md: pathlib.Path) -> str | None:
    """For ~/.claude/plugins/marketplaces/X/plugins/<plugin>/skills/... return <plugin>."""
    parts = skill_md.parts
    try:
        i = parts.index("plugins")
        # marketplaces/<m>/plugins/<plugin>/skills/...
        # find the SECOND "plugins"
        try:
            j = parts.index("plugins", i + 1)
            return parts[j + 1]
        except ValueError:
            return parts[i + 1]
    except ValueError:
        return None


def discover_mcps() -> dict[str, str]:
    """Return {server_name: source}."""
    found: dict[str, str] = {}
    if MCP_FILE.exists():
        try:
            data = json.loads(MCP_FILE.read_text())
            for k in data.get("mcpServers", {}):
                found[k] = "~/.claude/.mcp.json"
        except json.JSONDecodeError:
            pass
    # Project-local: any .mcp.json under PROJECTS_DIR (rare)
    # Plugin MCPs: ~/.claude/plugins/**/.mcp.json
    if PLUGINS_DIR.exists():
        for f in PLUGINS_DIR.rglob(".mcp.json"):
            try:
                data = json.loads(f.read_text())
                for k in data.get("mcpServers", {}):
                    found.setdefault(k, f"plugin:{_infer_plugin_namespace(f) or f.name}")
            except Exception:
                continue
    return found


def discover_agents() -> dict[str, pathlib.Path]:
    """Return {agent_name: install_path} for ~/.claude/agents/ + plugin agents."""
    found: dict[str, pathlib.Path] = {}
    if AGENTS_DIR.exists():
        for f in AGENTS_DIR.glob("*.md"):
            found.setdefault(f.stem, f)
    if PLUGINS_DIR.exists():
        for f in PLUGINS_DIR.rglob("agents/*.md"):
            found.setdefault(f.stem, f)
    return found


# ----- usage: what's been invoked --------------------------------------------


@dataclass
class Usage:
    tool_uses: collections.Counter = field(default_factory=collections.Counter)
    skill_uses: collections.Counter = field(default_factory=collections.Counter)
    agent_uses: collections.Counter = field(default_factory=collections.Counter)
    mcp_uses: collections.Counter = field(default_factory=collections.Counter)
    sessions_seen: int = 0
    lines_seen: int = 0
    oldest_ts: float | None = None
    newest_ts: float | None = None


def _iter_session_jsonl(logs_root: pathlib.Path) -> Iterable[pathlib.Path]:
    if not logs_root.exists():
        return []
    return logs_root.rglob("*.jsonl")


def collect_usage(
    logs_root: pathlib.Path,
    since_seconds: float | None = None,
) -> Usage:
    usage = Usage()
    cutoff = time.time() - since_seconds if since_seconds else None
    for jsonl in _iter_session_jsonl(logs_root):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if cutoff is not None and mtime < cutoff:
            continue
        usage.sessions_seen += 1
        if usage.oldest_ts is None or mtime < usage.oldest_ts:
            usage.oldest_ts = mtime
        if usage.newest_ts is None or mtime > usage.newest_ts:
            usage.newest_ts = mtime
        try:
            with jsonl.open() as fh:
                for line in fh:
                    usage.lines_seen += 1
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    _ingest_record(rec, usage)
        except OSError:
            continue
    return usage


def _ingest_record(rec: dict, usage: Usage) -> None:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = block.get("name") or "?"
        usage.tool_uses[name] += 1
        inp = block.get("input") or {}
        if name == "Skill":
            skill = inp.get("skill") or "?"
            usage.skill_uses[skill] += 1
        elif name == "Agent":
            subagent = inp.get("subagent_type") or "general-purpose"
            usage.agent_uses[subagent] += 1
        elif name.startswith("mcp__"):
            # Group by server slug: mcp__<server>__<tool>
            parts = name.split("__", 2)
            server = parts[1] if len(parts) > 1 else name
            usage.mcp_uses[server] += 1


# ----- rendering --------------------------------------------------------------


def _fmt_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds/60)}m"
    if seconds < 86400:
        return f"{int(seconds/3600)}h"
    return f"{int(seconds/86400)}d"


def _print_header(usage: Usage, days: float | None) -> None:
    span = "all-time"
    if days is not None:
        span = f"last {days:g}d"
    age = ""
    if usage.newest_ts and usage.oldest_ts:
        now = time.time()
        age = f"newest {_fmt_age(now-usage.newest_ts)} ago, oldest {_fmt_age(now-usage.oldest_ts)} ago"
    summary = f"paleo · {span} · {usage.sessions_seen} sessions · {usage.lines_seen:,} lines"
    if age:
        summary += f" · {age}"
    print(summary)
    print()


def _row(left: str, right: str, w: int = 60) -> str:
    pad = max(2, w - len(left))
    return f"  {left}{' ' * pad}{right}"


def cmd_dead(args: argparse.Namespace, usage: Usage) -> int:
    skills = discover_skills()
    mcps = discover_mcps()
    agents = discover_agents()

    used_skills = set(usage.skill_uses)
    installed_skills = set(skills)
    used_mcps = set(usage.mcp_uses)
    installed_mcps = set(mcps)
    used_agents = set(usage.agent_uses)
    installed_agents = set(agents)

    if args.json:
        report = {
            "window_days": args.days,
            "sessions": usage.sessions_seen,
            "lines": usage.lines_seen,
            "skills": {
                "installed": len(installed_skills),
                "used": sorted(installed_skills & used_skills),
                "dead": sorted(installed_skills - used_skills),
                "ghost": sorted(used_skills - installed_skills),
            },
            "mcps": {
                "installed": sorted(installed_mcps),
                "used": sorted(installed_mcps & used_mcps),
                "dead_local": sorted(
                    m for m in (installed_mcps - used_mcps)
                    if not m.startswith("claude_ai_")
                ),
                "ghost": sorted(used_mcps - installed_mcps),
            },
            "agents": {
                "installed": len(installed_agents),
                "used": sorted(installed_agents & used_agents),
                "dead": sorted(installed_agents - used_agents),
                "ghost": sorted(used_agents - installed_agents),
            },
        }
        json.dump(report, sys.stdout, indent=2, default=str)
        print()
        return 0

    _print_header(usage, args.days)

    dead_skills = sorted(installed_skills - used_skills)
    print(f"SKILLS   installed={len(installed_skills):<4} used={len(used_skills & installed_skills):<4} dead={len(dead_skills)}")
    if args.show > 0 and dead_skills:
        for s in dead_skills[: args.show]:
            print(_row(s, "never invoked"))
        if len(dead_skills) > args.show:
            print(f"  … +{len(dead_skills)-args.show} more (use --show 0 to suppress, larger N to expand)")
    print()

    # MCPs — only flag dead MCPs that are LOCAL (not claude.ai shared-account connectors)
    local_dead = sorted(m for m in (installed_mcps - used_mcps) if not m.startswith("claude_ai_"))
    shared_dead = sorted(m for m in (installed_mcps - used_mcps) if m.startswith("claude_ai_"))
    print(f"MCPs     installed={len(installed_mcps):<4} used={len(used_mcps & installed_mcps):<4} dead-local={len(local_dead)}")
    if args.show > 0:
        for m in local_dead[: args.show]:
            print(_row(m, f"never invoked  ({mcps.get(m,'?')})"))
        if shared_dead:
            print(f"  (shared/connector MCPs ignored: {len(shared_dead)})")
    print()

    # Agents
    dead_agents = sorted(installed_agents - used_agents)
    print(f"AGENTS   installed={len(installed_agents):<4} used={len(used_agents & installed_agents):<4} dead={len(dead_agents)}")
    if args.show > 0 and dead_agents:
        for a in dead_agents[: args.show]:
            print(_row(a, "never invoked"))
        if len(dead_agents) > args.show:
            print(f"  … +{len(dead_agents)-args.show} more")

    # Unused-installed vs used-but-unknown (ghost references)
    ghost_skills = sorted(used_skills - installed_skills)
    ghost_mcps = sorted(used_mcps - installed_mcps)
    ghost_agents = sorted(used_agents - installed_agents)
    if ghost_skills or ghost_mcps or ghost_agents:
        print()
        print("GHOSTS   invoked but not found in install paths (renamed? plugin gone?)")
        for s in ghost_skills:
            print(_row(f"skill: {s}", f"{usage.skill_uses[s]}x"))
        for m in ghost_mcps:
            print(_row(f"mcp:   {m}", f"{usage.mcp_uses[m]}x"))
        for a in ghost_agents:
            print(_row(f"agent: {a}", f"{usage.agent_uses[a]}x"))

    return 0


def cmd_top(args: argparse.Namespace, usage: Usage) -> int:
    _print_header(usage, args.days)
    print(f"TOP {args.show} TOOLS")
    for k, v in usage.tool_uses.most_common(args.show):
        print(_row(k, f"{v}"))
    return 0


def cmd_skills(args: argparse.Namespace, usage: Usage) -> int:
    _print_header(usage, args.days)
    skills = discover_skills()
    used = usage.skill_uses
    if args.used_only:
        rows = sorted(((s, used[s]) for s in used), key=lambda r: -r[1])
    else:
        rows = [(s, used.get(s, 0)) for s in sorted(skills)]
        rows.sort(key=lambda r: -r[1])
    print(f"SKILLS · {len(skills)} installed · {len(set(used) & set(skills))} used")
    for s, count in rows[: args.show or len(rows)]:
        tag = "" if count > 0 else "  (dead)"
        print(_row(s, f"{count}x{tag}"))
    return 0


def cmd_mcps(args: argparse.Namespace, usage: Usage) -> int:
    _print_header(usage, args.days)
    mcps = discover_mcps()
    used = usage.mcp_uses
    rows = sorted(
        [(s, used.get(s, 0), src) for s, src in mcps.items()] + [
            (s, used[s], "ghost") for s in used if s not in mcps
        ],
        key=lambda r: -r[1],
    )
    print(f"MCPs · {len(mcps)} installed (locally) · {len(used)} distinct used")
    for s, count, src in rows:
        tag = "  (dead)" if count == 0 else ""
        print(_row(f"{s} [{src}]", f"{count}x{tag}"))
    return 0


# ----- policy: hard-rule violation detection ---------------------------------


# Each policy: a declarative match spec (prefix / regex / exact) keeps the
# format JSON-loadable without ever executing user-supplied strings.

DEFAULT_POLICIES: list[dict] = [
    {
        "id": "shared-account-mcp",
        "match": {"tool_prefix": "mcp__claude_ai_"},
        "severity": "block",
        "reason": (
            "claude.ai connector MCPs run against your OAuth identity at "
            "claude.ai — every call reads or writes against THAT account. "
            "If you're on a shared/borrowed/team subscription, that's a "
            "data-scope leak. Off-limits unless the calling task explicitly "
            "authorizes the specific MCP. Edit DEFAULT_POLICIES to disable."
        ),
    },
]


def _compile_policy(p: dict) -> dict:
    """Return policy with a pre-compiled `_match` callable."""
    m = p.get("match", {})
    if "tool_exact" in m:
        target = m["tool_exact"]
        fn = lambda tn, inp: tn == target  # noqa: E731
    elif "tool_prefix" in m:
        target = m["tool_prefix"]
        fn = lambda tn, inp: tn.startswith(target)  # noqa: E731
    elif "tool_regex" in m:
        rx = re.compile(m["tool_regex"])
        fn = lambda tn, inp: rx.search(tn) is not None  # noqa: E731
    else:
        fn = lambda tn, inp: False  # noqa: E731
    return {**p, "_match": fn}


def collect_policy_hits(
    logs_root: pathlib.Path,
    since_seconds: float | None,
    policies: list[dict],
) -> tuple[dict[str, list[dict]], Usage]:
    """Two-pass: gather tool_use blocks matching policy, then cross-reference
    tool_result.is_error so we can label each hit as "blocked" or "succeeded".
    """
    compiled = [_compile_policy(p) for p in policies]
    cutoff = time.time() - since_seconds if since_seconds else None
    hits: dict[str, list] = {p["id"]: [] for p in compiled}
    usage = Usage()

    for jsonl in _iter_session_jsonl(logs_root):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if cutoff is not None and mtime < cutoff:
            continue
        usage.sessions_seen += 1
        if usage.oldest_ts is None or mtime < usage.oldest_ts:
            usage.oldest_ts = mtime
        if usage.newest_ts is None or mtime > usage.newest_ts:
            usage.newest_ts = mtime

        # Pass 1: tool_use blocks (matched by policy) + map tool_use_id -> hit-index.
        # Pass 2: tool_result blocks — annotate hits.is_error from tool_use_id match.
        file_hits: list[dict] = []
        id_to_hit: dict[str, dict] = {}
        try:
            with jsonl.open() as fh:
                for line in fh:
                    usage.lines_seen += 1
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = rec.get("message")
                    if not isinstance(msg, dict):
                        continue
                    content = msg.get("content")
                    if not isinstance(content, list):
                        continue
                    rec_ts = rec.get("timestamp") or msg.get("timestamp")
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "tool_use":
                            tn = block.get("name") or "?"
                            inp = block.get("input") or {}
                            for p in compiled:
                                if p["_match"](tn, inp):
                                    hit = {
                                        "session": jsonl,
                                        "tool_name": tn,
                                        "input": inp,
                                        "timestamp": rec_ts,
                                        "tool_use_id": block.get("id"),
                                        "blocked": None,  # filled in pass 2 below
                                        "policy_id": p["id"],
                                    }
                                    file_hits.append(hit)
                                    if hit["tool_use_id"]:
                                        id_to_hit[hit["tool_use_id"]] = hit
                        elif btype == "tool_result":
                            tuid = block.get("tool_use_id")
                            hit = id_to_hit.get(tuid) if tuid else None
                            if hit is not None:
                                hit["blocked"] = bool(block.get("is_error"))
        except OSError:
            continue
        for h in file_hits:
            hits[h["policy_id"]].append(h)
    return hits, usage


def cmd_policy(args: argparse.Namespace, _usage: Usage) -> int:
    logs_root = pathlib.Path(args.logs).expanduser()
    since = args.days * 86400 if args.days else None
    hits, usage = collect_policy_hits(logs_root, since, DEFAULT_POLICIES)

    if args.json:
        out = {
            "window_days": args.days,
            "sessions": usage.sessions_seen,
            "violations": {
                pid: {
                    "severity": next(p["severity"] for p in DEFAULT_POLICIES if p["id"] == pid),
                    "attempted": len(items),
                    "blocked": sum(1 for h in items if h["blocked"]),
                    "succeeded": sum(1 for h in items if h["blocked"] is False),
                    "unknown": sum(1 for h in items if h["blocked"] is None),
                    "by_tool": dict(collections.Counter(h["tool_name"] for h in items)),
                    "first_ts": min((h["timestamp"] for h in items if h["timestamp"]), default=None),
                    "last_ts":  max((h["timestamp"] for h in items if h["timestamp"]), default=None),
                }
                for pid, items in hits.items()
            },
        }
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        # exit 1 only on SUCCEEDED block-severity hits — blocked attempts are ok
        any_block_succeeded = any(
            next(p["severity"] for p in DEFAULT_POLICIES if p["id"] == pid) == "block"
            and any(h["blocked"] is False for h in items)
            for pid, items in hits.items()
        )
        return 1 if any_block_succeeded else 0

    _print_header(usage, args.days)

    any_block_succeeded = False
    total_attempted = 0
    total_succeeded = 0
    for policy in DEFAULT_POLICIES:
        ph = hits.get(policy["id"], [])
        attempted = len(ph)
        blocked = sum(1 for h in ph if h["blocked"])
        succeeded = sum(1 for h in ph if h["blocked"] is False)
        unknown = sum(1 for h in ph if h["blocked"] is None)
        total_attempted += attempted
        total_succeeded += succeeded
        sev = policy["severity"]
        marker = "BLOCK" if sev == "block" else sev.upper()
        outcome = f"{attempted} attempted · {blocked} blocked · {succeeded} succeeded"
        if unknown:
            outcome += f" · {unknown} unknown"
        print(f"[{marker}] {policy['id']} — {outcome}")
        print(f"   reason: {policy['reason']}")
        if ph:
            if sev == "block" and succeeded > 0:
                any_block_succeeded = True
            by_tool = collections.Counter(h["tool_name"] for h in ph)
            timestamps = [h["timestamp"] for h in ph if h["timestamp"]]
            shown = args.show if args.show > 0 else len(by_tool)
            for tool_name, count in by_tool.most_common(shown):
                tool_blocked = sum(1 for h in ph if h["tool_name"] == tool_name and h["blocked"])
                tool_succ = sum(1 for h in ph if h["tool_name"] == tool_name and h["blocked"] is False)
                sample = next((h["session"] for h in ph if h["tool_name"] == tool_name), None)
                sample_str = sample.name if sample else "?"
                breakdown = f"{count}x ({tool_blocked} blocked, {tool_succ} succeeded)"
                print(_row(tool_name, f"{breakdown}   first in: {sample_str}"))
            if timestamps:
                print(f"   timespan: {min(timestamps)}  →  {max(timestamps)}")
        print()

    if total_attempted == 0:
        print("✓ no policy attempts in window.")
        return 0
    if any_block_succeeded:
        print(
            f"✗ {total_succeeded} BLOCK-severity attempt(s) succeeded. "
            f"Check the timespan above — if all succeeded calls predate your "
            f"hook install, that's expected; otherwise the hook is missing or broken. Exit 1."
        )
        return 1
    print(f"✓ {total_attempted} attempt(s); hook blocked all matching calls.")
    return 0


# ----- plugins: supply-chain audit on installed plugin marketplaces ----------


INSTALLED_PLUGINS_FILE = CLAUDE / "plugins" / "installed_plugins.json"
KNOWN_MARKETPLACES_FILE = CLAUDE / "plugins" / "known_marketplaces.json"

TRUSTED_MARKETPLACES = {"anthropics", "anthropic"}  # extend as needed


def _load_json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def cmd_plugins(args: argparse.Namespace, _usage: Usage) -> int:
    marketplaces = _load_json(KNOWN_MARKETPLACES_FILE)
    installed = _load_json(INSTALLED_PLUGINS_FILE).get("plugins", {})
    now = time.time()

    # Marketplace risk: not from a trusted org
    market_rows = []
    for name, meta in marketplaces.items():
        src = meta.get("source", {})
        repo = src.get("repo", "?")
        owner = repo.split("/")[0] if "/" in repo else "?"
        last = meta.get("lastUpdated") or ""
        try:
            from datetime import datetime
            last_age_d = (now - datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()) / 86400 if last else float("inf")
        except (ValueError, OSError):
            last_age_d = float("inf")
        market_rows.append({
            "name": name,
            "repo": repo,
            "trusted": owner.lower() in TRUSTED_MARKETPLACES,
            "last_update_age_d": last_age_d,
        })

    # Per-plugin metadata
    plugin_rows = []
    for plugin_id, entries in installed.items():
        if not entries:
            continue
        e = entries[0]
        # plugin_id like "frontend-design@claude-plugins-official"
        mp = plugin_id.split("@", 1)[-1] if "@" in plugin_id else "?"
        last = e.get("lastUpdated") or e.get("installedAt") or ""
        try:
            from datetime import datetime
            last_age_d = (now - datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()) / 86400 if last else float("inf")
        except (ValueError, OSError):
            last_age_d = float("inf")
        # Trust comes from the source marketplace's owner
        mp_meta = marketplaces.get(mp, {})
        repo = mp_meta.get("source", {}).get("repo", "")
        owner = repo.split("/")[0] if "/" in repo else "?"
        plugin_rows.append({
            "id": plugin_id,
            "marketplace": mp,
            "version": e.get("version", "?"),
            "last_update_age_d": last_age_d,
            "trusted_owner": owner.lower() in TRUSTED_MARKETPLACES,
            "owner": owner,
        })

    if args.json:
        out = {
            "marketplaces": market_rows,
            "plugins": plugin_rows,
        }
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return 1 if any(not r["trusted"] for r in market_rows) else 0

    print(f"paleo plugins · {len(market_rows)} marketplaces · {len(plugin_rows)} plugins")
    print()

    print("MARKETPLACES")
    for r in market_rows:
        tag = "trusted" if r["trusted"] else "third-party"
        age = _age_label(r["last_update_age_d"])
        print(_row(f"{r['name']}  [{tag}]", f"{r['repo']}  · last update {age}"))
    print()

    if plugin_rows:
        # Sort by trust then by age (oldest first surfaces most stale-risk)
        plugin_rows.sort(key=lambda r: (r["trusted_owner"], -r["last_update_age_d"]))
        print("PLUGINS")
        for r in plugin_rows:
            tag = " [third-party]" if not r["trusted_owner"] else ""
            age = _age_label(r["last_update_age_d"])
            print(_row(
                f"{r['id']}{tag}",
                f"v{r['version']}  · last updated {age}  · owner: {r['owner']}",
            ))
        print()

    third_party_count = sum(1 for r in market_rows if not r["trusted"])
    if third_party_count == 0:
        print("✓ all marketplaces are from trusted owners.")
        return 0
    print(f"⚠ {third_party_count} marketplace(s) not from trusted owners — audit before trusting fully.")
    return 1


def _age_label(days: float) -> str:
    if days == float("inf"):
        return "never"
    if days < 1:
        return "<1d ago"
    if days < 30:
        return f"{int(days)}d ago"
    if days < 365:
        return f"{int(days/30)}mo ago"
    return f"{days/365:.1f}y ago"


# ----- crons: surface silent cron failures -----------------------------------


import subprocess

# Capture `>> /path/to/log` in a cron command tail.
CRON_LOG_RE = re.compile(r">>?\s*(\S+)")


def _list_crons() -> list[tuple[str, str]]:
    """Return [(schedule, command_tail)] from `crontab -l`. Ignores blank/comment lines."""
    try:
        out = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, check=False
        ).stdout
    except FileNotFoundError:
        return []
    rows = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        schedule = " ".join(parts[:5])
        command = parts[5]
        rows.append((schedule, command))
    return rows


def _expected_interval_hours(schedule: str) -> float:
    """Best-effort interval estimate from a 5-field cron schedule."""
    parts = schedule.split()
    if len(parts) != 5:
        return 24.0
    minute, hour, dom, mon, dow = parts
    if dow not in ("*", "?"):
        return 24.0 * 7
    if dom not in ("*", "?"):
        return 24.0 * 28
    if hour != "*":
        return 24.0
    if "/" in minute:
        try:
            return float(minute.split("/")[1]) / 60.0
        except (IndexError, ValueError):
            return 1.0
    if minute != "*":
        return 1.0
    return 1 / 60.0


def cmd_crons(args: argparse.Namespace, _usage: Usage) -> int:
    crons = _list_crons()
    now = time.time()
    rows: list[dict] = []
    for schedule, command in crons:
        log_match = CRON_LOG_RE.search(command)
        log_path = pathlib.Path(log_match.group(1)) if log_match else None
        interval_h = _expected_interval_hours(schedule)
        slack = max(args.slack_hours, interval_h * 2)
        status = "ok"
        last_age_h = None
        if log_path is None:
            status = "no-log-redirect"
        elif not log_path.exists():
            status = "missing-log"
        else:
            try:
                age_s = now - log_path.stat().st_mtime
                last_age_h = age_s / 3600.0
                if last_age_h > slack:
                    status = "stale"
            except OSError:
                status = "stat-error"
        rows.append({
            "schedule": schedule,
            "command": command.split(">>")[0].strip()[:60],
            "log": str(log_path) if log_path else "",
            "interval_h": round(interval_h, 2),
            "last_age_h": round(last_age_h, 1) if last_age_h is not None else None,
            "status": status,
        })

    if args.json:
        json.dump({"crons": rows}, sys.stdout, indent=2, default=str)
        print()
        return 1 if any(r["status"] in ("missing-log", "stale", "stat-error") for r in rows) else 0

    print(f"paleo crons · {len(rows)} cron jobs")
    print()
    counts = collections.Counter(r["status"] for r in rows)
    print(
        f"  ok={counts.get('ok',0)}  stale={counts.get('stale',0)}  "
        f"missing-log={counts.get('missing-log',0)}  no-log-redirect={counts.get('no-log-redirect',0)}"
    )
    print()
    problem_rows = [r for r in rows if r["status"] != "ok"]
    if not problem_rows:
        print("✓ all crons have fresh logs.")
        return 0
    for r in problem_rows:
        tag = f"[{r['status'].upper()}]"
        line = f"{tag} {r['schedule']}  {r['command']}"
        if r["last_age_h"] is not None:
            line += f"  (log {r['last_age_h']:.0f}h old, expected ≤ {max(args.slack_hours, r['interval_h']*2):.0f}h)"
        print(line)
        if r["log"]:
            print(f"     log: {r['log']}")
    print()
    print(f"{len(problem_rows)} cron(s) need attention.")
    return 1


# ----- claims: cross-check MEMORY.md paths against disk ----------------------


# Path extraction has two shapes:
#   1. Backtick-wrapped: `…/anything except backticks…` — allows spaces
#   2. Bare:             ~ or /home/<user>/  followed by safe chars only
BACKTICK_PATH = re.compile(r"`(?P<path>(?:~|/home/[a-z0-9_-]+)[^`]+)`")
BARE_PATH = re.compile(
    r"(?<![`A-Za-z0-9_/.])(?P<path>(?:~|/home/[a-z0-9_-]+)[/.A-Za-z0-9_+\-]+)"
)
# Heuristic: paths containing placeholder tokens are templates, not real paths.
PLACEHOLDER_TOKENS = ("<", ">", "${", "*", " X.X", "/X.X", "X.X/", "X.X.")
# Markdown link pattern: [label](file.md) — only relative paths
MD_LINK = re.compile(r"\]\((?P<href>[^)]+\.md)\)")

# Skip paths shorter than this (single chars like `/` or `~`)
PATH_MIN_LEN = 6


def _expand(p: str) -> pathlib.Path:
    return pathlib.Path(p.replace("~", str(HOME), 1)) if p.startswith("~") else pathlib.Path(p)


def _walk_memory(memory_md: pathlib.Path) -> list[pathlib.Path]:
    """Return memory_md plus every relative .md link reachable from it."""
    seen: set[pathlib.Path] = set()
    queue = [memory_md.resolve()]
    out: list[pathlib.Path] = []
    while queue:
        cur = queue.pop()
        if cur in seen or not cur.exists():
            continue
        seen.add(cur)
        out.append(cur)
        try:
            text = cur.read_text(errors="replace")
        except OSError:
            continue
        for m in MD_LINK.finditer(text):
            href = m.group("href").strip()
            if href.startswith(("http://", "https://", "/")):
                continue
            child = (cur.parent / href).resolve()
            if child.suffix == ".md":
                queue.append(child)
    return out


_CONTEXT_NEGATIVE = re.compile(
    r"(?i)\b(?:no longer (?:on disk|present|exists?)|does not exist|never (?:created|existed|fired)|graveyarded?|deleted)\b"
)


def _extract_paths(text: str) -> set[str]:
    """Return paths mentioned in text. Backtick-wrapped paths preserve spaces.

    Lines whose surrounding context explicitly says a path is missing
    (e.g. "no longer on disk", "does not exist") are skipped — those are
    documentation of absence, not assertions of presence.
    """
    found: set[str] = set()
    # Walk line-by-line so we can apply the negative-context heuristic.
    for line in text.splitlines():
        if _CONTEXT_NEGATIVE.search(line):
            continue
        for m in BACKTICK_PATH.finditer(line):
            p = m.group("path").rstrip()
            if len(p) >= PATH_MIN_LEN and not _looks_like_template(p):
                found.add(p)
        # Bare paths must not be inside a backtick-wrapped slice we already captured.
        for m in BARE_PATH.finditer(line):
            p = m.group("path").rstrip(".,;:)")
            if len(p) >= PATH_MIN_LEN and not _looks_like_template(p):
                found.add(p)
    return found


def _looks_like_template(path: str) -> bool:
    return any(t in path for t in PLACEHOLDER_TOKENS)


def cmd_claims(args: argparse.Namespace, _usage: Usage) -> int:
    if args.memory is None:
        candidates = sorted((CLAUDE / "projects").glob("*/memory/MEMORY.md"))
        if not candidates:
            print(
                "no MEMORY.md found under ~/.claude/projects/*/memory/. "
                "Pass --memory <path> to specify one.",
                file=sys.stderr,
            )
            return 2
        memory_md = candidates[0]
    else:
        memory_md = pathlib.Path(args.memory).expanduser().resolve()
    if not memory_md.exists():
        print(f"memory file not found: {memory_md}", file=sys.stderr)
        return 2

    files = _walk_memory(memory_md)
    stale_after = args.stale_days * 86400 if args.stale_days else None
    now = time.time()

    total_paths = 0
    missing_by_file: dict[pathlib.Path, list[str]] = {}
    stale_by_file: dict[pathlib.Path, list[tuple[str, float]]] = {}

    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        paths = _extract_paths(text)
        for raw in paths:
            total_paths += 1
            disk = _expand(raw)
            if not disk.exists():
                missing_by_file.setdefault(f, []).append(raw)
                continue
            if stale_after is not None:
                try:
                    age = now - disk.stat().st_mtime
                except OSError:
                    continue
                if age > stale_after:
                    stale_by_file.setdefault(f, []).append((raw, age))

    if args.json:
        out = {
            "memory_files": len(files),
            "paths_checked": total_paths,
            "missing": {str(f): sorted(set(v)) for f, v in missing_by_file.items()},
            "stale": {
                str(f): [(p, age) for p, age in sorted(set(v))]
                for f, v in stale_by_file.items()
            },
        }
        json.dump(out, sys.stdout, indent=2, default=str)
        print()
        return 1 if missing_by_file else 0

    print(
        f"paleo claims · {len(files)} memory files · {total_paths} claim paths"
    )
    print()

    missing_count = sum(len(v) for v in missing_by_file.values())
    stale_count = sum(len(v) for v in stale_by_file.values())

    if missing_by_file:
        for f, paths in sorted(missing_by_file.items()):
            print(f"[MISSING] {f.name}")
            for p in sorted(set(paths)):
                print(_row(p, "not found on disk"))
            print()

    if stale_by_file:
        for f, items in sorted(stale_by_file.items()):
            print(f"[STALE]   {f.name}")
            for p, age in sorted(set(items)):
                print(_row(p, f"unmodified {_fmt_age(age)}"))
            print()

    if not missing_by_file and not stale_by_file:
        print(f"✓ all {total_paths} claim paths verified.")
        return 0

    summary = f"{missing_count} missing"
    if stale_count:
        summary += f", {stale_count} stale"
    summary += f" (of {total_paths} total)"
    print(summary)
    return 1 if missing_by_file else 0


def cmd_agents(args: argparse.Namespace, usage: Usage) -> int:
    _print_header(usage, args.days)
    agents = discover_agents()
    used = usage.agent_uses
    rows = sorted(
        [(a, used.get(a, 0)) for a in agents] + [(a, used[a]) for a in used if a not in agents],
        key=lambda r: -r[1],
    )
    print(f"AGENTS · {len(agents)} installed · {len(used)} distinct used")
    for a, count in rows:
        tag = "  (dead)" if count == 0 else ""
        print(_row(a, f"{count}x{tag}"))
    return 0


# ----- entry point -----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paleo",
        description="Agent workspace archeology — surface dead capabilities.",
    )
    p.add_argument(
        "--logs",
        default=str(PROJECTS_DIR),
        help="Root containing session JSONL logs (default: ~/.claude/projects)",
    )
    p.add_argument(
        "--days",
        type=float,
        default=None,
        help="Only consider sessions modified in the last N days (default: all)",
    )
    p.add_argument(
        "--show",
        type=int,
        default=15,
        help="Max rows to show per section (0 = none). Default 15.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the human report.",
    )

    sub = p.add_subparsers(dest="cmd", required=True)
    for name, helptext in [
        ("dead", "Dead-capability map (skills+mcps+agents)"),
        ("top", "Top-used tools by raw count"),
        ("skills", "Skill invocation table"),
        ("mcps", "MCP usage table"),
        ("agents", "Subagent usage table"),
        ("policy", "Detect tool invocations that violate hard-block policies"),
        ("claims", "Fact-check paths referenced in your MEMORY.md tree against disk"),
        ("crons", "Surface silent cron failures (logs that stopped updating)"),
        ("plugins", "Audit installed plugin marketplaces — owner, age, supply-chain risk"),
    ]:
        sp = sub.add_parser(name, help=helptext)
        if name == "skills":
            sp.add_argument("--used-only", action="store_true")
        if name == "crons":
            sp.add_argument(
                "--slack-hours",
                type=float,
                default=25.0,
                help="Minimum slack tolerated for any cron, in hours (default 25 = 1 day + 1h).",
            )
        if name == "claims":
            sp.add_argument(
                "--memory",
                default=None,
                help=(
                    "Path to your memory index file. Default: auto-detect via "
                    "~/.claude/projects/*/memory/MEMORY.md (first match)."
                ),
            )
            sp.add_argument(
                "--stale-days",
                type=float,
                default=None,
                help="If set, flag paths whose mtime is older than N days as STALE.",
            )

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logs_root = pathlib.Path(args.logs).expanduser()
    since = args.days * 86400 if args.days else None
    usage = collect_usage(logs_root, since)
    dispatch = {
        "dead": cmd_dead,
        "top": cmd_top,
        "skills": cmd_skills,
        "mcps": cmd_mcps,
        "agents": cmd_agents,
        "policy": cmd_policy,
        "claims": cmd_claims,
        "crons": cmd_crons,
        "plugins": cmd_plugins,
    }
    return dispatch[args.cmd](args, usage)


if __name__ == "__main__":
    sys.exit(main())
