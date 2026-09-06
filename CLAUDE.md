# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git workflow

**Push to `dev`, never to `main`.** `main` advances only through a PR from
`dev`, opened when the maintainer asks for it — do not open one unprompted
at the end of a chunk of work. Stop at the push and report it.

## Project status

Milestone 1 (the BLE link) is implemented and verified on real hardware:
`desktop/push.py` pushes a Payload to `pi/receive.py`, which persists a
Reading to SQLite and returns an Ack. See `docs/e2e-verification.md` for
the verification run and the BlueZ configuration it depends on.

The Pi is provisioned by `pi/install-pi.sh` and runs `receive.py` unattended
under systemd — verified from scratch on real hardware, including reboot
and `bluetoothd`-restart survival, in `docs/provisioning-verification.md`.
As of #33 there is a repo-root curl bootstrap that fetches a versioned
tarball (no `git` on the Pi) and delegates to it — run on the Pi itself,
no scp needed:

```bash
curl -fsSL https://raw.githubusercontent.com/peterderkoala/zeropi.display/dev/install.sh | bash -s -- pi
```

`install.sh` runs unprivileged and re-execs `pi/install-pi.sh` under `sudo`
itself. That script owns the BlueZ configuration the link depends on — most
critically a `bluetoothd --noplugin=midi,sap,avrcp` systemd drop-in,
without which `bluetoothd` segfaults on every incoming LE connection. Do
not hand-apply Pi state; add it to `pi/install-pi.sh` instead. The Desktop
role (`desktop/install-desktop.sh`) is stubbed pending #34.

`pi/install-pi.sh` also provisions the **e-ink panel driver**: it enables SPI,
installs the apt-side stack (`python3-spidev`, `python3-gpiozero`,
`python3-lgpio`, `python3-pil`) and deploys the vendored Waveshare V4 driver.
The driver is vendored at a pinned upstream commit in `pi/waveshare_epd/` —
read that directory's `README.md` before touching it, in particular why the
PyPI package is the wrong thing and why importing `epdconfig` claims GPIO as a
side effect. `pi/epd-selftest.py` is the by-hand bench check that the panel
actually draws. **Rendering is still not wired into the BLE path** — nothing
in `receive.py` imports the driver.

There is still no build, lint, or test tooling. The Desktop entry point is
run by hand:

```bash
# Desktop (needs bleak; venv is gitignored)
uv venv .venv && uv pip install -r desktop/requirements.txt
.venv/bin/python desktop/push.py
```

## What this project is

zeropi.display is a Pi Zero e-ink display project (see
`pi-eink-ble-concept.md` for the full concept). It reuses existing pwnagotchi
Pi Zero + Waveshare e-ink HAT hardware to show a daily summary: weather,
calendar, and an AI-generated one-liner. The longer-term goal is to source
the one-liner/usage stat from local Claude Code session data (JSONL logs in
`~/.claude/projects/*.jsonl`) rather than a separate paid API key.

**Current phase**: prove out a Bluetooth (BLE) link between a desktop
machine and the Pi — no real data parsing, no case/UPS yet. **The e-ink
panel driver is now set up and proven to draw** (#39), but nothing renders
to it: `receive.py` does not import the driver, and no Payload reaches the
glass. Driver, not rendering.

Roles (see `CONTEXT.md` for the domain vocabulary):
- **Desktop (BLE central)**: `desktop/push.py`, Python + `bleak`. Will own
  the real data sources (weather API, calendar, Claude Code JSONL logs);
  currently pushes a hardcoded test Payload in a single write.
- **Pi Zero (BLE peripheral)**: `pi/receive.py`, Python + `bluezero`. Dumb
  receiver — advertises the GATT service, accepts a Payload write,
  persists it as a Reading, and returns an Ack. It does not fetch or
  compute anything itself.

Explicitly out of scope for the current prototype milestone: e-ink
**rendering** (the driver beneath it is set up — see #39 — but drawing a
Payload to the panel is not), real weather/calendar/usage parsing,
power/UPS/enclosure hardware, any cloud/API-key fallback.

## Hardware / infrastructure notes

`infrastructure.md` documents the physical Pi Zero 2W used for development
(SSH-enabled, connected to the final hardware sample) — its IP and SSH
credentials. This file is deliberately excluded via `.gitignore` from future
changes (it was already committed once); do not add secrets to tracked files
going forward.

## Agent skills

### Issue tracker

Issues live in GitHub Issues (github.com/peterderkoala/zeropi.display), using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary — the five canonical roles, label strings equal to their names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout (root `CONTEXT.md` + `docs/adr/`, created lazily as decisions are made). See `docs/agents/domain.md`.

### Handoff document

`handoff/handoff.md`, tracked in git, is the continuity document between sessions — not a temp file, and not what a generic handoff skill's own default (write to the OS temp directory) should produce here. Update it in place when a ticket resolves or a recorded fact goes stale; see `docs/agents/handoff.md`.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**This project has a knowledge graph. Start with the code-review-graph
MCP tools to narrow scope, then read the source.** The graph is cheaper than scanning files and
gives you structural context (callers, dependents, test coverage) that file search cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

### Verify in the source

- Narrow scope with the graph, then read the source. Do not change code from graph output alone.
- For any non-trivial change, read the implementation and the relevant tests before concluding.
- Verify the exact source when touching behavior, database logic, migrations, retries, fallbacks,
  recovery, or compatibility code.
- When the graph and the source disagree, the source wins. The graph may be stale or may not
  model that relationship.
- An empty graph result can mean "not indexed" or "not statically visible", not "does not exist".

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context_tool` | Need source snippets for review — token-efficient |
| `get_impact_radius_tool` | Understanding blast radius of a change |
| `get_affected_flows_tool` | Finding which execution paths are impacted |
| `query_graph_tool` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool` | Finding functions/classes by name or keyword |
| `get_architecture_overview_tool` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.
<!-- /code-review-graph MCP tools -->
