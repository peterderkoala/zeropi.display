# zeropi.display

A Pi Zero e-ink display: weather, calendar and a Claude Code usage summary.
See `pi-eink-ble-concept.md` for the concept and `CONTEXT.md` for the domain
vocabulary.

## Install

One command per role, run on the machine being provisioned. Neither needs
`git` or a clone -- it fetches a versioned tarball of the `dev` branch.

Pi (run this on the Pi itself):

```bash
curl -fsSL https://raw.githubusercontent.com/peterderkoala/zeropi.display/dev/install.sh | bash -s -- pi
```

Desktop (run this on the machine that will push):

```bash
curl -fsSL https://raw.githubusercontent.com/peterderkoala/zeropi.display/dev/install.sh | bash -s -- desktop
```

Both commands run unprivileged; the Pi role re-execs itself under `sudo`
internally where it needs root (systemd units, BlueZ config). Neither
touches an existing `data.db` or usage-archive store on re-run.

Override the fetched ref with `ZEROPI_REF=<branch-or-sha>` before the pipe.

The Desktop installer auto-detects whether it's running inside an existing
clone (sets up `.venv` in place) or standalone (installs to
`~/.local/share/zeropi-display/` with a `zeropi-push` shim on `PATH`).
Override the detection by appending a flag after `desktop`, e.g.:

```bash
curl -fsSL https://raw.githubusercontent.com/peterderkoala/zeropi.display/dev/install.sh | bash -s -- desktop --in-place
```

(`--prefix <dir>` also works, in place of `--in-place`.) Desktop-only,
Linux-only (see #32).
