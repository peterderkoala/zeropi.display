# Handoff — zeropi.display

## Where things stand

**Milestone 1 (BLE prototype) works on real hardware.** The Desktop pushes a
Payload over BLE, the Pi parses it, persists a Reading to SQLite, and returns
an Ack — verified 18/18 on the happy path, plus all four malformed-Payload
cases and reconnect-after-restart. Full write-up:
`docs/e2e-verification.md`.

The catch: it works because of **system config applied by hand over SSH**,
which exists nowhere executable. That is what the current map addresses.

- Design/concept: `pi-eink-ble-concept.md` (repo root) — settled BLE service
  shape, Payload/Ack format, SQLite schema, UUIDs, deployment path.
- Domain glossary: `CONTEXT.md` (Desktop, Pi, Payload, Reading, Ack,
  One-liner)
- ADRs: `docs/adr/0001-single-write-payload-no-chunking.md`,
  `docs/adr/0002-readings-persisted-on-pi.md`
- Agent-skill config: `docs/agents/issue-tracker.md`, `docs/agents/domain.md`

## Maps

Two maps are open. #13 is where the work is; #7 has one ticket left.

### Current: [Real Claude Code usage read, pushed, and stored in SQLite (#13)](https://github.com/peterderkoala/zeropi.display/issues/13)

Charted 2026-09-04. **Destination redrawn 2026-09-05** — read the map body
before anything else, including the redraw banner at the top.

The original map specced the daily-aggregate pipeline. The maintainer's
actual goal is a **live usage gauge**: current consumption against the
rolling 5-hour limit window (ideally a percentage), the weekly limit if
obtainable, and the **context size of the active session**. History is
demoted to a supporting role — an average/trend graph — but survives as
specced.

**The redraw invalidated some settled decisions.** They are struck through
in the map's tables rather than deleted, so you can see what changed:
cadence is no longer out of scope, and the "cost is the headline" decision
now governs only the historic view.

**[#24 (the hinge) is resolved and closed** — see its resolution comment and
the map's Decisions-so-far for the six-part answer (active-session rule,
context-size-as-percentage, ephemeral live gauge, two Payload shapes, an
explicit "no data yet" null state, and a 5-minute Pi-side staleness mark).
Resolving it unblocked four tickets at once.

**[#28 (Desktop-side usage store) is also resolved and closed.** File
location, ingest incrementality, winner-rank timing, store-only-aggregation
and schema are all settled — see the map's Decisions-so-far. Unblocked #30.

**[#29 (dev-era capture) is also done and closed.** The entries live at
`~/.local/share/zeropi-display/usage-archive.db` — 6,201 rows, 1.59 MB, not
committed. Quarry for #20's future test fixture; unblocked nothing further.

Frontier — open, unblocked, unclaimed:

1. [Settle the live Payload transport (#16)](https://github.com/peterderkoala/zeropi.display/issues/16)
   — newly unblocked by #24.

2. [Settle the live Payload schema (#17)](https://github.com/peterderkoala/zeropi.display/issues/17)
   — newly unblocked by #24. Note it may change what #7's #11 has to verify,
   and should be designed with #28's Desktop-store shape in mind (the Pi's
   `coverage_start` meta table).

3. [Settle the gauge push cadence (#25)](https://github.com/peterderkoala/zeropi.display/issues/25)
   — newly unblocked by #24, and by #23's eink-refresh research.

4. [The live-gauge prototype (#26)](https://github.com/peterderkoala/zeropi.display/issues/26)
   — `wayfinder:prototype`, newly unblocked by #24, #22 and #27 together.

5. [Pi retention/pruning (#30)](https://github.com/peterderkoala/zeropi.display/issues/30)
   — newly unblocked by #28.

**[#31 (context-window research) is resolved and closed.**
`docs/research/context-window-table.md` (branch `research/context-window-table`,
unmerged) found that the commonly-assumed 200K window is wrong for the two
models that matter most: Opus 5 and Sonnet 5 both carry a **1,000,000-token
window** (combined input+output), now GA with no pricing surcharge — doesn't
touch #14's pricing table. Haiku 4.5 stays at 200K, no extended option. Max
output: 128,000 for Opus 5/Sonnet 5 (300K on Batch API beta), 64,000 for
Haiku 4.5. This is an input to whatever ticket implements #24's
percentage-against-a-per-model-table decision — no open frontier ticket
consumes it yet, but it'll matter once #17 (schema) or the eventual spec
touches the context-size field.

Blocked: [#19 vocabulary/ADRs](https://github.com/peterderkoala/zeropi.display/issues/19)
(← #16, #17), [#20 the spec](https://github.com/peterderkoala/zeropi.display/issues/20)
(← six open tickets).

⚠ **`issue_dependencies_summary.blocked_by` lags.** It read `0` for #30
immediately after the edge was created, while
`gh api repos/<owner>/<repo>/issues/30/dependencies/blocked_by` correctly
listed #28. The tracker doc's frontier query leans on that summary field —
confirm against the `dependencies/blocked_by` list before treating a ticket
as takeable.

**The primary goal is achievable, but not the way this map first assumed.**
[#22](https://github.com/peterderkoala/zeropi.display/issues/22) (closed)
overturned the framing: there is **no denominator and none is needed** —
Anthropic computes utilization server-side and Claude Code carries
`five_hour` / `seven_day` percentages with their `resets_at` ready-made
(**field naming differs by source — see #27 below**). The
"hand-configured constant" fallback recorded earlier was **withdrawn as
unsound**: the limit is not a token count, so there is no number to configure.

**The load-bearing corollary**: the locally-parsed token sum is **not
proportional** to limit consumption. Never show it as a proxy for the gauge —
they are different quantities.

[#23](https://github.com/peterderkoala/zeropi.display/issues/23) (closed) set
the hardware floor: **minimum safe panel update is 180 s, 300 s recommended**
on a second-hand panel. So "live" means a ~5-minute gauge, not real-time. If
that disappoints, *time-until-window-reset* may read better than a
slowly-creeping percentage — flagged on #25.

Closed: [#14 pricing](https://github.com/peterderkoala/zeropi.display/issues/14)
(`docs/research/pricing-table.md`, branch `research/pricing-table`),
[#15 dedup](https://github.com/peterderkoala/zeropi.display/issues/15)
(`docs/research/dedup-rules.md`, branch `research/dedup-rules`) and
[#18 prototype](https://github.com/peterderkoala/zeropi.display/issues/18)
(`desktop/usage_prototype.py`, branch `prototype/usage-reader`). All three
branches are unmerged; the findings are summarised in the map body.
Also closed: [#21 backfill](https://github.com/peterderkoala/zeropi.display/issues/21),
which spun out #28, #29 and #30 — read its resolution comment before touching
any of them. Also closed: [#24 the live-usage data model](https://github.com/peterderkoala/zeropi.display/issues/24),
which spun out #31; [#31 context-window research](https://github.com/peterderkoala/zeropi.display/issues/31)
itself (`docs/research/context-window-table.md`, branch
`research/context-window-table`); [#28 the Desktop-side usage store](https://github.com/peterderkoala/zeropi.display/issues/28),
which unblocked #30; and [#29 the dev-era capture](https://github.com/peterderkoala/zeropi.display/issues/29)
(`~/.local/share/zeropi-display/usage-archive.db`, 6,201 rows, not committed).

**The prototype is worth running before you touch this pipeline** —
`python3 desktop/usage_prototype.py` on `prototype/usage-reader` prints the
rows a push would send from your real logs, with the dedup delta and the
cache-write TTL error measured live. It is throwaway, not the implementation.

### Also open: [Target installation reproducible from scratch (#7)](https://github.com/peterderkoala/zeropi.display/issues/7)

Get a stock Pi to an unattended, reboot-surviving install with one
repeatable provisioning path. The map body carries the full inventory of
hand-applied Pi state — read it before touching the Pi.

**Only [#11](https://github.com/peterderkoala/zeropi.display/issues/11)
remains open** (verify provisioning from scratch); #8, #9 and #10 are
closed. Note that map #13's ticket #17 may change what #11 has to verify —
settle #17 before running #11.

### Closed: [Milestone 1 BLE prototype (#1)](https://github.com/peterderkoala/zeropi.display/issues/1)

Destination reached; all five tickets (#2–#6) and the map itself are
closed. The round trip is verified on real hardware — see
`docs/e2e-verification.md` and the closing comments on #6 and #1.

## Hard-won facts — do not relearn these

- **The Pi Zero 2W's BLE hardware is fine.** An earlier session suspected a
  chip/firmware limit behind "coin-flip" reliability. It was `bluetoothd`
  segfaulting in its MIDI plugin on every incoming LE connection. Do not
  design chunking or retry logic around a presumed hardware limitation.
- **`DisablePlugins` in `/etc/bluetooth/main.conf` does nothing** — not a
  valid BlueZ 5.82 key. Plugin exclusion is a `bluetoothd` command-line
  option; see the drop-in described in #7.
- **`receive.py` does not survive a `bluetoothd` restart** (ticket #9). When
  the Desktop says "no device advertising service …", check
  `bluetoothctl show | grep ActiveInstances` on the Pi and
  `journalctl -u bluetooth` for a crash before suspecting the radio.
- `push.py`'s `finally: stop_notify(...)` masks the real exception when a
  connect fails, reporting "Service Discovery has not been performed yet"
  over the top of the actual error. Tracked as
  [#12](https://github.com/peterderkoala/zeropi.display/issues/12); until
  it is fixed, delete that `finally` block by hand when diagnosing a BLE
  failure.

Live-gauge facts, established 2026-09-05 (detail in the map body):

- **`~/.claude/sessions/<pid>.json` is a live session registry** — real-time
  `sessionId`, `cwd`, `status` (`busy`/otherwise), `startedAt`, `updatedAt`.
  The `sessionId` joins to the session's JSONL. This is how you detect the
  active session; no heuristics needed.
- **Context size** = the active session's latest assistant entry's
  `input + cache_creation + cache_read`. Measured 210,641 on a live session,
  which **exceeds 200K** — so don't assume the context-window denominator.
- **The gauge percentage is served ready-made**, not computed locally.
  Verified on this machine: `five_hour.utilization: 17`,
  `seven_day.utilization: 2`, with `limit_dollars` / `used_dollars` /
  `remaining_dollars` **all null**. No limit crosses the wire in any unit.
- **⚠ `~/.claude.json` → `cachedUsageUtilization` is a trap** — and worse than
  first measured. It is not maintained **at all** while Claude Code runs:
  **16.5 h stale and not updated once** across a full active session in which
  the live gauge moved 21% → 26%, its cached `seven_day` reading **2% against a
  live 18%**. **Never read this file.**
- **The 5h window's anchor is not locally reconstructible** — it matched
  neither the nearest local event, nor first-activity-after-a-gap, nor a clock
  boundary. **Read `resets_at`; never model the window.** The 7-day window is
  a different shape: a fixed account slot on an exact clock hour.
- **The gauge is account-wide**, computed server-side, so usage from
  claude.ai and other devices is already included. The *historic* rows are
  still this-machine-only.
- **Never read `~/.claude/.credentials.json`.** It sits next to the useful
  files; nothing in this project needs it.

Rate-limit snapshot facts, established 2026-09-05 by
[#27](https://github.com/peterderkoala/zeropi.display/issues/27):

- **The live snapshot exists now**:
  `~/.local/state/zeropi-display/rate-limits.json` (0600, atomic temp+rename),
  written by **claude-hud** via `display.externalUsageWritePath`. That option
  lives in **`~/.claude/plugins/claude-hud/config.json`** — *not*
  `~/.claude/settings.json`, which was left untouched. claude-hud never creates
  the parent directory, so it must exist first.
- **Shape**: three keys — `updated_at` (ISO-8601 UTC, always present), plus
  `five_hour` and `seven_day`, each `{used_percentage, resets_at}`.
  `used_percentage` is an **integer 0-100 or null**; `resets_at` an ISO string
  or null. `model_scoped` and `balance_label` are **dropped by the writer**.
- **⚠ The stdin field is `used_percentage`, not `utilization`.** `utilization`
  is right for `~/.claude.json` only. Spec against the snapshot's names.
- **⚠ `updated_at` is a write time, not a fetch time.** claude-hud rewrites on
  a 30 s throttle even when the value is unchanged — observed twice (23%→23%,
  24%→24%). A fresh `updated_at` does **not** mean a fresh percentage.
- **⚠ Headless `-p` sessions write nothing.** Print mode renders no status
  line, verified with a canary command that was never invoked. **The gauge is
  live only while an interactive Claude Code TUI is open** — a cron-fired
  `push.py` against a closed terminal reads a frozen file.
- **Absent, not zero.** No file before the first render; and if stdin carries
  no `rate_limits` at all, nothing is written and any existing file is left in
  place, so a stale snapshot can persist silently.
- **5 minutes has prior art as the staleness threshold** — claude-hud's own
  reader default (`externalUsageFreshnessMs`, 300 000 ms), independently the
  same number as #23's recommended 300 s panel operating point.

Live-usage data-model facts, established 2026-09-05 by
[#24](https://github.com/peterderkoala/zeropi.display/issues/24):

- **The live gauge is ephemeral** — never persisted to the Pi's SQLite,
  display-only. The daily table already covers the trend use case; a
  5-minute-grain history would just burn SD write cycles for no product
  value.
- **Two Payload shapes, not one.** The live-gauge Payload and the daily-row
  Payload are structurally different (window consumption/resets_at/context
  vs. tokens/cost/grain) and stay separate rather than one shape with fields
  left null depending on which kind of row it is. Field naming is #19's job,
  not settled here.
- **Active session = most-recent `updatedAt`** in
  `~/.claude/sessions/<pid>.json` — the single rule for both "which session"
  among several `busy` ones and "is anything live at all." Zero live
  sessions renders blank, not a stale number.
- **Context size displays as a percentage**, not a bare token count, against
  a hardcoded per-model context-window table — same pattern as #14's pricing
  table. **Resolved by [#31](https://github.com/peterderkoala/zeropi.display/issues/31)**:
  the commonly-assumed 200K window is wrong for the two models that matter
  most — Opus 5 and Sonnet 5 both carry a **1,000,000-token window** (combined
  input+output), now GA with no pricing surcharge (doesn't touch #14's
  table). Haiku 4.5 stays at 200K, no extended option. Max output: 128,000
  for Opus 5/Sonnet 5 (300K on Batch API beta), 64,000 for Haiku 4.5. Table
  in `docs/research/context-window-table.md`
  (branch `research/context-window-table`).
- **A null `used_percentage` gets its own explicit state** ("no data yet"),
  distinct from both zero and stale — collapsing it into either would
  misrepresent a real, observed condition (per #27, not hypothetical).
- **Staleness is Pi-side, not Desktop-suppressed.** The Payload carries a
  generated-at timestamp (from claude-hud's `updated_at`); the Pi compares
  against its own clock and dims (never blanks) a reading past 5 minutes.
  This matters because the Pi can go without a push longer than 5 minutes
  even when the Desktop's own read was fresh at push time.

Desktop-side usage store facts, established 2026-09-05 by
[#28](https://github.com/peterderkoala/zeropi.display/issues/28):

- **The store's location is configurable** — an env var or a `push.py` CLI
  flag, falling back to `~/.local/share/zeropi-display/usage-archive.db`
  when neither is set. This is the first configurable path in the codebase;
  everything else (Pi's `DB_PATH`, the GATT UUIDs) is a hardcoded constant.
- **Ingest resumes via a per-session high-water mark**, not a whole-file
  mtime check — a session's JSONL grows across days, so mtime alone would
  wrongly skip a file that's been partially ingested and then appended to.
- **The #15 winner-rank runs at ingest, and this is a one-way door.** Only
  the winner of each `(requestId, message.id)` duplicate group is stored;
  losers are discarded permanently. If the rank rule ever changes, only
  newly-ingested entries follow it — old stored history can't be re-ranked.
- **Store-only aggregation is an absolute rule, no repair escape hatch.**
  There is no `--rebuild-from-logs` mode; a corrupt store is restored from a
  backup of the store itself. Re-deriving from logs would reintroduce the
  exact degradation hazard (#21) the store exists to remove.
- **Schema is one entry table with a push-marks column** — no separate
  marks table, no separate ingest-offset table. All store state (dedup
  winner, push status, ingest position) lives in one SQLite file.

Backfill and retention facts, established 2026-09-05 by
[#21](https://github.com/peterderkoala/zeropi.display/issues/21):

- **⚠ The Pi has no read path.** `pi/receive.py:70` builds the Ack as
  `{status, received_at, reason?}` and nothing else, so **the Desktop can never
  ask the Pi what it holds**. Every "does it already have this?" question has
  to be answered from Desktop-side state. This is the single constraint that
  forced the Desktop store into existence.
- **⚠ The Desktop's logs self-delete on a rolling 30-day sweep.**
  `cleanupPeriodDays` defaults to 30 and the sweep deletes
  `projects/<project>/<session>.jsonl` outright. It is unset on this machine,
  so the default applies. Proof it already fired: `~/.claude/stats-cache.json`
  is exempt from the sweep and still remembers 2026-07-13 → 2026-07-19, days
  that no longer exist in the JSONL logs.
- **This is not an emergency.** Real use starts at the first prod build;
  everything before is *test material*, not history. #29 carries no deadline.
- **A day's completeness degrades gradually**, which is subtler than the sweep
  itself: 2026-07-28's usage survives only because it sits in a session file
  last written 2026-08-07, while that day's other sessions are already gone. So
  re-reading the logs later can yield a **smaller** row for a day already
  stored in full. Computing rows from the Desktop store rather than from the
  logs is what removes this; do not reintroduce a log-sourced push path.
- **The full history is 12 rows / 2,736 bytes / $317.43** across 9 active days
  and 3 projects — not the ~20 rows #21 originally estimated. Entry grain is
  6,022 records / 1.54 MB; a raw log copy would be 70.9 MB and would durably
  retain prompts.
- **`~/.claude/stats-cache.json` is not a usable data source** despite
  surviving the sweep: frozen at `lastComputedDate: 2026-07-19`, `costUSD: 0`,
  no project dimension.

Usage-log facts, from map #13's research (full detail in the map body and
in `docs/research/`):

- **A naive dedup of the JSONL logs loses 26.2% of all output tokens.**
  Duplication is streaming content-block fan-out, and the early copies carry
  a *provisional* usage snapshot. Keying on `(requestId, message.id)` is only
  half the rule — you must also pick a winner within each group.
- **Cache-write tokens are two billed classes, not one.**
  `cache_creation_input_tokens` is the sum of `ephemeral_5m` and
  `ephemeral_1h`, which price differently. Costing off the sum is wrong by
  ~5% on a real session.
- **Transcript-derived cost is ~92.8% of `cost-state`, and that is correct.**
  The transcript does not contain every call the accumulator saw. Do not
  chase the gap.

## Environment notes

- Dev Pi: `192.168.4.108`, creds in `infrastructure.md` (gitignored).
  `sshpass` is installed in this dev environment for non-interactive SSH;
  the sudo password is the same as the SSH password.
- Pi: Debian 13 (trixie), Python 3.13.5, aarch64, BlueZ `5.82-1.1+rpt2`,
  `python3-dbus` `1.4.0-1`, `bluezero` `0.9.1` in `~pi/.local`.
- Desktop: `bleak` 3.0.2 in a local `.venv/` (gitignored, not committed) —
  `uv venv .venv && uv pip install -r desktop/requirements.txt`.
- Labels `wayfinder:map`, `wayfinder:task`, `wayfinder:grilling`,
  `wayfinder:research`, `wayfinder:prototype` exist. `gh` CLI is
  authenticated as `peterderkoala`.
- Claude Code usage logs live at `~/.claude/projects/**/*.jsonl` — 124 files,
  ~70MB, 3 projects as of 2026-09-04. They contain prompts and file
  contents: **never commit them or excerpts of them.** Test fixtures must be
  synthetic or scrubbed.

## Suggested skills for the next session

- **`mattpocock-skills:wayfinder`** with map #13 — take #16, #17, #25, #26
  or #30 from the frontier, resolve one, record, advance. #24, #28, #29 and
  #31 are all resolved.
- **`mattpocock-skills:grilling`** for #16, #17, #25 and #30, all genuine
  open decisions; #26 is a prototype.
- **`mattpocock-skills:domain-modeling`** for #19, which rewrites the
  Payload/Reading vocabulary and supersedes ADR 0001.

## If you run subagents, isolate them

Two research subagents were run in parallel from the same working tree on
2026-09-04 and their git operations collided — one agent's commit landed on
the other's branch. No damage (`dev` and `main` were untouched) and it was
repaired with a fast-forward, but `research/dedup-rules` still carries the
pricing commit as a result. **Give parallel agents their own worktrees.**
