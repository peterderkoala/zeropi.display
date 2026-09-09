"""#56 bench: send a real Batch and time every Ack, against a receiver whose
render() blocks for a full 4.35s panel cycle inline in the write handler."""
import asyncio, json, sys, time
sys.path.insert(0, "desktop")
import push, usage

async def main():
    store = usage.open_store(usage.resolve_store_path(None))
    try:
        readings = usage.aggregate_readings(store)
    finally:
        store.close()
    did = push.desktop_id()
    batch = push.build_daily_batch(readings[:6], did)
    async def run(send_one):
        t0 = time.monotonic()
        for i, p in enumerate(batch, 1):
            t = time.monotonic()
            try:
                ack = await send_one(p)
            except Exception as exc:
                print(f"row {i}: EXCEPTION after {time.monotonic()-t:.2f}s: {exc!r}")
                continue
            dt = time.monotonic() - t
            status = "TIMEOUT (no Ack in 10s)" if ack is None else f"{ack.get('status')} drawn={ack.get('drawn')}"
            print(f"row {i}: {dt:6.2f}s  {status}")
        print(f"batch wall: {time.monotonic()-t0:.2f}s")
    await push._with_ble_connection(run)

asyncio.run(main())
