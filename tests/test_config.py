"""Tests for desktop/config.py (#81): the Configuration store and the Tier
schema (spec §3, §4, tested per §10.1/§10.2).

Pure filesystem work against `tmp_path` — no fixtures beyond a scratch file,
no `~/.claude`, no panel, no bluezero.
"""

from __future__ import annotations

import sqlite3

import pytest

import config


# ---------------------------------------------------------------------------
# §10.1 The Configuration store
# ---------------------------------------------------------------------------


def test_missing_store_is_created_with_defaults(tmp_path):
    path = tmp_path / "config.db"
    assert not path.exists()

    conn = config.open_config_store(path)
    try:
        assert path.exists()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == config.CONFIG_USER_VERSION
        assert config.read_config(conn) == {}
    finally:
        conn.close()

    cfg = config.resolve(cli_config_path=str(path), env={})
    assert cfg.usage_window_days == config.KEYS["usage.window_days"].default
    assert cfg.pi_address is None


def test_corrupt_store_fails_closed(tmp_path):
    path = tmp_path / "config.db"
    path.write_bytes(b"not a sqlite database at all")

    with pytest.raises(config.ConfigVersionError):
        config.open_config_store(path)


def test_user_version_mismatch_fails_closed(tmp_path):
    path = tmp_path / "config.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(config.SCHEMA_SQL)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    with pytest.raises(config.ConfigVersionError, match="user_version=2"):
        config.open_config_store(path)


def test_out_of_range_value_is_rejected_and_nothing_is_written(tmp_path):
    conn = config.open_config_store(tmp_path / "config.db")
    try:
        with pytest.raises(config.ConfigValueError):
            config.write_config_value(conn, "usage.window_days", 999)
        assert conn.execute("SELECT COUNT(*) FROM config").fetchone()[0] == 0
    finally:
        conn.close()


def test_unknown_key_is_rejected_on_write(tmp_path):
    conn = config.open_config_store(tmp_path / "config.db")
    try:
        with pytest.raises(config.UnknownKeyError):
            config.write_config_value(conn, "not.a.real.key", 1)
        assert conn.execute("SELECT COUNT(*) FROM config").fetchone()[0] == 0
    finally:
        conn.close()


def test_unknown_key_already_in_store_is_ignored_on_read_and_warns(tmp_path):
    conn = config.open_config_store(tmp_path / "config.db")
    try:
        conn.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            ("retired.setting", "1", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        with pytest.warns(UserWarning, match="retired.setting"):
            values = config.read_config(conn)
        assert "retired.setting" not in values
    finally:
        conn.close()


def test_precedence_resolves_cli_over_env_over_store_over_default(tmp_path):
    store_path = tmp_path / "config.db"

    # Default.
    cfg = config.resolve(cli_config_path=str(store_path), env={})
    assert cfg.paths_store == config.KEYS["paths.store"].default

    # Configuration.
    conn = config.open_config_store(store_path)
    try:
        config.write_config_value(conn, "paths.store", "/from/store.db")
    finally:
        conn.close()
    cfg = config.resolve(cli_config_path=str(store_path), env={})
    assert str(cfg.paths_store) == "/from/store.db"

    # Environment variable beats Configuration.
    cfg = config.resolve(
        cli_config_path=str(store_path), env={"ZEROPI_USAGE_STORE": "/from/env.db"}
    )
    assert str(cfg.paths_store) == "/from/env.db"

    # CLI flag beats everything.
    cfg = config.resolve(
        cli_config_path=str(store_path),
        cli_store_path="/from/cli.db",
        env={"ZEROPI_USAGE_STORE": "/from/env.db"},
    )
    assert str(cfg.paths_store) == "/from/cli.db"


def test_resolve_config_path_bootstrap_exception(tmp_path, monkeypatch):
    monkeypatch.delenv("ZEROPI_CONFIG", raising=False)
    assert config.resolve_config_path(None, env={}) == config.DEFAULT_CONFIG_PATH
    assert config.resolve_config_path(None, env={"ZEROPI_CONFIG": "/x/env-config.db"}) == config.Path(
        "/x/env-config.db"
    )
    assert config.resolve_config_path("/x/cli-config.db", env={"ZEROPI_CONFIG": "/x/env-config.db"}) == config.Path(
        "/x/cli-config.db"
    )


# ---------------------------------------------------------------------------
# §10.2 The schema is table-driven, and its derived bounds are tested
# ---------------------------------------------------------------------------


def test_no_key_is_tier_3():
    assert all(key.tier in (1, 2) for key in config.KEYS.values())


def test_gauge_throttle_ceiling_is_derived_not_a_literal():
    # A test asserting the literal 150.0 would pass while the invariant
    # rotted (spec §10.2) — assert the *relationship* survives.
    assert config.KEYS["service.gauge_throttle_s"].hi == config.GAUGE_EXPIRY_S / 2
    assert config.KEYS["service.poll_interval_s"].hi == config.GAUGE_EXPIRY_S / 2


def test_stale_threshold_ceiling_is_derived_not_a_literal():
    assert config.KEYS["gauge.stale_threshold_s"].hi == config.GAUGE_EXPIRY_S


@pytest.mark.parametrize(
    "name,value",
    [
        ("service.poll_interval_s", 5.0),
        ("service.poll_interval_s", 150.0),
        ("service.gauge_throttle_s", 30.0),
        ("service.gauge_throttle_s", 150.0),
        ("gauge.stale_threshold_s", 60),
        ("gauge.stale_threshold_s", 300),
        ("batch.catchup_threshold_s", 3600),
        ("batch.catchup_threshold_s", 604800),
        ("usage.window_days", 5),
        ("usage.window_days", 30),
        ("push.scan_timeout_s", 1.0),
        ("push.scan_timeout_s", 60.0),
        ("push.ack_timeout_s", 1.0),
        ("push.ack_timeout_s", 60.0),
        ("pi.idle_keepalive_s", 3600),
        ("pi.idle_keepalive_s", 604800),
        ("batch.scheduled_hour", 0),
        ("batch.scheduled_hour", 23),
        ("pricing.web_search_usd_per_request", 0.0),
    ],
)
def test_boundary_values_are_accepted(tmp_path, name, value):
    conn = config.open_config_store(tmp_path / "config.db")
    try:
        config.write_config_value(conn, name, value)  # must not raise
        [(stored,)] = conn.execute("SELECT value FROM config WHERE key = ?", (name,)).fetchall()
        assert config.KEYS[name].parse(stored) == value
    finally:
        conn.close()


@pytest.mark.parametrize(
    "name,value",
    [
        ("service.poll_interval_s", 4.999),
        ("service.poll_interval_s", 150.001),
        ("service.gauge_throttle_s", 29.999),
        ("service.gauge_throttle_s", 150.001),
        ("gauge.stale_threshold_s", 59),
        ("gauge.stale_threshold_s", 301),
        ("batch.catchup_threshold_s", 3599),
        ("batch.catchup_threshold_s", 604801),
        ("usage.window_days", 4),
        ("usage.window_days", 31),
        ("push.scan_timeout_s", 0.999),
        ("push.scan_timeout_s", 60.001),
        ("push.ack_timeout_s", 0.999),
        ("push.ack_timeout_s", 60.001),
        ("pi.idle_keepalive_s", 3599),
        ("pi.idle_keepalive_s", 604801),
        ("batch.scheduled_hour", -1),
        ("batch.scheduled_hour", 24),
        ("pricing.web_search_usd_per_request", -0.001),
    ],
)
def test_out_of_range_boundary_values_are_rejected(tmp_path, name, value):
    conn = config.open_config_store(tmp_path / "config.db")
    try:
        with pytest.raises(config.ConfigValueError):
            config.write_config_value(conn, name, value)
        assert conn.execute("SELECT COUNT(*) FROM config").fetchone()[0] == 0
    finally:
        conn.close()


def test_pricing_overlay_must_be_a_dict(tmp_path):
    conn = config.open_config_store(tmp_path / "config.db")
    try:
        config.write_config_value(conn, "pricing.overlay", {"claude-x": {"in": 1.0}})
        with pytest.raises(config.ConfigValueError):
            config.write_config_value(conn, "pricing.overlay", [1, 2, 3])
    finally:
        conn.close()


def test_pricing_free_models_must_be_a_list(tmp_path):
    conn = config.open_config_store(tmp_path / "config.db")
    try:
        config.write_config_value(conn, "pricing.free_models", ["some-model"])
        with pytest.raises(config.ConfigValueError):
            config.write_config_value(conn, "pricing.free_models", {"a": 1})
    finally:
        conn.close()


def test_pi_address_accepts_valid_mac_and_null(tmp_path):
    conn = config.open_config_store(tmp_path / "config.db")
    try:
        config.write_config_value(conn, "pi.address", "AA:BB:CC:DD:EE:FF")
        assert config.read_config(conn)["pi.address"] == "AA:BB:CC:DD:EE:FF"
        config.write_config_value(conn, "pi.address", None)
        assert config.read_config(conn)["pi.address"] is None
    finally:
        conn.close()


@pytest.mark.parametrize("bad", ["not-a-mac", "AA:BB:CC:DD:EE", "AA:BB:CC:DD:EE:GG", "AABBCCDDEEFF"])
def test_pi_address_rejects_malformed_values(tmp_path, bad):
    conn = config.open_config_store(tmp_path / "config.db")
    try:
        with pytest.raises(config.ConfigValueError):
            config.write_config_value(conn, "pi.address", bad)
    finally:
        conn.close()


def test_value_that_fails_to_coerce_is_rejected_same_as_out_of_range(tmp_path):
    conn = config.open_config_store(tmp_path / "config.db")
    try:
        with pytest.raises(config.ConfigValueError):
            config.write_config_value(conn, "usage.window_days", "not a number")
        assert conn.execute("SELECT COUNT(*) FROM config").fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# §4.6 pi.address — the 18th key
# ---------------------------------------------------------------------------


def test_pi_address_is_tier_1_type_address_default_null():
    key = config.KEYS["pi.address"]
    assert key.tier == 1
    assert key.kind == "address"
    assert key.default is None


def test_config_has_eighteen_keys_ten_tier_1_eight_tier_2():
    tier1 = [k for k in config.KEYS.values() if k.tier == 1]
    tier2 = [k for k in config.KEYS.values() if k.tier == 2]
    assert len(config.KEYS) == 18
    assert len(tier1) == 10
    assert len(tier2) == 8
