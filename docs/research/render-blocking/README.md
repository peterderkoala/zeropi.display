# Bench: what a panel refresh does to the BLE event loop (#56)

Throwaway measurements taken on the dev Pi on 2026-09-09 while resolving
[#56](https://github.com/peterderkoala/zeropi.display/issues/56). Kept as the
primary source behind that ticket's decision. **Not a harness** — nothing here
is wired into the test suite, and the receivers were `sed`-derived copies of
`pi/receive.py`, run from a scratch directory with the real service stopped.

## Method

Three receivers, each a copy of `pi/receive.py` with `DB_PATH` pointed at a
scratch DB and only `render()` replaced:

| Variant | `render()` does |
|---|---|
| inline | `time.sleep(4.35)` — a measured full panel cycle, in the write handler |
| worker | puts the view on a queue for one daemon thread, returns immediately |
| stuck | `time.sleep(30.0)` — a panel that never releases BUSY |

Driven by `batch_timing_probe.py` (in this directory) from the Desktop: a real
Batch of six Daily Payloads over one connection, timing each write-to-Ack.

**4.35 s is the real cycle cost**, not the 2.29 s usually quoted:
`init()` 0.05 + `display()` 2.29 + `sleep()` 2.00, the last being the driver's
own fixed `delay_ms(2000)` (numbers from `docs/eink-driver-verification.md`).

## Results

| Variant | row 1 | rows 2-6 | batch wall |
|---|---|---|---|
| inline | 4.50 s, `ok drawn=true` | 0.13 s each | **5.18 s** |
| worker | 0.18 s, `ok drawn=true` | 0.13 s each | **0.86 s** |
| stuck | **write raised at 5.09 s** | raised at 5.08 s | 10.17 s |

## What it means

1. **There is a ~5 s ceiling and it is not ours.** The stuck variant did not
   trip the Desktop's 10 s per-row Ack timeout — the *write* failed first, with
   `BleakGATTProtocolError(UNLIKELY_ERROR, 'GATT Protocol Error: Unlikely
   Error')`, i.e. BlueZ cut the transaction at about five seconds. Inline
   rendering therefore runs with **~0.5 s of margin** against a limit enforced
   below us, and e-ink refresh time varies with temperature.
2. **That error is one this project has already lost a session to.** It is
   verbatim the signature `docs/e2e-verification.md` chased before finding the
   `bluetoothd` segfault. An overrunning inline refresh would be
   indistinguishable from it in the logs.
3. **An overrun disagrees silently.** In the stuck run the Pi had already
   persisted the Reading before the write failed, so the Desktop does not mark
   it pushed and resends forever while the Pi holds it. Both sides behave
   correctly and the data diverges.
4. **Threading works, and this was verified rather than assumed**: rows 2-6
   were served *while the worker was mid-refresh*. Every wait in the driver is
   a `time.sleep` (`ReadBusy` polls at 10 ms, `sleep()` is one 2 s sleep), so
   the GIL is released for essentially the whole cycle.

The Pi was restored to the packaged service afterwards and re-verified: 10
Readings intact, `coverage_start` unchanged, a real Gauge round trip OK.
