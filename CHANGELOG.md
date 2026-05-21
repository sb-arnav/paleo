# Changelog

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
