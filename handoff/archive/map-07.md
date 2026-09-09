# Map #7 — Both ends reproducible from scratch

**Closed 2026-09-06. Destination reached: both roles provision through one documented `curl … | bash -s -- <role>`.** Issue: https://github.com/peterderkoala/zeropi.display/issues/7

> Archived from `handoff/handoff.md` on 2026-09-09, verbatim. This is a
> **record of a finished effort**, not live guidance: facts here were true when
> written and some have since been superseded. The live handoff, the specs and
> the ADRs are authoritative. Kept because the *reasoning* behind decisions —
> and the bugs found on the way — is not recoverable from the code.

---

### Also open: [Both ends reproducible from scratch (#7)](https://github.com/peterderkoala/zeropi.display/issues/7)

**Destination redrawn 2026-09-05** — read the map body first, including the
banner and the new **Delivery shape** section, which binds all three
tickets.

The original destination (a stock Pi reproducible from scratch) was
**reached** by #11. Rather than close, the map was redrawn to cover the two
things it had listed as unspecified: **delivery** (getting code onto a Pi
was a hand-run `scp`) and the **Desktop end**, which had no provisioning at
all. Desktop-side provisioning moved **out of Out-of-scope and into scope**
— struck through rather than deleted, so the reversal is visible.

The redraw's decisions came from a grilling session, not a ticket, so they
live in the map's **Delivery shape** section. The load-bearing ones:

- **Tarball, not a clone** — `git` is *not installed on the Pi* and costs
  ~50 MB on a Zero; the branch tarball is 36 KB. This overturned the
  maintainer's own opening instruction ("clones the repo"), deliberately.
- **Fetched by sha, not by branch.** A branch tarball unpacks to
  `zeropi.display-dev/` and carries **no version identity** — precisely what
  a clone would have given for free. Resolve ref → sha, fetch
  `/archive/<sha>.tar.gz`, stamp `VERSION`.
- **One root `install.sh`, role by argument**, running unprivileged, with
  the `pi` role re-execing under `sudo`.
- **Never "client"/"server"** in names — `CONTEXT.md` lists both as terms to
  avoid. It is `install-pi.sh` / `install-desktop.sh`.
- **The Desktop role works in-place *and* standalone**, because it must run
  on machines that are not the maintainer's; it detects a surrounding clone
  and says which mode it picked.
- **Points at `dev`** — no `dev` → `main` PR yet, the maintainer's call.

**[#33 (the curl bootstrap) is resolved and closed** — 2026-09-06. Repo-root
`install.sh` resolves `ZEROPI_REF` (default `dev`) to a commit sha via the
GitHub API, fetches/unpacks the sha tarball to a `/tmp` staging dir, and
delegates to `pi/install-pi.sh` (re-exec'd under `sudo`) or
`desktop/install-desktop.sh`. `pi/install.sh` renamed to `pi/install-pi.sh`
— needed only the rename plus VERSION-stamping (`sha`/`ref`/`installed_at`
at `/opt/zeropi-display/VERSION`); `SCRIPT_DIR` already resolved correctly
under the bootstrap via `BASH_SOURCE`, so **don't re-add a staging-root argv
override** — one was tried, flagged by review as unneeded complexity that
silently changed the script's argument contract, and reverted.
`desktop/install-desktop.sh` is a stub (`exit 1`, points at #34) whose
header comment fixes the contract #34 builds against: argv[1] is the
staging root, env carries `ZEROPI_REF`/`ZEROPI_SHA`/`ZEROPI_TIMESTAMP`,
always unprivileged. `data.db` untouched by construction — the deploy step
copies only the files it owns, never syncs a directory wholesale. Commits
`b6eaa2e`, `17c3182` on `dev`; full detail in the issue's resolution
comment.

⚠ **`sudo`'s password prompt breaks under the documented one-liner run
non-interactively** — `curl -fsSL ... | bash -s -- pi` leaves `sudo` with
the exhausted curl pipe as stdin and no controlling terminal, which fails
confusingly (not a hang) without `ssh -t`. `install.sh` now checks for
`/dev/tty` (or already-passwordless sudo) up front and fails with a clear
message and the `-t` fix instead. Relevant if #34 or #35 touch invocation.

⚠ **CONTEXT.md's avoid-list bites documentation too, not just code** — this
session's README draft called Pi "the BLE receiver" and Desktop "the BLE
sender," both on the avoid-list (`_Avoid_: Server, receiver` /
`_Avoid_: Client, sender`). Caught by review, not by writing it. Check new
prose against the avoid-lists before it ships, not after.

**[#34 (desktop/install-desktop.sh) is resolved and closed** —
2026-09-06, `dev` (`6b86e94`). Two modes, auto-detected: a real clone
(`.git` present — checked with `-e`, not `-d`, so a **git-worktree**
checkout counts too, since worktrees make `.git` a file not a directory)
gets `.venv` set up in place; anything else, including every curl-bootstrap
run (a GitHub archive tarball never carries `.git`), installs standalone to
`~/.local/share/zeropi-display/` with a `zeropi-push` shim on `PATH`.
`--in-place` / `--prefix <dir>` override the detection. Linux-only refusal
up front (#32's BlueZ-specific bleak API). **Idempotent, and deliberately
non-destructive of an existing venv**: one already present but missing pip
(e.g. one made by `uv venv`, this project's own documented dev setup) gets
pip added via `ensurepip` rather than `rm -rf`'d, and `python -m pip` is
used throughout since ensurepip's entry-point names aren't guaranteed
(observed: `pip3`/`pip3.12` but no bare `pip`). VERSION is stamped inside
the venv, not the install root — for in-place that root is the
maintainer's tracked checkout, where a stray file would be clutter. The
end-of-install reachability check reuses `push.py`'s own
`matches_service()`/`SERVICE_UUID` (no duplicated UUID to drift) and
**warns rather than fails** if no Pi answers, since the two roles are
provisioned independently. Verified live: a push through the standalone
shim round-tripped against the dev Pi. README documents the desktop
one-liner and the override flag. **Review caught three real bugs before
landing**: the worktree-is-a-file case, the destructive `rm -rf` on a
pip-less venv, and a broken doubled `--` in the README's override example —
all fixed. Unblocked #35.

**[#35 (hardware verification of both roles) is resolved and closed** —
2026-09-06. Write-up: `docs/curl-delivery-verification.md`. Both roles
install, survive reboot and a `bluetoothd` restart unattended, and
round-trip reliably (12/12 this session, 18 total rows) through the single
documented one-liner. **Found and fixed two real defects, both specific to
non-interactive automation of the curl path** (a maintainer typing the
one-liner at a real terminal would not have hit either): (1) `install.sh`'s
`[[ -e /dev/tty ]]` check is true even with no controlling terminal at all,
so a plain non-pty `ssh host 'curl ... | bash -s -- pi'` crashed instead of
falling back to passwordless sudo — replaced with an open/close probe run
in a subshell; (2) `install-desktop.sh`'s in-place detection checked its
own script location, which under the curl bootstrap is always a `/tmp`
staging unpack that never carries `.git` — so in-place could **never** fire
through the documented invocation path, silently installing standalone
even from inside the tracked clone. Fixed to detect from the invoking
shell's `$PWD` instead, which survives the pipe unchanged. **Desktop
standalone** ran on the same physical machine from a non-clone directory —
no second machine was available, so cross-machine/cross-OS behavior is
still unverified. Also: this run collided in real time with a concurrent
session's e-ink driver work (#39) on the same shared dev Pi — coordinated
directly, confirmed no disruption either way. **This session's own scope
was the `dev` branch**, which doesn't carry #39's e-ink panel steps
(unmerged, see below) — so despite a note left on #39 expecting otherwise,
that hardware verification was never reachable from here and is carried
forward as fog.

Frontier — **one open ticket, #40.** The map's original destination is fully
reached. What remains is the gap this run could not close: #39's e-ink panel
steps in `install-pi.sh` have never executed. That branch has **since been
merged to `dev`** (and #39 is a child of this map, added after this run), so
the fog is now a takeable ticket rather than a note — see #40.

**Update, same day: #40 closed too.** See the Maps section at the top of
this file for the full result — the frontier described above is now empty.

Also spun out, **not** a map child:
[#32](https://github.com/peterderkoala/zeropi.display/issues/32) —
`push.py`'s `_acquire_mtu()` is a private BlueZ-specific `bleak` API, so the
Desktop is Linux-only. Ruled out of scope for #7 for the same reason as #12
(a code wart, not an installation concern); `install-desktop.sh` refuses
non-Linux loudly instead.

**The original four tickets are closed** (#8, #9, #10, #11); three new ones
(#33, #34, #35) came from the redraw. #11 verified the Pi path on hardware: `install.sh` runs clean
from a torn-down Pi and is idempotent, reboot and `bluetoothd` restart both
survive unattended, **20/20** consecutive pushes and **23/23** round trips
with 0 `bluetoothd` crashes. Write-up: `docs/provisioning-verification.md`.

**#17's sequencing gate is now lifted** — it settled that the new SQLite
schema lands *after* #11 closes. It has closed, so #17's schema change is
free to land.

#11 left the fresh-card caveat standing: no spare was available, so "from
scratch" meant tearing the hand-applied state off the dev Pi —
`python3-gi` was never removed, BlueZ never downgraded, first-boot state not
reproduced. Still fog on the map. Its other open item, how code reaches the
Pi, is what the redraw answers.
