# paleo

[![tests](https://github.com/sb-arnav/paleo/actions/workflows/test.yml/badge.svg)](https://github.com/sb-arnav/paleo/actions/workflows/test.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)

> Agent workspace archeology. Surfaces silent decay in long-lived Claude Code workspaces.

`paleo` reads your `~/.claude/projects/*/*.jsonl` session logs (and your crontab, and your `MEMORY.md`) and tells you four things you don't currently know:

1. **Which skills, MCP servers, and subagents are installed but never invoked.** Most Claude Code power-user workspaces accumulate hundreds of capabilities; without telemetry you can't tell what's load-bearing and what's fossil.
2. **Which tool calls violate your hard-block rules.** If you've decided "never call `mcp__claude_ai_*`" — has anything called it anyway? Were the attempts actually blocked, or did they succeed?
3. **Which paths cited in your `MEMORY.md` no longer exist on disk.** Notes age silently; "the log lives at `~/.claude/foo.log`" stays convincing for months after `foo.log` was deleted.
4. **Which of your crons silently stopped firing.** Logs that should have been touched today and weren't.

No network. No mutation. Stdlib only. Reads from `~/.claude/`.

## What you might find

Real numbers from a long-lived power-user workspace:

```
$ paleo --days 30 dead
SKILLS   installed=248  used=10   dead=238
MCPs     installed=14   used=0    dead-local=14
AGENTS   installed=73   used=13   dead=60

$ paleo --days 30 policy
[BLOCK] shared-account-mcp — 8 attempted · 3 blocked · 5 succeeded
   timespan: 2026-05-12T14:53Z  →  2026-05-18T18:55Z
✗ 5 BLOCK-severity attempt(s) succeeded — check whether the timespan
  predates your hook install. Exit 1.

$ paleo claims
[MISSING] some-old-feedback.md
  /home/me/old-build-dir/                  not found on disk
  ~/dreaming/logs/cron-thing.log           not found on disk
3 missing (of 78 total)

$ paleo crons
[STALE]       0 21 * * *   /usr/bin/python3 ~/scripts/daily-job-a.py   log 58h old
[STALE]       5 21 * * *   /usr/bin/python3 ~/scripts/daily-job-b.py   log 58h old
[MISSING-LOG] 30 22 * * *  /usr/bin/python3 ~/scripts/never-fired.py
[MISSING-LOG]  0  0 * * 1  /usr/bin/python3 ~/scripts/weekly-thing.py
8 cron(s) need attention.
```

A first run on a workspace that's been in heavy use for months typically surfaces:
- A high percentage of installed skills never invoked (the long tail of plugin installs).
- Several memory notes pointing at paths that no longer exist.
- At least one cron whose log file hasn't been touched in days — most often because the host machine was shut down for a while, but sometimes because the script has been broken since install.
- Plus, the contrast between **attempted** and **succeeded** policy violations: a hard-block hook that's working will block attempts but they still appear in JSONL.

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
| `claims` | Paths cited in MEMORY.md tree that no longer exist | any missing path |
| `crons` | Cron jobs whose log file hasn't been touched recently | any stale or missing |
| `plugins` | Plugin marketplaces + per-plugin metadata; flags third-party marketplaces | any third-party marketplace |
| `top` | Top tools by raw invocation count | never |
| `skills` | Per-skill usage table | never |
| `mcps` | Per-MCP usage table (local + plugin + ghost connectors) | never |
| `agents` | Per-subagent dispatch count | never |

## Common flags

- `--days N` — restrict to sessions modified in last N days. Omit for all-time.
- `--show N` — max rows per section (default 15, `0` = none).
- `--json` — machine-readable output (currently `dead`, `policy`, `claims`, `crons`, `plugins`). Pipes into `jq`.
- `--logs PATH` — point at a different log root if you keep your `~/.claude/` elsewhere.

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

Composes with `git commit` gates, CI, or a `Stop` hook.

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

- Extend `paleo claims` to verify cron schedules embedded in prose ("runs daily at 07:00 IST" → check crontab too).
- Load policies from `~/.claude/paleo-policy.json` instead of editing source.
- Per-project breakdown (which project surfaced which dead skill?).
- `--since-commit <sha>` to scope to recent activity instead of wall-clock days.

## License

MIT. See [LICENSE](./LICENSE).
