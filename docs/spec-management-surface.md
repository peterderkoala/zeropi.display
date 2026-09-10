# Spec: the management surface

**Status**: binding. This document is the brief for the work that gives this
project one place to answer *is it working?*, *change this setting* and *do this
now* — for both ends, without SSHing to two machines and reading two journals.
It is the destination of
[map #70](https://github.com/peterderkoala/zeropi.display/issues/70) and was
written from that map's eight resolved tickets; **implementation is a separate
map**, opened against this document.

**Required reading, and nothing else is required**: `CONTEXT.md` (the binding
vocabulary — this spec leans on **Configuration**, **Settings**, **Tier**,
**Command**, **Unreachable** and **Panel Health** as written there), ADRs
[0005](adr/0005-desktop-store-is-the-archive-of-record.md),
[0006](adr/0006-pi-wipes-on-desktop-id-change.md),
[0008](adr/0008-pi-enforces-the-redraw-floor.md),
[0009](adr/0009-pi-is-given-durations-not-timestamps.md),
[0011](adr/0011-management-actions-are-never-deferred.md), and the two ADRs this
spec raises — [0012](adr/0012-status-is-requested-not-carried.md) and
[0013](adr/0013-no-command-overrides-a-verified-invariant.md) — plus §4.5, §6,
§7 and §8 of [`spec-usage-pipeline.md`](spec-usage-pipeline.md).

⚠ **`handoff/handoff.md` is not authoritative.** It is a running log for
continuity. Where it and this document disagree, this document wins.

⚠ **Nothing in this project is configurable today.** Every knob is a
module-level constant. Exactly one environment variable exists
(`ZEROPI_USAGE_STORE`) and two `--store` flags. This spec starts from zero, not
from a config format that needs extending — which is why §3 can pick the shape
freely and §6 has to *build* a seam on the Pi rather than widen one.

---

## 1. What you are building

Four things, in this order of dependency:

1. **A Configuration store on the Desktop** (§3) and the schema that validates
   it (§4) — a dedicated SQLite file, resolved once at startup into a frozen
   object that fills parameters which, on the Desktop, mostly already exist.
2. **Two new Payload kinds and three verbs** (§5), and the **configuration seam
   on the Pi** that makes a Setting mean anything after it lands (§6).
3. **A verdict** (§8): six comparisons the Desktop makes between what it sent
   and what the Pi says it holds, collapsed into one answer to *is it working?*
4. **`desktop/cli.py`** (§9), the surface a human types at, which exercises all
   of the above so the design is pressure-tested rather than merely written.

When you are done: `python desktop/cli.py status` answers *is it working?* in
one line and shows its evidence; `python desktop/cli.py config` shows every
tunable value in this project with its Tier; and a setting changed here reaches
the Pi without anyone opening an SSH session.

**What this milestone is not**: the web UI. That is deliberately a later map
(§12). This spec builds the substrate a web service will call, and a CLI that
proves the substrate is callable.

## 2. Vocabulary you will be held to

`CONTEXT.md` is binding and already carries every term this map coined. Four
distinctions do the most work here and are the ones most easily lost:

- **Configuration** is what the Desktop holds. **Settings** are the subset
  projected onto the Pi. They are not synonyms. A value can be Configuration
  without being a Setting; nothing is a Setting without first being
  Configuration. Today the Settings set is **one key**.
- A **Tier** is not a severity. Tier 3 is a *verified invariant* — a value fixed
  by a hardware run and its ADR — and it is **not a setting at all**.
- **Unreachable** is not an error. It is an expected steady state with **two
  cases**, *absent* and *busy*, and §8 shows why they cannot share a headline.
- A **Command** is imperative and never held; **Settings** are declarative and
  converge. This is the whole reason there is no queue anywhere in this
  document (ADR-0011).

Two terms are coined by this spec and added to `CONTEXT.md`:
**Management Surface** and **Verdict**.

## 3. Configuration — the store

### 3.1 Location and format

**`~/.config/zeropi-display/config.db`.** SQLite, WAL journal mode.

```sql
CREATE TABLE config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL    -- ISO-8601, Desktop local time
);
PRAGMA user_version = 1;
```

**Key/value rows, not typed columns**, exactly like the Pi's existing `meta`
table (`pi/receive.py:91`). Adding a setting is an `INSERT`, not a migration.
**Values are stored as TEXT and coerced by §4's schema**, which is the single
authority on each key's type and range — the storage layer stays dumb and
coerces nothing on its own.

`updated_at` exists for exactly one reason: §3.7's pending-restart check. Do not
add a second timestamp, a revision counter, or a loaded-config row.

**Deliberately not a section in the Desktop store**, on four grounds:

1. `open_store` refuses to run on a `user_version` mismatch
   (`desktop/usage.py:396`). Configuration in there becomes unreadable exactly
   when the archive is broken — which is when the Management Surface is most
   needed.
2. ADR-0005 makes store backups the only backups that matter, and repairs a
   corrupt store from a backup **of the store itself**. Restoring three-week-old
   history would silently revert Configuration by three weeks. Data and
   configuration have different restore semantics.
3. The live store is `-rw-r--r--` and 5.2 MB — the artifact you back up and
   copy. Nothing secret may ride along in it (§3.4).
4. Adding a knob would bump `STORE_USER_VERSION`, which gates access to
   *history*. Adding a setting must not risk locking anyone out of the archive.

⚠ **Accepted cost: the configuration is no longer `vi`-able.** Inspection is
`sqlite3 config.db 'select * from config'` or, normally, `cli.py config`. This
is why §9 treats the CLI as the *primary* inspection path rather than a
convenience.

### 3.2 Precedence

**CLI flag → environment variable → Configuration → compiled default.**

This preserves pipeline §4.5's blessed order and keeps both existing escapes
working (`--store`, `ZEROPI_USAGE_STORE`) above Configuration rather than
beside it.

**The bootstrap exception is exactly one item.** The config store's own location
cannot live inside the config store, so it needs `--config` / `ZEROPI_CONFIG`.
The *archive* store's path lives in Configuration normally, as
`paths.store`.

⚠ **There is no general per-key environment mechanism** — no
`ZEROPI_POLL_INTERVAL_S` and friends. A store, plus a Management Surface, plus
per-key env is three sources of truth, and *"why isn't my setting taking
effect"* stops being answerable. One documented escape beats a general mechanism
nobody asked for.

### 3.3 Resolution happens once, at the entry point

Configuration is resolved **once at startup into a frozen object**, whose values
fill the parameters that already exist.

⚠ **Not lazily at use sites, and never as module-level globals populated at
import.** The 246-test suite imports these modules with no `~/.claude`, no panel
and no `bluezero` present; an import-time read of a config store breaks every
one of them.

On the Desktop this is nearly free, because **the seam already exists**. Every
policy value is already a default argument the tests inject:
`run_forever(poll_interval_s=…)` (`desktop/service.py:229`),
`GaugeThrottle(throttle_s=…)` (`:110`), the batch scheduler's
`catchup_threshold_s` / `scheduled_hour` (`:179`),
`desktop_id(machine_id_paths=…)`, `discover_projects(root=…)`,
`init_db(db_path=…)`. **Configuration does not need new plumbing on the Desktop
— it needs to fill parameters that are already there.**

One consequence: `run_batch_pass`'s lazy `usage.resolve_store_path(store_path)`
call (`desktop/push.py:363`) moves up to the entry point.

⚠ **The Pi is the exact opposite and has no seam at all.** See §6 — that is real
work, not a rename.

### 3.4 The secret

**`~/.config/zeropi-display/token`, mode 0600. Never in either database.**

The token is not used by anything this spec builds — the CLI is local and needs
no auth. It is specified now so the config surface is **designed to hold a
secret** before the web UI arrives and someone reaches for the nearest table.

Argued from this repo's own history: `infrastructure.md` was committed with
credentials once and is gitignored only after the fact, and `CLAUDE.md` now says
"do not add secrets to tracked files going forward". §3.1's point 3 is the other
half — the store is the thing you copy around, so it must stay safe to copy by
construction.

**Token generation, rotation and revocation are out of scope** (§12).

### 3.5 Failure behaviour

| Situation | Behaviour |
|---|---|
| Config store missing | **Create it with defaults**, mirroring `open_store`'s behaviour for a new store. Not an error. |
| Corrupt, or `user_version` mismatch | **Fail closed**, with a precise error naming the file and the version found. |
| Value out of range | **Rejected, never clamped** (§3.6). |
| Value fails to coerce to its declared type | Rejected, same path as out-of-range. |

**Fail-closed is affordable because the panel is e-ink and holds its last
frame.** A Desktop service that refuses to start degrades to a *stale* display,
not a blank one. This is the property that makes refusing to run the safe
choice rather than the timid one.

### 3.6 Unknown keys, and the version gate

**Rejected on write. Warned-and-ignored on read.** Split by direction, and the
split is load-bearing:

- **Write** is where a human typo actually happens, and it is caught in front of
  the person who made it — so a typo can never silently do nothing.
- **Read** must tolerate an orphan row, because when a future version *retires*
  a setting, every existing config store holds a key that used to be legitimate.
  Reject-on-read would turn a routine upgrade into a hard failure on data the
  previous version wrote itself.

`user_version` on the config store exists so a retired key can be **actively
deleted by a migration** rather than lingering forever.

### 3.7 Restart semantics

**Changing a setting requires a restart of the affected process.** Configuration
is frozen at entry (§3.3), so a change takes effect on restart. This is
supported by three existing properties: the unit is already built to be
restarted (`Restart=on-failure`, `RestartSec=10s`); the tick is idempotent,
re-deriving pending Readings from the store every pass; and a restart is
**observable in the journal**, where a silent reload is not. It keeps *"which
Configuration is this process running?"* a single answerable question.

**A mid-Batch restart is safe by construction** — `mark_pushed` fires per-Ack
*inside* the send loop (`desktop/push.py:153`), so an interrupted Batch leaves
sent rows marked and the rest pending, and the next tick resends only the
remainder.

⚠ **A config write does NOT auto-restart.** The surface reports affected
settings as *pending restart* and offers an **explicit restart action** (§9.4).
Writing configuration and bouncing a service are different acts and must look
different; auto-restarting on write would also kill an in-flight Batch as an
invisible side effect of editing a field.

⚠ **"Pending restart" needs no new state.** It is

```
MAX(config.updated_at)  >  systemctl --user show zeropi-push -p ActiveEnterTimestamp
```

— the property is populated and this comparison is the whole mechanism. This
matters because it preserves the rule that **the resident service never writes
Configuration**; only the CLI and the later web surface do. **Do not add a
service-side write path to record a loaded revision.**

⚠ **The asymmetry, which §6 inherits: the Desktop restarts, the Pi applies
live.** You cannot restart `receive.py` from a Settings Payload without dropping
the BLE connection that delivered it.

## 4. The constant inventory

**18 Configuration keys (10 Tier 1, 8 Tier 2), 10 Tier 3 invariants that are not
stored at all, and 8 constants outside the Tier system entirely.** This section
is the single authority on each key's type, range and Tier.

Bounds written as **expressions over Tier 3 constants** are evaluated where the
schema is defined, not hand-copied; the parenthesised figure is what they
evaluate to today. **A Tier 3 amendment therefore moves the range
automatically** rather than leaving a stale literal to drift.

### 4.1 Tier 1 — deployment facts (10 keys, freely editable)

| Key | Type | Default | Replaces | Note |
| --- | --- | --- | --- | --- |
| `paths.projects_root` | `path` | `~/.claude/projects` | `usage.py:43`, `gauge.py:25` | **one key, two constants today** — see §11 trap 1 |
| `paths.store` | `path` | `~/.local/share/zeropi-display/usage-archive.db` | `usage.py:42` | `--store` and `ZEROPI_USAGE_STORE` still win above it (§3.2) |
| `paths.rate_limits` | `path` | `~/.local/state/zeropi-display/rate-limits.json` | `gauge.py:23` | |
| `paths.sessions_dir` | `path` | `~/.claude/sessions` | `gauge.py:24` | |
| `pricing.overlay` | `json` | `{}` | `usage.py:34` `PRICING` | **overlay, not replacement** — prefix-matched exactly as the built-in is |
| `pricing.free_models` | `json` | `[]` | `usage.py:40` | **union** with the built-in `{"<synthetic>"}`, never a replacement |
| `pricing.web_search_usd_per_request` | `real` | `0.01` | `usage.py:39` | `>= 0` — a type constraint, not a policy range |
| `context_window.overlay` | `json` | `{}` | `gauge.py:33` | overlay |
| `batch.scheduled_hour` | `int` | `4` | `service.py:48` | `0..23` — a clock hour, local (pipeline §7.5) |
| `pi.address` | `text` or null | `null` | — | **new in this spec** — see §4.6 |

⚠ **The pricing table and `CONTEXT_WINDOW` are overlays, and that is not a
detail.** A *missing* entry already degrades gracefully — **Cost Complete**
models exactly that, and `price_for()` returns `known=False`. A *wrong* entry
has no such safety net: it silently corrupts every cost the panel draws.
Replace-semantics turns "add the new model" into "delete Opus pricing"; an
overlay cannot. The cost is one non-scalar type in the schema (`json`), and it
is worth it.

### 4.2 Tier 2 — policy, with guardrails (8 keys)

| Key | Type | Default | Range | Why those bounds |
| --- | --- | --- | --- | --- |
| `service.poll_interval_s` | `real` | `30.0` | `5.0 .. GAUGE_EXPIRY_S/2` (150.0) | Floor: the loop re-reads a JSON file claude-hud rewrites at its own cadence — under 5 s is pure I/O. Ceiling: polling slower than half the expiry lets a Gauge change go unnoticed until the on-screen Gauge has already expired. |
| `service.gauge_throttle_s` | `real` | `120.0` | `30.0 .. GAUGE_EXPIRY_S/2` (150.0) | Ceiling is **#55's finding**, ADR-0008 as amended: a replacement Gauge must land well before expiry, or expiry stops meaning "the Desktop is gone" and starts firing in normal operation. Floor: below 30 s the BLE work per change dominates for zero panel benefit — ADR-0008's floor gates every draw at 300 s regardless. |
| `gauge.stale_threshold_s` | `int` | `300` | `60 .. GAUGE_EXPIRY_S` (300) | Third member of the coupled family. Ceiling is exactly expiry: a snapshot older than expiry arrives on the Pi already expired (`gauge.py:29`), so pushing it is guaranteed waste. |
| `batch.catchup_threshold_s` | `int` | `86400` | `3600 .. 604800` | Floor one hour — below a normal daily cadence, catchup trips constantly. Ceiling one week: `usage.window_days` caps the archive a Batch can cover, so catchup beyond the Window cannot recover anything. |
| `usage.window_days` | `int` | `7` | `5 .. 30` | **Floor is panel geometry**: `render.py:148` draws five rows at 20 px pitch, so a Window under 5 starves the Historic View. Ceiling: ADR-0003 sends one write per Reading, so each extra day is extra BLE work for rows the panel never draws. *(Not bound by `MAX_PAYLOAD_BYTES` — the Window scales the write **count**, not one Payload's size.)* |
| `push.scan_timeout_s` | `real` | `10.0` | `1.0 .. 60.0` | Ceiling: a scan longer than a minute outlives the cadence it feeds. Floor: 1 s is below observed scan/connect (~7 s in #55's measurement) and will simply always fail — kept as the type floor, not a recommendation. |
| `push.ack_timeout_s` | `real` | `10.0` | `1.0 .. 60.0` | As above; a Gauge push that fails is dropped silently with no retry (pipeline §7.4), so a long timeout costs a stalled loop rather than a retry storm. |
| `pi.idle_keepalive_s` | `int` | `86400` | `3600 .. 604800` | **The only Setting.** Floor one hour: ADR-0007 is full-refresh-only, so every keepalive is a full panel refresh against a panel rated for one update per 180 s. Ceiling one week: past that the "still alive" reassurance the keepalive exists to give is gone. |

⚠ **`BATCH_SCHEDULED_HOUR` and `BATCH_CATCHUP_THRESHOLD_S` are independently
ranged; there is no invariant against `IDLE_KEEPALIVE_S`.** Both happen to be
86400, and the coupling was looked for and **is not there**: a Batch landing
late does not break a keepalive redraw, it means the panel holds the Historic
View one refresh longer. Recorded so nobody invents the constraint later.

### 4.3 Tier 3 — verified invariants (10; not settings, not stored)

**Tier 3 never enters the Configuration store.** `CONTEXT.md` says a verified
invariant is *"displayed read-only rather than hidden"* — that is a UI
statement, satisfied by importing the module constant. A row in a SQLite file is
writable by anyone with `sqlite3`, which is the one thing this Tier exists to
prevent. **Consequence: the schema's tier field only ever reads 1 or 2.**

| Constant | Value | Fixed by |
| --- | --- | --- |
| `REDRAW_FLOOR_S` (`receive.py:38`) | `300` | ADR-0008 — panel rated for one update per 180 s; 300 s is the operating point |
| `GAUGE_EXPIRY_S` (`receive.py:45`) | `300` | ADR-0009 / ADR-0010, **amended 2026-09-09 by #55** on measurement |
| `MAX_PAYLOAD_BYTES` (`push.py:54`) | `512` | ADR-0001, re-confirmed by **#67** by bisection: 512 Acked, 513 raises `INVALID_ATTRIBUTE_VALUE_LENGTH`. Not the MTU. |
| `MAX_ACK_BYTES` — **new, see §5.5** | `min(512, ATT_MTU - 5)` = `512` | #73, **measured on the wire by #78**. Has no named constant today; this spec requires one. |
| `SERVICE_UUID`, `WRITE_CHARACTERISTIC_UUID`, `NOTIFY_CHARACTERISTIC_UUID` | — | Must match across both ends (`push.py:41-43`, `receive.py:26-28`); changing one end silently breaks discovery |
| the 13 px text floor | `13` | `spec-eink-rendering.md` §4, re-confirmed in #66 with text rasterised on the Pi. ⚠ **Not monotone — it re-collides at 14 px.** Lives as inline `_font(13)` calls. |
| `PANEL_W, PANEL_H` (`render.py:24`) | `250, 122` | Hardware fact |
| `WATCHDOG_TIMEOUT_S` (`render.py:35`) | `30.0` | Rendering spec §8 — `ReadBusy()` is an unbounded upstream loop; this only stops us caring about the thread, it cannot recover it |
| `FONT_DIR` (`render.py:29`) | DejaVu path | **Tier 3, not Tier 1.** The 13 px floor was verified on glass with DejaVu specifically; a different typeface invalidates that evidence |
| five-row Historic View pitch | 5 rows @ 20 px | `render.py:148`; it is what floors `usage.window_days` |

### 4.4 Outside the Tier system (8)

`CONTEXT.md` scopes **Tier** to *tunable values*. These are not tunable, so they
have no Tier. Listing them is what makes the inventory complete rather than
selective — an unlisted constant reads as an oversight.

- `STORE_USER_VERSION` (`usage.py:45`), `SCHEMA_VERSION` (`receive.py:34`) —
  migration machinery
- `WORKTREE_MARKER` (`usage.py:125`) — a parsing constant
- `APP_ID`, `MACHINE_ID_PATHS` (`push.py:77-79`) — Desktop identity; ADR-0006
  wipes the Pi when it changes
- `DB_PATH` (`receive.py:32`) — **Tier 1 in nature, but not Configuration.**
  Configuration is the Desktop's own values and the Pi holds none. Owned by
  `pi/install-pi.sh`, deliberately hardcoded per pipeline §8.1.
- the 60 s tick (`receive.py:739`, an unnamed `60_000` literal) —
  countdown-animation mechanism. **Name it**, even though it stays code:
  ADR-0008's amendment reasons about it explicitly ("a phase coincidence between
  the 60 s tick and the floor"), and reasoning about an anonymous literal is how
  it drifts.
- `PANEL_SERVICE`, `BUSY_PIN` (`epd-selftest.py:48,51`) — bench-tool constants,
  not part of the running system
- **The BLE lock path** (§7.1) — mechanism, not policy.

### 4.5 Migration

**No key is retired.** Nothing configurable exists today beyond
`ZEROPI_USAGE_STORE` and two `--store` flags, all of which §3.2 preserves *above*
Configuration in precedence. So `user_version = 1` is the initial schema with no
deletion step; the retired-key case §3.6 guards against does not yet arise.

### 4.6 `pi.address` — the 18th key

⚠ **This key amends #72's inventory of 17, and is stated loudly rather than
slipped in.** It exists because §8's fourth verdict state, *not paired*, is
otherwise not knowable.

Today `find_pi()` (`desktop/push.py:253`) filters on `SERVICE_UUID` and takes
**the first advertiser**. A Desktop with a Pi in range is therefore always
"paired", one without it is indistinguishable from Unreachable, and a household
with two Pis is a coin flip.

- **Type** `text` or null; validated as a colon-separated six-octet address
  (`AA:BB:CC:DD:EE:FF`, case-insensitive). Default `null`.
- **Written by `cli.py pair`** (§9.4) and by nothing else.
- **`find_pi()` prefers it** when set, falling back to the service-UUID filter
  when it is null. A stored address that does not answer is an ordinary
  *absent* Unreachable, not a special failure.
- `pi.address` being null **is** the *not paired* state.

It is Tier 1 because it is a deployment fact with no range to validate beyond
its shape.

## 5. The wire

Two new Payload kinds and three verbs. Both new kinds are new `kind` values on
the **existing** write characteristic — not a second characteristic, which would
give the Pi a second input channel that can disagree with the first, the exact
property an HTTP service on the Pi was ruled out for.

`parse_payload` (`receive.py:274`) already branches on `kind` and rejects
anything else, and `build_ack` already echoes it, so this **extends a
discriminator that exists**.

⚠ **Both new kinds run the Desktop Id wipe check unchanged**
(`check_desktop_id`, `receive.py:644`, before dispatch). A Command from an
unrecognised Desktop means a hand-off happened and must wipe exactly as data
would. Exempting Commands would create a path where a new Desktop's `redraw` —
or its `wipe` — executes against the old Desktop's Readings with no hand-off
recorded.

### 5.1 The Settings Payload

```json
{
  "kind": "settings",
  "desktop_id": "9f2c1ab34d5e6f70",
  "settings": {"idle_keepalive_s": 86400}
}
```

~92 bytes; no budget concern in either direction.

- **Nested under `settings`**, not flattened, so a key can never collide with
  the reserved `kind` / `desktop_id` and the Pi can iterate.
- ⚠ **The object is the complete Settings set, not a patch.** After applying
  one, the Pi's Settings are exactly what it was sent. This is what makes a
  resend free, and it is the property §7.2's re-assertion and ADR-0011's
  no-queue rule both rest on.
- **Unknown keys are rejected**, with the reason in the Ack — §3.6's
  reject-on-write, not a new rule. It means a Desktop upgraded ahead of its Pi
  fails **loudly**, which is the intent: a silently-ignored Setting is precisely
  the bug §6.2's persistence exists to prevent. Both ends install from this
  repo, so the skew window is a deploy, not a supported configuration.
- **Today the set is one key**, `idle_keepalive_s` (`int`, `3600..604800`,
  default `86400`).

**Ack**: `{"status": "ok", "kind": "settings", "wiped": false}`, with `reason`
on error. Deliberately minimal — confirming *what the Pi now holds* is
observability, and belongs to the status verb (§5.4).

### 5.2 The Command Payload

```json
{
  "kind": "command",
  "desktop_id": "9f2c1ab34d5e6f70",
  "verb": "redraw"
}
```

An unrecognised `verb` is rejected with an error Ack, the same way an
unrecognised `kind` already is.

⚠ **Natural idempotency is a membership rule for the vocabulary, not a per-verb
property to check afterwards.** A verb that cannot be made naturally idempotent
**does not get added**. There are no command ids, no dedup table and no
at-most-once machinery anywhere in this design, and there must not be — an Ack
can be lost, so every verb must tolerate arriving twice. This rule, more than
the list's shortness, is what keeps a vocabulary from becoming an RPC surface.

⚠ **No Command may override a verified invariant**
([ADR-0013](adr/0013-no-command-overrides-a-verified-invariant.md)). Enforcement
that a Tier moved out of reach of a settings form must not be reachable through
a verb instead.

### 5.3 The three verbs

**`redraw`** — refresh the panel with whatever the correct current frame is: the
Gauge if one is live and unexpired, otherwise the Historic View. It is **not**
"show me the Historic View".

```json
{"status": "ok", "kind": "command", "verb": "redraw",
 "drawn": false, "floor_remaining_s": 143, "wiped": false}
```

`floor_remaining_s` is `REDRAW_FLOOR_S - (now - last_drawn_at)`, and is omitted
when `drawn` is true. It is what stops "queued" reading as "nothing happened".

⚠ **`redraw` queues behind `REDRAW_FLOOR_S`. It does not override it and it is
not rejected.** ADR-0008 makes the Pi the enforcer of a *hardware wear limit*; a
verb that overrode it would relocate that enforcement to whoever types the
command, where the failure mode — a loop, a retry, a script — is invisible and
cumulative against a panel rated for one update per 180 s.

**`wipe`** — drop and recreate `readings`, delete `coverage_start`.

```json
{"status": "ok", "kind": "command", "verb": "wipe", "wiped": true}
```

Reuses the **existing** `wiped` Ack flag rather than adding one, and the reuse is
semantically exact: ADR-0006 has the Desktop clear its push marks on that flag,
which is precisely what must happen after a manual wipe too.

⚠ **This does not contradict ADR-0006**, which rejected a manual wipe as a
*replacement* for the automatic one. As **repair** it fills a real gap:
`upsert_reading` never deletes, so a Reading the Desktop's archive no longer
holds is otherwise unremovable except by re-coupling. It is safe only because
ADR-0005 makes the Desktop the archive of record — a re-push restores everything
that should be there.

**`status`** — see §5.4.

**Ruled out, and staying out**: `selftest` (`receive.py` owns the panel, so a
remote self-test either collides on GPIO or duplicates the render worker as a
second draw path — and the self-test's value is *eyes on the glass at the
bench*, which a remote invocation does not have); `re-pair` (Desktop Id change is
already automatic, and ADR-0006 rejected manual pairing for the
"a step you forget to run fails silently" reason, which is unchanged).

### 5.4 The status reply

**Status is requested, never carried**
([ADR-0012](adr/0012-status-is-requested-not-carried.md)). The ordinary Daily
and Gauge Acks **do not change at all**.

Request — an ordinary Command Payload, 71 bytes:

```json
{"kind": "command", "desktop_id": "9f2c1ab34d5e6f70", "verb": "status"}
```

Reply — an Ack, **234 bytes typical, 253 worst case**, against §5.5's measured
512:

```json
{
  "status": "ok", "kind": "command", "verb": "status",
  "drawn": false, "wiped": false,
  "frame": "historic",
  "since_redraw_s": 143,
  "panel": "ok",
  "readings": 312,
  "coverage_start": "2026-08-01",
  "uptime_s": 110000,
  "schema_version": 1
}
```

| Field | Type | Source | Meaning, and its limit |
|---|---|---|---|
| `frame` | `"gauge"`/`"historic"`/`"startup"`/`"empty"` | `GaugeState` + `RedrawGate` | What the Pi **last submitted**. E-ink cannot be read back, so this is never a readback of the glass — which is exactly why `panel` sits beside it. |
| `since_redraw_s` | int or null | `RedrawGate.last_drawn_at` | Monotonic seconds since the last accepted draw; null if never. Also not proof pixels moved. |
| `panel` | `"ok"`/`"unavailable"`/`"stuck"`/`"never"` | `PanelWorker.unavailable`, watchdog | **Panel Health.** `unavailable` = a render raised; `stuck` = the watchdog fired on the current episode; `never` = nothing attempted yet. |
| `readings` | int | `COUNT(*) FROM readings` | Divergence detector. |
| `coverage_start` | date string or null | `meta['coverage_start']` | Divergence detector. Null on a wiped or fresh Pi. |
| `uptime_s` | int | monotonic since **process** start | Not since boot — *"did `receive.py` restart"* is the question that matters. |
| `schema_version` | int | `SCHEMA_VERSION` | Catches a Pi running an older image. |

⚠ **Every field is a duration or a count — never a timestamp.** That is ADR-0009
running in reverse: the Pi is *given* durations because it has no wall clock, and
for the same reason it can only ever *report* them.

**Two governing rules for anything added here later:**

1. **Report what can disagree; derive what cannot.** `entries.pushed_at` already
   records what the Desktop pushed and when, so a "last push time" from the Pi
   could only ever *confirm*. `readings` and `coverage_start` earn their bytes
   **precisely because they can diverge**, and that divergence is what a lost
   Batch or a wipe desync actually looks like. **A field that can never disagree
   is decoration.**
2. **The Pi reports facts; the Desktop renders the Verdict.** `readings: 312`
   alone means nothing; *312, and I sent 312* is an answer, and only the Desktop
   can make that comparison. This keeps the Pi dumb, keeps the wire small, and
   lets the Verdict improve without touching the Pi.

**Two things deliberately absent:**

- **No lifetime refresh counter.** `since_redraw_s` only. A persisted count is a
  *panel-life* question, left open by the rendering map pending calendar time;
  adding it here would settle it **by accident**, as a side effect of an "is it
  working?" surface. It stays out of scope (§12).
- **No Gauge Age.** Two freshness numbers in one surface, one of them
  historically misread, is how the #66 trap gets re-lived (§11 trap 3).

### 5.5 The notify budget, and the silent truncation

**`MAX_ACK_BYTES = min(512, ATT_MTU - 5)` — 512 on this link.** Measured on the
wire by #78: the MTU exchange settles at 517, a 512-byte Ack arrives whole, and
**513 arrives as 512** and fails to parse.

⚠ **An over-budget notification is truncated SILENTLY, twice.** `bluetoothd`
clips at 512 in `gatt-database.c`, the ATT server clips again at the MTU bound
in `gatt-server.c`, **both return success**, and `bluezero` adds no check. The
Desktop then reports `malformed ack from Pi: … column 513 (char 512)` — blaming
the JSON rather than the length.

⚠ **This direction fails strictly worse than the write direction**, where #67's
enforcement produces a loud `INVALID_ATTRIBUTE_VALUE_LENGTH`. There is no
counterpart here, so **something must enforce the budget before BlueZ eats it.**
Three requirements, all binding:

1. **Add the named constant.** `MAX_ACK_BYTES`, symmetric with
   `MAX_PAYLOAD_BYTES`, on the Pi. Write it as the `min()`, not as the literal
   512 — see the correction below for why that form is what kept the answer
   right.
2. **The Pi measures every Ack before notifying.** An Ack that would exceed
   `MAX_ACK_BYTES` is replaced with a minimal, well-formed error Ack naming the
   overrun. A truncated reply must never leave the Pi.
3. ⚠ **Never echo unbounded input into an Ack.** `receive.py` echoes a rejected
   `kind` into the Ack's `reason`, which is exactly how #78 amplified a small
   Payload into an oversized Ack. Echoed values are truncated to a fixed length
   at the point they enter `reason`.

⚠ **The `- 5` is a correction and worth understanding.** BlueZ notifies with
`Handle Multiple Value Notification (0x23)`, not the classic `0x1b`, and `0x23`
carries a per-value length field — so the overhead is `ATT_MTU - 5`, not the
`- 3` #73 derived. **On this link the two-byte error was invisible**: both terms
of the `min()` land on 512 and 517 is already the maximum MTU, so the answer is
unchanged. At a smaller MTU it bites, exactly as #32 and #67 did.
**`min(512, …)` is what kept the answer right anyway**, which is the argument
for writing budgets as the expression rather than the number.

⚠ **The mechanism is still one of two.** Both candidate bounds evaluate to 512
here and cannot be told apart on this link. The number is measured; the reason
is recorded rather than re-derived. Do not "simplify" the `min()` away.

## 6. The Pi's configuration seam

⚠ **The Pi has no configuration seam at all. This is real work, not a rename.**

`receive.py` reads `GAUGE_EXPIRY_S`, `REDRAW_FLOOR_S` and `IDLE_KEEPALIVE_S` as
**module globals from inside methods** (`:366`, `:415`, `:418`), and binds
`DB_PATH` to class attributes at module scope (`:562`, `:704`) under a comment
stating it "stays a hardcoded constant (spec §8.1)". **Pipeline §8.1 therefore
has to be revisited, not worked around** — `DB_PATH` stays hardcoded and stays
correct (§4.4), but the sentence must no longer read as a rule covering every
constant in the file.

### 6.1 Settings apply live

The Desktop's answer is "restart to apply" (§3.7). **The Pi cannot be restarted
by a Settings Payload without dropping the connection that delivered it**, so
Settings must take effect under a running loop.

⚠ **`RedrawGate._idle_elapsed` (`receive.py:418`) reads `IDLE_KEEPALIVE_S` as a
module constant.** That is the single place the one Setting binds, and it must
become a looked-up value. **Otherwise "Settings apply live" is false** — the Pi
would accept and persist a Setting that changes nothing until restart, and
`CONTEXT.md` promises the opposite.

### 6.2 Settings persist, under a prefix

**The Pi persists Settings in `meta`, keyed `setting.<name>`.**

Not for the Pi's benefit — it is verified to survive reboot and `bluetoothd`
restarts. Without persistence a reboot silently reverts a Setting to the
compiled default **with nothing reporting it**: the same bug class ADR-0006's
wipe flag exists to prevent. The prefix is load-bearing, because `setting.*` keys
now share a table with `desktop_id` and `coverage_start`.

### 6.3 The code default, and the hand-off

**Before it has ever been told, the Pi runs the compiled-in constant**
(`IDLE_KEEPALIVE_S = 86400`). A code default **is not Configuration** — the same
line drawn for a Tier 3 constant — so this does not weaken "the Pi holds no
Configuration of its own".

**An ADR-0006 hand-off wipe clears Settings back to the code default.** Settings
are the *previous* Desktop's policy; leaving them would have a re-coupled Pi run
a stranger's keepalive until the new Desktop happened to push its own, with
nothing prompting it to. Cleared-to-default is exactly the state a Pi new to
this Desktop would be in, which is what the wipe means.

## 7. The Desktop's side

### 7.1 The BLE lock

**An advisory `flock(2)` acquired inside `_with_ble_connection`**
(`desktop/push.py:330`), on `~/.local/state/zeropi-display/ble.lock`, mode 0600.

Today **nothing on the Desktop can tell *absent* from *busy***: `push.py`
connects per push and disconnects, and there is **no lock, PID file or IPC of any
kind** — `service.py` simply imports `run_batch_pass` / `run_gauge_push` as
functions. Without the distinction a human is told "no Pi" while the Pi is
sitting right there mid-Batch.

- **Inside `_with_ble_connection`**, so both the CLI and the resident service
  take it by construction and nobody has to remember. `service.py` must go
  through the same seam rather than around it.
- **`flock` rather than a PID file**, because the kernel releases it when the
  holder dies. A crashed CLI leaves no stale lock, so there is no recovery story
  to design.
- **The CLI waits a bounded ~15 s; the service fails immediately.** Asymmetric
  on purpose: a Gauge push holds the link only ~2–3 s including scan and
  connect, so a short wait absorbs *every* Gauge collision and leaves only the
  twice-daily Batch (~2 min) as a genuine "busy" — while the service already
  treats both its jobs as droppable (pipeline §7.3 retries the Batch, §7.4 drops
  the Gauge).

**This is how §8 tells the two Unreachable cases apart**, and it is the whole
mechanism:

| Lock | Scan | Case |
|---|---|---|
| held past the wait | *not attempted* | **busy** |
| acquired | times out | **absent** |
| acquired | finds the Pi | reachable |

⚠ **An absent Pi costs a full `push.scan_timeout_s` (10 s default) before it is
known.** That is the floor on how fast the CLI can say so, and the CLI must say
it is scanning rather than appearing hung.

### 7.2 Settings re-assertion

**The Desktop re-asserts the complete Settings set at the head of every
connection it opens**, and holds no "last delivered" record.

This is what makes ADR-0011's no-queue rule work: because the Settings Payload
is declarative (§5.1), **the current Configuration *is* the pending state**.
There is nothing to store, and *"not applied yet — it will be applied on the
next successful connection"* is true with no bookkeeping behind it.

⚠ **A mark-based alternative was ruled out on §3.7's constraint**: the mark
would have to be written by the resident service, which **never writes
Configuration**, so it would have needed a third store invented to hold it.

⚠ **Re-assertion is best-effort, and this changes `_with_ble_connection`'s
*contract*, not just its callers.** A Settings write now precedes whatever the
connection was opened for, and:

- its failure **logs and continues**;
- it is **not counted** in the Batch's failed-row total;
- it **does not affect the exit code**.

Otherwise a flaky Setting starts failing Batches for no reason. **The one
exception is a CLI-initiated settings change**, whose only job is that write —
there a failure genuinely is the failure.

## 8. The Verdict

**Four states, three severities, one precedence order.** This is the part of the
design a prototype changed rather than confirmed, and it is the part most worth
implementing exactly as written.

### 8.1 Four states

| State | Glyph | Meaning |
|---|---|---|
| working | `✓` | Every comparison passed, or failed only as a note. |
| not working | `✗` | A real fault. |
| **can't tell** | `?` | **Unreachable** — both cases. Nothing was learned. |
| not paired | `–` | `pi.address` is null (§4.6). |

⚠ **Collapsing Unreachable into "not working" is the failure this state model
exists to prevent.** Unreachable is an expected steady state; a Pi that is
powered off is not broken, and a surface that says it is trains people to ignore
the surface.

### 8.2 Three severities

`OK` (`✓`), `FAIL` (`✗`), and **`NOTE` (`·`) — shown, but never headlining.**

⚠ **The third severity is not a nicety.** Without it, a Pi that rebooted four
minutes ago and is drawing its startup frame perfectly renders as
`✗ Not working — the Pi restarted since the last push`. **`Restarted` is a
comparison, not a fault.** This was invisible on paper and obvious the moment the
words were on a screen.

### 8.3 The six comparisons

Every one is a comparison the Pi could not make, because only the Desktop holds
the other side.

| Check | Rule |
|---|---|
| **Coupled** | A reply arrived and `wiped` is false. |
| **Readings** | `readings` vs the Desktop's count of distinct `(local_date, project_key, model)` with `pushed_at IS NOT NULL`. |
| **Coverage** | `coverage_start` vs `MIN(local_date)` over pushed entries. |
| **Panel** | `panel == "ok"` **and** `since_redraw_s <= pi.idle_keepalive_s + REDRAW_FLOOR_S`. The bound is **derived, not guessed**: at rest the Historic View redraws only when pending or when the keepalive is due, so anything beyond one keepalive plus one floor is genuinely wrong. |
| **Restarted** | `uptime_s` less than the elapsed time since the Desktop's last successful push. **Severity `NOTE`, never `FAIL`.** |
| **Image** | `schema_version` vs the Desktop's expectation. |

### 8.4 Precedence, and the Readings/Coverage coupling

**Headline the most explanatory failure**, in this fixed order:

```
Coupled  >  Image  >  Panel  >  Readings  >  Coverage  >  Restarted
```

⚠ **Counting checks produces a worse answer than naming the story.** The first
render of a diverged Pi said `✗ Not working — 2 checks failed`, when the truth is
*one* fact: a lost Batch shows up as Readings **and** Coverage failing together.

**So: when Readings has already failed, Coverage demotes to a `NOTE`.** It is
the same fault seen twice, and reporting it twice makes the surface less
informative, not more.

### 8.5 The two Unreachable headlines

⚠ **One glossary term, two headlines.** The first render printed *"the Pi is
unreachable"* and then, one line later, *"the Pi is there"* — for the **busy**
case, where this Desktop is the thing occupying the link.

| Case | Headline | Advice |
|---|---|---|
| absent | *Can't tell — the Pi is unreachable.* | "Re-run when the Pi is back." |
| busy | *Can't tell yet — the link is busy.* | "Retry in a moment — a Batch takes about two minutes." |

Both must state **"Nothing was queued"** (ADR-0011), because a human who has
just been refused will otherwise assume something is pending.

## 9. The CLI

### 9.1 Invocation

**`python desktop/cli.py <command>`. Not a `zeropi` console script.**

A bare command means packaging — `pyproject.toml`, `[project.scripts]`,
`pip install .` — which this repo has deliberately avoided twice over
(`pytest.ini` sets `pythonpath` precisely to dodge it; `install-desktop.sh`
deploys by copying files). **Every line of output in this section is identical
either way**, so packaging buys nothing here. It becomes natural when the web UI
brings a service that must be installed anyway.

⚠ **`push.py`'s existing CLI stays exactly as it is.** Pipeline §7.6 is binding
and the test suite drives those flags. `cli.py` is a **front end, not a
replacement**: it calls `run_batch_pass` / `run_gauge_push` as functions, never
by shelling out. Retiring `push.py`'s flags would be a second, unrelated
migration inside a milestone that has enough to carry.

Global options: `--config PATH` / `ZEROPI_CONFIG` (§3.2's one bootstrap escape),
`--store PATH` / `ZEROPI_USAGE_STORE` (pipeline §4.5, unchanged), `--json`.

### 9.2 `status`

**A headline Verdict, an identity block, then all six comparisons as evidence.**

⚠ **The evidence list is fixed, not filtered. All six rows print, always** —
the failing ones do not hide the passing ones. This is stated explicitly because
an implementer will otherwise reasonably "tidy" it into showing only failures,
which is a different design that was considered and rejected as the default.

```
  ✓  Working.

  Desktop   zeropi-push running, up 3d 4h
            last Batch 09:04 today, 312 Readings sent
  Pi        replied in 1.2s, up 1d 6h

  Coupled    ✓  replied, not wiped
  Readings   ✓  312
  Coverage   ✓  2026-08-01
  Panel      ✓  historic frame, drew 2m 23s ago
  Restarted  ✓  up 1d 6h
  Image      ✓  schema 1
```

A fault headlines by §8.4's precedence and names the story, not a count:

```
  ✗  Not working — the Pi is missing 47 Readings.
  …
  Readings   ✗  265  (47 missing)
  Coverage   ·  2026-08-08  (short)
```

Unreachable shows what is still known from the Desktop's own records, and says
plainly that nothing was queued:

```
  ?  Can't tell — the Pi is unreachable.

     no Pi advertising 6e400001-… within 10.0s.
     Normal when it is powered off or out of range. Not a fault.

  Desktop   zeropi-push running, up 3d 4h
  Pi        last seen 09:04 today (4h 7m ago)
            holding 312 Readings then

  Nothing was queued. Re-run when the Pi is back.
```

**`--brief`** is the one-line form: one line when everything is fine, detail only
for what is wrong. It is a mode, not the default.

```
  ✓ Working. Pi up 1d 6h, historic frame drawn 2m 23s ago,
    312 Readings agreed from 2026-08-01.
```

⚠ **A two-column Desktop-vs-Pi ledger was designed and rejected.** It looks
precise, but only two of the six comparisons have a genuine Desktop-side value,
so four cells are `—` or filler and the format promises a symmetry the data does
not have. **Do not reintroduce it.**

### 9.3 `config`

`config` with no arguments lists **all three Tiers**, with Tier 3 shown
**read-only beside its ADR** rather than hidden, and the Pi-projected Setting
marked `→ Pi`:

```
  Tier 2 — policy (editable within a validated range)

    service.gauge_throttle_s              120.0       30.0 .. 150.0
    …
    pi.idle_keepalive_s                   86400       3600 .. 604800   → Pi

  Tier 3 — verified invariants. NOT settings; shown so you can see them.

    REDRAW_FLOOR_S                        300                   ADR-0008
                                          panel rated for one update per 180 s
```

`config get <key>` prints one value. `config set <key> <value>` writes one, after
validating it against §4.

**Both refusal shapes are part of the deliverable**, because rejection is a
first-class output here — values are refused, never clamped.

An out-of-range Tier 2 value **explains that its bound is derived**:

```
  ✗ Refused. 600.0 is outside 30.0 .. 150.0.

    The ceiling is GAUGE_EXPIRY_S / 2 — derived from a verified invariant
    (ADR-0008, amended by #55), not typed in here. A replacement Gauge must
    land well before expiry, or expiry stops meaning "the Desktop is gone"
    and starts firing in normal operation.

    Nothing was written.
```

A Tier 3 write is refused as **"not a setting"**, naming the run it would
invalidate:

```
  ✗ REDRAW_FLOOR_S is not a setting.

    It is a verified invariant fixed by ADR-0008: the panel is rated for one
    update per 180 s and 300 s is the operating point established on hardware.
    Lowering it here would invalidate that run, so it is not stored as
    Configuration at all — it is a constant in the code, shown read-only.
```

A successful write reports **pending restart** (§3.7) and names the action.

### 9.4 The action commands

| Command | Does |
|---|---|
| `pair` | Scans, records `pi.address` (§4.6), reports the coupling and any ADR-0006 wipe, then pushes the archive. |
| `push [--now]` | Runs a Batch over one connection. Reports rows sent/failed, the new coverage range, and that Settings were re-asserted (§7.2). |
| `redraw` | Sends the `redraw` verb. |
| `wipe` | Sends the `wipe` verb, **after typed confirmation** of the Pi's short id. |
| `restart` | `systemctl --user restart zeropi-push` — §3.7's explicit restart action. |

**A queued `redraw` names its own wait**, so ADR-0008's floor is visible rather
than reading as an unexplained delay:

```
  Queued on the Pi. It will draw in 3m 22s.

    ADR-0008 gates every draw at 300 s and the last one was 98 s ago, so the
    Pi holds this rather than overriding the floor. Nothing further to do.
```

**A refusal states that nothing was queued, and why** — most sharply for `wipe`,
which is the case ADR-0011 turned on:

```
  ✗ Refused — the Pi is unreachable (no Pi advertising within 10.0s).

    Not queued. A wipe is the one destructive verb, and a queued one fires at
    whatever Pi answers next — the reason this Pi is unreachable may be the
    reason not to wipe it (powered down for a hand-off, or already coupled to
    another Desktop). Re-run with the Pi in range. [ADR-0011]
```

⚠ **`wipe` requires typed confirmation of the Pi's short id.** It is the one
destructive verb, and a `y/n` prompt is not a speed bump when the shell history
is one arrow key away.

**The human is the only retrier.** Nothing on the Desktop re-sends a Command on
its own — that is what keeps *"a Command will be retried"* from quietly meaning
*"the Desktop will fire it again at a time nobody chose"*.

### 9.5 `--json`

**`--json` exists now rather than being retrofitted.** The web UI is the
consumer, and the shape must be settled before something starts scraping the
text above.

```json
{
  "verdict": {"ok": true, "state": "ok", "headline": "Working."},
  "reachable": true,
  "desktop": {"desktop_id": "9f2c1ab34d5e6f70", "service": "running", "…": "…"},
  "pi": {"frame": "historic", "since_redraw_s": 143, "panel": "ok", "…": "…"},
  "checks": [
    {"name": "coupled", "severity": "ok", "ok": true, "detail": "replied, not wiped"}
  ]
}
```

⚠ **`verdict.ok` is nullable — `true` / `false` / `null` — and `severity` sits
beside it.** `null` is *can't tell*, and it exists to force a consumer to handle
that state rather than falling through to a falsy "broken". `verdict.state`
carries the four-way. **A consumer reading only `ok` as a boolean is the bug this
shape prevents**, and it is the same bug §8.1 prevents in the text rendering.

`checks` is always the six entries of §8.3, in §8.4's precedence order, present
even when `reachable` is false (with `ok: null`). Same rule as §9.2: **fixed, not
filtered.**

### 9.6 Exit codes

| Code | Meaning |
|---|---|
| `0` | Working. |
| `1` | A real fault — the Verdict is *not working*. |
| `2` | **Can't tell** — either Unreachable case, or *not paired*. |
| `3` | Refused, or a usage error — an out-of-range value, an unknown key, a Tier 3 write, or a Command refused because the Pi is Unreachable. |

⚠ **`2` is split from `1` deliberately.** A cron wrapper must not page on an
expected steady state, and folding a refusal into `1` would make *"the Pi is
broken"* and *"you typed a bad value"* indistinguishable to a script. This is
§8.1's four states surviving all the way to the shell.

## 10. Testing

The suite is **246 tests** and must stay green with **no panel, no SPI, no
`bluezero`, no `~/.claude`, and no Pi**. Everything below holds under those
constraints; what does not is named in §10.6.

### 10.1 The Configuration store

Pure filesystem work against `tmp_path` — no fixtures beyond a scratch file.
Assert: a missing store is created with defaults; a `user_version` mismatch
fails closed rather than proceeding; a value out of range is rejected and
**nothing is written**; an unknown key is rejected on write; an unknown key
already in the store is ignored on read **and warns**; precedence resolves
CLI over env over store over default, in that order.

### 10.2 The schema is table-driven, and its derived bounds are tested

Every key in §4 gets its boundary values asserted — both ends, in and out.

⚠ **Assert the derived bounds are still derived**, not merely correct:
`service.gauge_throttle_s`'s ceiling `== GAUGE_EXPIRY_S / 2`, and
`gauge.stale_threshold_s`'s `== GAUGE_EXPIRY_S`. **This is the test that catches
an ADR amendment drifting away from the range it is supposed to move**, which is
the failure §4's expression form exists to prevent. A test asserting the literal
150.0 would pass while the invariant rotted.

Also assert the schema's tier field **never reads 3** (§4.3).

### 10.3 The Verdict is a pure function

`(desktop facts, pi status dict or None) → Verdict`. No BLE, no clock, no I/O —
this is where the design lives and it must be testable without either machine.

The prototype's seven scenarios are the fixture list: `ok`, `unreachable`,
`busy`, `never-paired`, `diverged`, `stuck`, `restarted`. Assert for each: the
state, the headline, and that **six checks are present**.

Then the three findings that a naive implementation gets wrong, each as its own
test:

1. **`restarted` yields `working`**, with one `NOTE` — not `not working`.
2. **A diverged Pi headlines Readings**, and Coverage is demoted to a `NOTE`
   rather than reported as a second failure (§8.4).
3. **`unreachable` and `busy` yield different headlines**, both with state
   `can't tell` (§8.5).

### 10.4 The wire

- **Payload construction and Ack parsing** for `settings` and `command` are pure
  functions over dicts; test them as pipeline §11 tests the existing kinds.
- ⚠ **Assert the worst-case status Ack is under `MAX_ACK_BYTES`** — every field
  at its longest, as a one-line test. This is cheap and it is the only thing
  standing between a future field and a silent truncation.
- **Assert an over-budget Ack is replaced before notifying** (§5.5 requirement
  2), and that an oversized echoed `reason` is truncated at the point of entry
  (requirement 3).
- **The Pi's Settings path** is testable against a scratch SQLite file with no
  BLE: applying a Settings Payload writes `setting.*` rows; a hand-off wipe
  clears them; an unknown key is rejected with a reason.
- ⚠ **Assert the Setting actually binds live**: drive `RedrawGate` with an
  injected keepalive value and prove `_idle_elapsed` follows it (§6.1). **A test
  that only checks the row was written would pass against the bug.**

### 10.5 The lock and re-assertion

`flock` needs no second machine — two file descriptors in one test process are
enough. Assert: the second acquirer blocks and then gives up at the bound; the
CLI's wait and the service's immediate failure are distinct paths; **a failed
re-assertion does not fail the Batch it rode with**, and does not change the
exit code (§7.2); and a CLI-initiated settings write **does** fail on the same
error.

### 10.6 What tests cannot tell you

The BLE round trip itself, and therefore §5's real byte counts on the wire.
**This milestone is not done without a hardware verification run** in the shape
of `docs/e2e-verification.md` and `docs/usage-pipeline-verification.md`: a
Settings Payload landing and surviving a reboot, all three verbs, a real
`status` reply, both Unreachable cases produced deliberately, and `btmon`
confirming the status Ack's size on the wire.

⚠ **The dev Pi is shared.** Check for a live collision before touching it, and
remember `receive.py` owns the panel.

## 11. Known traps — do not rediscover these

1. ⚠ **`DEFAULT_PROJECTS_ROOT` is defined twice today** — `usage.py:43` as
   `Path("~/.claude/projects").expanduser()` and `gauge.py:25` as
   `Path.home() / ".claude/projects"`. Same value by two expressions, with
   nothing keeping them equal. §4.1 collapses them to one key; **two keys would
   mean a Gauge and a Daily reading different directories.** Pre-existing latent
   debt, found while tiering.
2. ⚠ **`PanelWorker.unavailable` and the watchdog have no route off the Pi.**
   They are computed and discarded into the Pi's own log — the journal this
   whole surface exists to stop people reading. §5.4's `panel` field is what
   gives them one.
3. ⚠ **Gauge Age is not "time since arrival".** It is the snapshot's own age
   *plus* the monotonic seconds since the Payload arrived. `CONTEXT.md` defined
   it wrongly until #74, so the #66 trap was baked into the binding glossary.
   **Any new freshness field must say which age it means**, or it reads as an
   off-by-300s bug. This is why §5.4 keeps Gauge Age out of the status reply
   entirely.
4. ⚠ **`drawn` does not mean "pixels moved".** It means the redraw gate accepted
   the frame for drawing. `drawn: true` with `panel: "stuck"` is a frame
   submitted into a black hole — which is exactly why both fields exist.
5. ⚠ **An expired-on-arrival Gauge is claude-hud being stale, not a slow link.**
   The CLI must say so, or the next person chases the radio:
   *"the snapshot was already 312 s old when it reached the Pi"*.
6. ⚠ **A truncated Ack reads as malformed JSON.** The underlying error is
   `malformed ack from Pi: … column 513`, and **the column number is the budget**
   — but nothing reads it. The CLI must name this failure explicitly (§5.5).

## 12. Out of scope — do not drift into these

- **The web service and the SPA.** Framework, process model, page inventory, how
  the token is presented, whether it runs under `systemd --user` beside
  `zeropi-push`. **A later map, by design**, opened against this substrate.
  Building it first produces a pile of shell-outs.
- **Token lifecycle** — generation, rotation, revocation, and what happens to a
  phone holding a stale one. Its *location* is settled (§3.4); nothing else is.
- **Panel refresh accounting.** The refresh budget over the panel's life is open
  from the rendering map, wanting calendar time rather than a decision. A status
  surface is the natural place to *count* refreshes, but whether that counter is
  persisted, what it costs the Pi, and what retention it implies is not settled
  — and §5.4 deliberately declines to settle it by accident.
- **An HTTP service on the Pi.** It contradicts the dumb-receiver property that
  lets the Pi run with no clock, no fetching and no credentials, and it gives the
  Pi a second input channel that can disagree with the first.
- **Two independent management interfaces, one per end.** Two places to look for
  *is it working* is the pain this document exists to remove.
- **Real auth or TLS.** LAN-bound behind a single shared token over plain HTTP,
  when the UI arrives. Disproportionate for a personal usage display on a home
  LAN.
- **Fixing `desktop/install-desktop.sh`'s standalone mode** (it deploys only
  `push.py`, so the Desktop service cannot run standalone). Installer debt
  inherited from #34, not a Management Surface. *Coupling a Desktop to a Pi is
  in scope* — that is `pair`, and it is a different thing from installing.
- **Reading history back over BLE**, and any change to the Daily or Gauge Acks
  (ADR-0012).

## 13. What this spec decided that no ticket had

The gap check. Each of these would otherwise have been invented by whoever
implemented it.

1. **`pi.address`, the 18th key** (§4.6). #76's prototype rendered a *not paired*
   state and a `pair` command, and nothing on the Desktop could have known
   either: discovery is first-advertiser-wins. Without this key the fourth
   Verdict state is decorative, and a two-Pi household is a coin flip. It amends
   a closed ticket's inventory, so §4.6 says so loudly rather than slipping it
   into the table.
2. **Exit codes** (§9.6), and specifically that **`2` is split from `1`**. The
   four-state Verdict is worth nothing to a script if Unreachable exits the same
   way a stuck panel does.
3. **How *absent* and *busy* are actually distinguished** (§7.1). #79 decided the
   lock and #76 found that the two cases need different headlines; neither wrote
   down the three-row table that turns the lock into the discriminator.
4. **The Pi must enforce `MAX_ACK_BYTES` itself, and never echo unbounded input**
   (§5.5). #73 and #78 established the budget and proved truncation is silent;
   the requirement that *something on the Pi checks before notifying* — and that
   the `reason` echo is the specific amplification path — is taken here.
5. **`config.updated_at` is a column, and the only one.** §3.7's pending-restart
   check needs a timestamp; the temptation is a revision counter or a
   service-written "loaded" row, and either breaks the rule that the resident
   service never writes Configuration.
6. **`push.py`'s CLI survives untouched** (§9.1). The alternative — folding it
   into `cli.py` — is a second migration with its own test churn, hiding inside
   a milestone that already changes both ends.
7. **The derived bounds get a test that asserts they are still derived**
   (§10.2). #72 made the bounds expressions specifically so an ADR amendment
   moves them; nothing said how that property is kept true, and a test on the
   literal would pass while it rotted.
8. **`wipe` requires typed confirmation of the Pi's short id** (§9.4). #75 kept
   the verb and #79 refused to queue it; neither said what stands between a
   present human and the destructive verb when the Pi *is* reachable.

**One thing deliberately left open**: whether `status` should ever be pushed
rather than requested — for a fault the Pi notices while nobody is asking, a
stuck panel being the obvious case. ADR-0012 settles the *current* direction on
a measured byte budget and a Batch's write count, and explicitly does not close
the question of an unsolicited fault notification. It is not fog on this map; it
is a question the implementation may make cheap or expensive, and it should be
re-asked then rather than now.
