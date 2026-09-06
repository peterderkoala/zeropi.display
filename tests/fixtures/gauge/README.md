# Synthetic Gauge fixtures (spec §5, ticket #43)

Everything under `tests/fixtures/gauge/` is **hand-constructed, synthetic**
data for `desktop/gauge.py`. Unlike `tests/fixtures/claude_projects/`
(#45, spec §11.2 — the JSONL usage-log fixture for `usage.py`/#42), these
fixtures are shaped like the *other* two Desktop-side sources gauge.py reads
(spec §5): claude-hud's rate-limit snapshot and the live session registry.
No real paths, pids, session ids or prompt content appear anywhere here.

## `rate_limits/`

Shaped like `~/.local/state/zeropi-display/rate-limits.json` (spec §5.1).

- **`valid.json`** — both windows populated, `used_percentage` non-null,
  `resets_at` in the future relative to the fixture's nominal "now"
  (`2026-09-05T09:00:00Z`, used by `test_gauge.py`). Exercises the ordinary
  duration computation (§5.3).
- **`null_windows.json`** — `updated_at` present (never null, per spec), but
  both `five_hour.used_percentage`/`resets_at` and `seven_day`'s are `null`.
  Exercises "null survives as null, never becomes 0" — this is the observed
  "no `rate_limits` on stdin yet" / "both windows null" failure mode (§5.1's
  table), which is read normally and produces a Payload with null fields, as
  opposed to the file being entirely absent (which is a different case,
  covered without a fixture — the test simply points `gauge.py` at a path
  that doesn't exist).

Staleness (a snapshot ≥300s old, §5.3) is exercised in `test_gauge.py` by
passing a fixed `now` far enough after `valid.json`'s `updated_at`, rather
than by a separate fixture file — the same JSON works for both the fresh and
stale cases depending which `now` the test supplies.

## `sessions/`

Shaped like the registry directory `~/.claude/sessions/` (spec §5.2), one
file per `<pid>.json`. Fictional pids — tests inject a `pid_alive` callable
(per the ticket's testability seam) rather than relying on any of these pids
existing as real processes on the test machine.

| File | `kind` | `updatedAt` | Purpose |
|---|---|---|---|
| `40001.json` | `interactive` | `08:00:00Z` (older) | A live interactive session that should lose the "most recent" comparison to `40002.json`. |
| `40002.json` | `interactive` | `08:55:00Z` (newest) | The session that should be selected as active: interactive, alive, and most recently updated. Points at the `projects/` fixture below for the context-size read. |
| `40003.json` | `headless` | `08:59:00Z` (newest of all) | Must be excluded by the `kind == "interactive"` filter (§5.2) despite having the freshest `updatedAt` of any file here — proves the filter runs before the recency comparison. |
| `40004.json` | `interactive` | `08:30:00Z` (middle) | Must be excluded by liveness: tests configure `pid_alive` to report this pid as dead. With `40002` also excluded, it is the next-most-recent interactive+alive session, proving the liveness check (not just recency) drives the exclusion. |

## `projects/-home-tester-code-gauge-fixture/`

Shaped like `~/.claude/projects/<project-key>/<sessionId>.jsonl` (spec §5.2),
the encoding of the fictional project root `/home/tester/code/gauge-fixture`.
`sess-gaugeA.jsonl` (the `sessionId` referenced by `40002.json`'s `cwd`)
contains two `type: "assistant"` entries, chronologically ordered:

1. `req-gauge-nonside` (non-sidechain, `claude-sonnet-5`, tokens summing to
   300) — the entry the context read must select.
2. `req-gauge-sidechain` (`isSidechain: true`, `claude-sonnet-5`, tokens
   summing to 900, and chronologically *later* than entry 1) — must be
   skipped per §5.2's rule, which is the *opposite* of `usage.py`'s dedup
   rule for the same field (§4.1 counts sidechain entries; §5.2's context
   read does not). Naively taking "the last entry in the file" would report
   900 tokens; the correct read reports 300.

This mirrors `tests/fixtures/README.md`'s case 8 (same idea, applied fresh
here rather than reusing that ticket's fixture file directly, since #45's
JSONL fixture is documented as belonging to `usage.py`/#42's case table).
