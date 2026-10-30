"""Operations over HTTP, minus the HTTP (web-ui spec §6).

One BLE Operation at a time per process (§6.3), each on its own worker
thread with its own `asyncio.run` (§4.6), its events buffered in memory for
replay (§6.5). ⚠ ADR-0011: `start` returns an Operation that has already
started, or refuses; nothing here ever holds an action to run later.
"""

from __future__ import annotations

import secrets
import threading
from typing import Callable, Optional

from operations import Outcome, Refused


def refusal_json(r: Refused) -> dict:
    return {"kind": r.kind, "message": r.message, "rationale": r.rationale}


class Operation:
    def __init__(self, verb: str) -> None:
        self.id = secrets.token_hex(8)  # §6.5: random, so a stale id is a 404
        self.verb = verb
        # Append-only; readers index by position, so no lock is needed.
        self.events: list[tuple[str, dict]] = []
        self.done = False
        self.outcome: Optional[str] = None

    def progress(self, kind: str, text: str) -> None:
        self.events.append(("progress", {"kind": kind, "text": text}))

    def summary(self) -> dict:
        return {"id": self.id, "verb": self.verb, "running": not self.done, "outcome": self.outcome}


class BusyHere(Exception):
    """§6.3's `busy_here`: this process is running `running` already. Not
    `unreachable: busy`, which is another process holding the link."""

    def __init__(self, running: Operation) -> None:
        super().__init__(f"{running.verb} is already running here.")
        self.running = running
        self.refusal = Refused(
            "busy_here",
            f"{running.verb} is already running here.",
            "One Operation at a time; nothing was queued.",
        )


class Runner:
    """Holds the running Operation plus the most recent finished one (§6.5),
    so a page reattaching to the one before still gets its result."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: Optional[Operation] = None
        self._previous: Optional[Operation] = None

    def get(self, op_id: str) -> Optional[Operation]:
        return next((op for op in (self.current, self._previous) if op is not None and op.id == op_id), None)

    def start(
        self,
        verb: str,
        run: Callable[[Operation], tuple[dict, Outcome]],
        precheck: Callable[[], None] = lambda: None,
    ) -> Operation:
        """Raises `BusyHere`, or `Refused` from `precheck` (§6.6: decided
        before BLE, so no Operation is created); else the Operation runs."""
        with self._lock:
            if self.current is not None and not self.current.done:
                raise BusyHere(self.current)
            precheck()
            op = Operation(verb)
            threading.Thread(target=self._run, args=(op, run), name=f"op-{verb}", daemon=True).start()
            # Only once it has started: a thread that never ran must not hold single-flight.
            self._previous, self.current = self.current, op
            return op

    @staticmethod
    def _run(op: Operation, run: Callable[[Operation], tuple[dict, Outcome]]) -> None:
        try:
            result, outcome = run(op)
        except Refused as r:  # raced past the precheck, e.g. Configuration changed
            result, outcome = {"refusal": refusal_json(r)}, r.outcome
        except Exception as exc:  # noqa: BLE001 - e.g. this Desktop's own store; never lost silently
            result, outcome = {"error": f"{type(exc).__name__}: {exc}"}, Outcome.FAULT
        op.outcome = outcome.value
        op.events.append(("result", {**result, "outcome": outcome.value}))
        op.done = True
