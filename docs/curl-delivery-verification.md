# Curl delivery verification (2026-09-06)

Hardware verification of the curl-delivered install path (`install.sh` +
`pi/install-pi.sh` + `desktop/install-desktop.sh`) for ticket
[#35](https://github.com/peterderkoala/zeropi.display/issues/35), the last
open ticket on map [#7](https://github.com/peterderkoala/zeropi.display/issues/7).
`docs/provisioning-verification.md` verified the hand-run `install.sh`
(scp'd to the Pi); this verifies the **documented one-liner** end to end,
both roles.

**Result: pass, after fixing two real defects in the bootstrap that only a
genuinely non-interactive run could surface.** Neither defect was visible
in `docs/provisioning-verification.md`'s verification because that ran the
script from an interactive shell over a real terminal.

## Target

Same dev Pi as the prior verification (`192.168.4.108`, Debian 13 trixie,
Python 3.13.5, aarch64, BlueZ `5.82-1.1+rpt2`), torn down before each
from-scratch run using the same procedure as
[#11](https://github.com/peterderkoala/zeropi.display/issues/11). Desktop
role tested on the maintainer's own dev machine (Linux), both in-place
(the tracked clone) and standalone (run from `/tmp`, outside any clone, to
exercise the "no clone" code path) — **no second physical machine was
available**, so cross-machine and cross-OS behavior is unverified; see
below.

**Automation note**: the driving session had no interactive terminal, so
the documented one-liner's `sudo` prompt (for the `pi` role) had no tty to
read a password from. A temporary `NOPASSWD` sudoers entry for the `pi`
user (`/etc/sudoers.d/99-verification-temp`) was added for the duration of
this verification and removed afterward — not something the installer
itself needs or configures.

**Shared-hardware note**: the dev Pi is shared with other concurrent
sessions. A different session was doing unrelated e-ink driver work
(`/home/pi/epd-bench`, `python3-pil`) during part of this verification;
coordinated directly and confirmed no collision — see the ticket's
resolution comment for detail.

## Defects found and fixed

Both are in `install.sh`'s pi-role sudo re-exec and `install-desktop.sh`'s
mode detection — non-interactive-automation and cwd-detection bugs, not
provisioning-logic bugs.

### 1. `[[ -e /dev/tty ]]` is true with no controlling terminal

`install.sh`'s non-root branch decides how to invoke `sudo` by testing
`[[ -e /dev/tty ]]`. That test is true even when the process has no
controlling terminal at all — `/dev/tty` is a device alias that always
exists as a node — so a plain `ssh host 'curl ... | bash -s -- pi'` (no
`-t`) took the "prompt on /dev/tty" branch and crashed:

```
bash: line 108: /dev/tty: No such device or address
```

— even with passwordless sudo already configured, which the script's own
error message names as the intended fallback for exactly this case.

**Fix** (`370129a`, `fdf9192`): replaced the existence check with an
open/close probe run in a subshell:

```bash
tty_usable() { ( exec 3</dev/tty ) 2>/dev/null; }
```

The subshell matters: an unqualified `exec 3</dev/tty` in the current
shell is the right way to *test and then hold* a real tty, but doing the
open/close probe that way would leave a stray fd (and, if a stderr clause
were ever added carelessly, a permanently redirected stderr) on success.
A first version of this fix ran the probe inline and, although it worked,
leaked a bash error line to real stderr on the no-tty path — caught on a
second hardware run and fixed by wrapping the probe in `( )`.

### 2. Desktop in-place detection could never fire via the curl bootstrap

`install-desktop.sh` detected an existing clone by checking
`$REPO_ROOT/.git`, where `$REPO_ROOT` is derived from the script's own
`BASH_SOURCE`. Under the curl bootstrap the running copy always lives in a
`/tmp` staging unpack of a tarball — which never carries `.git` (that's
the whole reason the bootstrap uses a tarball, see map #7's Delivery
shape) — so this check could **never** find a clone via the one documented
invocation path. Running `curl ... | bash -s -- desktop` from inside the
tracked clone silently installed *standalone* instead, defeating the
"auto-detects a surrounding clone" design map #7 specifies.

**Fix** (`e91e623`): detect from the invoking shell's `$PWD` instead,
which survives `curl ... | bash` unchanged (bash inherits the caller's
cwd):

```bash
CWD_REPO_ROOT="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$CWD_REPO_ROOT" && -e "$CWD_REPO_ROOT/desktop/install-desktop.sh" ]] || CWD_REPO_ROOT=""
```

with a check that the discovered repo actually looks like this project
(not just any git repo the command happened to run inside). An explicit
`--in-place` flag had the identical bug — it set the mode correctly but
still resolved paths against the staging tarball — and is fixed by the
same variable.

## What was run

### Step 1 — Pi role, from-scratch, idempotency, VERSION

| Run | State before | Result |
| --- | --- | --- |
| 1 | torn down, `install.sh` run as root directly (`sudo bash -c '... \| bash -s -- pi'`) | **FAIL** — the initial `mktemp -d` staging dir is created before role dispatch, so running as root from the outset makes it root-owned/0700; the later `sudo -u pi pip install -r $SCRIPT_DIR/requirements.txt` step can't read into it. Not a defect in the documented path — this is exactly the "don't prefix sudo yourself" case the script's comments already warn against — but confirms the failure mode. Torn down and re-run correctly. |
| 2 | torn down, documented one-liner, no tty | **FAIL** — defect 1 (`/dev/tty` check) |
| 2′ | torn down, defect 1 fixed | pass, but leaked a stray `/dev/tty` error line — defect 2 (the subshell issue) |
| 2″ | torn down, both fixed | **pass**, clean output, exit 0 |
| 3 (idempotency) | already provisioned | **pass**, exit 0 — exactly one `ControllerMode = le` line, drop-in unchanged at 3 lines, `VERSION` timestamp advanced |

`git` confirmed absent throughout. `VERSION` correctly stamped the sha
actually fetched at every run.

### Step 2 — `data.db` survives a re-install

Pushed 2 rows via `desktop/push.py`, confirmed present, re-ran the
one-liner, confirmed both rows still present afterward (untouched by the
installer, which only copies the files it owns).

### Step 3 — reboot, then `bluetoothd` restart, both unattended

`sudo systemctl reboot`; Pi back up in ~35 s (SSH reachable); one
untouched `push.py` round trip succeeded with no intervention.
`sudo systemctl restart bluetooth`; `zeropi-display` restarted via
`BindsTo=bluetooth.service` as designed; a second round trip succeeded.

### Step 4 — Desktop role, in-place

Ran from inside the tracked clone (`.venv` present but pip-less, a `uv
venv` — the documented dev setup). After the fix: detected in-place
correctly, added pip via `ensurepip` in place (no venv rebuild), stamped
`VERSION` inside `.venv/`, found the Pi advertising, and `push.py` ran and
round-tripped from the existing venv. No second copy created under
`~/.local/share/zeropi-display`.

### Step 5 — Desktop role, standalone

Ran from `/tmp` (outside any clone) on the **same physical machine** — no
second machine was available. Correctly chose standalone mode, deployed
`push.py`/`requirements.txt` to `~/.local/share/zeropi-display`, built a
fresh venv, installed the `zeropi-push` shim to `~/.local/bin`, found the
Pi, and round-tripped via the shim.

**What this leaves unverified**: a genuinely separate machine or user
account, and — per `install-desktop.sh`'s own Linux-only guard — any
non-Linux OS. The standalone *code path* is exercised; cross-machine and
cross-OS behavior is not.

### Step 6 — success rate

12 consecutive `push.py` round trips, 1 s apart, from the in-place venv:
**12 / 12 pass**. Combined with steps 2-4's pushes: **18 rows** in
`readings` by the end of the session.

Journals for the session: `bluetooth` — **0** lines matching
`segfault|SIGSEGV|status=11|core-dump`; `zeropi-display` — **0**
error/traceback lines, 2 `Started` entries (boot, plus the bluetoothd
restart).

## Conclusion

Map #7's destination is met for the documented delivery mechanism as well
as the provisioning logic it delivers: both roles install from the single
`curl ... | bash -s -- <role>` command with no manual steps, survive
reboot and a `bluetoothd` restart unattended, and the Desktop role
correctly distinguishes in-place from standalone. The two defects fixed
here were specific to *non-interactive automation* of the curl path
(no tty, cwd-based detection) — a maintainer running the one-liner by hand
on a terminal, as the docs show it, would not have hit either one. The
same from-scratch caveats `docs/provisioning-verification.md` recorded
(no spare SD card, `python3-gi` never removed, no BlueZ downgrade tested,
no first-boot state) still apply, plus this run's own: no second physical
machine for the Desktop standalone case.
