"""Desktop (BLE central): the transport (spec §7).

Owns the Desktop Id (§7.1), the Batch loop over Daily Payloads (§7.3), the
single Gauge push (§7.4), `wiped: true` handling (§7.2), and the manual CLI
entry point (§7.6). The data layer (which Payloads to send) comes from
``usage.py`` and ``gauge.py``; this module is the transport around it and
keeps the existing BLE mechanics (§10) — service-UUID scanning, a single
held connection, and deferred-Ack-aware write/notify. It uses **only public
`bleak` API**, so it is not tied to the BlueZ backend (#32).

Two functions are the seam #47's resident service is expected to import
directly, without subprocessing into this file's CLI:

- `run_batch_pass(store_path=None)` — one Batch pass (a full push of every
  pending Reading, including the wipe's one extra pass).
- `run_gauge_push(store_path=None)` — one Gauge push.

⚠ Both go through `_with_ble_connection`, which since #84 owns two things
besides the connection (management-surface spec §7): the advisory **BLE
lock** (§7.1 — so *busy* can be told from *absent*, and so both the CLI and
the resident service take it by construction rather than by remembering),
and the **Settings re-assertion** (§7.2 — the complete Settings set is
written at the head of every connection, best-effort). Anything that opens
the link goes through this seam, never around it.

Everything that decides *what* to send and *how to interpret an Ack* is
plain, BLE-free Python (`desktop_id`, `build_daily_batch`,
`apply_wipe_if_needed`, the CLI dispatch) — it takes a ``send_one`` callable
(one write-and-wait-for-Ack) as a parameter rather than calling `bleak`
inline, so it is unit-testable with a fake radio. Only `_send_over_ble` and
the connect/scan glue around it touch `bleak`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import errno
import fcntl
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import config
import gauge
import usage

SERVICE_UUID = "abbac370-5a95-490d-a1fc-921c1c95300d"
WRITE_CHARACTERISTIC_UUID = "014ca0e2-c76c-4443-a755-e5a1ad25368d"
NOTIFY_CHARACTERISTIC_UUID = "08c89458-52f1-47eb-ab58-f7f7995d8efb"

SCAN_TIMEOUT_SECONDS = 10.0
ACK_TIMEOUT_SECONDS = 10.0

# The hard ceiling on one Payload, in bytes (#67). This is ATT's maximum
# attribute value length, NOT the MTU -- measured against the dev Pi by
# bisection: 512 bytes is Acked normally, 513 raises
# `INVALID_ATTRIBUTE_VALUE_LENGTH`. A negotiated 517-byte MTU would suggest
# 514 (517 - 3), and spec §6 said so for months; the attribute limit binds
# first, so 514 was wrong by two bytes.
MAX_PAYLOAD_BYTES = 512


# --- §7.1 The BLE lock -----------------------------------------------------
#
# Mechanism, not policy: the path is deliberately NOT a Configuration key
# (management-surface spec §4.4). `flock` rather than a PID file, because the
# kernel releases it when the holder dies -- a crashed CLI leaves no stale
# lock and there is no recovery story to design.
BLE_LOCK_PATH = Path("~/.local/state/zeropi-display/ble.lock").expanduser()
BLE_LOCK_MODE = 0o600

# Asymmetric on purpose (spec §7.1): a Gauge push holds the link ~2-3s, so a
# short wait absorbs every Gauge collision and leaves only the twice-daily
# Batch (~2 min) as a genuine "busy". The resident service does not wait at
# all -- it already treats both its jobs as droppable (pipeline §7.3 retries
# the Batch, §7.4 drops the Gauge).
CLI_LOCK_WAIT_S = 15.0
SERVICE_LOCK_WAIT_S = 0.0
LOCK_POLL_INTERVAL_S = 0.25


class BleLinkBusy(RuntimeError):
    """The BLE lock is held by another Desktop-side job (spec §7.1).

    Distinct from a scan failure on purpose: *held past the wait, scan not
    attempted* is **busy**, while *acquired, scan timed out* is **absent**.
    Telling those apart is the whole reason the lock exists — §8.5 gives
    them different headlines, and a Pi sitting right there mid-Batch must
    never be reported as unreachable.
    """


@dataclass
class SettingsOutcome:
    """What the §7.2 re-assertion did, for the caller that needs to know.

    `wiped` is the load-bearing half: re-assertion is the *first* write of
    every connection, so it is the write a Desktop Id hand-off wipes on, and
    the Pi flags that wipe on **that Ack and only that Ack**
    (`receive.py:check_desktop_id`). A caller passes one of these in and
    reads it afterwards, rather than each caller growing its own `nonlocal`
    flag.
    """

    ok: bool = False
    wiped: bool = False


class SettingsAssertionError(RuntimeError):
    """A Settings write failed where it *was* the job (spec §7.2).

    Re-assertion at the head of an ordinary connection is best-effort and
    never raises this; only a CLI-initiated settings change, whose sole
    purpose is that write, asks for `settings_required=True`.
    """


class PayloadTooLarge(ValueError):
    """One Payload exceeds `MAX_PAYLOAD_BYTES` (#67).

    Raised before the write reaches the radio, so it is reported against the
    row that caused it rather than as a GATT-layer error. Both callers treat
    a raised `send_one` as a failed row (`_send_batch_once`) or a dropped
    Gauge push (`run_gauge_with_connection`), so this needs no special
    handling to be surfaced -- only to be readable when it is.
    """

# A `send_one` callable: writes one Payload, waits for and returns its Ack
# dict. `None` return means "no Ack" (e.g. a timeout) — callers must treat
# that as a failed row, never as success.
SendOne = Callable[[dict], Awaitable[Optional[dict]]]


# ---------------------------------------------------------------------------
# §7.1 The Desktop Id
# ---------------------------------------------------------------------------

APP_ID = "zeropi.display.desktop-id.v1"

MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")


def desktop_id(machine_id_paths: tuple[str, ...] = MACHINE_ID_PATHS) -> str:
    """An app-specific hash of `/etc/machine-id` (spec §7.1).

    `machine_id_paths` is injectable so tests can point at a fake machine-id
    file without touching the real one.
    """
    for p in machine_id_paths:
        try:
            raw = Path(p).read_text().strip()
        except OSError:
            continue
        if raw:
            return hmac.new(bytes.fromhex(raw), APP_ID.encode(), hashlib.sha256).hexdigest()[:16]
    raise RuntimeError(
        f"no machine id at {' or '.join(machine_id_paths)}"
    )


# ---------------------------------------------------------------------------
# §7.3 Batch-building — pure, no BLE
# ---------------------------------------------------------------------------


def build_daily_batch(readings: list, desktop_id_: str) -> list[dict]:
    """Turns pending Readings (newest-date-first, per usage.py) into an
    ordered list of Daily Payload dicts with correct `batch_size` /
    `batch_index` (spec §6.1, §7.3).
    """
    batch_size = len(readings)
    return [
        usage.reading_to_daily_payload(reading, desktop_id_, batch_size, index)
        for index, reading in enumerate(readings)
    ]


# ---------------------------------------------------------------------------
# §7.3/§7.5 Batch loop result, and the loop itself
# ---------------------------------------------------------------------------


@dataclass
class BatchResult:
    sent: int = 0
    failed: int = 0
    wiped: bool = False

    @property
    def ok(self) -> bool:
        return self.failed == 0


async def _send_batch_once(payloads: list[dict], send_one: SendOne, conn) -> BatchResult:
    """Sends one pass of Payloads sequentially over the already-open
    connection, marking each successfully-Acked Reading as pushed (spec
    §7.3 steps 3-5). Returns whether any Ack in this pass had `wiped: true`.
    """
    result = BatchResult()
    for payload in payloads:
        try:
            ack = await send_one(payload)
        except Exception as exc:  # noqa: BLE001 - a failed row must not abort the Batch
            print(f"Row failed: {payload['date']} {payload['project']} {payload['model']}: {exc}")
            result.failed += 1
            continue

        if ack is None or ack.get("status") != "ok":
            reason = (ack or {}).get("reason", "no Ack")
            print(f"Row failed: {payload['date']} {payload['project']} {payload['model']}: {reason}")
            result.failed += 1
            continue

        usage.mark_pushed(
            conn,
            payload["date"],
            payload["project"],
            payload["model"],
            _now_iso(),
        )
        result.sent += 1
        if ack.get("wiped"):
            result.wiped = True

    return result


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


async def run_batch_with_connection(
    conn,
    desktop_id_: str,
    send_one: SendOne,
    *,
    wiped_already: bool = False,
) -> BatchResult:
    """The full Batch loop against an already-open connection (spec §7.3,
    §7.2): one pass over the pending Readings, and — if any Ack in that
    pass reports `wiped: true` — exactly one further pass covering the
    whole (now-pending-again) Window. Never loops more than that.

    `wiped_already` is the wipe reported by a Settings re-assertion earlier
    in this same connection (management-surface §7.2): the wipe happened on
    *that* Ack, so no Daily Ack in this pass will report it, and without
    this flag the extra pass would never run.

    Callers (the CLI, and #47's resident service) are expected to open one
    BLE connection, call this once, then disconnect (§7.3 step 2 and 6).
    """
    readings = usage.pending_readings(conn)
    if not readings and not wiped_already:
        return BatchResult()

    payloads = build_daily_batch(readings, desktop_id_)
    result = await _send_batch_once(payloads, send_one, conn)
    if wiped_already:
        result.wiped = True

    if result.wiped:
        # §7.2: clear every pushed_at, finish the in-flight Batch normally
        # (done above), then run exactly one further pass covering the
        # whole Window. Never loop again regardless of this pass's Acks.
        usage.clear_pushed_marks(conn)
        readings2 = usage.pending_readings(conn)
        payloads2 = build_daily_batch(readings2, desktop_id_)
        result2 = await _send_batch_once(payloads2, send_one, conn)
        result.sent += result2.sent
        result.failed += result2.failed
        # `wiped` stays True: it already happened this invocation.

    return result


# ---------------------------------------------------------------------------
# §7.4 The Gauge push — pure dispatch, no BLE
# ---------------------------------------------------------------------------


async def run_gauge_with_connection(payload: Optional[dict], send_one: SendOne) -> tuple[bool, bool]:
    """Sends one Gauge Payload over an already-open connection. Returns
    `(ok, wiped)`. `payload=None` (gauge.py found nothing to send) is a
    no-op that returns `(False, False)` without attempting a connection —
    callers should not even connect in that case (see `run_gauge_push`).

    A failed Gauge push is dropped silently (spec §7.4): no retry, no
    queue, no mark. `wiped` is still meaningful on an ok Ack — spec §7.2
    is explicit that wipe handling applies "on any Ack with wiped: true,
    of either kind", so a Gauge Ack's `wiped` flag must not be discarded
    the way a plain success/failure signal could be.
    """
    if payload is None:
        return False, False
    try:
        ack = await send_one(payload)
    except Exception as exc:  # noqa: BLE001 - dropped silently per spec
        print(f"Gauge push failed: {exc}")
        return False, False
    if ack is None or ack.get("status") != "ok":
        print(f"Gauge push failed: {(ack or {}).get('reason', 'no Ack')}")
        return False, False
    return True, bool(ack.get("wiped"))


def build_gauge_wire_payload(desktop_id_: str, projects_root: Optional[Path] = None) -> Optional[dict]:
    """The Gauge Payload with `kind`/`desktop_id` attached (spec §6.2),
    or None if gauge.py has nothing to send this cycle (spec §7.4/§5.3).

    ⚠ management-surface spec §11.1 trap 1: `projects_root` must be threaded
    through to `gauge.build_gauge_payload`'s context read, or a configured
    `paths.projects_root` reaches every Daily path (via `run_batch_pass`)
    while the Gauge's context read silently keeps falling back to
    `gauge.DEFAULT_PROJECTS_ROOT` -- one Configuration key, two roots.
    """
    body = gauge.build_gauge_payload(projects_root=projects_root)
    if body is None:
        return None
    return {"kind": "gauge", "desktop_id": desktop_id_, **body}


# ---------------------------------------------------------------------------
# BLE plumbing — the only part of this module that touches `bleak`.
# ---------------------------------------------------------------------------


def matches_service(device, advertisement) -> bool:
    return SERVICE_UUID.lower() in [uuid.lower() for uuid in advertisement.service_uuids]


async def find_pi(timeout: float = SCAN_TIMEOUT_SECONDS, address: Optional[str] = None):
    """Finds the Pi (spec §4.6). `address`, when given, is `pi.address` from
    Configuration -- `find_pi` prefers it over the service-UUID filter alone,
    since without it discovery is first-advertiser-wins and a Desktop cannot
    tell *which* Pi it is coupled to (a two-Pi household is a coin flip).

    ⚠ The address filter narrows the *same* service-UUID-filtered scan
    rather than replacing it: a stored MAC does not, by itself, prove the
    device answering at it is still running the zeropi GATT service (a
    reused/rotated address on different hardware). Requiring both is what
    turns that case into a clean *absent* Unreachable instead of a
    confusing GATT/connect failure against the wrong device.

    ⚠ A stored address that does not answer is an ordinary *absent*
    Unreachable, not a special failure (§4.6) -- there is deliberately no
    fallback to scanning for a *different* Pi.
    """
    from bleak import BleakScanner

    def _matches(device, advertisement) -> bool:
        if not matches_service(device, advertisement):
            return False
        return address is None or device.address.lower() == address.lower()

    device = await BleakScanner.find_device_by_filter(_matches, timeout=timeout)
    if device is None:
        if address:
            raise RuntimeError(f"no reply from paired Pi {address} within {timeout}s")
        raise RuntimeError(
            f"No device advertising service {SERVICE_UUID} found within {timeout}s"
        )
    return device


class BleConnection:
    """Holds one open BLE connection for the duration of a Batch or Gauge
    push (spec §7.3 step 2: connect once, hold it for the whole loop).
    Exposes `send_one` as the injectable seam the pure dispatch functions
    above call.
    """

    def __init__(self, client):
        self._client = client
        self._ack_received: Optional[asyncio.Event] = None
        self._ack: dict = {}

    def _handle_ack(self, _characteristic, data: bytearray) -> None:
        try:
            self._ack = json.loads(bytes(data).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._ack = {"status": "error", "reason": f"malformed ack from Pi: {exc}"}
        self._ack_received.set()

    async def send_one(self, payload: dict) -> Optional[dict]:
        self._ack_received = asyncio.Event()
        self._ack = {}
        body = json.dumps(payload).encode("utf-8")
        # One write per Reading, never chunked (ADR-0003). Checked here
        # rather than left to the radio: over the limit, BlueZ raises
        # `INVALID_ATTRIBUTE_VALUE_LENGTH`, which is accurate but points at
        # the transport for what is really a too-long `project` key. The
        # row would also be retried by every subsequent Batch forever,
        # since a Reading is only marked pushed on a successful Ack.
        if len(body) > MAX_PAYLOAD_BYTES:
            raise PayloadTooLarge(
                f"Payload is {len(body)} bytes, over the {MAX_PAYLOAD_BYTES}-byte "
                f"single-write limit (#67). This is almost certainly a long "
                f"project key: {payload.get('project', '<none>')!r}"
            )
        await self._client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, body, response=True)
        try:
            await asyncio.wait_for(self._ack_received.wait(), timeout=ACK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return None
        return self._ack

    async def __aenter__(self) -> "BleConnection":
        # ⚠ Nothing here negotiates the MTU, and nothing needs to (#32).
        # The ATT MTU is negotiated by the kernel/BlueZ when the link comes
        # up, before any of our code runs. This used to call bleak's private
        # `_backend._acquire_mtu()`, which despite the name negotiates
        # nothing -- it calls BlueZ's AcquireWrite purely to *read* the
        # already-negotiated value into `client.mtu_size`. Neither of the
        # paths we use consults that number: `write_gatt_char` goes through
        # D-Bus WriteValue, and `start_notify` uses BlueZ's StartNotify.
        # Removing it is what makes this module backend-agnostic rather
        # than BlueZ-only. Do not reintroduce it to "fix" a size problem;
        # see the budget note on `send_one` for the limit that is real.
        await self._client.start_notify(NOTIFY_CHARACTERISTIC_UUID, self._handle_ack)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # No explicit stop_notify: exiting the outer BleakClient context
        # disconnects, which implicitly stops notifications. An explicit
        # stop_notify() here would re-resolve the characteristic against
        # client.services, which raises a misleading BleakError when the
        # real failure happened before discovery completed — masking the
        # actual exception (spec §10 trap #4, #12). Do not reintroduce it.
        return None


def _open_client(device):
    """The `BleakClient` factory, named so a test can substitute a fake
    radio for the whole `_with_ble_connection` seam without a `bleak`
    import or a Pi (spec §10.5)."""
    from bleak import BleakClient

    return BleakClient(device)


@contextlib.asynccontextmanager
async def ble_lock(
    *,
    wait_s: float,
    path: Optional[Path] = None,
    poll_interval_s: float = LOCK_POLL_INTERVAL_S,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
):
    """Holds the advisory `flock(2)` of spec §7.1 for the body's duration.

    `wait_s=0` makes exactly one non-blocking attempt and raises
    `BleLinkBusy` — the resident service's path. A positive `wait_s` retries
    until that bound and then raises the same exception — the CLI's path.
    The two are the same mechanism with different bounds, which is why the
    lock is taken here, inside the connection seam, rather than at each
    caller: every job that opens the link takes it by construction.
    """
    path = BLE_LOCK_PATH if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, BLE_LOCK_MODE)
    try:
        deadline = monotonic_fn() + wait_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                # Only "someone else holds it" means busy. ENOLCK, EBADF and
                # friends are this Desktop's own problem and must fail loudly
                # rather than be reported as a Pi that is merely occupied.
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
                remaining = deadline - monotonic_fn()
                if remaining <= 0:
                    raise BleLinkBusy(
                        f"the BLE link is busy: {path} is held by another "
                        f"zeropi-display job (waited {wait_s:.0f}s)"
                    ) from None
                await sleep_fn(min(poll_interval_s, remaining))
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# §7.2 Settings re-assertion
# ---------------------------------------------------------------------------


def settings_from_config(cfg) -> dict:
    """The complete Settings set the Pi is told, derived from Configuration
    (spec §5.1). Today one key; a new Setting is added here and on the Pi's
    `_SETTING_COERCERS` together — the Payload is the whole set, never a
    patch, which is what makes a resend free and ADR-0011's no-queue rule
    work."""
    return {"idle_keepalive_s": cfg.pi_idle_keepalive_s}


def build_settings_payload(settings: dict, desktop_id_: str) -> dict:
    """The Settings Payload (spec §5.1). Nested under `settings` so a key
    can never collide with the reserved `kind`/`desktop_id`."""
    return {"kind": "settings", "desktop_id": desktop_id_, "settings": dict(settings)}


# Mirrors `pi/receive.py:COMMAND_VERBS` -- kept in step by hand, like the
# Tier 3 constants `config.py`/`verdict.py` mirror, since neither deployment
# installs the other's code. `test_cli.py` asserts this against the Pi's own
# tuple where the Pi module is importable.
COMMAND_VERBS = ("redraw", "wipe", "status")


def build_command_payload(verb: str, desktop_id_: str) -> dict:
    """The Command Payload (spec §5.2) — `cli.py`'s `redraw`/`wipe`/`status`
    all send this, differing only in `verb`."""
    return {"kind": "command", "desktop_id": desktop_id_, "verb": verb}


async def write_settings(
    payload: dict,
    send_one: SendOne,
    *,
    required: bool = False,
) -> SettingsOutcome:
    """Writes one Settings Payload. Returns what it did.

    Best-effort by default (spec §7.2): a failure logs and continues, is not
    counted in the Batch's failed-row total, and does not affect the exit
    code — otherwise a flaky Setting starts failing Batches for no reason.
    `required=True` is the one exception, a CLI-initiated settings change
    whose only job is this write.

    ⚠ `wiped` is not decoration. Re-assertion is the *first* write of every
    connection, so it is the write a Desktop Id hand-off wipes on, and the
    Pi flags that wipe on **that Ack and only that Ack**
    (`receive.py:check_desktop_id`). Dropping it here would lose the wipe
    entirely — the exact failure pipeline §7.2 exists to prevent.
    """
    try:
        ack = await send_one(payload)
    except Exception as exc:  # noqa: BLE001 - best-effort unless required
        if required:
            raise SettingsAssertionError(f"settings write failed: {exc}") from exc
        print(f"Settings re-assertion failed (continuing): {exc}")
        return SettingsOutcome()

    if ack is None or ack.get("status") != "ok":
        reason = (ack or {}).get("reason", "no Ack")
        if required:
            raise SettingsAssertionError(f"settings write failed: {reason}")
        print(f"Settings re-assertion failed (continuing): {reason}")
        return SettingsOutcome(ok=False, wiped=bool((ack or {}).get("wiped")))

    return SettingsOutcome(ok=True, wiped=bool(ack.get("wiped")))


async def _with_ble_connection(
    coro_fn,
    *,
    settings_payload: Optional[dict] = None,
    settings_required: bool = False,
    settings_outcome: Optional[SettingsOutcome] = None,
    lock_wait_s: float = CLI_LOCK_WAIT_S,
    pi_address: Optional[str] = None,
) -> Any:
    """Takes the BLE lock, scans for the Pi, opens one BleakClient, re-asserts
    Settings, and runs `coro_fn(send_one)` inside it. Raised exceptions
    propagate (a scan/connect failure fails the whole Batch, spec §7.3's "no
    Pi advertising" case); `BleLinkBusy` propagates too and means something
    else on this Desktop holds the link — never that the Pi is absent.

    ⚠ The Settings write of §7.2 precedes whatever the connection was opened
    for, and that is part of this function's *contract*, not just its
    callers'. `coro_fn=None` is the CLI-initiated settings change: the write
    is the entire job, so there is nothing else to run.
    """
    async with ble_lock(wait_s=lock_wait_s):
        # ⚠ Say that we are scanning (§7.1): an absent Pi costs a full
        # `SCAN_TIMEOUT_SECONDS` before it is known, and silence for ten
        # seconds reads as hung.
        print(f"Scanning for the Pi (up to {SCAN_TIMEOUT_SECONDS:.0f}s)…")
        device = await find_pi(address=pi_address)
        print(f"Found {device.name or device.address} ({device.address})")
        async with _open_client(device) as client:
            conn = BleConnection(client)
            async with conn:
                # Deliberately not printing `client.mtu_size`. With
                # `_acquire_mtu()` gone (#32) bleak never reads the negotiated
                # value, so the property reports a placeholder 23 and warns.
                # Printing that is worse than printing nothing: it invites
                # exactly the wrong diagnosis, since the real MTU is large and
                # the link is fine.
                print(f"Connected to {device.address}")
                if settings_payload is not None:
                    outcome = await write_settings(
                        settings_payload, conn.send_one, required=settings_required
                    )
                    if settings_outcome is not None:
                        settings_outcome.ok = outcome.ok
                        settings_outcome.wiped = outcome.wiped
                if coro_fn is None:
                    return None
                return await coro_fn(conn.send_one)


# ---------------------------------------------------------------------------
# Top-level entry points — the seam #47's resident service imports directly.
# ---------------------------------------------------------------------------


async def run_batch_pass(
    store_path: Optional[str] = None,
    projects_root: Optional[Path] = None,
    *,
    settings: Optional[dict] = None,
    lock_wait_s: float = CLI_LOCK_WAIT_S,
    pi_address: Optional[str] = None,
) -> BatchResult:
    """One Batch pass: ingest the logs, compute pending Readings, and (if
    any) push them over one BLE connection (spec §7.3). `--dry-run` is
    handled separately by `print_dry_run`, which never calls this.

    `store_path` is expected to already be the fully resolved path (spec
    §3.3) — an entry point's `main()` resolves Configuration once and passes
    it down; this only falls back to re-resolving when called with `None`
    directly (as service.py's `run_forever` default, and existing tests, do).

    `settings` is the complete Settings set to re-assert at the head of this
    connection (management-surface §7.2), or `None` for no re-assertion.
    `lock_wait_s` is the CLI's bounded wait by default; the resident service
    binds `SERVICE_LOCK_WAIT_S` in via `functools.partial` (§7.1).

    ⚠ `BleLinkBusy` **propagates**, unlike every other failure here. A busy
    link means nothing was attempted and nothing was learned (§8.1's *can't
    tell*), which is not the same fact as a Batch that failed — collapsing
    it into `BatchResult(failed=…)` is exactly the conflation §7.1's lock
    exists to end. Callers decide what to do with it: `_async_main` reports
    it as §9.6's exit code 2, and the resident service drops the pass.
    """
    store = usage.open_store(usage.resolve_store_path(store_path))
    try:
        usage.ingest_projects_root(store, root=projects_root or usage.DEFAULT_PROJECTS_ROOT)
        readings = usage.pending_readings(store)
        if not readings:
            # No connection is opened, so there is nothing to re-assert on:
            # §7.2 re-asserts at the head of every connection the Desktop
            # opens, not on a cadence of its own.
            print("No pending Readings — nothing to push.")
            return BatchResult()

        did = desktop_id()
        settings_outcome = SettingsOutcome()

        async def _run(send_one: SendOne) -> BatchResult:
            return await run_batch_with_connection(
                store, did, send_one, wiped_already=settings_outcome.wiped
            )

        try:
            result = await _with_ble_connection(
                _run,
                settings_payload=None if settings is None else build_settings_payload(settings, did),
                settings_outcome=settings_outcome,
                lock_wait_s=lock_wait_s,
                pi_address=pi_address,
            )
        except BleLinkBusy:
            raise
        except Exception as exc:  # noqa: BLE001 - no Pi advertising fails the whole Batch (§7.3)
            print(f"Batch failed: {exc}")
            return BatchResult(sent=0, failed=len(readings))
        print(f"Batch complete: {result.sent} sent, {result.failed} failed, wiped={result.wiped}")
        return result
    finally:
        store.close()


async def run_gauge_push(
    store_path: Optional[str] = None,
    projects_root: Optional[Path] = None,
    *,
    settings: Optional[dict] = None,
    lock_wait_s: float = CLI_LOCK_WAIT_S,
    pi_address: Optional[str] = None,
) -> bool:
    """One Gauge push (spec §7.4). `--dry-run` is handled separately by
    `print_dry_run`, which never calls this.

    `projects_root` only matters for the wipe-recovery re-ingest below — it
    is threaded through to that `run_batch_pass` call so a configured
    `paths.projects_root` (spec §4.1) is honoured there too, not just on the
    primary Batch path.

    Spec §7.2 requires wipe handling "on any Ack with wiped: true, of
    either kind" — not just the Daily-batch path. A wiped Gauge Ack means
    the Pi just dropped and recreated its Readings out from under this
    Desktop's store, which still believes everything is pushed; left
    unhandled the Pi would stay permanently empty (the exact failure §7.2
    exists to prevent). So on a wiped Gauge Ack, clear every `pushed_at`
    and run exactly one further Batch pass in this same invocation —
    mirroring the Daily-batch path's one-extra-pass cap.
    """
    did = desktop_id()
    payload = build_gauge_wire_payload(did, projects_root)
    if payload is None:
        print("No Gauge to push this cycle.")
        return False

    settings_outcome = SettingsOutcome()

    async def _run(send_one: SendOne) -> tuple[bool, bool]:
        return await run_gauge_with_connection(payload, send_one)

    try:
        ok, wiped = await _with_ble_connection(
            _run,
            settings_payload=None if settings is None else build_settings_payload(settings, did),
            settings_outcome=settings_outcome,
            lock_wait_s=lock_wait_s,
            pi_address=pi_address,
        )
    except BleLinkBusy:
        # §7.1: nothing was attempted, so this is not "no Pi". The caller
        # decides — the service drops the push, the CLI reports can't-tell.
        raise
    except Exception as exc:  # noqa: BLE001 - a Gauge push is dropped silently (§7.4)
        print(f"Gauge push dropped (no Pi / connection failed): {exc}")
        return False

    # The re-assertion's wipe is independent of `ok`: it is reported on that
    # write's own Ack, so it has already happened even if the Gauge write
    # that followed it failed and was dropped (§7.4).
    if (ok and wiped) or settings_outcome.wiped:
        print("Ack reported wiped=true — clearing pushed marks and running one Batch pass.")
        store = usage.open_store(usage.resolve_store_path(store_path))
        try:
            usage.clear_pushed_marks(store)
        finally:
            store.close()
        await run_batch_pass(
            store_path,
            projects_root,
            settings=settings,
            lock_wait_s=lock_wait_s,
            pi_address=pi_address,
        )
    print(f"Gauge push {'ok' if ok else 'failed'}.")
    return ok


# ---------------------------------------------------------------------------
# §7.6 CLI
# ---------------------------------------------------------------------------


def print_dry_run(store_path: Optional[str] = None, projects_root: Optional[Path] = None) -> None:
    """`--dry-run`: ingest, aggregate, and print what *would* be sent —
    Payloads, sizes, Project Label + its R-rule, pending/total row counts,
    and the Gauge state. No BLE, no store writes to `pushed_at` (spec §7.6).
    """
    projects_root = projects_root or usage.DEFAULT_PROJECTS_ROOT
    store = usage.open_store(usage.resolve_store_path(store_path))
    try:
        usage.ingest_projects_root(store, root=projects_root)
        all_readings = usage.aggregate_readings(store)
        pending = [r for r in all_readings if r.pending]

        print(f"Pending {len(pending)} / {len(all_readings)} Readings in the Window.")

        projects = usage.discover_projects(projects_root)
        did = "n/a (dry-run: not resolving desktop id)"
        try:
            did = desktop_id()
        except RuntimeError as exc:
            did = f"<unavailable: {exc}>"

        batch = build_daily_batch(pending, did)
        for reading, payload in zip(pending, batch):
            body = json.dumps(payload).encode("utf-8")
            project_dir = projects.get(reading.project_key)
            label_info = ""
            if project_dir is not None:
                cwds = usage.collect_cwds(project_dir)
                label = usage.derive_project_label(reading.project_key, cwds)
                label_info = f" label={label.label} ({label.rule}, verified={label.verified})"
            print(
                f"  {payload['date']} {payload['project']} {payload['model']}"
                f" ({len(body)} bytes){label_info}"
            )

        print("Gauge state:")
        gauge_payload = gauge.build_gauge_payload(projects_root=projects_root)
        if gauge_payload is None:
            print("  no Gauge to push this cycle (no snapshot, or stale >= 300s)")
        else:
            body = json.dumps({"kind": "gauge", "desktop_id": did, **gauge_payload}).encode("utf-8")
            print(f"  {gauge_payload} ({len(body)} bytes)")
    finally:
        store.close()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ingest, aggregate, and print the Payloads that would be sent. No BLE.",
    )
    parser.add_argument(
        "--resend-all",
        action="store_true",
        help="Clear every pushed_at, then Batch the whole Window.",
    )
    parser.add_argument("--store", metavar="PATH", help="Override the store location (spec §4.5).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gauge-only", action="store_true", help="Run only the Gauge push.")
    mode.add_argument("--batch-only", action="store_true", help="Run only the Batch pass.")
    args = parser.parse_args(argv)
    if args.resend_all and args.gauge_only:
        # --resend-all's whole point (spec §7.6) is to Batch the whole
        # Window; --gauge-only would clear every pushed_at and then skip
        # the Batch pass that was supposed to resend it, silently leaving
        # the Window pending until some later, unrelated invocation.
        parser.error("--resend-all cannot be combined with --gauge-only (it needs to run the Batch pass)")
    return args


async def _async_main(
    args: argparse.Namespace,
    resolved_store_path: Optional[str] = None,
    resolved_projects_root: Optional[Path] = None,
    settings: Optional[dict] = None,
    pi_address: Optional[str] = None,
) -> int:
    """`resolved_store_path`/`resolved_projects_root`/`settings`/`pi_address`
    are Configuration, already resolved once by `main()` (spec §3.3).
    Defaulting to `None` (falling back to `args.store` / the compiled
    default, no re-assertion, and the generic service-UUID scan) keeps this
    directly callable the way the existing tests call it, without a
    Configuration store in the picture.
    """
    store_path = args.store if resolved_store_path is None else resolved_store_path

    if args.dry_run:
        print_dry_run(store_path, resolved_projects_root)
        return 0

    if args.resend_all:
        store = usage.open_store(usage.resolve_store_path(store_path))
        try:
            usage.clear_pushed_marks(store)
        finally:
            store.close()

    exit_code = 0

    # `push.py` is a CLI, so it waits out a collision rather than dropping
    # the job the way the resident service does (spec §7.1). If the link is
    # still busy after that wait, nothing was attempted and nothing was
    # learned — §9.6's exit code 2 (*can't tell*), never 1 (*a real fault*),
    # so a cron wrapper cannot page on an expected steady state.
    try:
        if not args.gauge_only:
            result = await run_batch_pass(
                store_path,
                resolved_projects_root,
                settings=settings,
                lock_wait_s=CLI_LOCK_WAIT_S,
                pi_address=pi_address,
            )
            if not result.ok:
                exit_code = 1

        if not args.batch_only:
            await run_gauge_push(
                store_path,
                resolved_projects_root,
                settings=settings,
                lock_wait_s=CLI_LOCK_WAIT_S,
                pi_address=pi_address,
            )
    except BleLinkBusy as exc:
        print(f"Can't tell yet — {exc}.", file=sys.stderr)
        print(
            "Nothing was queued. Retry in a moment — a Batch takes about two minutes.",
            file=sys.stderr,
        )
        return 2

    return exit_code


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    # Configuration is resolved once, here, into a frozen object (spec
    # §3.3) — the lazy `usage.resolve_store_path` call that used to live
    # inside `run_batch_pass` moves up to this entry point.
    try:
        cfg = config.resolve(cli_store_path=args.store)
    except config.ConfigVersionError as exc:
        print(f"Refusing to run: {exc}", file=sys.stderr)
        return 1
    try:
        return asyncio.run(
            _async_main(
                args,
                str(cfg.paths_store),
                cfg.paths_projects_root,
                settings_from_config(cfg),
                cfg.pi_address,
            )
        )
    except usage.StoreVersionError as exc:
        print(f"Refusing to run: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
