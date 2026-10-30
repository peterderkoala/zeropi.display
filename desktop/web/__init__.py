"""Desktop: the web UI of the Management Surface (web-ui spec §3, §5.1, §6.7).

`python desktop/cli.py web [--port]`: FastAPI on `127.0.0.1`, hardcoded. A
skin over `operations.py`, never a second surface. The only module allowed
to import `fastapi` (§3.1); `cli.py` imports it lazily.

Only the config store's path (and `--store`) is fixed at startup; values
are resolved per request (§4.6). Handlers are plain `def`, so FastAPI runs
their blocking store reads and `systemctl` on its thread pool; only the
events stream is async. BLE actions are Operations (§6), run by `runner`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.sse import EventSourceResponse, format_sse_event
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import operations
import usage
import verdict
from operations import Outcome, Refused
from web import guard
from web.runner import BusyHere, Operation, Runner, refusal_json

HOST = "127.0.0.1"  # §5.1: no --host flag
STATIC_DIR = Path(__file__).parent / "static"


# §7.5: a success of one of these after a check labels the check out of date.
MUTATING_VERBS = {"push", "pair", "redraw", "wipe"}


def _json_value(value: Any) -> Any:
    """An operations-module result as JSON (§4.3: serialised, never rebuilt)."""
    if dataclasses.is_dataclass(value):
        return {f.name: _json_value(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_value(v) for k, v in value.items()}
    return value


def _batch_phase(batch, unreachable) -> Optional[str]:
    if unreachable is not None:
        return unreachable.reason.value
    return None if batch is None else ("ok" if batch.ok else "fault")


def _phases(verb: str, r: Any) -> Optional[dict]:
    """§6.6: a two-phase Operation reports each phase, e.g.
    `{"command": "ok", "repush": "busy"}`."""
    if verb == "wipe":
        absent = None if r.repush is None or r.repush.unreachable is None else verdict.Reach.ABSENT.value
        repush = "busy" if r.repush_busy is not None else absent or _batch_phase(r.repush, None)
        return {"command": r.command.outcome.value, "repush": repush}
    if verb == "pair":
        if r.address is None:
            return {"scan": "absent", "batch": None}
        return {"scan": "ok", "batch": _batch_phase(r.batch, r.unreachable)}
    return None


def _raise(exc: Exception):
    raise exc


class OperationRequest(BaseModel):
    verb: Literal["status", "push", "pair", "redraw", "wipe", "discover"]
    args: dict[str, Any] = {}


class ConfigWrite(BaseModel):
    value: str  # §3.3: raw, as `cli.py config set` receives it; config.py validates


def create_app(
    *,
    port: int,
    cfg_path: Path,
    cli_store_path: Optional[str] = None,
    service_info_fn: Callable[[], operations.ServiceInfo] = operations.service_info,
    systemctl_fn: Callable[..., Any] = subprocess.run,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    guard.install(app, port)
    runner = Runner()

    @app.exception_handler(RequestValidationError)
    def bad_request(request, exc: RequestValidationError):
        # §6.7: every refusal carries {kind, message, rationale}, FastAPI's own included.
        message = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"refusal": refusal_json(Refused("bad_request", message))}, 422)
    # §7.2: the most recent status reply, in its own slot that later
    # Operations never replace -- only label (§7.5). In memory only.
    app.state.last_status = None

    def resolve() -> config.Configuration:
        return config.resolve(cli_config_path=str(cfg_path), cli_store_path=cli_store_path)

    def pending(service: operations.ServiceInfo) -> list[str]:
        conn = config.open_config_store(cfg_path)
        try:
            return operations.pending_restart(conn, service)
        finally:
            conn.close()

    @app.get("/api/desktop")
    def desktop() -> dict:
        service = service_info_fn()
        marked = pending(service)  # straight after: `uptime_s` is relative to now
        cfg = resolve()
        store = usage.open_store(cfg.paths_store)
        try:
            facts = operations.desktop_facts(cfg, usage.pushed_summary(store))
        finally:
            store.close()
        return {
            "service": {"state": service.state, "uptime_s": service.uptime_s},
            "seconds_since_last_batch": facts.seconds_since_last_push,
            "readings_sent": facts.readings,
            "pending_restart": marked,
        }

    @app.get("/api/config")
    def config_listing() -> dict:
        conn = config.open_config_store(cfg_path)
        try:
            values = config.read_config(conn)
        finally:
            conn.close()
        marked = set(pending(service_info_fn()))
        tiers: dict[str, list] = {"tier1": [], "tier2": []}
        for name, key in config.KEYS.items():
            tiers[f"tier{key.tier}"].append(
                {
                    "key": name,
                    "value": _json_value(values.get(name, key.default)),
                    "lo": key.lo,
                    "hi": key.hi,
                    "to_pi": name == operations.PI_PROJECTED_SETTING,
                    "pending_restart": name in marked,
                }
            )
        tiers["tier3"] = [
            {"key": name, "value": e.value, "fixed_by": e.fixed_by, "why": e.why}
            for name, e in operations.TIER3.items()
        ]
        return tiers

    @app.get("/api/status/last")
    def status_last():
        if app.state.last_status is None:
            return Response(status_code=204)
        return app.state.last_status

    # --- §6 Operations ------------------------------------------------------

    def run_verb(verb: str, cfg: config.Configuration, args: dict) -> Callable[[Operation], tuple[dict, Outcome]]:
        def run(op: Operation) -> tuple[dict, Outcome]:
            calls = {
                "status": lambda: operations.status(cfg, progress=op.progress),
                "push": lambda: operations.push_batch(cfg, progress=op.progress),
                "pair": lambda: operations.pair(cfg, cfg_path, address=args.get("address"), progress=op.progress),
                "redraw": lambda: operations.redraw(cfg, progress=op.progress),
                "wipe": lambda: operations.wipe(cfg, str(args.get("confirm", "")), progress=op.progress),
                "discover": lambda: operations.discover(progress=op.progress),
            }
            r = asyncio.run(calls[verb]())
            if verb == "status":
                result = operations.status_json(r, service_info_fn())
                # §7.3: a check with no reply empties the slot, so the next load re-checks.
                app.state.last_status = None if r.status is None else {
                    "result": {**result, "outcome": r.outcome.value},
                    "checked_at": now_fn().isoformat(),
                    "outdated_by": None,
                }
                return result, r.outcome
            result = _json_value(r)
            phases = _phases(verb, r)
            if phases is not None:
                result["phases"] = phases
            slot = app.state.last_status
            # A wipe whose re-push did not land still wiped the Pi (§6.6).
            succeeded = (r.command.outcome if verb == "wipe" else r.outcome) is Outcome.OK
            if verb in MUTATING_VERBS and succeeded and slot is not None and slot["outdated_by"] is None:
                slot["outdated_by"] = {"verb": verb, "at": now_fn().isoformat()}
            return result, r.outcome

        return run

    def precheck_for(verb: str, cfg: config.Configuration, args: dict) -> Callable[[], None]:
        address = args.get("address")
        if verb == "pair" and address is not None and not isinstance(address, str):
            return lambda: _raise(Refused("bad_request", "args.address must be a string."))
        if verb in ("push", "redraw"):
            return lambda: operations.require_paired(cfg)
        if verb == "wipe":
            return lambda: operations.check_wipe(cfg, str(args.get("confirm", "")))
        return lambda: None

    def start(verb: str, run, precheck=lambda: None):
        try:
            op = runner.start(verb, run, precheck)
        except BusyHere as busy:
            return JSONResponse({"refusal": refusal_json(busy.refusal), "running": busy.running.id}, 409)
        except Refused as r:
            return JSONResponse({"refusal": refusal_json(r)}, 422)
        return JSONResponse({"id": op.id}, 202)

    @app.post("/api/operations")
    def post_operation(req: OperationRequest):
        cfg = resolve()
        return start(req.verb, run_verb(req.verb, cfg, req.args), precheck_for(req.verb, cfg, req.args))

    @app.get("/api/operations/current")
    def current_operation():
        op = runner.current
        return Response(status_code=204) if op is None else op.summary()

    @app.get("/api/operations/{op_id}/events")
    async def operation_events(op_id: str):
        op = runner.get(op_id)
        if op is None:
            # §6.5: after a restart the page says the outcome is unknown.
            raise HTTPException(404, "unknown Operation")

        async def stream():
            sent = 0  # §6.5: replay the whole buffer, then continue live
            while True:
                while sent < len(op.events):
                    event, data = op.events[sent]
                    sent += 1
                    yield format_sse_event(event=event, data_str=json.dumps(data))
                    if event == "result":
                        return
                # shortcut: polls the in-memory buffer (not BLE); a Condition if it ever costs anything.
                await asyncio.sleep(0.1)

        return EventSourceResponse(stream())

    # --- §3.3 Configuration writes, §6.1 a `→ Pi` key is an Operation -------

    @app.post("/api/config/{key}")
    def config_set(key: str, body: ConfigWrite):
        cfg = resolve()
        written: list[Any] = []

        def write() -> None:
            conn = config.open_config_store(cfg_path)
            try:
                written.append(operations.write_config(conn, key, body.value))
            finally:
                conn.close()

        if not operations.is_pi_push(cfg, key):
            try:
                write()
            except Refused as r:
                return JSONResponse({"refusal": refusal_json(r)}, 422)
            return {"written": _json_value(written[0])}

        def run(op: Operation) -> tuple[dict, Outcome]:
            r = asyncio.run(operations.push_setting(resolve(), written[0], progress=op.progress))
            return _json_value(r), r.outcome

        # The write is the precheck: busy_here refuses before anything is written.
        return start("config_set", run, write)

    # --- §9.4 unit actions: Desktop-side, no BLE, not Operations ------------

    def unit_action(action) -> Any:
        try:
            operations.require_unit(run_fn=systemctl_fn)
        except Refused as r:
            return JSONResponse({"refusal": refusal_json(r)}, 422)
        r = asyncio.run(action(run_fn=systemctl_fn))
        if r.error is not None:
            return JSONResponse({"systemctl": r.error}, 502)
        return {"service": r.service}

    @app.post("/api/service/start")
    def service_start():
        return unit_action(operations.start_service)

    @app.post("/api/service/restart")
    def service_restart():
        return unit_action(operations.restart)

    # §10.1: one HTML page, client-side tabs, reload-stable routes.
    for route in ("/", "/config", "/setup"):
        app.add_api_route(route, lambda: FileResponse(STATIC_DIR / "index.html"), include_in_schema=False)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def serve(*, port: int, cfg_path: Path, cli_store_path: Optional[str] = None) -> int:
    import uvicorn

    # §5.1: a taken port fails loudly; there is no fallback port.
    uvicorn.run(create_app(port=port, cfg_path=cfg_path, cli_store_path=cli_store_path), host=HOST, port=port)
    return 0
