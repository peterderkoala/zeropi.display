"""pi/epd-selftest.py must refuse to touch the panel while zeropi-display.service
owns it (spec-eink-rendering.md §10).

Importing `waveshare_epd` claims GPIO as a side effect (see
pi/waveshare_epd/README.md), so the service check has to run, and refuse,
*before* that import -- not after. These tests load the module fresh (it
isn't valid Python module syntax to `import` a hyphenated filename, hence
importlib) and never let a real `waveshare_epd` import happen: `_service_is_active`
is monkeypatched to simulate the collision, which is the only path that
doesn't require real panel hardware.
"""

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parent.parent / "pi" / "epd-selftest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("epd_selftest", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_service_is_active_true_when_systemctl_reports_active(monkeypatch):
    module = _load_module()

    class FakeResult:
        returncode = 0

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: FakeResult())
    assert module._service_is_active() is True


def test_service_is_active_false_when_systemctl_reports_inactive(monkeypatch):
    module = _load_module()

    class FakeResult:
        returncode = 3  # systemctl's "inactive" exit code

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: FakeResult())
    assert module._service_is_active() is False


def test_service_is_active_false_when_no_systemd(monkeypatch):
    module = _load_module()

    def raise_not_found(*a, **k):
        raise FileNotFoundError("no systemctl on this box")

    monkeypatch.setattr(module.subprocess, "run", raise_not_found)
    assert module._service_is_active() is False


def test_service_is_active_true_when_systemctl_hangs(monkeypatch):
    """A wedged systemd/D-Bus must not hang the self-test forever, and
    must not be read as "safe to proceed" -- treated the same as active.
    """
    module = _load_module()

    def raise_timeout(*a, **k):
        raise module.subprocess.TimeoutExpired(cmd="systemctl", timeout=10)

    monkeypatch.setattr(module.subprocess, "run", raise_timeout)
    assert module._service_is_active() is True


def test_main_refuses_without_importing_waveshare_epd_when_service_active(monkeypatch):
    """The regression this ticket exists to prevent: main() must return
    non-zero and never reach `from waveshare_epd import ...` while the
    service holds the panel -- proven here by there being no real
    waveshare_epd package on sys.path/pythonpath for the import to succeed
    against.
    """
    module = _load_module()
    monkeypatch.setattr(module, "_service_is_active", lambda: True)
    monkeypatch.setattr(sys, "argv", ["epd-selftest.py"])

    assert "waveshare_epd" not in sys.modules
    result = module.main()
    assert result == 1
    assert "waveshare_epd" not in sys.modules
