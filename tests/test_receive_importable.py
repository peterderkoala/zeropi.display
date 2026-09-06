"""pi/receive.py's DB functions must be importable without bluezero (#45,
spec §11.3):

    `pi/receive.py`'s DB functions must be importable without bluezero for
    these to run — take the import of the BLE stack out of module scope, or
    guard it.

`pi/receive.py` is being rewritten in parallel by #44 (new schema, two
Payload shapes, the wipe, the redraw floor, the Gauge state machine — see
spec §8). Deliberately NOT touched here: hand-editing the same file from two
concurrent worktrees risks exactly the merge collision spec §10's trap 12
warns about, and this ticket's own instructions say it is fine to leave this
as a documented, currently-skipped requirement instead.

TODO(#44): once receive.py is rewritten, its `bluezero` import(s) must move
out of module scope (guarded in a `try/except ImportError` or imported
lazily inside the functions/classes that actually need the BLE stack — e.g.
`main()`, `ReceiveState.send_ack`), so that `init_db`, the upsert function
and the other DB-only helpers can be imported and unit-tested in an
environment with no `bluezero` installed (which is the norm for this
project's dev/CI machines — only the Pi itself has it). When that's done,
remove the `pytest.mark.skip` below; the test as written already asserts the
right thing.
"""

import builtins
import sys
from pathlib import Path

import pytest

PI_DIR = str((Path(__file__).parent.parent / "pi").resolve())


def test_receive_py_importable_without_bluezero(monkeypatch):
    """`import receive` must succeed even when `bluezero` is not installed.

    Simulates "bluezero not installed" by making any `import bluezero...`
    raise ImportError, then imports pi/receive.py fresh and checks it
    exposes DB-only functionality. This currently fails because
    pi/receive.py imports `bluezero.adapter`, `.async_tools`, `.device` and
    `.peripheral` at module scope (see the top of the file) — see the
    TODO(#44) above for what needs to change.
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


test_receive_py_importable_without_bluezero = pytest.mark.skip(
    reason=(
        "pi/receive.py imports bluezero at module scope; #44 (parallel "
        "rewrite of this file) owns moving/guarding that import. See this "
        "test's docstring for the exact change needed."
    )
)(test_receive_py_importable_without_bluezero)
