# paleo

[![tests](https://github.com/sb-arnav/paleo/actions/workflows/test.yml/badge.svg)](https://github.com/sb-arnav/paleo/actions/workflows/test.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)

> Agent workspace archeology. The diff between what your Claude Code workspace claims is true and what your sessions actually show.

You have 248 skills installed. You used 10 this month. Your `MEMORY.md` points at a log file you deleted in March. One of your hard-block hooks stopped firing two weeks ago and nothing told you. A cron has been silently dead since the last reboot.

None of this shows up anywhere. Claude Code has no dashboard for workspace decay, and the data that would reveal it — your session JSONL, your settings, your crontab — is exactly the data nobody reads.

`paleo` reads it. One command:

```
$ paleo health
WORKSPACE HEALTH

  [✓] dead       238 skills · 60 agents · 14 mcps never invoked
  [✓] policy     2 attempts, 0 succeeded (all blocked)
  [✓] hooks      0 of 4 Stop hooks never fired, 1 orphan
  [✓] ghosts     0 subagent runs did nothing
  [✗] claims     2 missing of 80 paths checked in MEMORY.md
  [✗] crons      2 of 10 jobs need attention
  [✗] plugins    1 third-party of 2 marketplaces

Run the individual subcommand for details on any failing line.
```

Exit code is non-zero when anything's wrong, so `paleo health` drops straight into a daily-digest cron or a CI gate. No network. No mutation. Stdlib only. Reads from `~/.claude/`.

## The seven things it checks

Each subcommand surfaces one place where your workspace's claim diverges from what actually happened:

1. **`dead`** — config says "248 skills installed," sessions say "you used 10." Most power-user workspaces accumulate hundreds of skills/MCPs/subagents; without telemetry you can't tell load-bearing from fossil.
2. **`policy`** — your hook says "blocked," the JSONL says "succeeded." If you decided "never call `mcp__claude_ai_*`," has anything called it anyway — and did the call actually get stopped?
3. **`hooks`** — `settings.json` says "installed," the JSONL says "never fired." Catches Stop hooks that broke silently (the failure mode in [#16047](https://github.com/anthropics/claude-code/issues/16047) / [#2891](https://github.com/anthropics/claude-code/issues/2891)) plus orphan hooks still firing from uninstalled plugins.
4. **`ghosts`** — a subagent says "I created the file," the JSONL says it made zero tool calls. The provably-false success in [#4462](https://github.com/anthropics/claude-code/issues/4462) (open, 37+ comments, unfixed).
5. **`claims`** — `MEMORY.md` says "log at `~/foo.log`," disk says "no such file." Notes stay convincing for months after the path they cite is gone ([this is an open Anthropic bug](https://github.com/anthropics/claude-code/issues/26757)).
6. **`crons`** — crontab says "fires daily," the log mtime says "not in four days."
7. **`plugins`** — flags third-party plugin marketplaces. Claude Code plugins are a supply-chain surface; you should know whose code you're running.

## What a first run usually surfaces

On a workspace that's been in heavy use for months:
- A high percentage of installed skills never invoked — the long tail of plugin installs.
- Several memory notes pointing at paths that no longer exist.
- At least one cron whose log hasn't moved in days — sometimes because the host was off, sometimes because the script has been broken since install.
- An orphan hook or two: still firing in your sessions, but no longer in any config file.
- The contrast between **attempted** and **succeeded** policy violations — a working hard-block hook blocks attempts, but they still appear in JSONL, so you can prove the hook is doing its job.

## Install

```bash
git clone https://github.com/sb-arnav/paleo ~/paleo
ln -s ~/paleo/paleo.py ~/.local/bin/paleo   # optional
```

Requires Python 3.10+. Zero dependencies.

## Subcommands

| Command | Surfaces | Exit non-zero when |
|---|---|---|
| `dead` | Installed skills/MCPs/subagents that were never invoked in window | never |
| `policy` | Tool invocations matching hard-block rules | BLOCK attempt **succeeded** |
| `hooks` | Stop hooks configured but never fired (silent breakage detection), plus orphan fires from removed hooks | any Stop hook never fired in window |
| `claims` | Paths cited in MEMORY.md tree that no longer exist | any missing path |
| `crons` | Cron jobs whose log file hasn't been touched recently | any stale or missing |
| `ghosts` | Subagents that claimed a side-effect (file write, test run, commit) but made zero tool calls | any ghost found |
| `plugins` | Plugin marketplaces + per-plugin metadata; flags third-party marketplaces | any third-party marketplace |
| `health` | One-screen summary across `dead`, `policy`, `hooks`, `ghosts`, `claims`, `crons`, `plugins` — designed for daily-digest cron use | any constituent check fails |
| `top` | Top tools by raw invocation count | never |
| `skills` | Per-skill usage table | never |
| `mcps` | Per-MCP usage table (local + plugin + ghost connectors) | never |
| `agents` | Per-subagent dispatch count | never |
| `project` | Activity attributed to the project (`cwd`) each session ran in — sessions, tool calls, top tools/skills | never |
| `ghosts` | Subagents that claimed a side-effect (file write, test run, commit) but made zero tool calls | any ghost found |

## Common flags

- `--days N` — restrict to sessions modified in last N days. Omit for all-time.
- `--show N` — max rows per section (default 15, `0` = none).
- `--json` — machine-readable output (currently `dead`, `policy`, `hooks`, `ghosts`, `claims`, `crons`, `plugins`, `project`, `health`). Pipes into `jq`.
- `--logs PATH` — point at a different log root if you keep your `~/.claude/` elsewhere.

## Project (where your time actually went)

`paleo project` attributes each session to the project directory it ran in (its dominant `cwd`) and aggregates activity: session count, tool calls, and the top tools and skills used in each.

```bash
paleo project               # all projects, busiest first
paleo --days 7 project      # this week only
paleo --json project        # pipe into jq
```

```
PROJECTS · 9 projects · 290 sessions

  app-frontend       159 sessions ·  9,630 tool calls · last 5m ago
     tools: Bash(5072)  Read(2316)  Edit(1038)  Write(375)  WebSearch(204)
     skills: deploy(4)  e2e-check(2)
  api-service         32 sessions ·  2,230 tool calls · last 18h ago
     tools: Bash(905)  Read(466)  Edit(164)  Write(140)
  ...
```

It answers "which project is eating my Claude Code time" and "which skills actually fire where" — so a workspace-global `dead` finding ("you never use skill X") can be checked against the project it would have fired in. Informational: never exits non-zero. Sessions wander across directories, so attribution uses the dominant `cwd` per session (empirically 70–100% of a session's records share one home directory). Agent worktrees (`<project>/.claude/worktrees/agent-*`) are rolled up to their parent project — they're sub-agent execution sandboxes, and their work belongs to the project that spawned them.

## Claims (memory fact-check)

`paleo claims` walks your `MEMORY.md` index and every `.md` link it transitively references, extracts every Unix path mentioned (backtick-wrapped or bare), and checks them against disk.

```bash
paleo claims                     # paths missing on disk → exit 1
paleo claims --stale-days 60     # also surface paths whose mtime is older than N days
paleo claims --memory <path>     # point at a different memory index
```

The point is to catch silent rot: notes that say "X is wired" or "log file at Y" stay convincing in memory long after the underlying paths disappear. Default behavior auto-detects `~/.claude/projects/*/memory/MEMORY.md`. The extractor is conservative about false positives — it skips lines that explicitly mark a path as absent ("no longer on disk", "graveyarded", "never created") and template placeholders (`X.X`, `${...}`, glob `*`).

## Policy checks

`paleo policy` scans for tool invocations that violate hard-block rules — and uses each tool_use's matching tool_result.is_error to distinguish **blocked attempts** (your hook caught it) from **succeeded ones** (it got through).

Default policy:

- **`shared-account-mcp`** — any `mcp__claude_ai_*` invocation. These connectors authenticate via OAuth at claude.ai; on a shared/borrowed/team subscription, every call writes against that account scope. Likely a data-leak risk. Edit `DEFAULT_POLICIES` in `paleo.py` to disable or modify.

Adding your own rules:

```python
{
    "id": "my-rule",
    "match": {"tool_prefix": "Bash"},        # or "tool_exact" or "tool_regex"
    "severity": "block",                     # "block" → exit 1 if SUCCEEDED; anything else → exit 0
    "reason": "Why this is forbidden",
}
```

Exit codes:
- `0` — no attempts, OR all matching attempts were blocked by your hooks.
- `1` — at least one BLOCK-severity attempt actually succeeded.

Excluding pre-hook history with `--since`:

```bash
paleo policy --since 2026-05-21                       # whole-day cutoff (00:00 UTC)
paleo policy --since 2026-05-21T11:04:00Z             # precise hook install time
paleo health --since 2026-05-21T11:04:00Z             # same flag works on health
```

Why this matters: when you install a hard-block hook part-way through your JSONL history, every pre-install attempt remains in the log forever. Without `--since`, the dashboard yells about historical violations until they age out of the `--days` window. With it, you anchor the policy to "from this moment on" — succeeded violations now mean "the hook is actually broken," not "you've had this hook for less than 30 days." Use `stat -c %y ~/.claude/hooks/your-hook.sh | cut -d. -f1` to get the exact install timestamp.

Composes with `git commit` gates, CI, or a `Stop` hook.

## Hooks (silent breakage detection)

`paleo hooks` enumerates every hook configured across `~/.claude/settings.json`, `~/.claude/settings.local.json`, and each enabled plugin's `hooks/hooks.json` (resolved via `installed_plugins.json`), then cross-references against JSONL `stop_hook_summary` records to determine which Stop hooks actually fired.

```bash
paleo hooks                 # exits 1 if any Stop hook never fired in window
paleo --days 7 hooks        # tighter window for fast feedback
paleo --json hooks          # pipe into jq
```

What you'll see:

```
paleo hooks · 33 configured · 4 firing · 0 never-fired · 29 config-only

FIRING (Stop hooks)
    387x  avg  292ms  ⚠ max 17000ms  last: 2026-05-21T13:16:11
         bash ~/.claude/hooks/session-state-write.sh
    ...

CONFIG-ONLY (29 hooks on non-Stop events — fire status not observable from JSONL)
  PreToolUse: 8
  UserPromptSubmit: 7
  ...

ORPHAN FIRES (1 commands fired but not in any settings file —
              uninstalled plugin or removed hook still referenced)
    320x  bash "${CLAUDE_PROJECT_DIR}/.claude/hooks/uat-evidence-required.sh"
```

Three signals, each catches a distinct failure mode:

- **NEVER-FIRED** — a Stop hook is configured in `settings.json` but no `stop_hook_summary` record references it in the window. Most likely cause: the script broke silently. Maps directly to anthropics/claude-code issues [#16047](https://github.com/anthropics/claude-code/issues/16047) ("Hooks stop executing after ~2.5 hours… no error messages, warnings, or indication of failure") and [#2891](https://github.com/anthropics/claude-code/issues/2891).
- **ORPHAN FIRES** — a hook is still firing in your sessions but doesn't exist in any current config file. Usually means you uninstalled the plugin that registered it, but Claude Code is still running it from a stale cache. Catch-and-investigate.
- **⚠ slow max-duration** — Stop hooks above 1s of `durationMs` are flagged inline. Slow Stop hooks block Claude's response cycle.

**Why only Stop hooks?** Claude Code emits an explicit `hookInfos` array (per-hook command + duration) only on system records with `subtype=stop_hook_summary`. Fires of PreToolUse / PostToolUse / SessionStart / UserPromptSubmit / Notification / PostCompact are not recorded in JSONL. paleo lists them as `config-only` so you know what's registered, but cannot verify they fired.

## Ghosts (subagents that lied about doing work)

[anthropics/claude-code#4462](https://github.com/anthropics/claude-code/issues/4462) is open, has 37+ comments, and is still unfixed: sub-agents report success, burn tokens, and never persist anything to disk. People in that thread describe burning through session limits only to find empty output folders and a `tasks.md` edited to *claim* the work was done. One commenter: *"we can ask Claude 100 times to verify it persisted to disk, and it will lie 100 times."*

In-loop self-verification fails because the model lies about its own writes. An out-of-band check that reads the artifacts directly is the only thing you can trust — which is exactly paleo's shape.

```bash
paleo ghosts                    # exits 1 if any ghost found
paleo --days 1 ghosts           # just today's runs (good in a Stop hook)
paleo ghosts --min-tokens 5000  # raise the floor
```

```
GHOSTS · 2 subagent run(s) claimed a side-effect with 0 tool calls · 27,500 tokens burned (≥1,000 floor)

     21,300 tokens · gsd-executor  (2026-05-22T10:15:49)
             claimed: "created the integration tests in tests/auth.test.ts"  — but made 0 tool calls
             session: 5ce5c116-....jsonl
      6,200 tokens · general-purpose  (2026-05-22T11:02:13)
             claimed: "ran the test suite"  — but made 0 tool calls
             session: a1b2c3d4-....jsonl
```

A ghost is a subagent whose `toolUseResult` shows `status: completed` and `totalToolUseCount: 0`, **but whose return text asserts a side-effect** — "created the file," "ran the tests," "committed the changes." With zero tool calls, that assertion is provably false: nothing happened. paleo prints the matched claim so each finding justifies itself.

The action-claim gate is what makes this precise. A research or writing agent that legitimately returns prose with zero tool calls (e.g. "here are the three drafts you asked for") makes no side-effect claim and is **not** flagged — verified against a real workspace where the naive "zero tool calls" heuristic false-positived on a content-generation agent. paleo errs toward precision: it would rather miss a phantom than cry wolf on an agent that did its job. Detection is heuristic (it reads the claim, not the disk), so treat findings as "look here," not "proven."

## Crons (silent failure detection)

`paleo crons` reads `crontab -l`, parses the schedule + log-redirect target (`>> /path/to/log`), and checks each log file's mtime against the expected fire interval (with a configurable slack). Catches the failure mode where cron silently stops without anything else noticing.

```bash
paleo crons                      # default: 25h slack OR 2× expected interval, whichever is larger
paleo crons --slack-hours 12     # tighter (good for hourly crons)
paleo --json crons | jq '.crons[] | select(.status != "ok")'
```

## Plugins (supply-chain audit)

`paleo plugins` reads `~/.claude/plugins/{known_marketplaces.json, installed_plugins.json}` and reports each marketplace + each installed plugin, flagging marketplaces from non-trusted owners.

```bash
paleo plugins
```

Trusted-owner list is hardcoded (`anthropics`); extend `TRUSTED_MARKETPLACES` in source. Motivated by the May 2026 GitHub VSCode-extension supply-chain breach — Claude Code plugins are the same risk surface.

## What this is not

- Not a network tool. No telemetry leaves your machine.
- Not a recommender — it surfaces gaps, it doesn't tell you which dead skills to prune. You decide.
- Not a session-debugger. There are good ones for that (`cc-birdee`, `ccdiag`, `sana`, `claude-code-trace`). `paleo`'s wedge is the installed-vs-invoked diff plus the claims/crons/policy audits — workspace state across many sessions, not per-session debugging.

## Design notes

- Stdlib only. No pip install, no venv, no dependencies to keep current.
- Read-only against `~/.claude/`, `crontab -l`, and disk. Cannot break your workspace.
- One file (`paleo.py`). Easy to fork, easy to vendor into another tool.
- Policy rules are declarative (`tool_prefix` / `tool_exact` / `tool_regex`). No `eval`, safe to load from JSON later.

## Roadmap

- `paleo cost` — per-skill / per-MCP / per-project token attribution from `message.usage` (close the gap with [unclog](https://github.com/thomaschill/unclog)'s token-cost angle without copying its deletion UI).
- Per-project breakdown — group every check by `cwd` / `gitBranch` so dead-skill findings aren't workspace-global noise.
- Extend `paleo claims` to verify cron schedules embedded in prose ("runs daily at 07:00 IST" → check crontab too).
- Load policies from `~/.claude/paleo-policy.json` instead of editing source.
- `--since-commit <sha>` to scope to recent activity instead of wall-clock days.

## License

MIT. See [LICENSE](./LICENSE).
