"""The resident `systemd --user` service (spec §7.5, ticket #47).

Owns cadence, not data: it polls claude-hud's rate-limit snapshot every 30s,
decides whether a *displayed* Gauge value changed, and dispatches to the two
seams `push.py` already exposes for exactly this purpose —
`run_batch_pass(store_path)` and `run_gauge_push(store_path)`, both already
no-op cleanly with no BLE attempt when there is nothing to send. Nothing in
this module touches BLE, SQLite, or the JSONL logs directly.

Two pieces of pure decision logic, each independently testable with an
injected clock and no real sleeping (spec §"Testing" for #47):

- `GaugeGate` — spec §7.5's push-on-change trigger plus the 300s Desktop-side
  throttle, with coalescing (a pending flag, never a dropped change) and an
  explicit `batch_in_progress` input so "the two jobs never interleave" is a
  property of this class's contract, not just an accident of the loop being
  single-threaded.
- `BatchScheduler` — the 04:00-local trigger plus the >24h-stale startup
  catch-up (spec §7.5's "covers a laptop asleep at 04:00").

`run_forever` wires the two together into the actual loop. Every external
effect it touches (the wall clock, the monotonic clock, the snapshot reader,
the two push.py entry points, and even `asyncio.sleep` itself) is an
injectable parameter, so a test can drive several iterations of the real
loop body with fakes and no waiting.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

import gauge
import push

POLL_INTERVAL_S = 30.0
GAUGE_THROTTLE_S = 300.0
BATCH_CATCHUP_THRESHOLD_S = 24 * 60 * 60
BATCH_SCHEDULED_HOUR = 4  # local, spec §7.5

logger = logging.getLogger("zeropi.service")


# ---------------------------------------------------------------------------
# The displayed Gauge state, and change detection (spec §7.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisplayedGaugeState:
    """The subset of the rate-limit snapshot that actually drives a Gauge
    push: the two Limit Windows' percentage (value AND null-ness) and their
    `resets_at`. Deliberately excludes the context percentage and
    `updated_at` — neither triggers a push (spec §7.5, §13 judgment call #5).

    Compared by value (a plain `==` on the dataclass), so any of a pct
    changing, a pct's null-ness changing, or a `resets_at` moving (a Limit
    Window rolling) all show up as "changed" with no special-casing.
    """

    five_hour_pct: Optional[int]
    five_hour_resets_at: Optional[str]
    seven_day_pct: Optional[int]
    seven_day_resets_at: Optional[str]

    @classmethod
    def from_snapshot(cls, snapshot: Optional[dict]) -> Optional["DisplayedGaugeState"]:
        """`None` in, `None` out: an absent/corrupt snapshot (spec §5.1's
        "no Gauge" case, as `gauge.read_snapshot` already reports it) is not
        a state distinct from every real one — it is "nothing to compare".
        """
        if snapshot is None:
            return None
        five_hour = snapshot.get("five_hour") or {}
        seven_day = snapshot.get("seven_day") or {}
        return cls(
            five_hour_pct=five_hour.get("used_percentage"),
            five_hour_resets_at=five_hour.get("resets_at"),
            seven_day_pct=seven_day.get("used_percentage"),
            seven_day_resets_at=seven_day.get("resets_at"),
        )


class GaugeGate:
    """Pure decision logic for spec §7.5's Gauge-push trigger and its 300s
    Desktop-side throttle. Takes an injectable monotonic-style clock so it
    needs no real sleeping to test.

    Call `observe(state, now, batch_in_progress)` once per poll tick with
    the latest `DisplayedGaugeState` (or `None`) and the current time on
    whatever monotonic scale `now` is drawn from — consistent within one
    `GaugeGate` instance is all that matters. Returns `True` exactly when a
    Gauge push should be attempted *now*.

    This class never caches a payload, only a change flag — "push the
    then-current value" (not the value observed when the change first
    arrived) falls out for free because the caller re-reads live state at
    push time via `push.run_gauge_push`.
    """

    def __init__(self, throttle_s: float = GAUGE_THROTTLE_S):
        self.throttle_s = throttle_s
        self._seen_before = False
        self._last_state: Optional[DisplayedGaugeState] = None
        self._pending = False
        self._last_push_at: Optional[float] = None

    def observe(
        self,
        state: Optional[DisplayedGaugeState],
        now: float,
        batch_in_progress: bool = False,
    ) -> bool:
        if not self._seen_before:
            # The first observation only establishes a baseline: there is
            # nothing yet to compare it against, so it is never itself a
            # "change" (spec: never push on a fresh observation alone).
            self._seen_before = True
            self._last_state = state
            return False

        if state != self._last_state:
            self._pending = True
        self._last_state = state

        if not self._pending:
            return False

        # One service, one loop: a Gauge push waits for an in-flight Batch
        # to finish (spec §7.5). The pending flag stays set so this fires on
        # the first tick after the Batch releases, rather than being lost.
        if batch_in_progress:
            return False

        if self._last_push_at is not None and (now - self._last_push_at) < self.throttle_s:
            return False

        self._pending = False
        self._last_push_at = now
        return True


# ---------------------------------------------------------------------------
# The Batch trigger: 04:00 local, plus a >24h-stale startup catch-up
# ---------------------------------------------------------------------------


class BatchScheduler:
    """Pure decision logic for spec §7.5's Batch cadence: 04:00 local, plus
    an immediate catch-up on service start if the last successful Batch is
    already >24h stale (covers a laptop asleep at 04:00).

    Takes an injectable wall-clock `now` (a tz-aware `datetime`) per call —
    the 04:00 trigger is inherently a local-wall-clock concept, unlike the
    Gauge throttle above.

    `last_success_at=None` (the default, and the only option this service
    itself has across a restart — nothing here is persisted to disk) is
    treated as "unknown, assume stale": the very first `due()` call always
    fires a catch-up check. This errs toward one extra Batch pass on every
    service start rather than silently missing a 04:00 the service wasn't
    running for; `run_batch_pass` is already a cheap no-op when nothing is
    pending, so the common case (nothing to send) costs one open-then-idle
    check, not a wasted BLE connection attempt.
    """

    def __init__(
        self,
        last_success_at: Optional[datetime] = None,
        catchup_threshold_s: float = BATCH_CATCHUP_THRESHOLD_S,
        scheduled_hour: int = BATCH_SCHEDULED_HOUR,
    ):
        self.last_success_at = last_success_at
        self.catchup_threshold_s = catchup_threshold_s
        self.scheduled_hour = scheduled_hour
        self._catchup_checked = False
        self._prev_now: Optional[datetime] = None

    def due(self, now: datetime) -> bool:
        fire = False

        if not self._catchup_checked:
            self._catchup_checked = True
            if (
                self.last_success_at is None
                or (now - self.last_success_at).total_seconds() >= self.catchup_threshold_s
            ):
                fire = True

        if not fire and self._prev_now is not None:
            # A "crossing" of today's 04:00, detected between two
            # consecutive ticks, rather than "is it currently >= 4am and
            # haven't fired today" -- the latter would misfire on every
            # service start that happens to occur after 4am, which is not
            # what "at 04:00" means.
            scheduled = now.replace(hour=self.scheduled_hour, minute=0, second=0, microsecond=0)
            if self._prev_now < scheduled <= now:
                fire = True

        self._prev_now = now
        return fire

    def note_success(self, now: datetime) -> None:
        self.last_success_at = now


# ---------------------------------------------------------------------------
# The loop itself
# ---------------------------------------------------------------------------


WallClockFn = Callable[[], datetime]
MonoClockFn = Callable[[], float]
ReadSnapshotFn = Callable[[], Optional[dict]]
RunBatchPassFn = Callable[[Optional[str]], Awaitable["push.BatchResult"]]
RunGaugePushFn = Callable[[Optional[str]], Awaitable[bool]]
SleepFn = Callable[[float], Awaitable[None]]


async def run_forever(
    store_path: Optional[str] = None,
    poll_interval_s: float = POLL_INTERVAL_S,
    *,
    max_iterations: Optional[int] = None,
    now_wall_fn: WallClockFn = lambda: datetime.now().astimezone(),
    now_mono_fn: MonoClockFn = time.monotonic,
    read_snapshot_fn: ReadSnapshotFn = gauge.read_snapshot,
    run_batch_pass_fn: RunBatchPassFn = push.run_batch_pass,
    run_gauge_push_fn: RunGaugePushFn = push.run_gauge_push,
    sleep_fn: SleepFn = asyncio.sleep,
) -> None:
    """The resident loop (spec §7.5). Runs until cancelled (or, for tests,
    until `max_iterations` ticks have run).

    Each tick, in order — a single coroutine, never concurrent tasks, so
    the Batch and Gauge jobs never interleave by construction:

    1. Check `BatchScheduler.due()`; if due, run `run_batch_pass_fn` and
       record success.
    2. Read the snapshot and feed it to `GaugeGate.observe()`; if it says
       to push, run `run_gauge_push_fn`.
    3. Sleep for `poll_interval_s`.

    All I/O is behind the injectable parameters above, so this function
    itself is what a test drives directly, with fakes, rather than testing
    only the pure `GaugeGate`/`BatchScheduler` pieces in isolation.
    """
    gate = GaugeGate()
    scheduler = BatchScheduler()

    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        # Reset at the top of each tick, not in a `finally` around the Batch
        # call below. A Batch that runs *this* tick must still read as
        # in-progress for the Gauge check later in *this same* tick (spec
        # §7.5: a Gauge push waits for an in-flight Batch to finish, i.e.
        # until the *next* tick after it releases) -- resetting it eagerly
        # the moment the Batch's await returns would let a Gauge push open a
        # second BLE connection back-to-back with the Batch's, in the same
        # iteration, which is the exact interleaving this is meant to
        # prevent.
        batch_in_progress = False

        now_wall = now_wall_fn()
        if scheduler.due(now_wall):
            batch_in_progress = True
            try:
                result = await run_batch_pass_fn(store_path)
                if result.ok:
                    scheduler.note_success(now_wall)
            except Exception:  # noqa: BLE001 - a bad Batch must not kill the loop
                logger.exception("Batch pass failed")

        snapshot = read_snapshot_fn()
        state = DisplayedGaugeState.from_snapshot(snapshot)
        now_mono = now_mono_fn()
        if gate.observe(state, now_mono, batch_in_progress=batch_in_progress):
            try:
                await run_gauge_push_fn(store_path)
            except Exception:  # noqa: BLE001 - a bad Gauge push must not kill the loop
                logger.exception("Gauge push failed")

        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            await sleep_fn(poll_interval_s)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", metavar="PATH", help="Override the store location (spec §4.5).")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(run_forever(args.store))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
