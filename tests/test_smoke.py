"""Smoke test for the pytest harness itself (#45).

This does not assert anything about `usage.py`, `gauge.py` or
`pi/receive.py`'s real logic — those modules/behaviours don't exist yet in
this worktree (they land with #42/#43/#44). Its only job is to prove the
harness works: `pytest.ini`'s `pythonpath = desktop` resolves, and the
synthetic JSONL fixtures under `tests/fixtures/` are present and well-formed
JSON, one object per line.
"""

import json
import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "claude_projects"


def test_pytest_runs():
    assert 1 + 1 == 2


def test_desktop_is_on_pythonpath():
    # pytest.ini sets `pythonpath = desktop`, so `import usage` / `import
    # gauge` will resolve once those modules land (#42/#43). Neither exists
    # yet in this worktree, and desktop/push.py imports bleak at module
    # scope (tests must run with no BLE, spec §11.1), so this only checks
    # the path wiring itself rather than importing a real module.
    desktop_dir = (Path(__file__).parent.parent / "desktop").resolve()
    assert str(desktop_dir) in sys.path


def test_fixture_directory_exists():
    assert FIXTURES_DIR.is_dir()


def test_fixture_jsonl_files_are_well_formed():
    jsonl_files = sorted(FIXTURES_DIR.glob("**/*.jsonl"))
    assert jsonl_files, "expected at least one synthetic JSONL fixture file"
    for path in jsonl_files:
        with path.open() as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                assert isinstance(obj, dict), f"{path}:{lineno} is not a JSON object"


def test_fixture_project_directories_encode_their_paths():
    # Spec §4.3: encode(path) = re.sub(r"[^a-zA-Z0-9]", "-", path). Each
    # fixture project directory name must be the encoding of the fictional
    # project root named in tests/fixtures/README.md.
    import re

    def encode(path: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]", "-", path)

    roots = {
        "-home-tester-code-zeropi-fixture": "/home/tester/code/zeropi-fixture",
        "-home-tester-code-zeropi-allzero": "/home/tester/code/zeropi-allzero",
        "-home-tester-code-myproj": "/home/tester/code/myproj",
        "-home-tester-code-zeropi-append": "/home/tester/code/zeropi-append",
    }
    for dirname, root in roots.items():
        assert (FIXTURES_DIR / dirname).is_dir(), f"missing fixture dir {dirname}"
        assert encode(root) == dirname
