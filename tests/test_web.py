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
    assert body == {
        "seconds_since_last_batch": None,
        "readings_sent": 0,
        "last_batch_at": None,
        "pending_restart": [],
        "pi_address": None,
        "short_id": None,
    }


def test_the_pi_one_liner_is_the_pi_role_of_the_same_bootstrap():
    assert operations.PI_INSTALL_ONE_LINER.endswith("| bash -s -- pi")
    assert operations.PI_INSTALL_ONE_LINER.rsplit(" ", 1)[0] == operations.DESKTOP_INSTALL_ONE_LINER.rsplit(" ", 1)[0]


def test_desktop_facts_carry_the_banner_state_and_the_short_id(paired):
    body = paired.get("/api/desktop").json()
    assert body["service"]["unit"] == "unknown"  # the fixture's ServiceInfo names none
    assert body["pi_address"] == "AA:BB:CC:DD:EE:FF" and body["short_id"] == "ddeeff"


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


# --- §6 Operations over HTTP ------------------------------------------------

import json  # noqa: E402
import threading  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import push  # noqa: E402
from operations import Outcome, Refused  # noqa: E402

CHECKED_AT = datetime(2026, 10, 30, 14, 4, tzinfo=timezone.utc)
PAIRED = {"pi.address": "AA:BB:CC:DD:EE:FF"}


@pytest.fixture
def paired_app(tmp_path):
    _cfg(tmp_path, **PAIRED)
    runs = _Systemctl()
    app = web.create_app(
        port=PORT,
        cfg_path=_cfg_path(tmp_path),
        cli_store_path=str(tmp_path / "usage.db"),
        service_info_fn=lambda: operations.ServiceInfo(state="running", uptime_s=1.0),
        systemctl_fn=runs,
        now_fn=lambda: CHECKED_AT,
    )
    app.state.systemctl = runs
    return app


@pytest.fixture
def paired(paired_app):
    return TestClient(paired_app, base_url=BASE)


class _Systemctl:
    def __init__(self, load_state="loaded", returncode=0, stderr=""):
        self.load_state, self.returncode, self.stderr = load_state, returncode, stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if "show" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=f"{self.load_state}\n", stderr="")
        return subprocess.CompletedProcess(argv, self.returncode, stdout="", stderr=self.stderr)


def _fake(monkeypatch, name, result, *, gate=None, calls=None):
    """Replaces `operations.<name>` with an async fake that reports one
    progress line, optionally blocks on `gate`, and returns `result`."""

    async def fake(*a, progress=push.print_progress, **kw):
        if calls is not None:
            calls.append((a, kw))
        progress("scanning", "Scanning (up to 10s)…")
        if gate is not None:
            gate.wait(5)
        return result

    monkeypatch.setattr(operations, name, fake)


def _events(client, op_id):
    body = client.get(f"/api/operations/{op_id}/events").text
    out = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        out.append((fields["event"], json.loads(fields["data"])))
    return out


def _finish(client, op_id):
    return _events(client, op_id)[-1][1]


def _push_ok():
    return operations.PushResult(push.BatchResult(sent=3), None, 3600)


def _fake_status(status, outcome, reach="reachable"):
    import verdict

    return SimpleNamespace(
        status=status,
        outcome=outcome,
        verdict=SimpleNamespace(advice=None),
        reach=verdict.Reach(reach),
        error_detail=None,
        round_trip_s=1.0,
        facts=SimpleNamespace(seconds_since_last_push=None),
    )


def _status_ok(monkeypatch):
    _fake(monkeypatch, "status", _fake_status({"uptime_s": 5}, Outcome.OK))
    monkeypatch.setattr(operations, "status_json", lambda r, s: {"verdict": {"ok": True}})


def test_post_returns_202_with_a_handle_that_has_already_started(paired, monkeypatch):
    gate, calls = threading.Event(), []
    _fake(monkeypatch, "push_batch", _push_ok(), gate=gate, calls=calls)

    resp = paired.post("/api/operations", json={"verb": "push"})

    assert resp.status_code == 202
    op_id = resp.json()["id"]
    current = paired.get("/api/operations/current").json()
    assert current["id"] == op_id and current["running"] is True
    gate.set()
    assert _finish(paired, op_id)["outcome"] == "ok"
    assert len(calls) == 1


def test_second_post_while_one_runs_is_busy_here_and_the_fake_ran_once(paired, monkeypatch):
    gate, calls = threading.Event(), []
    _fake(monkeypatch, "push_batch", _push_ok(), gate=gate, calls=calls)
    first = paired.post("/api/operations", json={"verb": "push"}).json()["id"]

    resp = paired.post("/api/operations", json={"verb": "push"})

    assert resp.status_code == 409
    assert resp.json()["refusal"]["kind"] == "busy_here"
    assert resp.json()["running"] == first
    gate.set()
    _finish(paired, first)
    assert len(calls) == 1


def test_pre_ble_refusal_is_422_and_creates_no_operation(paired, monkeypatch):
    _fake(monkeypatch, "push_batch", _push_ok())
    done = paired.post("/api/operations", json={"verb": "push"}).json()["id"]
    _finish(paired, done)

    resp = paired.post("/api/operations", json={"verb": "wipe", "args": {"confirm": "aabbcc"}})

    assert resp.status_code == 422
    refusal = resp.json()["refusal"]
    assert refusal == {"kind": "confirm_mismatch", "message": "Confirmation did not match. Nothing was sent.", "rationale": None}
    assert paired.get("/api/operations/current").json()["id"] == done


def test_not_paired_is_refused_before_any_operation(client, monkeypatch):
    _fake(monkeypatch, "redraw", None)
    resp = client.post("/api/operations", json={"verb": "redraw"})
    assert resp.status_code == 422 and resp.json()["refusal"]["kind"] == "not_paired"
    assert client.get("/api/operations/current").status_code == 204


def test_events_replay_the_buffer_to_a_late_subscriber_then_one_result(paired, monkeypatch):
    _fake(monkeypatch, "push_batch", _push_ok())
    op_id = paired.post("/api/operations", json={"verb": "push"}).json()["id"]
    _finish(paired, op_id)

    events = _events(paired, op_id)  # subscribing after it finished

    assert events[0] == ("progress", {"kind": "scanning", "text": "Scanning (up to 10s)…"})
    assert [e for e, _ in events].count("result") == 1 and events[-1][0] == "result"
    result = events[-1][1]
    assert result["outcome"] == "ok" and result["batch"]["sent"] == 3


def test_unknown_operation_id_is_404(paired):
    assert paired.get("/api/operations/nope/events").status_code == 404


def test_wipe_reports_each_phase(paired, monkeypatch):
    wiped = operations.WipeResult(operations.CommandResult({"status": "ok"}, None), repush_busy="link busy")
    _fake(monkeypatch, "wipe", wiped)
    op_id = paired.post("/api/operations", json={"verb": "wipe", "args": {"confirm": "ddeeff"}}).json()["id"]
    result = _finish(paired, op_id)
    assert result["phases"] == {"command": "ok", "repush": "busy"}
    assert result["outcome"] == "cant_tell"


def test_a_refusal_raised_inside_the_operation_is_its_result(paired, monkeypatch):
    async def refuses(*a, **kw):
        raise Refused("not_paired", "Not paired — no Pi address is recorded.")

    monkeypatch.setattr(operations, "push_batch", refuses)
    op_id = paired.post("/api/operations", json={"verb": "push"}).json()["id"]
    result = _finish(paired, op_id)
    assert result["refusal"]["kind"] == "not_paired" and result["outcome"] == "cant_tell"


# --- §7.2 the last-status slot ----------------------------------------------


def test_status_fills_the_slot_with_the_desktop_clock(paired, monkeypatch):
    _status_ok(monkeypatch)
    _finish(paired, paired.post("/api/operations", json={"verb": "status"}).json()["id"])

    slot = paired.get("/api/status/last").json()
    assert slot["result"]["verdict"] == {"ok": True} and slot["result"]["outcome"] == "ok"
    assert slot["checked_at"] == CHECKED_AT.isoformat()
    assert slot["outdated_by"] is None


def test_slot_survives_a_later_push_and_is_labelled_out_of_date(paired, monkeypatch):
    _status_ok(monkeypatch)
    _finish(paired, paired.post("/api/operations", json={"verb": "status"}).json()["id"])
    _fake(monkeypatch, "push_batch", _push_ok())
    _finish(paired, paired.post("/api/operations", json={"verb": "push"}).json()["id"])

    slot = paired.get("/api/status/last").json()
    assert slot["result"]["verdict"] == {"ok": True}
    assert slot["outdated_by"] == {"verb": "push", "at": CHECKED_AT.isoformat()}


def test_a_status_with_no_reply_empties_the_slot(paired, monkeypatch):
    _status_ok(monkeypatch)
    _finish(paired, paired.post("/api/operations", json={"verb": "status"}).json()["id"])
    _fake(monkeypatch, "status", _fake_status(None, Outcome.CANT_TELL, "absent"))
    monkeypatch.setattr(operations, "status_json", lambda r, s: {"verdict": {"ok": None}})
    _finish(paired, paired.post("/api/operations", json={"verb": "status"}).json()["id"])
    assert paired.get("/api/status/last").status_code == 204


# --- §3.3 Configuration over HTTP -------------------------------------------


def test_config_value_passes_through_as_a_string_with_configs_refusal(paired, tmp_path):
    import config

    conn = config.open_config_store(_cfg_path(tmp_path))
    try:
        with pytest.raises(Refused) as expected:
            operations.write_config(conn, "usage.window_days", "99")
    finally:
        conn.close()

    resp = paired.post("/api/config/usage.window_days", json={"value": "99"})

    assert resp.status_code == 422
    assert resp.json()["refusal"] == {
        "kind": "out_of_range",
        "message": expected.value.message,
        "rationale": expected.value.rationale,
    }
    ok = paired.post("/api/config/usage.window_days", json={"value": "7"})
    assert ok.status_code == 200 and ok.json() == {"written": 7}


def test_config_value_must_be_a_string(paired):
    resp = paired.post("/api/config/usage.window_days", json={"value": 7})
    assert resp.status_code == 422 and resp.json()["refusal"]["kind"] == "bad_request"


def test_a_to_pi_key_is_written_then_sent_as_an_operation(paired, monkeypatch, tmp_path):
    calls = []
    _fake(monkeypatch, "push_setting", operations.SettingPushResult(operations.PI_PROJECTED_SETTING, 7200, None), calls=calls)

    resp = paired.post(f"/api/config/{operations.PI_PROJECTED_SETTING}", json={"value": "7200"})

    assert resp.status_code == 202
    result = _finish(paired, resp.json()["id"])
    assert result["outcome"] == "ok" and calls[0][0][1] == 7200


# --- §9.4 unit actions ------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/service/start", "/api/service/restart"])
def test_unit_missing_is_refused(paired_app, path):
    paired_app.state.systemctl.load_state = "not-found"
    resp = TestClient(paired_app, base_url=BASE).post(path, json={})
    assert resp.status_code == 422 and resp.json()["refusal"]["kind"] == "unit_missing"


def test_service_start_enables_it_now(paired_app):
    resp = TestClient(paired_app, base_url=BASE).post("/api/service/start", json={})
    assert resp.status_code == 200
    assert paired_app.state.systemctl.calls[-1][2:4] == ["enable", "--now"]


def test_systemctl_error_text_is_rendered_verbatim(paired_app):
    paired_app.state.systemctl.returncode, paired_app.state.systemctl.stderr = 1, "Job failed. See journal.\n"
    resp = TestClient(paired_app, base_url=BASE).post("/api/service/restart", json={})
    assert resp.status_code == 502 and resp.json() == {"systemctl": "Job failed. See journal."}


def test_a_wipe_whose_repush_was_busy_still_outdates_the_check(paired, monkeypatch):
    _status_ok(monkeypatch)
    _finish(paired, paired.post("/api/operations", json={"verb": "status"}).json()["id"])
    wiped = operations.WipeResult(operations.CommandResult({"status": "ok"}, None), repush_busy="link busy")
    _fake(monkeypatch, "wipe", wiped)
    _finish(paired, paired.post("/api/operations", json={"verb": "wipe", "args": {"confirm": "ddeeff"}}).json()["id"])
    assert paired.get("/api/status/last").json()["outdated_by"]["verb"] == "wipe"


def test_the_previous_operation_still_replays_while_the_next_runs(paired, monkeypatch):
    _fake(monkeypatch, "push_batch", _push_ok())
    first = paired.post("/api/operations", json={"verb": "push"}).json()["id"]
    _finish(paired, first)
    gate = threading.Event()
    _fake(monkeypatch, "redraw", operations.CommandResult({"status": "ok", "drawn": True}, None), gate=gate)
    paired.post("/api/operations", json={"verb": "redraw"})
    try:
        assert _finish(paired, first)["outcome"] == "ok"
    finally:
        gate.set()


def test_a_non_string_pair_address_is_refused(paired, monkeypatch):
    _fake(monkeypatch, "pair", None)
    resp = paired.post("/api/operations", json={"verb": "pair", "args": {"address": ["x"]}})
    assert resp.status_code == 422 and resp.json()["refusal"]["kind"] == "bad_request"


def test_a_wipe_repush_with_no_pi_is_absent_not_fault(paired, monkeypatch):
    repush = push.BatchResult(failed=2, unreachable="no reply from paired Pi")
    _fake(monkeypatch, "wipe", operations.WipeResult(operations.CommandResult({"status": "ok"}, None), repush))
    op_id = paired.post("/api/operations", json={"verb": "wipe", "args": {"confirm": "ddeeff"}}).json()["id"]
    assert _finish(paired, op_id)["phases"] == {"command": "ok", "repush": "absent"}


def test_status_result_carries_what_the_page_shows_beside_the_json(paired, monkeypatch):
    import verdict

    facts = verdict.DesktopFacts("AA:BB:CC:DD:EE:FF", 3, "2026-09-01", 3600, 4000.0)
    v = verdict.build_verdict(facts, None, verdict.Reach.ABSENT)
    r = operations.StatusResult(v, facts, None, None, "no Pi advertising within 10.0s", verdict.Reach.ABSENT)
    _fake(monkeypatch, "status", r)

    result = _finish(paired, paired.post("/api/operations", json={"verb": "status"}).json()["id"])

    assert result["unreachable"] == {"reason": "absent", "detail": "no Pi advertising within 10.0s"}
    assert result["advice"] == v.advice
    assert result["seconds_since_last_push"] == 4000.0 and result["round_trip_s"] is None
    assert result["checked_at"] == CHECKED_AT.isoformat()


# --- result summaries: the page's one line per result, pinned here (§12.4) ---


def _result_of(paired, monkeypatch, verb, result, args=None):
    _fake(monkeypatch, {"push": "push_batch"}.get(verb, verb), result)
    body = {"verb": verb, "args": args or {}}
    return _finish(paired, paired.post("/api/operations", json=body).json()["id"])


def test_summary_of_a_partial_wipe(paired, monkeypatch):
    wiped = operations.WipeResult(operations.CommandResult({"status": "ok"}, None), repush_busy="link busy")
    r = _result_of(paired, monkeypatch, "wipe", wiped, {"confirm": "ddeeff"})
    assert r["summary"] == "Wiped, but could not re-push yet — link busy. Run push once the link is free."


def test_summary_of_an_unreachable_push_uses_the_clis_words(paired, monkeypatch):
    import verdict

    u = operations.Unreachable(verdict.Reach.BUSY, "lock held")
    r = _result_of(paired, monkeypatch, "push", operations.PushResult(None, u, 3600))
    assert r["summary"] == "Refused — the link is busy (lock held). Not queued. Retry in a moment — a Batch takes about two minutes."


def test_summary_of_a_redraw_held_by_the_floor(paired, monkeypatch):
    ack = {"status": "ok", "drawn": False, "floor_remaining_s": 143}
    r = _result_of(paired, monkeypatch, "redraw", operations.CommandResult(ack, None))
    assert r["summary"] == "Queued on the Pi. It will draw in 2m 23s."


def test_summary_of_a_setting_that_did_not_reach_the_pi_says_it_was_written(paired, monkeypatch):
    failed = operations.SettingPushResult(operations.PI_PROJECTED_SETTING, 7200, "no Pi")
    _fake(monkeypatch, "push_setting", failed)
    resp = paired.post(f"/api/config/{operations.PI_PROJECTED_SETTING}", json={"value": "7200"})
    r = _finish(paired, resp.json()["id"])
    assert r["summary"] == (
        f"Set {operations.PI_PROJECTED_SETTING} = 7200. Could not push it to the Pi yet — no Pi. "
        "It will apply on the next successful connection (§7.2)."
    )


def test_summary_of_a_refusal_inside_an_operation_is_its_message(paired, monkeypatch):
    async def refuses(*a, **kw):
        raise Refused("not_paired", "Not paired — no Pi address is recorded.")

    monkeypatch.setattr(operations, "push_batch", refuses)
    r = _finish(paired, paired.post("/api/operations", json={"verb": "push"}).json()["id"])
    assert r["summary"] == "Not paired — no Pi address is recorded."


def _found(*addresses):
    return operations.DiscoverResult(tuple(operations.FoundPi("zeropi-display", a, -60) for a in addresses), None)


def test_summary_of_a_discover_that_found_no_pi_gives_the_pi_one_liner(paired, monkeypatch):
    r = _result_of(paired, monkeypatch, "discover", _found())
    assert r["summary"] == f"No Pi found. If it has not been set up yet, run this on the Pi: {operations.PI_INSTALL_ONE_LINER}"


def test_summary_of_a_discover_with_one_or_several_pis(paired, monkeypatch):
    assert _result_of(paired, monkeypatch, "discover", _found("A"))["summary"] == "One Pi found."
    assert _result_of(paired, monkeypatch, "discover", _found("A", "B"))["summary"] == "2 Pis found. Choose one."
