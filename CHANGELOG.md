# Changelog

## v0.13 — 2026-05-22

- Bare `paleo` (no subcommand) now runs `health` instead of erroring with a usage message — the first thing a new user types now does the most useful thing.
- Added `--version` (and a `__version__` string in source).
- 3 new tests (49 total): bare-invocation default, explicit-subcommand preserved, version string present.

## v0.12 — 2026-05-22

- `paleo project` now rolls agent worktrees (`<project>/.claude/worktrees/agent-<hash>`) up to their parent project. These are isolated sub-agent execution sandboxes, not projects — before this, each one showed as its own one-session "project," fragmenting the view and hiding that the work belonged to the parent. On the dev workspace this collapsed 8 phantom entries and reclaimed their sessions into the parent projects that spawned them.
- 2 new tests (46 total): worktree path normalization, and worktree activity attributed to the parent project.

## v0.11 — 2026-05-22

- New: `paleo ghosts` — detects sub-agents that reported `status: completed` with `totalToolUseCount: 0` **and** whose return text asserts a side-effect ("created the file," "ran the tests," "committed"). With zero tool calls that claim is provably false: nothing was persisted. This is the open, unfixed failure in [anthropics/claude-code#4462](https://github.com/anthropics/claude-code/issues/4462) (37+ comments). Prints the matched claim so each finding justifies itself. Exits 1 on any ghost; wired into `health`; supports `--json` and `--min-tokens`.
- The action-claim gate is the whole design. An early version flagged any zero-tool-call completion — and immediately false-positived on a real content-generation agent (asked to "write three short pieces," it correctly returned prose with no tool calls). Flagging that would have made paleo itself a claim-vs-reality failure. The gate flips the rule to: only flag agents that *asserted* a side-effect they couldn't have performed. High precision over recall — paleo would rather miss a phantom than cry wolf.
- Detection is heuristic (reads the claim, not the disk). Findings mean "look here," not "proven."
- 6 new tests (44 total): phantom-with-claim flagged, prose agent NOT flagged (the false-positive guard), zero-token exclusion, tool-using agent excluded, `--min-tokens` floor, failed-agent exclusion.

## v0.10 — 2026-05-22

- New: `paleo project` — attributes each session to the project directory it ran in (dominant `cwd`) and aggregates session count, tool calls, and top tools/skills per project. Answers "which project is eating my Claude Code time" and "which skills fire where," so a workspace-global `dead` finding can be checked against the project it would have fired in. Informational; never exits non-zero. Supports `--json`.
- Attribution uses the dominant `cwd` per session because sessions wander across directories — empirically the home directory covers 70–100% of a session's records.
- README rewritten: leads with the pain (not a feature list), promotes `paleo health` to the hero block, and reframes all checks under one thesis — each surfaces a place where the workspace's claim diverges from what the sessions show. Links the Anthropic issues documenting the claims (#26757) and hooks (#16047, #2891) failure modes.
- 3 new tests (38 total): dominant-cwd attribution, multi-session aggregation, no-cwd skip.

## v0.9 — 2026-05-21

- New: `paleo hooks` — enumerates every Claude Code hook configured across `~/.claude/settings.json`, `~/.claude/settings.local.json`, and each enabled plugin's `hooks/hooks.json` (resolved via `installed_plugins.json` `installPath`), then cross-references against JSONL `stop_hook_summary` records to determine which Stop hooks actually fired.

  Three signals surfaced:
  - **NEVER-FIRED** — Stop hook in config but no `stop_hook_summary` record in window. Maps to anthropics/claude-code issues #16047 ("Hooks stop executing after ~2.5 hours… no error messages, warnings, or indication of failure") and #2891.
  - **ORPHAN FIRES** — hook still firing in sessions but no longer in any config file. Catches uninstalled-plugin-still-running and stale-cache cases.
  - **slow max-duration** — Stop hooks above 1s `durationMs` flagged inline; they block Claude's response cycle.

  Non-Stop event hooks (PreToolUse / PostToolUse / SessionStart / UserPromptSubmit / Notification / PostCompact) are listed as `config-only` because Claude Code does not emit fire records into JSONL for those event types.

- `paleo health` now includes the `hooks` line and fails if any Stop hook is never-fired in window.

- On the dev workspace's first run: 4 Stop hooks firing healthy (387/384/365/358 fires each over 30 days), 1 orphan fire surfaced (`uat-evidence-required.sh` — 320 historical fires, script no longer exists on disk anywhere), 29 hooks correctly classified as config-only.

- 5 new tests (35 total): hooks-file parsing of nested settings shape, malformed-input tolerance, subtype filtering in fire collection, event classification.

## v0.8.2 — 2026-05-21

- New: `--since TIMESTAMP` on `paleo policy` and `paleo health`. Drops policy hits older than the given moment, so hard-block hooks installed mid-history don't poison the dashboard with pre-install attempts. Accepts `YYYY-MM-DD` (00:00 UTC) or full ISO 8601 (e.g. `2026-05-21T11:04:00Z`). Common pairing: `paleo health --since "$(stat -c %y ~/.claude/hooks/<hook>.sh | cut -d. -f1)"`.
- On the development workspace, a hard-block hook installed today turned policy from "9 attempts, 5 succeeded ✗" into "1 attempt, 0 succeeded ✓" once the cutoff was applied — confirming the hook is working without 30 days of false-alarm noise.
- 4 new tests (30 total): cutoff filters pre-cutoff hits, ISO/date parsing, garbage rejection, and timestamp-less hits stay kept (defensive).

## v0.8.1 — 2026-05-21

- `paleo claims` now also recognizes double-quoted paths (`--file="/x/y z/foo"`) as path claims, allowing spaces inside the quotes — same as backtick-wrapped. Catches paths inside shell command examples in memory notes (e.g. `gradle --signing.store.file="/path with space/key.jks"`). The bare-path regex's negative lookbehind now also skips the inside of `"..."` slices so we don't double-count partial matches.
- New test for the quoted-path case (26 total).
- On the development workspace: missing-path findings dropped further to 2 (from 8 at v0.6).

## v0.8 — 2026-05-21

- New: `paleo health` — composes `dead`, `policy`, `claims`, `crons`, and `plugins` into a single one-screen summary. Exits non-zero when any constituent check would have exited non-zero. Intended for daily-digest cron use:

  ```
  $ paleo health
  WORKSPACE HEALTH

    [✓] dead       239 skills · 60 agents · 14 mcps never invoked
    [✗] policy     9 attempts, 5 succeeded (see details)
    [✗] claims     4 missing of 81 paths checked
    [✓] crons      0 of 10 jobs need attention
    [✗] plugins    1 third-party of 2 marketplaces
  ```

- Also available as `paleo --json health` for dashboard ingestion.
- Test added (25 total).

## v0.7 — 2026-05-21

- `paleo claims` is meaningfully more precise. Three improvements:
  - **Backtick paths can contain spaces.** `\`/home/x/My Folder/file\`` no longer truncates to `/home/x/My`.
  - **Plus signs allowed in bare paths.** `~/.jdk/jdk-17.0.13+11` parses fully now.
  - **Template-placeholder paths are skipped.** Anything containing `X.X`, `${...}`, `<...>`, or glob `*` is treated as a literal example, not an asserted path.
  - **Context-aware negation skip.** Lines containing "no longer on disk", "does not exist", "graveyarded", "never created", etc. are skipped — those are *documentation* of absence, not claims of presence.
- Net effect on this workspace: missing-path findings dropped from 8 → 3 (62% noise reduction).
- 4 additional tests (24 total).

## v0.6 — 2026-05-21

- `paleo policy` now distinguishes **blocked** attempts (tool_use → tool_result.is_error=True) from **succeeded** ones (tool_use → tool_result.is_error not set). Output: `8 attempted · 3 blocked · 5 succeeded`. Exit `1` only when at least one BLOCK-severity attempt SUCCEEDED. Blocked-by-hook attempts no longer trip the gate.
- Hits include `timestamp` and `tool_use_id` so users can correlate with hook-install dates and follow up by ID.
- Added test for the policy outcome path (20 total).

## v0.5 — 2026-05-21

- New: `paleo plugins` — reads `~/.claude/plugins/{known_marketplaces.json, installed_plugins.json}` and reports each marketplace (with source repo + last update age) and each installed plugin (with version + age + owner). Flags marketplaces from non-trusted owners (default trust list: anthropics). Exit `1` if any third-party marketplace is present. Motivated by the May 2026 VSCode-extension supply-chain breach — Claude Code plugins are the same risk surface.

## v0.4 — 2026-05-21

- New: `paleo crons` — reads `crontab -l`, extracts the log-redirect target from each non-comment line, and flags entries whose log file is too old (default: 25h or 2× expected interval, whichever is larger) or has never existed. Surfaces silent cron failures.
- MIT LICENSE.
- README rewritten to lead with concrete audit output from the workspace it was written in.

## v0.3 — 2026-05-21

- New: `--json` flag for `dead`, `policy`, `claims`. Emits machine-readable output suitable for piping into `jq` or downstream dashboards. Exit codes unchanged.

## v0.2 — 2026-05-21

- New: `paleo claims` — walks `MEMORY.md` plus every relative `.md` link it transitively references, extracts every Unix path mentioned, and checks each against disk. Surfaces paths cited in memory notes that no longer exist. `--stale-days N` additionally flags paths whose mtime is older than N days.
- Exit `1` when missing paths are found; `0` otherwise.

## v0.1 — 2026-05-21

- New: `paleo policy` — declarative `tool_prefix` / `tool_exact` / `tool_regex` match spec, no eval, JSON-loadable. Ships with one default rule: `shared-account-mcp` flags any `mcp__claude_ai_*` invocation. Exit `1` on any BLOCK-severity hit.

## v0.0 — 2026-05-21

Initial release. Five subcommands: `dead`, `top`, `skills`, `mcps`, `agents`. Stdlib-only Python CLI. Reads `~/.claude/projects/*/*.jsonl` session logs, diffs against installed capabilities found in `~/.claude/skills/`, `~/.claude/plugins/`, `~/.claude/agents/`, `~/.claude/.mcp.json`. No mutation, no network.
