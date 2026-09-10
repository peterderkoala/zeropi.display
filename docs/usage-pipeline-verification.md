# Usage-pipeline E2E verification (2026-09-09)

Hardware verification of the pipeline specified in
`docs/spec-usage-pipeline.md` — ticket
[#48](https://github.com/peterderkoala/zeropi.display/issues/48), the last
child of [map #41](https://github.com/peterderkoala/zeropi.display/issues/41).
Everything below ran against the real dev Pi and the maintainer's real Claude
Code logs; nothing here is a unit test or a fixture.

**Result: the pipeline works end to end. Two defects were found and fixed
during the run** (`31bb8e7`, `94665e3`), both the same one: under systemd
neither end's log reached its journal at all.

## Environment

| | |
|---|---|
| Pi | `192.168.4.108`, Debian 13 (trixie), Python 3.13.5, aarch64, BlueZ `5.82-1.1+rpt2` |
| Pi software | `/opt/zeropi-display/receive.py` at `31bb8e7`, provisioned by the documented curl one-liner |
| Desktop | Pop!_OS 24.04, Python 3.12.3, `bleak` 3.0.2 in the repo's `.venv` |
| Store | `~/.local/share/zeropi-display/usage-archive.db`, 8,016 entries |
| Suite | `181 passed` before the run, unchanged by it |

## 0. Getting the Pi onto the rewritten receiver

The dev Pi was still running the **milestone-1** `receive.py` (167 lines,
`user_version = 0`, 18 rows on the old schema) — #46 had already flagged this.
Re-provisioned through the documented path, not by hand:

```bash
curl -fsSL https://raw.githubusercontent.com/peterderkoala/zeropi.display/dev/install.sh | bash -s -- pi
```

⚠ **The bootstrap resolves `dev` → sha through the unauthenticated GitHub
API, which is rate-limited to 60 requests/hour per egress IP.** The Pi's IP
was already exhausted by something else on the network, and the install
failed at its first step with a bare `curl: (22) ... 403`. It is not
diagnosable from that message. If provisioning fails on a fresh Pi, check
this before suspecting the script — the reset is at most an hour away, and
`curl -i https://api.github.com/rate_limit` from the Pi says when.

**§8.1's version gate did exactly what it is for**: on first start the new
`receive.py` read `user_version = 0`, dropped the 18-row milestone-1 table,
recreated both tables on the new DDL and stamped `user_version = 1`. No
migration, no manual step. A second provisioning run later (to deploy the
first fix) left the by-then-populated DB untouched — same code path, no
spurious wipe.

## 1. `--dry-run` against the real corpus (§11.4)

```
Pending 10 / 10 Readings in the Window.
  2026-09-09 -home-ryzen-git-zeropi-display claude-opus-5 (348 bytes) label=zeropi.display (R1, verified=True)
  ...
  2026-09-06 -home-ryzen-git-zeropi-display--claude-worktrees-eink-driver-setup claude-opus-5 (390 bytes) label=eink-driver-setup (R1, verified=True)
Gauge state:
  {'snapshot_age_s': 211, 'five_hour': {'pct': 2, 'resets_in_s': 17362},
   'seven_day': {'pct': 52, 'resets_in_s': 188362},
   'context': {'tokens': 84008, 'pct': 8, 'model': 'claude-opus-5'}} (236 bytes)
```

- Ten Daily Payloads, **347–390 bytes**, against the 514-byte budget. The
  largest is the worktree project, whose Project Key is the longest string on
  the wire.
- Labels resolve by **R1** for both the main project and its worktree.
- A real, non-null Gauge with a live context read.

## 2. A real Batch round trip (§7.3)

`push.py --batch-only`, cold: **10 sent, 0 failed**, exit 0, MTU negotiated to
**517**, 10.2 s wall including the scan. The ten sequential write-and-wait-for-
Ack round trips themselves took **~2 s** (Pi journal, 10:52:38 → 10:52:40).

On the Pi afterwards:

```
user_version: 1
meta: {'desktop_id': 'a6805332099d91a1', 'coverage_start': '2026-09-04'}
readings: 10
```

- `desktop_id` **adopted, not wiped** — the absent-id case of §8.3.
- `coverage_start` derived automatically as the earliest date pushed (§8.2),
  with no Desktop signal.
- A second `--batch-only` with nothing pending **does not connect at all**
  (§7.3 step 1).

Ack correlation (§6.3), read off the wire from a single-row probe:

```
daily ack : {"status": "ok", "kind": "daily", "date": "2026-09-09",
             "project": "-home-ryzen-git-zeropi-display",
             "model": "claude-opus-5", "drawn": false, "wiped": false}
```

`drawn: false` is correct here and not a failure: a live Gauge was showing, so
the Reading marked the panel dirty rather than drawing over it (§8.5).

## 3. The redraw floor coalesces (§8.5)

Two Gauge Payloads over **one** connection, 0.08 s apart:

```
gauge #1: ack={"status": "ok", "kind": "gauge", "drawn": true,  "wiped": false}
gauge #2: ack={"status": "ok", "kind": "gauge", "drawn": false, "wiped": false}
```

and exactly **one** `render:` line on the Pi for the two of them:

```
render: {'five_hour': {'pct': 11, 'resets_in_s': 16777},
         'seven_day': {'pct': 53, 'resets_in_s': 187777},
         'context': {'tokens': 141011, 'pct': 14, 'model': 'claude-opus-5'},
         'gauge_age_s': 8.000036561999877}
```

The floor is a hard gate on the Pi, the second Payload was coalesced, and its
Ack said so. Note `gauge_age_s` starts at **8.0**, not 0 — seeded with the
Desktop's `snapshot_age_s` exactly as §8.4 and §13 require, which is what
makes ADR-0010's "nothing on the panel is over 300 s old" literally true.

## 4. Payload validation (§6.4)

Four bad Payloads, one connection, nothing persisted:

```
  not an object: {"status": "error", "reason": "expected a JSON object, got str"}
   unknown kind: {"status": "error", "reason": "unknown kind: 'weather'"}
 missing fields: {"status": "error", "kind": "daily", "reason": "missing field(s): project, model, input_tokens, ..."}
     wrong type: {"status": "error", "kind": "daily", "reason": "wrong type for field(s): input_tokens"}
```

## 5. The Desktop-Id wipe and the hand-back (§8.3, §7.2, ADR-0006)

The failure ADR-0006 exists for is *handing the Pi back to a Desktop that
still believes everything is pushed*. Simulated over the wire rather than by
editing the Pi's DB — one Gauge Payload carrying a **different** Desktop Id,
as a second Desktop would send:

```
desktop B gauge ack: {"status": "ok", "kind": "gauge", "drawn": false, "wiped": true}
Pi after: meta: {'desktop_id': 'ffffffffdeadbeef'}   readings: 0
```

The Pi dropped and recreated `readings`, deleted `coverage_start`, stored the
new id, and set `wiped: true` on **that Ack only**.

Then the real Desktop pushed again — `push.py --gauge-only`, with **zero**
pending Readings in its store:

```
Gauge Ack reported wiped=true — clearing pushed marks and running one Batch pass.
Batch complete: 10 sent, 0 failed, wiped=False
Gauge push ok.                                          (exit 0, 21.4 s)

Pi after: meta: {'desktop_id': 'a6805332099d91a1', 'coverage_start': '2026-09-04'}
          readings: 10
```

This is the path a `/code-review` pass caught missing from #46's first draft —
`wiped` handled on the Daily Ack but not the Gauge one. **It is now confirmed
working on the Gauge Ack specifically, on real hardware**: marks cleared, one
extra Batch pass (never a loop), Pi refilled, Coverage Start re-derived.

## 6. The resident service, unattended (§7.5)

Installed as a `systemd --user` service the way #47's closing report
describes — the shipped `desktop/zeropi-push.service` with `ExecStart`
rewritten for the in-place repo layout — then left to run on its own.

- **Startup Batch catch-up fired 8 s after start**: with push marks cleared
  first, it scanned, connected and pushed all **10 sent, 0 failed** with no
  operator involvement.
- **Incremental ingest is real**: restarted a minute later, the same catch-up
  found exactly **1** Reading pending — today's row, changed by the session
  running in the meantime — and pushed only that.
- **Its Gauge trigger fired on its own** at 10:56:20, about a minute in, when
  the 5-hour percentage moved 11 → 14 between two 30 s polls. Push-on-change,
  not push-on-poll.
- The Pi coalesced that Gauge (44 s after its previous draw), as it should.

## 7. The Pi's own clock, and the expiry fallback (§8.5, ADR-0010)

With the service stopped and nothing pushing at all, the Pi kept the display
state moving by itself:

```
10:50:23  render: ... gauge_age_s: 8.0        <- Gauge Payload arrived
10:55:36  render: ... gauge_age_s: 48.8       <- Pi's own clock, one floor later
```

The second frame is the countdown animating on the Pi's own monotonic clock
with no Payload behind it — §8.5's "the Pi also redraws on its own clock,
whichever comes first".

## What was wrong: neither service's log reached its journal

Both defects found by this run are the same one, and neither is visible from a
terminal — they appear only once the code runs under systemd.

**`receive.py` and `push.py`/`service.py` log with `print()`.** Under systemd
stdout is a journal socket, not a tty, so CPython **block-buffers** it: nothing
is written until 4 KB has accumulated, and what is buffered is lost outright
when systemd `SIGTERM`s the process on the next restart. On this workload
4 KB is hours of output.

The symptom is total silence, on both ends:

- A full Batch of **10 Readings** produced **zero** lines in the Pi's journal —
  no `Reading upserted`, no malformed-Payload rejections, and no `render:`
  line, which is the one artifact the display seam (§8.6) is *defined* to
  produce. A milestone whose deliverable is a log line was emitting none.
- On the Desktop, `service.py` logs only exceptions itself; every informative
  line on a normal run is a `print()` in `push.py`. The unit file's own header
  tells you to watch it with `journalctl --user -u zeropi-push -f`, which
  showed nothing at all through a full 10-Reading startup Batch.

Fixed with `Environment=PYTHONUNBUFFERED=1` in both unit files — `31bb8e7`
(`pi/zeropi-display.service`) and `94665e3` (`desktop/zeropi-push.service`).
The Pi's fix was deployed through the same curl one-liner and verified: the
journal came alive immediately (`Advertisement registered`, then every upsert
and render line above). Everything in sections 2–7 was captured *after* the
fix; the first Batch, before it, is the evidence of the defect.

**This is a general hazard for anything else this repo runs under systemd**:
keep the logging in `print()` where the spec put it, and set
`PYTHONUNBUFFERED=1` in the unit.

Then the Gauge expired and the panel fell back on its own (ADR-0010):

```
11:00:36  render: ... gauge_age_s: 268.5     <- last live Gauge frame
11:05:37  render: {'historic': True}         <- expired; Historic View
```

Nothing pushed in between; the Pi reached that state unaided, on its own
monotonic clock.

### ⚠ One spec-level discrepancy: the drawn frame outlives the 300 s bound

ADR-0010 states as a consequence that **"whatever the Gauge frame shows is
under 300 s old by construction"**, and retires the freshness footer on that
basis. Measured on hardware, that holds at *draw* time but not for the
*duration the frame is displayed*, because the fallback is itself gated by the
300 s redraw floor (which the ADR says, one paragraph later).

Above: a frame drawn at 268 s of Gauge Age expired at ~11:01:08, but the panel
could not be redrawn until the floor elapsed at 11:05:36 — so it kept showing
that frame until it was **~570 s old**. The worst case is a frame drawn at
299 s, replaced ~300 s later: **just under 600 s**, twice the stated bound.

`receive.py` is not wrong — it does exactly what §8.4, §8.5 and ADR-0010
specify, and the two 300 s constants are what produce this. The ADR's
consequence sentence is the thing that overstates. Left as found rather than
"fixed": narrowing it means changing the floor, the expiry, or the claim, and
that is a design decision, not an implementation one. Flagged on
[#48](https://github.com/peterderkoala/zeropi.display/issues/48) and back to
the spec map [#13](https://github.com/peterderkoala/zeropi.display/issues/13).

## What this does *not* verify

- **The glass.** Rendering is still the stub seam of §8.6 — every `render:`
  line above is a log line, not a pixel. Nobody looked at the panel; the
  e-ink driver is a separate milestone.
- **04:00.** The Batch's scheduled trigger was not waited out; only the
  startup catch-up path was exercised. The 04:00 crossing is unit-tested.
- **The 24 h idle keep-alive**, for the same reason.
- **A standalone Desktop.** The service ran from the maintainer's repo clone
  with a rewritten `ExecStart`. `install-desktop.sh`'s standalone mode still
  deploys only `push.py` — not `gauge.py`, `usage.py` or `service.py` — so the
  shipped unit file's own `ExecStart` still cannot work there. Flagged by #47,
  still open, belongs to #34.
- **Sustained running.** The longest continuous service run here was minutes,
  not days.

## Reproducing this

The Desktop half runs from the repo clone; the Pi half is provisioned by the
documented one-liner and needs nothing else.

```bash
# Pi (run on the Pi itself; needs a tty for sudo, so ssh -t over ssh)
curl -fsSL https://raw.githubusercontent.com/peterderkoala/zeropi.display/dev/install.sh | bash -s -- pi

# Desktop
uv venv .venv && uv pip install -r desktop/requirements.txt
.venv/bin/python desktop/push.py --dry-run      # no BLE, no store writes
.venv/bin/python desktop/push.py --batch-only   # a real Batch
.venv/bin/python desktop/push.py --gauge-only   # a real Gauge push

# watch both ends
ssh pi@<pi> 'journalctl -u zeropi-display -f'
journalctl --user -u zeropi-push.service -f
```

The resident service was run for this verification as a `systemd --user`
unit installed by hand — `desktop/zeropi-push.service` copied to
`~/.config/systemd/user/` with `ExecStart` rewritten to
`<repo>/.venv/bin/python3 <repo>/desktop/service.py`, since #34 does not yet
install it. **That hand-installed copy was removed again afterwards**, so the
Desktop is back to how it started; `.venv/bin/python desktop/service.py` runs
the same loop in the foreground.

The four probe scripts used here (two Gauge Payloads on one connection, a
single Daily Payload, four malformed Payloads, and a foreign Desktop Id) are
not committed: each is a dozen lines calling `push.py`'s own seams
(`_with_ble_connection`, `build_gauge_wire_payload`, `build_daily_batch`), and
the point of reading the raw Acks was to check the Pi, not to keep a harness.
