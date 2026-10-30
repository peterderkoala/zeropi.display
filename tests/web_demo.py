"""The web UI against fake BLE, for checking the pages by hand (web-ui §12.4).

    .venv/bin/python tests/web_demo.py [scenario] [--port 18737] [--delay 1.5]

Runs the real `zeropi-web` app, with real operations, a real Verdict and
real stores in a temporary directory. Only the radio and `systemctl` are
faked: `operations.send_command`, `push.run_batch_pass`, `push.find_pi` and
`push.scan_for_pis`. Every step sleeps `--delay` seconds, so a running
Operation stays visible for long enough to look at.

`docs/web-ui-page-checks.md` lists the §10.7 states and the scenario that
shows each one. Not a test module (pytest collects `test_*.py` only), and
never deployed.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop"))

import config  # noqa: E402
import operations  # noqa: E402
import push  # noqa: E402
import web  # noqa: E402

PI = SimpleNamespace(name="zeropi-display", address="B8:27:EB:7C:97:0F")
OTHER_PI = SimpleNamespace(name="zeropi-display", address="B8:27:EB:41:0C:22")

# scenario -> (paired, zeropi-push's unit state, how the radio answers)
SCENARIOS = {
    "working": (True, "running", "ok"),
    "not_working": (True, "running", "panel_failed"),
    "absent": (True, "running", "absent"),
    "busy": (True, "running", "busy"),
    "stopped": (True, "stopped", "ok"),
    "failed": (True, "failed", "ok"),
    "not_installed": (True, "missing", "ok"),
    "wipe_partial": (True, "running", "repush_busy"),
    "not_paired": (False, "stopped", "ok"),  # Setup with one Pi
    "setup_none": (False, "stopped", "absent"),
    "setup_two": (False, "stopped", "two_pis"),
    "setup_missing": (False, "missing", "ok"),  # pairs, then zeropi-push isn't installed
}


def install_fakes(radio: str, delay: float) -> None:
    async def connect(progress) -> None:
        progress("scanning", f"Scanning for the Pi (up to {push.SCAN_TIMEOUT_SECONDS:.0f}s)…")
        await asyncio.sleep(delay)
        if radio == "absent":
            raise RuntimeError(f"no Pi advertising {push.SERVICE_UUID} within {push.SCAN_TIMEOUT_SECONDS}s")
        if radio == "busy":
            raise push.BleLinkBusy("another zeropi-display process held the BLE lock for 15s")
        progress("found", f"Found {PI.name} ({PI.address})")
        progress("connected", f"Connected to {PI.address}")
        await asyncio.sleep(delay)

    async def send_command(verb, did, address, cfg, *, round_trips=None, before_send=None, progress=push.print_progress, **kw):
        await connect(progress)
        if before_send is not None:
            before_send()
        if round_trips is not None:
            round_trips.append(1.2)
        ack = {"status": "ok", "kind": "command", "verb": verb, "drawn": False, "wiped": verb == "wipe"}
        if verb == "redraw":
            ack["floor_remaining_s"] = 143
        if verb == "status":
            ack.update(
                frame="historic",
                since_redraw_s=143,
                panel="failed" if radio == "panel_failed" else "ok",
                readings=0,
                coverage_start=None,
                uptime_s=110_000,
                schema_version=1,
            )
        return ack

    async def run_batch_pass(*a, progress=push.print_progress, **kw):
        if radio == "repush_busy":
            raise push.BleLinkBusy("another zeropi-display process held the BLE lock for 15s")
        await connect(progress)
        return push.BatchResult(sent=3, settings_ok=True)

    async def find_pi(*a, address=None, **kw):
        await asyncio.sleep(delay)
        if radio == "absent":
            raise RuntimeError("no Pi advertising within 10.0s")
        return OTHER_PI if address == OTHER_PI.address else PI

    async def scan_for_pis(*a, **kw):
        await asyncio.sleep(delay)
        if radio == "absent":
            return []
        found = [(PI, SimpleNamespace(local_name=PI.name, rssi=-61))]
        if radio == "two_pis":
            found.append((OTHER_PI, SimpleNamespace(local_name=OTHER_PI.name, rssi=-78)))
        return found

    operations.send_command = send_command
    push.run_batch_pass = run_batch_pass
    push.find_pi = find_pi
    push.scan_for_pis = scan_for_pis
    push.desktop_id = lambda *a, **kw: "a6805332099d91a1"


def fake_systemctl(unit: dict, started: list):
    def run(argv, **kw):
        if "show" in argv:
            load = "not-found" if unit["state"] == "missing" else "loaded"
            return subprocess.CompletedProcess(argv, 0, stdout=f"{load}\n", stderr="")
        started[0] = time.monotonic()  # a restart picks the pending changes up
        unit["state"] = "running"  # enable --now / restart
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", nargs="?", default="working", choices=SCENARIOS)
    parser.add_argument("--port", type=int, default=18737)
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds each fake BLE step takes.")
    args = parser.parse_args()

    paired, unit, radio = SCENARIOS[args.scenario]
    install_fakes(radio, args.delay)

    tmp = Path(tempfile.mkdtemp(prefix="zeropi-web-demo-"))
    store = tmp / "usage.db"
    cfg_path = tmp / "config.db"
    conn = config.open_config_store(cfg_path)
    try:
        config.write_config_value(conn, "paths.projects_root", str(tmp / "no-projects"))
        if paired:
            config.write_config_value(conn, "pi.address", PI.address)
    finally:
        conn.close()

    unit = {"state": unit}  # Setup's Start zeropi-push changes it
    started = [time.monotonic()]  # zeropi-push "started" now, so only later edits are pending restart

    def service_info():
        running = unit["state"] == "running"
        state = "running" if running else "not running"
        return operations.ServiceInfo(state, time.monotonic() - started[0] if running else None, unit["state"])

    app = web.create_app(
        port=args.port,
        cfg_path=cfg_path,
        cli_store_path=str(store),
        service_info_fn=service_info,
        systemctl_fn=fake_systemctl(unit, started),
    )
    import uvicorn

    print(f"scenario {args.scenario}: http://127.0.0.1:{args.port}/  (stores in {tmp})")
    uvicorn.run(app, host=web.HOST, port=args.port, timeout_graceful_shutdown=web.SHUTDOWN_GRACE_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
