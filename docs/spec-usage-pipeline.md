# Spec: the Claude Code usage pipeline

**Status**: settled. This is the implementation brief produced by
[map #13](https://github.com/peterderkoala/zeropi.display/issues/13) and
[#20](https://github.com/peterderkoala/zeropi.display/issues/20). Every
decision below is closed. If you find yourself deciding something while
implementing, it is a gap in this document — see
[§13](#13-what-this-spec-decided-that-no-ticket-had) and raise it rather than
inventing an answer.

**Read first, in this order**: `CONTEXT.md` (binding vocabulary — the terms
below are used in its exact senses) and `docs/adr/0003`–`0010`. This spec does
not restate them; where it appears to disagree with an ADR, the ADR wins and
the disagreement is a bug in this spec.

**Do not read** `handoff/handoff.md` as a source of truth. It is a narrative
log, it is deliberately not authoritative, and parts of it are superseded.

---

## 1. What you are building

Five units of work. Nothing here touches the e-ink panel driver.

| Path | State | What it is |
|---|---|---|
| `desktop/usage.py` | **new** | The reader. Parses the Claude Code JSONL logs, dedups, costs, and maintains the Desktop store. No BLE, no I/O beyond files — importable and testable without a radio. |
| `desktop/gauge.py` | **new** | The Gauge reader. Reads claude-hud's rate-limit snapshot and the live session registry, and builds a Gauge Payload. Also no BLE. |
| `desktop/push.py` | **rewrite** | The transport. Batch loop over Daily Payloads, single Gauge pushes, Ack handling. Keeps its current BLE mechanics (§10). |
| `desktop/zeropi-push.service` + its loop | **new** | The resident `systemd --user` service that owns cadence: the 30 s poll, the 04:00 Batch, the throttles. |
| `pi/receive.py` | **rewrite** | The Pi. New schema, two Payload shapes, the wipe, the redraw floor, the Gauge state machine. |

The Desktop end is **Linux-only** by an existing constraint
([#32](https://github.com/peterderkoala/zeropi.display/issues/32)) — `push.py`
calls a private BlueZ-specific `bleak` API. Do not attempt to widen it.

---

## 2. The data sources on the Desktop

Four paths. Three are read; the fourth is written by us.

| Path | Read/written | Notes |
|---|---|---|
| `~/.claude/projects/<project-key>/**/*.jsonl` | read | The usage logs. Sub-agent logs are at `<project-key>/<session-uuid>/subagents/*.jsonl` — nested, so a recursive glob attributes them to the right project. |
| `~/.local/state/zeropi-display/rate-limits.json` | read | claude-hud's Gauge snapshot. Written atomically (temp+rename), mode 0600. |
| `~/.claude/sessions/<pid>.json` | read | The live session registry. |
| `~/.local/share/zeropi-display/usage-archive.db` | **written** | The Desktop store. Overridable (§4.5). |

### Files you must never read

- **`~/.claude.json`** (`cachedUsageUtilization`). Measured 16.5 h stale and
  **not updated once** across a full active session in which the live Gauge
  moved 21% → 26%; its cached `seven_day` read 2% against a live 18%. It is
  not a fallback. It is not a cross-check. Never open it.
- **`~/.claude/.credentials.json`**. It sits next to the useful files and
  nothing in this pipeline needs it.

### Privacy

The JSONL logs contain prompts and file contents. **Never commit them, excerpt
them, or paste them into an issue.** Test fixtures are synthetic (§11.2).

---

## 3. Vocabulary you will be held to

Defined in `CONTEXT.md`; listed here only so you know which words are load-bearing.

Desktop · Pi · Desktop Id · Payload (**Daily Payload** / **Gauge Payload**) ·
Batch · Ack · Reading · Coverage Start · Usage · Gauge · **Project Key** vs
**Project Label** · Window · **Limit Window** · **Reset Countdown** ·
**Gauge Age** · **Historic View** · Cost Complete · One-liner.

Two pairs cause most of the bugs:

- **Project Key** is the encoded absolute path (`-home-ryzen-git-zeropi-display`).
  It is what is stored, keyed on and put on the wire. **Project Label**
  (`zeropi.display`) is derived for display only and is **never stored**.
- **Window** is the seven calendar days a Batch covers. A **Limit Window** is
  the server-side 5-hour or 7-day rate-limit period. They are never the same
  word.

Never write "client" or "server" for the two ends (`CONTEXT.md` says why).

---

## 4. `desktop/usage.py` — the reader

### 4.1 Selection, dedup key and winner rank

Consider only entries with `type == "assistant"` carrying a `message.usage`
object. No other entry type in the corpus carries usage.

**Key**: `(requestId, message.id)`. Do **not** add `sessionId` — `ccusage`
does, and here it splits 17 legitimate session-resume keys and over-counts by
946,816 tokens.

**Drop unkeyed entries**: skip any usage entry missing `requestId` or
`message.id`. The two in the corpus are `<synthetic>` API-error records with
all-zero usage.

**Include sidechain entries.** `isSidechain` sub-agent entries are ~18% of real
spend and do not double-count (1 overlapping key out of 5,801). They are
counted, not filtered. (This is the *opposite* of §5.2's rule for the Gauge's
context read — different question, different answer.)

**Winner rank.** 4,328 of 5,708 keys appear more than once, because one
assistant turn is written as one JSONL line **per content block**, each
restating the full `message.usage`. Early copies are written as the response
streams and carry a **provisional** snapshot (`output_tokens: 5` where the
final says `114`). Keep-first loses **26.2% of all output tokens** — nearly
invisible in the token total, very visible in cost.

Within each duplicate group keep the single highest-ranking entry, comparing in
this order (see ADR-0004):

1. Prefer `isSidechain` falsy over truthy.
2. Else prefer the higher sum of `input_tokens + output_tokens +
   cache_creation_input_tokens + cache_read_input_tokens`.
3. Else prefer the entry whose `message.usage.speed` is non-null.

Read totals from the top-level `message.usage` fields, **never** from
`message.usage.iterations` — that key purely restates them.

### 4.2 Pricing

USD per million tokens. Verified against all 7 `cost-state` entries in the real
corpus to floating-point exactness (worst delta 3.6e-15 USD).

```python
# docs/research/pricing-table.md (branch research/pricing-table)
PRICING = {
    "claude-opus-5":    {"in": 5.00, "out": 25.00, "w5m": 6.25, "w1h": 10.00, "read": 0.50},
    "claude-sonnet-5":  {"in": 2.00, "out": 10.00, "w5m": 2.50, "w1h":  4.00, "read": 0.20},
    "claude-haiku-4-5": {"in": 1.00, "out":  5.00, "w5m": 1.25, "w1h":  2.00, "read": 0.10},
}
WEB_SEARCH_USD_PER_REQUEST = 0.01     # $10 per 1,000 searches
FREE_MODELS = {"<synthetic>"}         # no API call happened: free, and *known*
```

Rules, each of which the research pins to measured evidence:

1. **Match model ids by prefix.** Logged ids may be date-suffixed
   (`claude-haiku-4-5-20251001`). String equality silently drops them into the
   unknown-model path.
2. **Price the cache-write TTL split separately.** Take
   `message.usage.cache_creation.ephemeral_5m_input_tokens` and
   `.ephemeral_1h_input_tokens`. **Never price
   `cache_creation_input_tokens`** — they sum to it in 11,593/11,593 entries,
   but the halves cost 1.25x and 2x input respectively, and the corpus mix is
   ~45/55. Flat-rating one real session is off by +5.4% / −4.1%.
3. **Do not add `thinking_tokens` to cost.** They are billed as output and are
   already inside `output_tokens` (`thinking <= output` in 11,593/11,593).
   `output_tokens_details` is **absent in 8,616 of 11,593 entries** — default
   it to 0.
4. **Add `server_tool_use.web_search_requests * 0.01`.** Ignore
   `web_fetch_requests` — free. Both counters are currently 0 in this corpus;
   implement it anyway.
5. **Ignore `service_tier`.** No pricing effect.
6. **`<synthetic>` is free and `cost_complete` stays true.** Any other
   unmatched model id: count its tokens, contribute `0.0` cost, and set the
   row's `cost_complete` to **false**. Never hard-fail. There is no pricing API
   to look a model up in — `GET /v1/models` carries no price field of any kind.

```python
def price_for(model):
    """Returns (rates_or_None, known). known=False drives cost_complete."""
    if model in FREE_MODELS:
        return None, True
    for prefix, rates in PRICING.items():
        if model and model.startswith(prefix):
            return rates, True
    return None, False
```

### 4.3 Project identity and label derivation

**The Project Key is the top-level directory name under `~/.claude/projects/`**,
verbatim — e.g. `-home-ryzen-git-zeropi-display`. That is what is stored, what
is aggregated on, and what goes on the wire as `project`.

The directory name is a lossy but **verifiable** encoding of the project root:

```python
def encode(path):
    return re.sub(r"[^a-zA-Z0-9]", "-", path)
```

It is not invertible (`_` and `.` both become `-`) but it is *checkable*: a
candidate `cwd` is the project root iff `encode(cwd) == dirname`. That turns a
guess into a test.

**The Project Label cascade (R1–R4).** Collect every `cwd` seen anywhere under
that directory, including `subagents/`, and apply in order:

- **R1.** If any `cwd` encodes to the directory name → `basename(cwd)`.
- **R2.** Else, for any `cwd` containing `/.claude/worktrees/`, truncate at that
  marker; if the truncation encodes to the directory name → `basename(root)`.
- **R3.** Else, walk each `cwd`'s ancestors; if an ancestor encodes to the
  directory name → that ancestor's name.
- **R4.** Else, strip the directory name's leading `-` and take the segment
  after the last `-`, and **mark the label unverified**. R4 is lossy and
  wrong in real cases (`messagebroker_demo` → `demo`,
  `zeropi.display` → `display`).

⚠ **The Label has exactly one consumer in this milestone: `--dry-run` output.**
It is not stored, not pushed, and not in the Pi's schema — per
[#36](https://github.com/peterderkoala/zeropi.display/issues/36) the display
label is derived at render time, which is the e-ink milestone. Implement the
cascade in `usage.py` and expose it; do not wire it into the store or the wire
format. (See §13 for the consequence this leaves for that later milestone.)

### 4.4 Date bucketing

Bucket by **local date on the Desktop**, from the entry's `timestamp`:

```python
datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()
```

Entries with no `timestamp` are skipped. The `date` string is opaque
everywhere downstream: **the Pi never parses, compares or interprets it**
(ADR-0009).

### 4.5 The Desktop store

The store is the **archive of record** (ADR-0005). Readings are aggregated from
it, never re-derived from the logs.

**Location**, in precedence order:

1. `--store <path>` on `push.py`
2. `$ZEROPI_USAGE_STORE`
3. `~/.local/share/zeropi-display/usage-archive.db`

This is the first configurable path in the codebase; the Pi's `DB_PATH` and the
GATT UUIDs stay hardcoded constants.

**Schema.** One entry table. No separate push-marks table, no separate
ingest-offset table (#28).

```sql
PRAGMA user_version = 1;

CREATE TABLE entries (
    request_id             TEXT    NOT NULL,
    message_id             TEXT    NOT NULL,
    session_id             TEXT,
    project_key            TEXT    NOT NULL,
    local_date             TEXT    NOT NULL,
    model                  TEXT    NOT NULL,
    input_tokens           INTEGER NOT NULL,
    output_tokens          INTEGER NOT NULL,
    cache_write_5m_tokens  INTEGER NOT NULL,
    cache_write_1h_tokens  INTEGER NOT NULL,
    cache_read_tokens      INTEGER NOT NULL,
    web_search_requests    INTEGER NOT NULL DEFAULT 0,
    cost_usd               REAL    NOT NULL,
    cost_complete          INTEGER NOT NULL,
    rank_sidechain         INTEGER NOT NULL,   -- 1 = not sidechain (preferred)
    rank_tokens            INTEGER NOT NULL,
    rank_speed             INTEGER NOT NULL,
    source_file            TEXT    NOT NULL,
    source_end_offset      INTEGER NOT NULL,   -- byte offset of the line's end
    pushed_at              TEXT,               -- NULL = not yet Acked
    PRIMARY KEY (request_id, message_id)
);

CREATE INDEX entries_reading ON entries (local_date, project_key, model);
CREATE INDEX entries_source  ON entries (source_file, source_end_offset);
```

⚠ **The store's version gate is not the Pi's.** The Pi drops and recreates on
a `user_version` mismatch (§8.1) because it is a rebuildable cache. The store
is the **archive of record**: on a mismatch it must **refuse to run and say
so**, never drop. A migration for the store is written when one is needed.

The three `rank_*` columns exist so the winner comparison (§4.1) can be
re-applied **across runs**, not only within one pass: a line re-read on a later
run must not overwrite a stored winner with a loser.

**Ingest.**

- Ingest is **idempotent**: upsert on `(request_id, message_id)`, replacing the
  stored row only if the incoming entry outranks it.
- If a replacement changes any token, cost or `cost_complete` value, set that
  row's `pushed_at = NULL` — the Reading covering it is now stale on the Pi and
  must be resent.
- If the winner is unchanged, **leave `pushed_at` alone.**
- **Resume via a per-session-file high-water mark**: for each JSONL file,
  `SELECT MAX(source_end_offset) FROM entries WHERE source_file = ?` and seek
  past it. A session's JSONL grows across days, so a whole-file mtime skip
  would wrongly skip a partially-ingested file that was later appended to.
- The high-water mark is a **speed optimisation only**. Correctness does not
  depend on it (ingest is idempotent), so a file with no usage entries yet is
  simply re-read; #18 measured a full read of the whole corpus at 0.39 s.

**Absolute rule: there is no `--rebuild-from-logs`.** A corrupt store is
restored from a backup **of the store**. Re-deriving from the logs is not a
free repair path: the logs self-delete on a rolling 30-day sweep
(`cleanupPeriodDays`, default 30) and a day's completeness decays *gradually*
as individual session files age out at different times — so re-reading later
can yield a **smaller** row for a day already stored in full, silently
overwriting good history with worse. That is the exact failure the store exists
to prevent (ADR-0005).

**The store is never pruned** (#30). It is the archive of record, so deleting
from it is the one deletion in this system that is genuinely unrecoverable:
`--resend-all` refills the Pi *from here*, and the logs cannot refill *this*
(the rolling 30-day sweep above). It grows at entry grain — thousands of rows
where the Pi holds tens — so this is the one place a size argument could
eventually be real. It is a deliberate choice, not an omission: if the store
ever needs bounding, that is a decision about the archive, taken with a backup
in hand, and never a `DELETE` bolted onto the ingest path.

### 4.6 Aggregating Readings

A **Reading** is one `(date, Project Key, model)` group. Machine-wide and
per-project totals are derived by `SUM` at read time; there is no other grain.

Per group, from the store only:

| Field | Rule |
|---|---|
| `input_tokens` | sum |
| `output_tokens` | sum |
| `cache_creation_tokens` | `cache_write_5m_tokens + cache_write_1h_tokens`, summed. **A deliberate one-way loss** — cost is computed on the Desktop, and the Pi can never recover the split. |
| `cache_read_tokens` | sum |
| `cost_usd` | sum, rounded to 4 decimal places at the wire boundary |
| `session_count` | count of **distinct** `session_id` in the group |
| `cost_complete` | logical AND over the group |

`thinking_tokens` is **not** aggregated, not pushed and not stored on the Pi —
there is no column for it in the settled DDL and it is already inside
`output_tokens`.

**Model id normalisation.** Store and push the model id **normalised to the
matched pricing-table prefix** when a prefix matches (so
`claude-haiku-4-5-20251001` is stored as `claude-haiku-4-5`), and the raw
logged id otherwise. Without this, a date-suffix change splits one model into
two permanent series in the graph. (See §13 — this spec decided it.)

**The Window.** A Batch covers the **seven calendar days** ending today,
inclusive. Not seven *active* days: on real logs the last seven active days
spanned 27 calendar days, which makes "the last week" a lie.

- Days with zero usage are **skipped**, not sent as zeroes. The read side
  treats an absent date within Coverage Start as zero.
- **Any all-zero row is dropped** before pushing, so a zero-usage pseudo-model
  never burns a BLE round trip.
- **Today's row is legitimately partial** and is corrected by the next Batch:
  new entries for today arrive unmarked, which makes the Reading pending again,
  and the Pi's upsert is last-write-wins. No extra mechanism.

**Which Readings go in a Batch.** A Reading is **pending** iff any entry in its
group has `pushed_at IS NULL`. Pending Readings within the Window form the
Batch. `--resend-all` clears every `pushed_at` first and so pushes the whole
Window.

Ordering: **newest date first**, so an interrupted Batch has delivered the most
useful Readings (ADR-0003).

---

## 5. `desktop/gauge.py` — the Gauge reader

### 5.1 The rate-limit snapshot

Path: `~/.local/state/zeropi-display/rate-limits.json`, written by claude-hud
via `display.externalUsageWritePath` in
`~/.claude/plugins/claude-hud/config.json` (**not** `~/.claude/settings.json`).
Exactly three top-level keys, always all present:

```json
{
  "updated_at": "2026-09-05T08:59:38.303Z",
  "five_hour": { "used_percentage": 26, "resets_at": "2026-09-05T11:40:00.000Z" },
  "seven_day": { "used_percentage": 18, "resets_at": "2026-09-11T13:00:00.000Z" }
}
```

- `updated_at` — ISO-8601 UTC string, never null.
- `used_percentage` — **integer 0–100, or `null`**. Already rounded and
  clamped by the writer; never a float.
- `resets_at` — ISO-8601 UTC string, or `null`.
- **No `model_scoped`, no `balance_label`** — the writer drops them.

⚠ **The field is `used_percentage`, not `utilization`.** `utilization` is the
name in `~/.claude.json`, which you must never read.

⚠ **`updated_at` is a WRITE time, not a FETCH time.** claude-hud rewrites on a
30 s throttle even when the value is unchanged — observed twice (23%→23%,
24%→24%). A fresh `updated_at` does not mean a fresh percentage.

⚠ **The snapshot only advances while an interactive TUI is open.** Headless
`-p` sessions render no status line and write nothing. The Gauge is live only
while someone is actually using Claude Code — a design constraint, not a bug.

Failure modes, and the required behaviour:

| Situation | On disk | `gauge.py` must |
|---|---|---|
| Never configured / no session ever run | file absent | return "no Gauge". **Never zero.** |
| Claude Code closed, or headless only | frozen file | still read it; freshness is judged by `updated_at` at push time (§5.3) |
| stdin carried no `rate_limits` | not written, old file left in place | same — a stale file can persist with no signal |
| Both windows null | not written | same |

The writer's temp files are `.<base>.<pid>.<ms>.<rand>.tmp` in the same
directory. If you ever scan that directory, **ignore dotfiles**.

### 5.2 The active session and the context size

⚠ **This entire subsection exists for one wire field that nothing currently
draws.** [#38](https://github.com/peterderkoala/zeropi.display/issues/38)
dropped the context readout from the display; the maintainer kept the field in
the Gauge Payload so a future readout needs no protocol change. Implement it
exactly, and do not let it grow a display.

Registry: `~/.claude/sessions/<pid>.json`, carrying `sessionId`, `cwd`, `pid`,
`kind`, `status`, `startedAt`, `updatedAt`, `statusUpdatedAt`.

- **Glob narrowly** — `*.json`. `.key` files sit alongside them.
- **Liveness is `/proc/<pid>`**, and only that.
  ⚠ **`updatedAt` is a status-TRANSITION timestamp, not a heartbeat.**
  Measured: `updatedAt == statusUpdatedAt` exactly, and both sat **frozen at
  467 s** while the session was working with `status: "busy"`. Any freshness
  test near 5 minutes calls a busy session dead.
- **Filter `kind == "interactive"`.** A headless session writes no snapshot, so
  treating one as active gives a permanently frozen Gauge.
- **Among the survivors, the active session is the most recent `updatedAt`.**
  That is what the field is good for: choosing between sessions, never deciding
  whether anything is live. Note this only ever discriminates between separate
  terminals — **sub-agents do not register** in this registry.
- No live session → the Gauge's `context` object is `null`. The percentages are
  account-wide and need no session at all.

**Context size** = the active session's **latest non-sidechain
`type:"assistant"` entry**, in `~/.claude/projects/encode(cwd)/<sessionId>.jsonl`:

```
tokens = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
```

Skip `isSidechain` entries: if one were last in the file, the naive rule would
report the *sub-agent's* context. `isSidechain` is false in all 42 local
sessions, so this guard is untested insurance — keep it anyway; it is cheap.

**Percentage** against a hardcoded per-model context-window table, prefix
matched, same pattern as the pricing table:

```python
# docs/research/context-window-table.md (branch research/context-window-table)
CONTEXT_WINDOW = {
    "claude-opus-5":    1_000_000,
    "claude-sonnet-5":  1_000_000,
    "claude-haiku-4-5":   200_000,
}
```

⚠ **Not 200K** for the two models that matter. 1M is the standard window for
Opus 5 and Sonnet 5 — GA, no beta header, no pricing surcharge. Unknown model →
`pct: null`, `tokens` still reported.

### 5.3 Durations, not instants

Computed on the Desktop at push time, because **the Pi has no clock it can
trust** (ADR-0009):

- `five_hour.resets_in_s` = `max(0, round((resets_at - now).total_seconds()))`,
  or `null` when `resets_at` is null. Same for `seven_day`.
- `snapshot_age_s` = `max(0, round((now - updated_at).total_seconds()))`.

Nothing time-shaped crosses the wire as an instant. `resets_at` and
`updated_at` stop on the Desktop.

**Refuse to push a snapshot already `>= 300` s old** — it would arrive already
expired (§8.5). Skip the push; the next change pushes a better value.

---

## 6. The wire format

MTU 517 is negotiated reliably, giving a **514-byte single-write budget**.
Both shapes are comfortably inside it with verbose named keys; no terse or
positional encoding.

### 6.1 Daily Payload

```json
{
  "kind": "daily",
  "desktop_id": "9f2c1ab34d5e6f70",
  "batch_size": 3,
  "batch_index": 0,
  "date": "2026-09-05",
  "project": "-home-ryzen-git-zeropi-display",
  "model": "claude-opus-5",
  "input_tokens": 12345,
  "output_tokens": 6789,
  "cache_creation_tokens": 201775,
  "cache_read_tokens": 8727247,
  "cost_usd": 33.5412,
  "session_count": 4,
  "cost_complete": true
}
```

| Field | Type | Notes |
|---|---|---|
| `kind` | `"daily"` | Self-describing. The Pi branches on this, never on which fields are populated. |
| `desktop_id` | string | §7.1. Present on **both** shapes. |
| `batch_size` | int ≥ 1 | Total Payloads in this Batch. |
| `batch_index` | int, 0-based | Position within the Batch. No separate start/end write. |

⚠ **The Pi consumes `batch_size`/`batch_index` for logging only** in this
milestone. They exist so an incomplete Batch is recognisable without an extra
round trip; the Desktop recognises it from its own Acks. Do not invent Pi-side
batch-completion behaviour — there is no read path to report it through.
| `date` | string | Local date, `YYYY-MM-DD`. **Opaque to the Pi.** |
| `project` | string | The **Project Key**, not the Label. |
| `model` | string | Normalised per §4.6. |
| `cost_usd` | float | Rounded to 4 dp. |
| `session_count` | int | Distinct sessions. |
| `cost_complete` | bool | False if any model in the group was unpriced. |

Measured size: 197–236 bytes for the row fields (worst case 262), plus ~60 for
`kind`/`desktop_id`/`batch_*`. Well inside 514.

### 6.2 Gauge Payload

```json
{
  "kind": "gauge",
  "desktop_id": "9f2c1ab34d5e6f70",
  "snapshot_age_s": 12,
  "five_hour": { "pct": 32, "resets_in_s": 9060 },
  "seven_day": { "pct": 18, "resets_in_s": 486000 },
  "context": { "tokens": 210641, "pct": 21, "model": "claude-opus-5" }
}
```

- `pct` is an integer 0–100 **or `null`**. Null is a real, observed state and
  is rendered distinctly (§9.2) — it is not zero and not stale.
- `resets_in_s` is a non-negative integer or `null`.
- `context` is the whole object or `null` (no live session). Inside it, `pct`
  may be null on an unknown model while `tokens` is still reported.
- **No `resets_at`. No `updated_at`. No `generated_at`.** Instants do not cross
  this wire (ADR-0009). The #26 prototype's payload predates that decision.

Measured ~279 bytes with instants; smaller with countdowns.

### 6.3 Ack

The Pi's reply on the notify characteristic, per write.

```json
{ "status": "ok", "kind": "daily",
  "date": "2026-09-05", "project": "-home-…-display", "model": "claude-opus-5",
  "drawn": false, "wiped": false }
```

```json
{ "status": "ok", "kind": "gauge", "drawn": true, "wiped": false }
```

```json
{ "status": "error", "reason": "missing field(s): model", "kind": "daily",
  "drawn": false, "wiped": false }
```

| Field | Notes |
|---|---|
| `status` | `"ok"` or `"error"`. Persistence failure is an error, not an ok. |
| `kind` | Echoed. Omitted if the Payload was too malformed to have one. |
| `date`/`project`/`model` | **Explicit correlation echo** on a Daily Ack, not a bare sequence number — a log line is then self-describing and the Desktop needs no sequence state. Echoed on an error Ack too, whenever they parsed. |
| `drawn` | `true` = this Payload moved the panel; `false` = coalesced by the 300 s floor (ADR-0008). Present on both shapes. |
| `wiped` | `true` **only** on the Ack for the Payload that triggered a wipe. **Load-bearing, not a nicety** — see §7.2. |
| `reason` | Only on `status: "error"`. |

⚠ **`received_at` is gone**, from the Ack and from the Reading. It was the last
thing forcing a wall clock onto the Pi, and nothing read it at either site
(ADR-0009). Do not reintroduce it.

---

### 6.4 Validation on the Pi

`parse_payload()` rejects, with `status: "error"` and a `reason`, and persists
nothing:

- non-UTF-8 bytes, or JSON that is not an object;
- a missing or unrecognised `kind` (`reason: "unknown kind: ..."`) — the Pi
  branches on `kind` alone and **never** on which fields happen to be
  populated;
- a missing `desktop_id`;
- for `kind: "daily"`, any of `date`, `project`, `model`, `input_tokens`,
  `output_tokens`, `cache_creation_tokens`, `cache_read_tokens`, `cost_usd`,
  `session_count`, `cost_complete`, `batch_size`, `batch_index` missing, or of
  the wrong type;
- for `kind: "gauge"`, `snapshot_age_s`, `five_hour` or `seven_day` missing.
  Inside those objects `pct` and `resets_in_s` must be **present but may be
  null**; `context` may be null or absent entirely.

The wipe check (§8.3) runs **after** validation — a malformed Payload never
destroys Readings.

⚠ **The `oneliner` field is gone from the wire.** Milestone 1's Payload
carried it and the old `readings` table had a column for it; the settled DDL
does not. The One-liner stays a stub and re-enters the protocol when something
generates it.

---

## 7. `desktop/push.py` and the resident service

### 7.1 The Desktop Id

An **app-specific hash of `/etc/machine-id`** — project-salted, so the raw
machine id never crosses the wire. This is the systemd
`sd_id128_get_machine_app_specific` pattern.

```python
APP_ID = "zeropi.display.desktop-id.v1"

def desktop_id():
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            raw = Path(p).read_text().strip()
        except OSError:
            continue
        if raw:
            return hmac.new(bytes.fromhex(raw), APP_ID.encode(), hashlib.sha256).hexdigest()[:16]
    raise RuntimeError("no machine id at /etc/machine-id or /var/lib/dbus/machine-id")
```

Chosen because its change conditions are the only ones that actually mean
"different Desktop": it survives a hostname change, a BLE-dongle swap, deletion
of our state directory and a reinstall of our software, and changes on OS
re-image. **It needs no provisioning code in `install-desktop.sh`.**

Rejected: hostname (renames wipe), adapter MAC (dongle swap wipes), an
installer-generated UUID (state-dir deletion re-introduces the spurious wipe).

### 7.2 Handling `wiped: true`

On **any** Ack with `wiped: true`, of either kind:

1. Clear every `pushed_at` in the store (`UPDATE entries SET pushed_at = NULL`).
2. Finish the in-flight Batch normally.
3. Run **exactly one** further Batch pass in the same invocation, which now
   contains the whole Window. Cap it at one extra pass — never loop.

**Why this is not optional.** Push marks are per-Desktop. Hand a Pi from
Desktop A to B and it works (B has no marks, so B pushes everything). Hand it
**back to A** and the Pi wipes on the id change while A's store still believes
every Reading is pushed — the Pi sits **permanently empty** and A never
resends. Silent on both sides. The flag is what closes that (ADR-0006).

### 7.3 The Batch loop

Per ADR-0003 and #16:

1. Compute the pending Readings (§4.6). **If none, do not connect at all.**
2. Scan for the Pi by service UUID, connect **once**, negotiate the MTU, and
   hold that one connection for the whole loop. Per-row reconnection would be
   dominated by connection setup.
3. For each Reading, newest date first: write the Daily Payload, then **wait
   for its Ack before writing the next**. Sequential, never pipelined —
   stacking writes lands in exactly the fragile BlueZ ATT window that
   `receive.py`'s deferred Ack exists to avoid.
4. Per-row Ack timeout stays **10 s**. There is no overall loop timeout: the
   worst case (~20 rows × 10 s) is already an unambiguous failure signal.
5. On a failed or timed-out row, **continue to the next**. Mark only
   successfully-Acked Readings as pushed — set `pushed_at` on every entry in
   that group. An unmarked Reading is retried by the next Batch with no retry
   logic written for the purpose.
6. Disconnect. Report a summary; exit non-zero if anything failed.

Partial delivery is not an error case: the Pi holds fewer Readings, each
individually complete and already Acked.

**If no Pi is advertising the service** within the 10 s scan: the Batch fails
as a whole, nothing is marked pushed, and it is retried by the next Batch or a
manual run. Exit non-zero. A Gauge push in the same situation is **dropped
silently** (§7.4) — it is the expected steady state whenever the Pi is off.

### 7.4 The Gauge push

A single connect-push-disconnect, one Payload, one Ack. **A failed Gauge push
is dropped silently** — no retry, no queue, no mark. The Gauge is ephemeral, so
there is nothing to recover, and the next change pushes a *better* value than
the one that failed.

### 7.5 The resident service

A `systemd --user` service on the Desktop, holding one single-threaded loop. It
must be a **user** service: the snapshot is 0600 under `~/.local/state`.

- **Poll the snapshot every 30 s.** ⚠ **Poll; do not inotify.** claude-hud
  writes atomically via temp+rename, so a watch on the *file* misses every
  write — it would have to watch the directory. A 30 s poll of a small local
  JSON file is cheaper than getting that right. Not a systemd timer either
  (5-minute granularity cannot express "event-driven") and not a Claude Code
  hook (dies with the session that owns it).
- **Push the Gauge when a displayed value changes**: `five_hour.used_percentage`
  or `seven_day.used_percentage` changes value, **or its null-ness changes**, or
  either `resets_at` moves (a Limit Window rolled). All are integers or
  null-transitions, so the integer is the natural quantum and there is no float
  threshold to pick.
  ⚠ **Never push on a fresh `updated_at` alone.**
  ⚠ The **context percentage does not trigger a push** — see §13.
- **Desktop-side Gauge throttle: 300 s minimum between Gauge pushes.** Measured,
  the 5-hour Gauge moves ~1.1 points per minute under heavy Opus 5 use (27% →
  36% in 8.2 min), so the trigger fires **~5.5x per redraw floor**. Without this
  the link does five times the BLE work for one redraw. If a change arrives
  inside the window, hold a pending flag and push the **then-current** value
  when the window elapses — coalesce, do not drop. The Pi's gate stays the
  guarantee; this is the limiter.
- **Batch at 04:00 local**, plus **on service start if the last successful
  Batch is older than 24 h** (covers a laptop asleep at 04:00).
- The unit file lives in the repo at `desktop/zeropi-push.service` and is
  installed to `~/.config/systemd/user/` by
  [#34](https://github.com/peterderkoala/zeropi.display/issues/34)'s
  `install-desktop.sh`, on map #7. Ship the file; do not write the installer.
- **The two jobs never interleave.** One service, one loop: a Gauge push waits
  for an in-flight Batch to finish.

### 7.6 CLI

`push.py` keeps a manual entry point alongside the service.

| Flag | Behaviour |
|---|---|
| `--dry-run` | Ingest, aggregate, and print the Payloads that *would* be sent — with sizes, the Project Label and its R-rule, the pending/total row counts, and the Gauge state. **No BLE, no store writes to `pushed_at`.** For eyeballing against real logs. |
| `--resend-all` | Clear every `pushed_at`, then Batch the whole Window. |
| `--store <path>` | Override the store location (§4.5). |
| `--gauge-only` / `--batch-only` | Run one job. |

---

## 8. `pi/receive.py` — the Pi

The Pi stays a **dumb receiver**: it does not fetch or compute data. Enforcing
a duty cycle on its own panel is device care, not data work (ADR-0008).

### 8.1 Schema ownership and the version gate

`init_db()` stays the **sole owner** of the schema. `DB_PATH` stays the
hardcoded `/opt/zeropi-display/data.db` — the store's configurable path (§4.5)
is the exception in this codebase, not a new convention. `pi/install.sh` is
untouched and gains no DB awareness.

Make it **self-healing and version-gated**: read `PRAGMA user_version`; on
mismatch, drop `readings` and `meta` and recreate them, then set the marker.
This makes a fresh Pi and the maintainer's already-provisioned Pi take the
*identical* path — both read `user_version = 0` — so there is no migration
script and no second code path.

```sql
CREATE TABLE readings (
    date                    TEXT    NOT NULL,
    project                 TEXT    NOT NULL,
    model                   TEXT    NOT NULL,
    input_tokens            INTEGER NOT NULL,
    output_tokens           INTEGER NOT NULL,
    cache_creation_tokens   INTEGER NOT NULL,
    cache_read_tokens       INTEGER NOT NULL,
    cost_usd                REAL    NOT NULL,
    session_count           INTEGER NOT NULL,
    cost_complete           INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (date, project, model)
);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- meta keys: 'coverage_start', 'desktop_id'
```

`PRIMARY KEY (date, project, model)` doubles as the uniqueness constraint and
the index the graph query needs — `date` leads, so it range-scans. Keep the
table **named `readings`**: only the columns changed, not the concept.

⚠ **`received_at` is not in this DDL.** #17's original DDL had it; ADR-0009
removed it. Arrival order, if ever wanted, is `rowid`.

### 8.2 Upsert and Coverage Start

Both in **one transaction**:

```sql
INSERT INTO readings (...) VALUES (...)
ON CONFLICT(date, project, model) DO UPDATE SET ...;   -- last-write-wins
```

then set `meta['coverage_start'] = MIN(current, incoming date)`, treating an
absent current value as +infinity. **Fully automatic — no Desktop signal.**

Coverage Start is *"the earliest date the current Desktop has pushed"*. It
exists so a date the Pi simply never received reads as **outside coverage**
rather than as zero usage.

Explicitly rejected, do not re-derive it: `SELECT MIN(date) FROM readings`.
That is a fact about what the table currently *holds*, not what was ever
*observed*; the two diverge silently the moment anything is pruned.

### 8.3 The Desktop Id wipe

On **every** Payload, either shape, before anything else:

- `meta['desktop_id']` absent → **adopt**, do not wipe (the `DROP` would be a
  no-op anyway).
- Stored id equals the Payload's → proceed.
- Stored id differs → **drop and recreate `readings`**, delete
  `meta['coverage_start']`, store the new id, and set `wiped: true` on **this
  Payload's Ack only**.

The id lives only in `meta` and on the Payload. The Pi **compares** it; it
never joins on it. That is what keeps `(date, project, model)` and the Ack's
correlation fields untouched (ADR-0006).

### 8.4 The Gauge state machine

A Gauge Payload produces **no Reading** and is **never written to SQLite** —
display-only, held in memory. The daily table already covers the trend use
case; 5-minute-grain history would burn SD write cycles for nothing.

On arrival, hold: the two `pct` values, the two `resets_in_s` values, the
`context` object, and the **arrival mark**.

**Everything time-shaped runs on `time.monotonic()`** — boot-relative, correct
on a Pi that thinks it is 1970, and immune to NTP steps (which also fixes a
quieter bug: a wall-clock design would jerk the display by the size of an NTP
correction at the exact moment the clock became right).

- **Gauge Age** = `snapshot_age_s + (monotonic_now - arrival_mark)`. Seed it
  with the Desktop-computed `snapshot_age_s`; that field exists for exactly
  this and has no other consumer. This is what makes ADR-0010's guarantee —
  *whatever the Gauge frame shows is under 300 s old* — literally true rather
  than approximately.
- **Reset Countdown** = `max(0, resets_in_s - (monotonic_now - arrival_mark))`.
  It **clamps at zero**, and reaching zero does **not** expire the reading — a
  Limit Window genuinely can reset mid-session, and the next push says so.
- **Expiry: Gauge Age ≥ 300 s.** Exactly one redraw floor, so an expired Gauge
  always means at least one genuinely missed push rather than a race.

### 8.5 The redraw floor

**The Pi enforces 300 s as a hard gate.** It is the only side that knows when
the panel last physically moved, and a floor enforced only by the pusher is not
a floor. The Desktop's throttle (§7.5) is the limiter; this is the guarantee.

- **Every update is a full refresh, and every cycle ends in `epd.sleep()`.**
  Do **not** implement a two-speed partial/full scheme — the panel must not be
  left in a high-voltage state, and deep sleep does not retain RAM, which
  destroys the partial-refresh base image. With a 300 s floor there is never a
  burst to amortise a wake over (ADR-0007). #23's N = 5 bound therefore never
  binds.
- The Pi also **redraws on its own (monotonic) clock**, whichever comes first:
  the countdown moves every minute with no usage change at all.
- A redraw arriving inside the floor is **coalesced** — the newest state is
  kept and drawn when the floor next allows. The Ack reports
  `drawn: false` for the coalesced Payload.
- A successful Reading upsert marks the panel dirty; it redraws at the next
  allowed moment **if** no live Gauge is showing.
- **Idle keep-alive: one full refresh every 24 h** even with nothing new.

### 8.6 Rendering is a stub in this milestone

The e-ink driver is out of scope. Implement the state machine above against a
`render(view)` seam that, for now, logs one line naming the frame and its
values. The display milestone replaces that function and nothing else.

### 8.7 Retention: Readings are never pruned

**The Pi never deletes a Reading on size grounds.** There is no age rule, no
row-count cap, no `DELETE` anywhere in `receive.py`. Settled by #30 against
measurement, not taste: at `(date, project, model)` grain the maintainer's
entire machine history is **18 rows** over 41 calendar days — mean 1.8, max 3
rows per *active* day. A pessimistic 5 rows every calendar day is under 200 KB
a year including the primary-key index; twenty years of that is ~4 MB. Growth
is also bounded by the Pi's **uptime**, not by log history, because the rolling
7-calendar-day window (§4.6) means the Pi only ever accumulates days it was
actually pushed.

Three consequences you must not undo:

- **Coverage Start is unaffected**, and §8.2's rejection of
  `SELECT MIN(date) FROM readings` still stands. It is rejected for what it
  *means*, not for what pruning would do to it — a fact about what the table
  holds is not a fact about what was observed, and only the explicit scalar can
  make a never-received date read as *outside coverage* rather than as zero.
- **`wiped` stays exclusive to the Desktop-Id change** (§8.3). It is never set
  for a size- or age-driven reason. The two are different in kind: a hand-off
  wipe invalidates the Desktop's whole model of what the Pi holds, while a
  prune would be the Pi discarding rows the Desktop deliberately stopped
  caring about — signalling one as the other would trigger a `--resend-all`
  that re-pushes exactly the rows just pruned.
- **There is no operator reset command**, and no Payload field asks for one.
  Adding one would put a *remote destructive command* on an unauthenticated
  link (trap 11). Clearing the Pi is `rm /opt/zeropi-display/data.db` over SSH
  plus a restart — already supported by construction, since the version gate
  (§8.1) makes a missing DB and a stale DB take the identical path.

**Revisit signal, for a human, never a code path**: if `data.db` ever passes
~50 MB, or the display milestone's graph query over `readings` is visibly slow,
re-open this. Disk is not the plausible trigger; a query over a large table is.

---

## 9. The display contract

What the panel shows — settled by #38 and rendered as
`docs/research/gauge-mocks/settled-*.png` on `prototype/live-gauge`
(`dde1c58`). **What to display, not how to draw it.**

### 9.1 The Gauge frame (Layout C)

Panel is 250x122, **1-bit monochrome — there is no grey.**

- **Headline row, split by a vertical rule**: the `5H` percentage on the left
  and the `RESETS IN` countdown on the right, both at full size. Co-equal: a
  percentage without a horizon is anxiety, a horizon without a percentage is
  trivia.
- **Second row**: `7D` percentage with its own countdown, smaller.
- **Third row stays white.** Deliberately — not pending a better idea, and
  specifically **not** filled with a historic number. The Gauge frame is *now*;
  the Historic View is *then*, and that distinction is what the design rests on.
- **No footer.** Neither half: freshness can now only ever say "fresh", and the
  model name only existed to explain the context denominator, which is gone.
- **No context readout.** The field is on the wire; nothing draws it.

**Countdown clamps**: `<1m` under a minute; **`RESETS NOW`** at and past zero,
held until fresh data arrives. `0m` is a lie for 59 of its 60 seconds and
`-1m` is nonsense. In the `RESETS NOW` state the cell **drops its `RESETS IN`
label** — it is not resetting *in* anything any more — because that string
overran the panel edge when drawn.

### 9.2 The two non-Gauge states, which are different faults

- **Null `used_percentage`** → its own frame reading **`NO USAGE DATA`** with a
  second line, `waiting for first snapshot`. The null frame **drops the
  vertical divider entirely**: when the snapshot is absent `resets_at` is null
  too, so there is no countdown to divide the row for. (Drawn naively, `NO
  USAGE` ran straight through the divider.)
- **Expired Gauge (Age ≥ 300 s)** → **the Gauge is not drawn at all.** The
  panel falls back to the **Historic View**.

⚠ **Nothing is ever marked stale.** Do not implement dimming, hatching, a
stale banner, or inversion. All four were considered and rejected: the panel
has no grey, and a marked-stale number is one you are asking a viewer not to
trust on a display read in about a second (ADR-0010). Keeping the two states
distinct matters because they point somewhere different — **null** means the
Desktop is talking and has nothing to say (look at claude-hud's config);
**expiry** means the Desktop is gone (look at the link).

### 9.3 The Historic View

The daily trend from stored Readings. **Not a fallback screen and not an error
state** — it is the display's resting picture, and the Gauge temporarily
replaces it. Idle is the *common* case: the snapshot only advances while an
interactive TUI is open, so the panel is idle for most of the day.

**The Pi never computes a date.** It renders the `date` strings it was given,
in the order given, and never asks what today is. If gaps should be visible,
the Desktop sends explicit zero rows.

---

## 10. Known traps — do not rediscover these

1. **`bluetoothd` segfaults on every incoming LE connection** unless started
   with `--noplugin=midi,sap,avrcp`. `pi/install.sh` owns that systemd drop-in.
   `DisablePlugins` in `/etc/bluetooth/main.conf` **does nothing** — it is not
   a valid BlueZ 5.82 key. If pushes look like a coin flip, this is why; the Pi
   Zero 2W's BLE hardware is fine. **Do not design chunking or retry logic
   around a presumed hardware limit.**
2. **`await client._backend._acquire_mtu()` is required** before
   `start_notify`. It is a private, BlueZ-specific `bleak` API, and it is the
   only way past the 23-byte default MTU, because bleak's public `start_notify`
   uses BlueZ's low-MTU path unless the remote characteristic advertises
   `NotifyAcquired` — which bluezero never does. Keep it, keep the comment
   explaining it, and keep the Linux-only consequence.
3. **The Ack must stay deferred.** `receive.py` sends it via
   `async_tools.add_timer_ms(0, ...)` because notifying from inside the write's
   own D-Bus call confuses BlueZ's ATT state machine — the pending write reply
   races the notification. Do not "simplify" it into the write callback.
4. **`push.py`'s `finally: stop_notify(...)` masks the real exception** on a
   failed connect, reporting "Service Discovery has not been performed yet"
   over the top of the actual error
   ([#12](https://github.com/peterderkoala/zeropi.display/issues/12)). Until
   that is fixed, delete the `finally` block by hand when diagnosing a BLE
   failure. Fixing it properly is welcome but is #12's scope, not this spec's.
5. **The LE advertisement takes ~1.5 s to appear** after `receive.py` starts,
   longer on a cold install racing a `bluetoothd` restart. Poll for it; do not
   sample once after a fixed sleep. Read it with
   `busctl get-property org.bluez /org/bluez/hci0 org.bluez.LEAdvertisingManager1 ActiveInstances`,
   **not** by grepping `bluetoothctl show` — bluetoothctl interleaves
   colourised async `[CHG]` lines with its own property block, so a grep can
   return two lines with different values.
6. **A venv on the Pi needs `--system-site-packages`.** `bluezero` needs
   PyGObject and dbus-python, both C extensions; a sealed venv makes pip build
   them from sdists and the build dies at `Dependency "cairo" not found`.
7. **Do not assert that the pipeline's total equals `cost-state`'s
   `totalCostUSD`.** Transcript-derived cost is ~92.8% of it, **always under,
   never over**, because the transcript does not contain every call the
   accumulator saw (`claude-haiku-4-5` never appears as an assistant entry at
   all). That gap is expected and is itself evidence the dedup rule is not
   over-counting. Chasing it is a phantom bug hunt.
8. **The locally-parsed token sum is NOT proportional to Limit Window
   consumption.** Never show it as a proxy for the Gauge; they are different
   quantities. There is no denominator anywhere in local data, and none is
   needed — the server computes the percentage.
9. **Never model a Limit Window's boundary.** The 5-hour window's anchor is not
   locally reconstructible: measured, it matched neither the nearest local
   event, nor first-activity-after-a-gap, nor a clock boundary. Read
   `resets_at`. The 7-day window is a different shape — a fixed account slot on
   an exact clock hour (`13:00Z` observed, against the 5-hour's `23:40Z`).
10. **`ReceiveState.ack_characteristic` is a class attribute**, set by whichever
    Desktop last subscribed. Moot under the one-Desktop-at-a-time shape settled
    by #36 — but do not build anything that assumes concurrent Desktops.
11. **The link is unauthenticated, by decision, not oversight.**
    `flags=["write"]` / `flags=["notify"]` — not `encrypt-write`, not
    `secure-write` — with no pairing, bonding or trusted-device list anywhere,
    and the Desktop finds the Pi by scanning for the service UUID rather than a
    stored address. "Couple the Pi to a different Desktop" is therefore a
    **no-op** today: run the installer on the new machine and it works. Access
    control is a separate effort; bonding in particular risks the BLE
    reliability this project already paid for once.
12. **If you run parallel subagents, give each its own worktree.** Two research
    agents sharing a working tree once collided and one's commit landed on the
    other's branch.

---

## 11. Testing

The repo has **no test tooling at all today**. Adding it is part of this work.

### 11.1 Adding pytest

- `desktop/requirements-dev.txt` containing `pytest`.
- `tests/` at the repo root.
- A root `pytest.ini` with `pythonpath = desktop` so `import usage` works
  without packaging anything.
- Document the two commands in `CLAUDE.md`'s build section, which currently
  says there is no test tooling:

```bash
uv pip install -r desktop/requirements-dev.txt
.venv/bin/python -m pytest
```

Tests must run with **no BLE, no Pi, and no `~/.claude` present**. `usage.py`
and `gauge.py` take their root paths as arguments defaulting to the real ones.

### 11.2 The fixture

⚠ The dev-era capture at `~/.local/share/zeropi-display/usage-archive.db`
(6,201 rows, 1.59 MB, uncommitted) is the **quarry, not the fixture**. Entry
grain is scrubbed by construction — integers, model strings, opaque ids — but
1.59 MB of project names and session UUIDs is not a reviewable test asset.

Build a **small, derived, synthetic** JSONL fixture under `tests/fixtures/`,
with a comment block listing the cases it covers. It must contain at least:

| # | Case | Why |
|---|---|---|
| 1 | A duplicate group whose early copies are provisional (`output_tokens: 5` → `114`) with `speed`/`iterations`/`server_tool_use` null on the losers | The 26.2% output-token trap. Keep-first must **fail** this test. |
| 2 | **Mixed-TTL cache writes** in one group — both `ephemeral_5m_input_tokens` and `ephemeral_1h_input_tokens` non-zero | Without it a flat-rate implementation passes. This is the single most important fixture case after #1. |
| 3 | An entry with `output_tokens_details` **absent** | 8,616 of 11,593 real entries lack it; it must default to 0. |
| 4 | An entry with `thinking_tokens` present and non-zero | Must **not** be added to cost. |
| 5 | A date-suffixed model id (`claude-haiku-4-5-20251001`) | Prefix match, and §4.6 normalisation. |
| 6 | An unknown model id | Tokens counted, cost 0, `cost_complete: false`, no exception. |
| 7 | A `<synthetic>` all-zero entry, and one entry missing `requestId` | Dropped rows; `cost_complete` stays true for `<synthetic>`. |
| 8 | `isSidechain: true` entries | **Counted** by the reader (§4.1), **skipped** by the context read (§5.2). One fixture, two opposite assertions. |
| 9 | Two sessions on the same `(date, project, model)` | `session_count` distinctness. |
| 10 | Entries either side of local midnight, in a non-UTC zone | Local-date bucketing. |
| 11 | A day with only all-zero rows | The row is dropped, not pushed as zeroes. |
| 12 | A project directory whose only `cwd`s are worktree paths | R2 of the label cascade. |
| 13 | A session file **appended to** between two ingest runs | The high-water mark resumes rather than skipping. |
| 14 | Re-ingesting a line whose stored winner already outranks it | Must not overwrite, and must not clear `pushed_at`. |

### 11.3 What the tests assert

- **Pricing, as a pure unit test**: given `cost-state`'s own `modelUsage`
  numbers, does the pricing function return `costUSD`? That is the valid
  oracle. An end-to-end equality against `totalCostUSD` is **not** (trap 7).
- **Dedup**: keep-first and keep-winner produce measurably different output
  totals on fixture case 1.
- **Wire format**: every Payload serialises under **514 bytes**, including a
  deliberately maximal row (long Project Key, 9-digit token counts,
  `cost_complete: false`).
- **Store**: ingest is idempotent; a better winner clears `pushed_at`; an equal
  winner does not; a Reading is pending iff any of its entries is unmarked.
- **Gauge**: null `used_percentage` survives as `null` and never becomes 0; an
  absent snapshot file yields "no Gauge" and never zero; countdowns clamp at 0;
  a snapshot ≥ 300 s old is refused.
- **Pi**: the version gate drops and recreates from `user_version = 0`; the
  upsert is last-write-wins; `coverage_start` only ever moves backwards; a
  changed `desktop_id` wipes and sets `wiped` on exactly one Ack; the floor
  coalesces and reports `drawn: false`.

`pi/receive.py`'s DB functions must be importable without bluezero for these
to run — take the import of the BLE stack out of module scope, or guard it.

### 11.4 `--dry-run` is the other half

Unit tests run against synthetic data by construction, so `--dry-run` against
the real logs is the only thing that exercises the real corpus. It prints the
Payloads, their sizes, the label and R-rule per project, pending vs total row
counts, and the current Gauge state. Run it before and after any change to the
reader.

---

## 12. Out of scope — do not drift into these

- **The One-liner.** Stays the hardcoded stub. Generating it is its own effort.
- **The e-ink driver.** Writing pixels to the panel. §9 says *what* to display;
  §8.6 gives you the seam.
- **Weather and calendar Payload fields.**
- **The context-size readout on the display.** The field stays on the wire
  (§5.2); nothing draws it.
- **Retention/pruning of Readings on the Pi** — **no longer open**. Settled by
  [#30](https://github.com/peterderkoala/zeropi.display/issues/30): there is no
  pruning, on either end. See §8.7 (the Pi) and §4.5 (the store).
- **Link authentication / BLE bonding** (trap 11).
- **A hardware RTC.** Nothing in the data path reads a wall clock any more.
- **Reading Readings back for the graph** — query shape and aggregation window
  belong to the display milestone.
- **Fixing [#12](https://github.com/peterderkoala/zeropi.display/issues/12)**
  (the masked exception) and
  [#32](https://github.com/peterderkoala/zeropi.display/issues/32) (Linux-only
  `_acquire_mtu`). Known, ticketed, not this work.

---

## 13. What this spec decided that no ticket had

The gap check required by #20: read as if you had no context from map #13, and
list every place a decision would still have to be invented. Twelve were found
— six judgment calls and six mechanical fills. All are closed above; they are
listed here so they are visible as *new* decisions rather than inherited ones.

### The six judgment calls

1. **Push marks live on entries, not on Readings** (§4.5, §4.6). #28 forbade a
   separate push-marks table, but the store is entry-grain while the push unit
   is a Reading. Resolved by marking every entry in an Acked group, and
   defining a Reading as pending iff any of its entries is unmarked. This is
   better than a Reading-level flag: a late-arriving entry for an
   already-pushed day makes that Reading pending again automatically, and the
   Pi's last-write-wins upsert makes the resend idempotent.

2. **The high-water mark is derived, not stored** (§4.5). #28 forbade a
   separate ingest-offset table, so the resume point is
   `MAX(source_end_offset)` per source file. Correctness does not depend on it
   — ingest is idempotent — so a file with no usage entries yet is simply
   re-read.

3. **The Window includes today** (§4.6). #25 put the Batch at 04:00 because
   "days are only complete after midnight", which left today's partial row
   undecided. Today is included; it is legitimately partial and self-corrects
   on the next Batch. The alternative (ending the Window yesterday) would
   leave the most interesting day off the graph for up to 24 hours.

4. **Model ids are normalised to the matched pricing-table prefix** (§4.6).
   Nothing settled the exact form of the `model` string, and the grain is
   `(date, project, model)`. Left raw, a date-suffix change splits one model
   into two permanent series. The alternative — store raw — is defensible if
   you want the exact logged id preserved; `cost_complete` already flags the
   unknown case either way.

5. **The context percentage no longer triggers a Gauge push** (§7.5). #25 made
   it one of three trigger values while the context readout was on the display;
   #38 then removed the readout. Triggering a BLE push and a panel redraw on a
   value nothing draws is pure cost. It still rides along on any push a
   displayed value triggers.

6. **Gauge Age is seeded with `snapshot_age_s`** (§8.4). `CONTEXT.md` defines
   Gauge Age as monotonic seconds since the Payload arrived, which would let a
   snapshot that was already stale at push time survive on the panel for a
   further 300 s. Seeding makes ADR-0010's guarantee — nothing on the panel is
   ever untrustworthy — literally true, and gives `snapshot_age_s` its only
   consumer. Belt and braces: the Desktop refuses to push a snapshot ≥ 300 s
   old.

### The six mechanical fills

Smaller, but each was a place an implementer would otherwise have had to guess:

- **Payload validation and the unknown-`kind` rule** (§6.4), including that the
  wipe check runs *after* validation so a malformed Payload can never destroy
  Readings.
- **`oneliner` leaves the wire** (§6.4). Milestone 1 carried it and the settled
  DDL has no column for it.
- **The store refuses to run on a schema mismatch; it never drops** (§4.5). The
  opposite of the Pi's gate, and the asymmetry is the whole point — one is an
  archive, one is a cache.
- **`batch_size`/`batch_index` are Pi-side logging only** (§6.1). There is no
  read path to report batch completeness through.
- **No Pi advertising → the Batch fails whole and is retried; a Gauge push is
  dropped** (§7.3).
- **`DB_PATH` stays hardcoded** (§8.1). The store's configurable path is the
  exception, not a new convention.

### One gap deliberately left open

**The Historic View has no way to get a Project Label.** The R1–R4 cascade
needs the log corpus, which only the Desktop has; the Pi holds only the Project
Key. So at render time the Pi can only do R4, which the research shows is wrong
in real cases (`zeropi.display` → `display`). Closing it means either sending
the Label on the Daily Payload (a schema and protocol change) or accepting R4.

This is **not a gap in this spec**: e-ink rendering is out of scope, and the
decision sits past this map's destination. It is recorded on map #13's
**Out of scope** section so the display milestone starts from it rather than
rediscovering it.
