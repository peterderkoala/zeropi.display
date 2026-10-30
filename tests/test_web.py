"""Tests for desktop/web (#105): web-ui spec §12.2's guard and import-rule
tests, plus the read-only endpoints of §6.7. No BLE, no Pi, no browser."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import operations  # noqa: E402
import web  # noqa: E402
from test_cli import _cfg, _cfg_path  # noqa: E402

PORT = 8737
BASE = f"http://127.0.0.1:{PORT}"
FRAMING = {"content-security-policy": "frame-ancestors 'none'", "x-frame-options": "DENY"}


@pytest.fixture
def app(tmp_path):
    _cfg(tmp_path)
    started = time.monotonic()  # zeropi-push "started" after _cfg's writes
    app = web.create_app(
        port=PORT,
        cfg_path=_cfg_path(tmp_path),
        cli_store_path=str(tmp_path / "usage.db"),
        service_info_fn=lambda: operations.ServiceInfo(state="running", uptime_s=time.monotonic() - started),
    )

    @app.post("/api/_echo")
    async def echo() -> dict:
        return {"ok": True}

    return app


@pytest.fixture
def client(app):
    return TestClient(app, base_url=BASE)


def _assert_framed(resp):
    for name, value in FRAMING.items():
        assert resp.headers.get(name) == value, (name, resp.status_code)


# --- §8.2 the guard ---------------------------------------------------------


def test_cross_origin_form_post_is_403(client):
    resp = client.post("/api/_echo", data={"verb": "wipe"}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    _assert_framed(resp)


def test_json_post_with_mismatched_origin_is_403(client):
    resp = client.post("/api/_echo", json={}, headers={"Origin": "http://127.0.0.1:9999"})
    assert resp.status_code == 403


def test_non_json_post_is_403(client):
    resp = client.post("/api/_echo", content=b"{}", headers={"Content-Type": "text/plain"})
    assert resp.status_code == 403


def test_post_without_origin_is_accepted(client):
    assert client.post("/api/_echo", json={}).status_code == 200


@pytest.mark.parametrize("origin", [f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"])
def test_json_post_with_own_origin_is_accepted(client, origin):
    resp = client.post(
        "/api/_echo", content=b"{}", headers={"Origin": origin, "Content-Type": "application/json; charset=utf-8"}
    )
    assert resp.status_code == 200


@pytest.mark.parametrize("path", ["/api/desktop", "/api/operations/abc/events"])
def test_rebound_host_is_400(app, path):
    resp = TestClient(app, base_url=f"http://evil.example:{PORT}").get(path)
    assert resp.status_code == 400
    _assert_framed(resp)


@pytest.mark.parametrize("path", ["/", "/config", "/setup", "/api/desktop", "/api/config", "/api/status/last", "/nope"])
def test_every_response_is_unframeable(client, path):
    _assert_framed(client.get(path))


def test_no_cors_headers_and_no_preflight_answer(client):
    resp = client.options(
        "/api/_echo",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code != 200
    for r in (resp, client.get("/api/desktop", headers={"Origin": "https://evil.example"})):
        assert not any(h.startswith("access-control-") for h in r.headers)


# --- §6.7 read-only endpoints -----------------------------------------------


@pytest.mark.parametrize("path", ["/", "/config", "/setup"])
def test_page_shell_served_on_every_tab_route(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert '<script type="importmap">' in resp.text
    assert "https://" not in resp.text  # §3.2: nothing from a CDN


@pytest.mark.parametrize("name", ["preact.module.js", "hooks.module.js", "htm.module.js"])
def test_vendored_modules_are_served(client, name):
    resp = client.get(f"/static/vendor/{name}")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_vendor_readme_pins_each_file_by_sha256():
    import hashlib

    vendor = Path(web.__file__).parent / "static" / "vendor"
    readme = (vendor / "README.md").read_text()
    for js in vendor.glob("*.js"):
        assert hashlib.sha256(js.read_bytes()).hexdigest() in readme, js.name


def test_status_last_is_empty(client):
    assert client.get("/api/status/last").status_code == 204


def test_desktop_facts(client):
    body = client.get("/api/desktop").json()
    assert body.pop("service")["state"] == "running"
    assert body == {"seconds_since_last_batch": None, "readings_sent": 0, "pending_restart": []}


def test_config_lists_three_tiers_with_marks(client, tmp_path):
    import config

    conn = config.open_config_store(_cfg_path(tmp_path))
    try:
        config.write_config_value(conn, "usage.window_days", 5)
    finally:
        conn.close()

    body = client.get("/api/config").json()
    tier1 = {r["key"]: r for r in body["tier1"]}
    tier2 = {r["key"]: r for r in body["tier2"]}
    assert set(tier1) | set(tier2) == set(config.KEYS)
    assert tier2["usage.window_days"]["value"] == 5
    assert tier2["usage.window_days"]["pending_restart"] is True
    assert tier2[operations.PI_PROJECTED_SETTING]["to_pi"] is True
    assert tier2["usage.window_days"]["to_pi"] is False
    assert isinstance(tier1["paths.store"]["value"], str)
    assert {r["key"] for r in body["tier3"]} == set(operations.TIER3)
    assert client.get("/api/desktop").json()["pending_restart"] == ["usage.window_days"]


# --- §3.1 the import rule ---------------------------------------------------


def test_non_web_modules_import_without_fastapi():
    desktop = Path(__file__).parent.parent / "desktop"
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in {'fastapi', 'starlette', 'uvicorn', 'pydantic'}:\n"
        "            raise ImportError('hidden: ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {str(desktop)!r})\n"
        "import cli, push, service, operations\n"
        "assert not any(m.split('.')[0] in {'fastapi', 'starlette', 'uvicorn'} for m in sys.modules)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cli_web_parses_port():
    import cli

    assert cli.build_parser().parse_args(["web"]).port == PORT
    assert cli.build_parser().parse_args(["web", "--port", "9000"]).port == 9000
