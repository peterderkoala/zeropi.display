# Handoff — zeropi.display

## Where things stand

**Milestone 1 (BLE prototype) works on real hardware.** The Desktop pushes a
Payload over BLE, the Pi parses it, persists a Reading to SQLite, and returns
an Ack — verified 18/18 on the happy path, plus all four malformed-Payload
cases and reconnect-after-restart. Full write-up:
`docs/e2e-verification.md`.

That system config is **no longer hand-applied**: `pi/install-pi.sh` owns it
(renamed from `pi/install.sh` by #33), and #11 verified the whole
provisioning path from a torn-down Pi (see
`docs/provisioning-verification.md`). As of #33, it's also no longer reached
by hand-run `scp` — a repo-root `install.sh` curl bootstrap fetches a
versioned tarball and delegates to it. As of #34,
`desktop/install-desktop.sh` is built too — Map #7's only remaining open
ticket is #35 (hardware verification of both roles), now unblocked.

- Design/concept: `pi-eink-ble-concept.md` (repo root) — settled BLE service
  shape, Payload/Ack format, SQLite schema, UUIDs, deployment path.
- Domain glossary: `CONTEXT.md` — **rewritten by #19 and now binding.**
  Desktop, Desktop Id, Pi, Payload (Daily/Gauge), Batch, Ack, Reading,
  Coverage Start, Usage, Gauge, Project Key, Project Label, Window, **Limit
  Window** (added by #25), **Reset Countdown** and **Gauge Age** (added by
  #37), **Historic View** (added by #38), Cost Complete, One-liner.
- ADRs: `docs/adr/0001` (**superseded by 0003**), `0002` readings-on-the-Pi,
  `0003` one-write-per-Reading, `0004` dedup-winner-rank, `0005`
  Desktop-store-is-archive-of-record, `0006` wipe-on-Desktop-Id-change,
  `0007` full-refresh-only-no-two-speed, `0008` pi-enforces-the-redraw-floor
  (its unsatisfied clock dependency now **resolved by 0009**, and amended by
  0010 for the daily keep-alive), `0009` pi-is-given-durations-not-timestamps,
  `0010` an-expired-gauge-is-not-drawn.
- Agent-skill config: `docs/agents/issue-tracker.md`, `docs/agents/domain.md`

## Maps

Two maps are open. **#13 has reached its destination and has no open tickets
left** — the spec is written and #30, the last child, is resolved. Closing the
map is the maintainer's call; the next real move is an implementation map
opened against the spec. #7 has two tickets left, #34 the only takeable one.

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

**[#16 (multi-row transport protocol) is also resolved and closed.** One
connection per loop, sequential Acks, continue-past-failure with push-marks
recovery, newest-day-first ordering, an explicit Ack correlation field
(`date`/`project`/`model` echo), an explicit batch marker
(`batch_size`/`batch_index` on the Payload), unchanged 10s per-row timeout,
unchanged characteristic UUIDs. See the map's Decisions-so-far for the full
eight-part answer. Flagged two new fields for #17, which is now resolved
(see below).

**[#17 (the new SQLite schema on the Pi) is also resolved and closed.**
`receive.py`'s `init_db()` stays sole owner, made self-healing and
version-gated (`PRAGMA user_version`; drop+recreate on mismatch — same path
for a fresh Pi and the maintainer's already-provisioned one). `install.sh`
untouched. Lands **after #11 closes** — a sequencing note, not a checklist
change, since install.sh isn't touched. Table keeps the name `readings`.
Full DDL (readings + a key-value `meta` table for `coverage_start`,
auto-derived on every insert) in the resolution comment. Unblocked #19.

**[#36 (what a second Desktop means for the data) is resolved and closed.**
Charted this session by graduating the fog entry the previous handoff flagged,
then resolved. **Its premise was wrong and that is the main result**: this is a
*lifecycle* question, not a concurrency one. The maintainer wants one Desktop
at a time, with the Pi **freshly couplable to a different Desktop** — so no
machine dimension enters the grain (#17's PK and #16's Ack fields both
untouched), the machine id is a **scalar in the `meta` table**, and the Pi
**drops and recreates `readings` when it changes**. See its resolution comment
for the eight-part answer. Unblocked #19.

**[#19 (vocabulary and ADRs) is resolved and closed** — second ticket of that
session. `CONTEXT.md` and `docs/adr/0003`–`0006` are on `dev` in `9ac14ba`.
**Payload keeps meaning one BLE write**; the set is a **Batch**, the two shapes
are **Daily Payload** / **Gauge Payload**, and nine further terms are pinned
(**Desktop Id**, **Usage**, **Gauge**, **Project Key** vs **Project Label**,
**Window**, **Cost Complete**, **Coverage Start**). Four ADRs written, and the
`(date, project, model)` **grain was deliberately refused one**. Read
`CONTEXT.md` before naming anything in #20's spec — that is now the binding
vocabulary, not this handoff.

**[#25 (push cadence and redraw floor) is resolved and closed** — this
session's ticket. Eleven decisions; the deliverable is **push on any integer
change** (a resident `systemd --user` service polling the snapshot every 30 s),
a **300 s redraw floor enforced by the Pi as a hard gate**, **full refresh
only — no two-speed**, and **idle showing the historic view, held**. Two ADRs
written (`0007`, `0008`) and the term **Limit Window** added to `CONTEXT.md`.
Full detail in its resolution comment and the map's Decisions-so-far.

⚠ **It did not shorten the critical path — it spun out
[#37](https://github.com/peterderkoala/zeropi.display/issues/37)**, which
blocked #20 in #25's place. **#37 has since closed** — see below.

**[#26 (the live-gauge prototype) is resolved and closed** — also this
session. Branch `prototype/live-gauge`, mocks committed at
`docs/research/gauge-mocks/`. **The gauge tells the truth and the layout is
legible, but the prototype overturned three of #24's paper decisions and
corrected two facts this handoff had recorded as settled.** Read its
resolution comment before touching the gauge. Spun out
[#38](https://github.com/peterderkoala/zeropi.display/issues/38).

⚠ **Two tickets in, two tickets out.** #25 and #26 both closed that session and
the critical path to #20 was the same length: #37 and #38 replaced them. That is
the prototype doing its job — both new tickets exist because contact with real
data and a real panel invalidated decisions made on paper.

**[#37 (how the Pi knows the time) is resolved and closed** — 2026-09-05,
latest session, and it **spun out nothing**, so the critical path finally got
shorter. **The answer is that the Pi does not know the time and no longer needs
to.** Three of its four open decisions dissolved rather than resolving. The
wire now carries **durations, not instants**: a **Reset Countdown** in place of
`resets_at`, a snapshot age in seconds in place of `updated_at`, both computed
on the Desktop, both advanced on the Pi with `time.monotonic()`.
[ADR-0009](../blob/dev/docs/adr/0009-pi-is-given-durations-not-timestamps.md)
is on `dev`; **Reset Countdown** and **Gauge Age** are in `CONTEXT.md`.
⚠ **It supersedes the time fields #24 and #25 assumed** — read it before
writing the Gauge Payload's shape into #20.

**[#38 (re-settle the gauge readout) is resolved and closed** — 2026-09-06,
and it **spun out nothing**. **Two of its five decisions dissolved, and the
gauge lost a row, a footer and a readout**: what is left is one split headline
row, one 7D row, and white space. **Layout C confirmed.** The **context
readout is dropped from the display** — that **narrowed the map's Destination**
— though the **field stays in the Gauge Payload** by the maintainer's call, so
the spec must still define active-session detection and the context computation
for a value nothing draws. **There is no stale rendering, because an expired
Gauge is not drawn**: at 300 s of Gauge Age the panel falls back to the
Historic View. Null reads **`NO USAGE DATA`**; the countdown clamps to `<1m`
then **`RESETS NOW`**; the idle panel gains a **24-hour keep-alive refresh**
(amending #25). [ADR-0010](../blob/dev/docs/adr/0010-an-expired-gauge-is-not-drawn.md)
written, **Historic View** added to `CONTEXT.md`, ADR-0008 amended. Settled
design rendered at `docs/research/gauge-mocks/settled-*.png` on
`prototype/live-gauge` (`dde1c58`) — **drawing it broke it twice**, which is
why it was drawn.

**[#20 (the spec) is DONE and closed** — 2026-09-06, and it is **the map's
destination**. `docs/spec-usage-pipeline.md` is on `dev` (`d925bb0`): thirteen
sections, written to stand alone as a session's brief. ⚠ **It names
`CONTEXT.md` and ADRs 0003–0010 as the only other required reading and says
outright that THIS FILE is not a source of truth.** If the spec and this
handoff disagree, the spec wins.

It corrected three places the older material still disagreed with the settled
position, all of which would have bitten an implementer: the **#26 prototype's
Gauge Payload sends instants** (ADR-0009 superseded that — durations only), the
**#18 prototype aggregates on the Project Label** (#36 made the Project *Key*
the stored key), and **#17's DDL still carries `received_at`** while milestone
1's Payload still carries `oneliner` — neither survives.

The gap check found **twelve** places an implementer would have had to invent
an answer, all closed and recorded in the spec's §13. The two worth knowing
here: **Gauge Age is seeded with `snapshot_age_s`** (without it ADR-0010's
"nothing on the panel is untrustworthy" is only approximately true, and the
field has no other consumer), and **the Desktop store refuses to run on a
schema mismatch rather than dropping** — deliberately the opposite of the Pi's
version gate, because one is an archive of record and one is a rebuildable
cache. **Spun out nothing.**

Frontier — **empty**. Every child of #13 is closed.

[Pi retention/pruning (#30)](https://github.com/peterderkoala/zeropi.display/issues/30),
the last one, was resolved 2026-09-06: **no pruning, on either end.** Measured
at the grain the Pi stores, the machine's entire history is **18 rows** over 41
calendar days (mean 1.8/active day), and growth is bounded by the Pi's uptime
rather than by log history. The invariant is *the Pi never deletes a Reading on
size grounds*; `wiped` stays exclusive to the Desktop-Id change (answering the
question #36 left); there is no operator reset command (`rm data.db` + restart
is the path); and the Desktop archive is never pruned either, as an ADR-0005
corollary. Recorded as spec §8.7 and §4.5, plus a deletion lifecycle on
`CONTEXT.md`'s **Reading**. No ADR — additive and easily reversed.

**The map's destination is reached and nothing is open on it.** It is still
left open rather than closed — that call is the maintainer's. Implementation is
**its own map**, opened against the finished spec.

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

**Unblocked: [#20 the spec](https://github.com/peterderkoala/zeropi.display/issues/20)**
— all twelve blockers closed (#16, #17, #18, #19, #21, #24, #25, #26, #27, #28,
#36, #37, #38).

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
which unblocked #30; [#29 the dev-era capture](https://github.com/peterderkoala/zeropi.display/issues/29)
(`~/.local/share/zeropi-display/usage-archive.db`, 6,201 rows, not committed);
[#16 the multi-row transport protocol](https://github.com/peterderkoala/zeropi.display/issues/16);
and [#17 the new SQLite schema](https://github.com/peterderkoala/zeropi.display/issues/17),
which unblocked #19.

**The prototype is worth running before you touch this pipeline** —
`python3 desktop/usage_prototype.py` on `prototype/usage-reader` prints the
rows a push would send from your real logs, with the dedup delta and the
cache-write TTL error measured live. It is throwaway, not the implementation.

### Also open: [Both ends reproducible from scratch (#7)](https://github.com/peterderkoala/zeropi.display/issues/7)

**Destination redrawn 2026-09-05** — read the map body first, including the
banner and the new **Delivery shape** section, which binds all three
tickets.

The original destination (a stock Pi reproducible from scratch) was
**reached** by #11. Rather than close, the map was redrawn to cover the two
things it had listed as unspecified: **delivery** (getting code onto a Pi
was a hand-run `scp`) and the **Desktop end**, which had no provisioning at
all. Desktop-side provisioning moved **out of Out-of-scope and into scope**
— struck through rather than deleted, so the reversal is visible.

The redraw's decisions came from a grilling session, not a ticket, so they
live in the map's **Delivery shape** section. The load-bearing ones:

- **Tarball, not a clone** — `git` is *not installed on the Pi* and costs
  ~50 MB on a Zero; the branch tarball is 36 KB. This overturned the
  maintainer's own opening instruction ("clones the repo"), deliberately.
- **Fetched by sha, not by branch.** A branch tarball unpacks to
  `zeropi.display-dev/` and carries **no version identity** — precisely what
  a clone would have given for free. Resolve ref → sha, fetch
  `/archive/<sha>.tar.gz`, stamp `VERSION`.
- **One root `install.sh`, role by argument**, running unprivileged, with
  the `pi` role re-execing under `sudo`.
- **Never "client"/"server"** in names — `CONTEXT.md` lists both as terms to
  avoid. It is `install-pi.sh` / `install-desktop.sh`.
- **The Desktop role works in-place *and* standalone**, because it must run
  on machines that are not the maintainer's; it detects a surrounding clone
  and says which mode it picked.
- **Points at `dev`** — no `dev` → `main` PR yet, the maintainer's call.

**[#33 (the curl bootstrap) is resolved and closed** — 2026-09-06. Repo-root
`install.sh` resolves `ZEROPI_REF` (default `dev`) to a commit sha via the
GitHub API, fetches/unpacks the sha tarball to a `/tmp` staging dir, and
delegates to `pi/install-pi.sh` (re-exec'd under `sudo`) or
`desktop/install-desktop.sh`. `pi/install.sh` renamed to `pi/install-pi.sh`
— needed only the rename plus VERSION-stamping (`sha`/`ref`/`installed_at`
at `/opt/zeropi-display/VERSION`); `SCRIPT_DIR` already resolved correctly
under the bootstrap via `BASH_SOURCE`, so **don't re-add a staging-root argv
override** — one was tried, flagged by review as unneeded complexity that
silently changed the script's argument contract, and reverted.
`desktop/install-desktop.sh` is a stub (`exit 1`, points at #34) whose
header comment fixes the contract #34 builds against: argv[1] is the
staging root, env carries `ZEROPI_REF`/`ZEROPI_SHA`/`ZEROPI_TIMESTAMP`,
always unprivileged. `data.db` untouched by construction — the deploy step
copies only the files it owns, never syncs a directory wholesale. Commits
`b6eaa2e`, `17c3182` on `dev`; full detail in the issue's resolution
comment.

⚠ **`sudo`'s password prompt breaks under the documented one-liner run
non-interactively** — `curl -fsSL ... | bash -s -- pi` leaves `sudo` with
the exhausted curl pipe as stdin and no controlling terminal, which fails
confusingly (not a hang) without `ssh -t`. `install.sh` now checks for
`/dev/tty` (or already-passwordless sudo) up front and fails with a clear
message and the `-t` fix instead. Relevant if #34 or #35 touch invocation.

⚠ **CONTEXT.md's avoid-list bites documentation too, not just code** — this
session's README draft called Pi "the BLE receiver" and Desktop "the BLE
sender," both on the avoid-list (`_Avoid_: Server, receiver` /
`_Avoid_: Client, sender`). Caught by review, not by writing it. Check new
prose against the avoid-lists before it ships, not after.

**[#34 (desktop/install-desktop.sh) is resolved and closed** —
2026-09-06, `dev` (`6b86e94`). Two modes, auto-detected: a real clone
(`.git` present — checked with `-e`, not `-d`, so a **git-worktree**
checkout counts too, since worktrees make `.git` a file not a directory)
gets `.venv` set up in place; anything else, including every curl-bootstrap
run (a GitHub archive tarball never carries `.git`), installs standalone to
`~/.local/share/zeropi-display/` with a `zeropi-push` shim on `PATH`.
`--in-place` / `--prefix <dir>` override the detection. Linux-only refusal
up front (#32's BlueZ-specific bleak API). **Idempotent, and deliberately
non-destructive of an existing venv**: one already present but missing pip
(e.g. one made by `uv venv`, this project's own documented dev setup) gets
pip added via `ensurepip` rather than `rm -rf`'d, and `python -m pip` is
used throughout since ensurepip's entry-point names aren't guaranteed
(observed: `pip3`/`pip3.12` but no bare `pip`). VERSION is stamped inside
the venv, not the install root — for in-place that root is the
maintainer's tracked checkout, where a stray file would be clutter. The
end-of-install reachability check reuses `push.py`'s own
`matches_service()`/`SERVICE_UUID` (no duplicated UUID to drift) and
**warns rather than fails** if no Pi answers, since the two roles are
provisioned independently. Verified live: a push through the standalone
shim round-tripped against the dev Pi. README documents the desktop
one-liner and the override flag. **Review caught three real bugs before
landing**: the worktree-is-a-file case, the destructive `rm -rf` on a
pip-less venv, and a broken doubled `--` in the README's override example —
all fixed. Unblocked #35.

Frontier — **empty**. #35 is the only ticket left on the map, and it needs
the maintainer's hardware to run.

Also spun out, **not** a map child:
[#32](https://github.com/peterderkoala/zeropi.display/issues/32) —
`push.py`'s `_acquire_mtu()` is a private BlueZ-specific `bleak` API, so the
Desktop is Linux-only. Ruled out of scope for #7 for the same reason as #12
(a code wart, not an installation concern); `install-desktop.sh` refuses
non-Linux loudly instead.

**The original four tickets are closed** (#8, #9, #10, #11); three new ones
(#33, #34, #35) came from the redraw. #11 verified the Pi path on hardware: `install.sh` runs clean
from a torn-down Pi and is idempotent, reboot and `bluetoothd` restart both
survive unattended, **20/20** consecutive pushes and **23/23** round trips
with 0 `bluetoothd` crashes. Write-up: `docs/provisioning-verification.md`.

**#17's sequencing gate is now lifted** — it settled that the new SQLite
schema lands *after* #11 closes. It has closed, so #17's schema change is
free to land.

#11 left the fresh-card caveat standing: no spare was available, so "from
scratch" meant tearing the hand-applied state off the dev Pi —
`python3-gi` was never removed, BlueZ never downgraded, first-boot state not
reproduced. Still fog on the map. Its other open item, how code reaches the
Pi, is what the redraw answers.

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
- **`receive.py` not surviving a `bluetoothd` restart is fixed** (ticket #9,
  verified by #11): `BindsTo=bluetooth.service` cycles the receiver with the
  daemon, settling in ~2 s. When the Desktop says "no device advertising
  service …", check the advertisement on the Pi and `journalctl -u bluetooth`
  for a crash before suspecting the radio. Read the advertisement with
  `busctl get-property org.bluez /org/bluez/hci0
  org.bluez.LEAdvertisingManager1 ActiveInstances` (→ `y 1`), **not** by
  grepping `bluetoothctl show` — bluetoothctl interleaves colourised async
  `[CHG] Controller … ActiveInstances` lines with its own property block, so
  a grep can return two lines with different values.
- **A venv on the Pi must be created with `--system-site-packages`** (#11).
  `bluezero` needs PyGObject and dbus-python, both C extensions; a sealed
  venv makes pip build them from sdists and the build dies at `Dependency
  "cairo" not found`. The apt-installed `python3-gi`/`python3-dbus` satisfy
  them instead. `install.sh` rebuilds a flagless venv rather than reusing it.
- **The LE advertisement takes ~1.5 s to appear** after `receive.py` starts,
  and longer on a cold install racing a `bluetoothd` restart. Poll for it;
  do not sample once after a fixed sleep.
- `push.py`'s `finally: stop_notify(...)` masks the real exception when a
  connect fails, reporting "Service Discovery has not been performed yet"
  over the top of the actual error. Tracked as
  [#12](https://github.com/peterderkoala/zeropi.display/issues/12); until
  it is fixed, delete that `finally` block by hand when diagnosing a BLE
  failure.

Live-gauge facts, established 2026-09-05 (detail in the map body):

- **`~/.claude/sessions/<pid>.json` is a live session registry** —
  ~~real-time~~ `sessionId`, `cwd`, `status` (`busy`/otherwise), `startedAt`,
  `updatedAt`, `kind`. The `sessionId` joins to the session's JSONL. This is
  how you detect the active session; no heuristics needed. **⚠ "Real-time" was
  wrong** — #26 measured `updatedAt` frozen at 467 s during active work. See
  the #26 block below before using any timestamp here.
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

Hand-off facts, established 2026-09-05 by
[#36](https://github.com/peterderkoala/zeropi.display/issues/36):

- **⚠ There is no coupling between Desktop and Pi, at any layer.**
  `pi/receive.py:139` declares `flags=["write"]` / `flags=["notify"]` — not
  `encrypt-write`, not `secure-write` — and there is no pairing, bonding or
  trusted-device list in `install.sh` or `push.py`. The Desktop finds the Pi by
  **scanning for the service UUID**, not a stored address. So "couple the Pi to
  a different Desktop" is currently a **no-op**: run `install-desktop.sh` on the
  new machine and it works. There is nothing to un-couple. This stays
  unauthenticated **by decision**, not oversight — #20 must say so.
- **⚠ `project` is an absolute-path label, not a repo name** —
  `-home-ryzen-git-zeropi-display`. Two Desktops collide on the PK only at the
  *identical* absolute path. A different username is the worse case, not the
  safer one: no overwrite, no error, just a graph that grows a permanent second
  set of series.
- **⚠ The hand-off wipe desyncs against push marks on a hand-BACK.** Pi goes
  A→B fine (B has no marks, pushes everything). Back to A, the Pi wipes on the
  id change while A's store still says everything is pushed — **the Pi sits
  empty and A never resends**, silently. The `wiped` flag on the first Ack
  after a wipe is what closes this; do not drop it as a nicety.
- **`ReceiveState.ack_characteristic` is a class attribute**
  (`pi/receive.py:76`), set by whichever Desktop last subscribed to notify — so
  two *concurrent* Desktops would clobber each other's Ack channel. Moot under
  the sequential shape settled by #36, but it is why concurrent multi-Desktop
  would have cost far more than a schema change.

Live-gauge facts, MEASURED 2026-09-05 by
[#26](https://github.com/peterderkoala/zeropi.display/issues/26) — these
correct earlier entries in this file, so prefer them:

- **⚠ `updatedAt` in the session registry is a status-TRANSITION timestamp, not
  a heartbeat.** This file previously called the registry "real-time"; it is
  not. Measured: `updatedAt` and `statusUpdatedAt` are **exactly equal**, and
  both sat **frozen at 467 s** while the session was actively working with
  `status: "busy"`. **Never test liveness with it** — a freshness threshold
  anywhere near 5 minutes calls a busy session dead. **Liveness is
  `/proc/<pid>`**, cheap and exact.
- **The registry carries `kind: "interactive"`**, which turns #27's headless
  trap from a silent freeze into a **detectable** condition. Filter on it.
- **⚠ Sub-agents do NOT register in the session registry.** One `<pid>.json`
  per interactive CLI process, nothing more — verified across a session that
  ran skills and heavy tool work. So #24's multi-session rule only ever
  discriminates between **separate terminals**. Related: `isSidechain` is
  present on every assistant entry and **false in all 42 sessions on this
  machine**, so the sub-agent-context risk is unverified — keep the filter as
  cheap insurance, not because it has bitten.
- **`.key` files sit alongside the `.json` ones** in `~/.claude/sessions/`.
  Glob narrowly.
- **The gauge moves at ~1.1 percentage points per minute** under heavy Opus 5
  use (27% -> 36% in 8.2 min), so a full 5-hour window is ~91 minutes of
  continuous work. **The trigger therefore fires ~5.5x per 300 s floor** — the
  Desktop-side throttle #25 called a courtesy is doing real work.
- **The Gauge Payload is 279 bytes against the 514 budget**, verbose keys and
  all.
- **⚠ Context-as-a-percentage is a dead readout.** Against #31's 1,000,000
  window, 42 real sessions peaked at **589,408 (59%)**, median peak **15.8%**,
  and **0 of 42** ever passed 900K. The bar is a permanent stub. (The 589,408
  peak does independently **confirm** #31's 1M table — it exceeds any 200K
  window.) **#38 went further and dropped the context readout from the display
  entirely**, narrowing the map's Destination. The **field still crosses the
  wire**, so the active-session machinery below is still spec'd — it exists
  only for this field.
- **⚠ "Dim a stale reading" is not implementable.** The panel is 1-bit
  monochrome: there is no grey. #24 settled dimming anyway. **Resolved by
  [#38](https://github.com/peterderkoala/zeropi.display/issues/38) and
  [ADR-0010](../blob/dev/docs/adr/0010-an-expired-gauge-is-not-drawn.md):**
  nothing is ever marked stale, because an **expired Gauge is not drawn at
  all** — the panel falls back to the Historic View. Do not re-propose hatching,
  a banner or inversion; all three were considered and rejected on the ground
  that a marked-stale number is one you are asking a viewer not to trust.
- **The 5h and 7d windows have visibly different shapes**, confirming #22 by
  observation: `five_hour.resets_at` was 23:40Z — **off any clock hour** —
  while `seven_day.resets_at` was 13:00Z, **exactly on one**.

Cadence and panel facts, established 2026-09-05 by
[#25](https://github.com/peterderkoala/zeropi.display/issues/25):

- **⚠ The Pi has no idea what time it is — and by decision, it never needs
  to.** **Resolved by [#37](https://github.com/peterderkoala/zeropi.display/issues/37)
  and [ADR-0009](../blob/dev/docs/adr/0009-pi-is-given-durations-not-timestamps.md):**
  everything time-shaped crosses the wire as a **duration computed on the
  Desktop**, and the Pi advances it with `time.monotonic()`. Never send the Pi
  an instant it has to interpret. The hardware facts, measured: **no RTC**, and
  — contrary to expectation — **`fake-hwclock` is not installed** either. But
  `systemd-timesyncd` **is enabled and active** out of the box (the OS image
  ships it; `install.sh` does not), and the dev Pi has **WiFi on the LAN**, so
  the clock is usually right. Usually is not a guarantee: boot at `13:47:32`,
  first NTP sync at `13:48:17` — a **45-second window** — with
  `systemd-time-wait-sync` disabled, so nothing holds `receive.py` back through
  it, and a Pi off for a week starts that window a week behind.
- **⚠ Partial refresh is unusable here, and the reason is not obvious.** Two
  vendor statements combine: the panel must not be left in a high-voltage
  state, so every cycle ends in `epd.sleep()` — and deep sleep does **not
  retain RAM**, which destroys the partial-refresh base image. Partial only
  pays off across a burst you stay awake for, and a 300 s floor never produces
  a burst. **Do not re-propose a two-speed scheme**; see
  `docs/adr/0007-full-refresh-only-no-two-speed.md`. #23's **N = 5** bound
  consequently never binds.
- **⚠ Poll the snapshot; do not inotify it.** claude-hud writes
  `rate-limits.json` atomically via temp+rename, so a watch on the *file*
  misses every write — it would have to watch the directory. A 30 s poll of a
  small local JSON file is cheaper than getting that right.
- **Idle is the common state, not the exception.** Per #27 the snapshot only
  advances while an interactive TUI is open, so the panel spends most of the
  day with no live gauge. That is why idle shows the historic view rather than
  blanking — blanking would waste the display's standing purpose for the
  majority of hours.
- **The floor is 300 s and three independent sources agree on it**: #23's
  recommended operating point, claude-hud's `externalUsageFreshnessMs` default
  (300 000 ms), and #24's Pi-side staleness mark. #23's 180 s is headroom, not
  the setting.
- **The likely UPS is a PiSugar 3** (maintainer). Not designed for — mains is
  an explicit assumption — and its RTC is **not** the answer to #37, which
  removed the clock dependency instead. Fitting one is now explicitly **out of
  scope** on map #13; it would improve `journalctl` and would not require
  revisiting ADR-0009.

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

- **The spec is done. The next real move is implementation** — a new map
  opened against `docs/spec-usage-pipeline.md`, per map #13's own Destination
  ("Implementation is not part of this map"). Read the spec, not this file.
- **`mattpocock-skills:wayfinder`** with map #13 — **nothing left to grab.**
  The map is reached with every child closed; whether to close the map itself
  is the maintainer's call.
- **`mattpocock-skills:wayfinder`** with map #7 — only #35 is left, and it
  needs the maintainer's own hardware (a second machine, or the same dev
  box, to actually run `install-desktop.sh` and `install-pi.sh` on real
  gear); not a session-only task.
- **`mattpocock-skills:grilling`** has no open decisions left on #13 — #25,
  #26 and #30 are all resolved. Reach for it on map #7 or on the
  implementation map when that is charted.
- **`mattpocock-skills:domain-modeling`** is **done for now** — #19 rewrote
  the vocabulary and superseded ADR 0001. Reach for it again only if #25 or
  #26 coins a term the glossary does not have.

## If you run subagents, isolate them

Two research subagents were run in parallel from the same working tree on
2026-09-04 and their git operations collided — one agent's commit landed on
the other's branch. No damage (`dev` and `main` were untouched) and it was
repaired with a fast-forward, but `research/dedup-rules` still carries the
pricing commit as a result. **Give parallel agents their own worktrees.**
