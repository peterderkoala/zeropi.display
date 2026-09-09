# Map #13 — Real Claude Code usage read, pushed, and stored in SQLite (spec)

**Charted 2026-09-04, closed 2026-09-06. Destination reached: `docs/spec-usage-pipeline.md`.** Issue: https://github.com/peterderkoala/zeropi.display/issues/13

> Archived from `handoff/handoff.md` on 2026-09-09, verbatim. This is a
> **record of a finished effort**, not live guidance: facts here were true when
> written and some have since been superseded. The live handoff, the specs and
> the ADRs are authoritative. Kept because the *reasoning* behind decisions —
> and the bugs found on the way — is not recoverable from the code.

---

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

**The map's destination is reached and nothing is open on it. Closed
2026-09-06.** Implementation is **its own map**, opened against the finished
spec.

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
