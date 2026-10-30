"""Desktop: the web UI of the Management Surface (web-ui spec §3, §5.1, §6.7).

`python desktop/cli.py web [--port]`: FastAPI on `127.0.0.1`, hardcoded. A
skin over `operations.py`, never a second surface. The only module allowed
to import `fastapi` (§3.1); `cli.py` imports it lazily.

Only the config store's path (and `--store`) is fixed at startup; values
are resolved per request (§4.6). Handlers are plain `def`, so FastAPI runs
their blocking store reads and `systemctl` on its thread pool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
import operations
import usage
from web import guard

HOST = "127.0.0.1"  # §5.1: no --host flag
STATIC_DIR = Path(__file__).parent / "static"


def _json_value(value: Any) -> Any:
    return str(value) if isinstance(value, Path) else value


def create_app(
    *,
    port: int,
    cfg_path: Path,
    cli_store_path: Optional[str] = None,
    service_info_fn: Callable[[], operations.ServiceInfo] = operations.service_info,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    guard.install(app, port)
    app.state.last_status = None  # §7.2's slot; filled by #106

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
