"""pi/receive.py's DB functions must be importable without bluezero (#45,
spec §11.3):

    `pi/receive.py`'s DB functions must be importable without bluezero for
    these to run — take the import of the BLE stack out of module scope, or
    guard it.

#44 rewrote `pi/receive.py` (new schema, two Payload shapes, the wipe, the
redraw floor, the Gauge state machine — spec §8) and moved every `bluezero`
import out of module scope and into the functions/classes that actually
need the BLE stack (`main()`, `ReceiveState.send_ack`), so `init_db` and the
other DB-only helpers import cleanly with no `bluezero` installed.
"""

import builtins
import sys
from pathlib import Path

PI_DIR = str((Path(__file__).parent.parent / "pi").resolve())


def test_receive_py_importable_without_bluezero(monkeypatch):
    """`import receive` must succeed even when `bluezero` is not installed.

    Simulates "bluezero not installed" by making any `import bluezero...`
    raise ImportError, then imports pi/receive.py fresh and checks it
    exposes DB-only functionality.
    """
    for name in list(sys.modules):
        if name == "bluezero" or name.startswith("bluezero."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.delitem(sys.modules, "receive", raising=False)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "bluezero" or name.startswith("bluezero."):
            raise ImportError(f"simulated: {name} is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    if PI_DIR not in sys.path:
        monkeypatch.syspath_prepend(PI_DIR)

    import receive  # noqa: F401 — pi.py is not otherwise on pythonpath.

    assert hasattr(receive, "init_db")
