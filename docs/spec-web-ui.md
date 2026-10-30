# Spec: the web UI

**Status**: binding. This document is the brief for the web UI of the
**Management Surface**: a small local web service on the Desktop that does what
`desktop/cli.py` does, in a browser. It is the destination of
[map #89](https://github.com/peterderkoala/zeropi.display/issues/89) and was
written from that map's nine resolved tickets. **Implementation is a separate
map**, opened against this document.

**Required reading, and nothing else is required**:
[`spec-management-surface.md`](spec-management-surface.md) (this spec is a skin
over it; every binding rule there still binds, and §3.7, §7, §8 and §9 are
referenced throughout), `CONTEXT.md` (the binding vocabulary, in particular
**Management Surface**, **Verdict**, **Configuration**, **Operation** and
**Unreachable**), and ADRs
[0006](adr/0006-pi-wipes-on-desktop-id-change.md),
[0011](adr/0011-management-actions-are-never-deferred.md),
[0012](adr/0012-status-is-requested-not-carried.md) and
[0013](adr/0013-no-command-overrides-a-verified-invariant.md).

⚠ **`handoff/handoff.md` is not authoritative.** It is a running log for
continuity. Where it and this document disagree, this document wins. Where this
document and a ticket's resolution comment disagree, this document wins too; it
was written from them, and records the one place it overrides one (§9.3).

⚠ **The web UI is a skin, not a second surface.** It calls the same operations
as `cli.py`, in process, never by shelling out to it. If a rule in this spec
seems to need a behaviour the CLI does not have, the behaviour belongs in the
shared operations (§4), not in the web layer.

---

## 1. What you are building

Six things, in this order of dependency:

1. **The operations module** (§4): the actions extracted from `cli.py` into
   functions that return structured results and never print. `cli.py` becomes
   a renderer over them. This is the bulk of the work and it changes no CLI
   output.
2. **The web service** (§3, §5): `python desktop/cli.py web`, a FastAPI app on
   `127.0.0.1:8737` that renders the same results as JSON.
3. **Operations over HTTP** (§6): a handle per BLE action, followed over
   Server-Sent Events, one at a time.
4. **The localhost guard** (§8): Host allowlist, JSON-plus-Origin on every
   `POST`, never framed.
5. **The pages** (§10): three tabs (Status, Configuration, Setup) in Preact +
   htm, vendored, with no build step.
6. **Install and first-run setup** (§9): `install-desktop.sh` installs both
   units and starts the web service; `/setup` does the rest in the browser.

Two `systemd --user` units exist when this is done: `zeropi-push` (unchanged,
the resident pipeline loop) and `zeropi-web` (new). They are independent of each
other (§5.2).

---

## 2. Vocabulary you will be held to

`CONTEXT.md` is binding. In particular:

- Say **Management Surface**, never "dashboard" or "admin panel". The web UI is
  one rendering of it; the CLI is the other.
- **Operation** (added by this map): one management action that touches the
  link. It has **started or been refused**; there is no third state in which it
  waits to start. A surface runs one at a time. Once started it runs to
  completion whether or not anyone is watching.
- **Configuration is read once per run**, and for a long-lived surface process
  a run is **one operation** (narrowed by this map). The resident `zeropi-push`
  is unchanged: its run is its process lifetime.
- **Verdict** keeps its four states and three severities (management surface
  §8). Nothing in this spec adds a Verdict state.

---

## 3. The stack

*(Decided by [Pick the stack](https://github.com/peterderkoala/zeropi.display/issues/91),
from [Candidate stacks](https://github.com/peterderkoala/zeropi.display/issues/90).)*

### 3.1 Python: FastAPI + plain `uvicorn`

`fastapi` and `uvicorn` with **no `[standard]` extras**. With `bleak` that is 15
packages. Handlers are async; SSE is FastAPI's built-in
`fastapi.sse.EventSourceResponse`.

**Dependencies live in `desktop/requirements-web.txt`**, never in
`requirements.txt` (what `zeropi-push` is installed from). `requirements-dev.txt`
includes the web requirements plus **`httpx2`**: FastAPI's `TestClient` needs
`httpx2`, not `httpx`.

⚠ **Import rule: nothing outside the web module imports `fastapi`.** `cli.py`,
`push.py`, `service.py` and the operations module must stay importable without
it, as the suite already requires modules to import without `bluezero` or a
panel. `cli.py web` imports the web module lazily inside its subcommand.

### 3.2 The page: Preact 10 + htm, three vendored ES modules

`preact.module.js` (10.x, pin a release, never the 11 RC), `hooks.module.js`
and `htm.module.js`, about 16.5 KB raw, wired by an **import map** in the page.

- **Not `htm/preact/standalone.module.js`**: it was built in 2022 against
  `preact ^10.2.0` and would pin the UI to 2022 Preact.
- **Not htmx**: the page consumes the JSON the operations already produce
  (§4.3). HTML fragments would be a second rendering that bypasses it.

**Vendoring follows the `pi/waveshare_epd/` precedent**: the three files are
committed under the web module's static `vendor/` directory with a `README.md`
giving each file's exact version, source URL and sha256, and stating that no
npm is involved. No node, no bundler, no `dist/`. The page loads nothing from a
CDN at runtime.

### 3.3 ⚠ Pydantic describes the HTTP shape, never a key's type or range

`config.py`'s schema is the **single validation authority** (management surface
§4). A Configuration value crosses HTTP as a raw **string**, exactly as
`cli.py config set` receives it, and `config.py` validates it and produces the
refusal, including the explanation of a derived bound (`GAUGE_EXPIRY_S / 2`).

The web layer **never declares a key's type or range** (no
`conint(ge=…, le=…)`), and does not generate models from the schema. Either
would be a second authority with its own error text, and the bounds derived
from verified invariants would drift away from it. Pydantic models describe
request and response envelopes only.

---

## 4. The seam: what the web service calls

*(Decided by [What the web service calls](https://github.com/peterderkoala/zeropi.display/issues/92).)*

### 4.1 Extract the operations from `cli.py`

Today every `cmd_*` mixes the operation, its `print()` output and the §9.6 exit
code. Extract the operations into **their own module**:

- Each operation is `async`, takes its inputs as arguments, **returns a
  structured result, and never prints or picks an exit code**.
- `cli.py` becomes a renderer: result → text → exit code. **CLI output stays
  byte-identical**; the existing `test_cli.py` rendering tests pin it and must
  pass unchanged.
- `TIER3` and `TIER2_RATIONALE` move out of `cli.py` into the operations
  module (or beside `config.py`), because both renderers need them.
- `wipe`'s confirmation becomes an **argument** (the typed short id), not
  `input()`. The CLI prompts and passes it; the web page sends it.
- `pair` takes an **optional address** (from `discover`, §9.3) and connects to
  it through `find_pi(address=…)`. Without one it behaves as today.
- A new operation, **`discover`** (§9.3), returns every Pi seen in one scan.

### 4.2 Structured refusals

Every refusal is a kind plus its message and rationale. The kinds:

| Kind | Raised by | Before BLE? |
|---|---|---|
| `unknown_key` | `config get/set` | yes |
| `not_a_setting` | `config set` of a Tier 3 name | yes |
| `out_of_range` | `config set`, including "fails to coerce" | yes |
| `pair_only` | `config set pi.address` | yes |
| `not_paired` | any Pi action with `pi.address` unset | yes |
| `confirm_mismatch` | `wipe` with the wrong short id | yes |
| `busy_here` | any Operation while this process runs one (§6.3) | yes |
| `unit_missing` | starting or restarting a unit that is not installed (§9.4) | yes (no BLE involved) |
| `unreachable` (`absent` / `busy`) | inside an Operation's result | no |

The CLI's text for each existing kind stays byte-identical. `busy_here` and
`unit_missing` are new; `confirm_mismatch` exists today as the CLI's
"Confirmation did not match" text.

### 4.3 One `status` JSON builder

⚠ **`status` JSON has a single builder**, used by both `cli.py status --json`
and the web layer, so management surface §9.5's shape cannot split in two. The
same rule extends to every result the web layer renders: it serialises the
operations module's result, it does not rebuild one.

### 4.4 Progress

Operations take an optional **`progress(kind, text)`** callback. Its default
prints `text`, so CLI output is unchanged. `kind` is from a closed set:
`lock_wait`, `scanning`, `found`, `connected`, `row_failed`, `settings_failed`,
`phase` (the second phase of `pair` / `wipe`). `text` is the CLI's exact line.

- No new counters (no "row *n* of *m*"). `push.py` does not grow.
- `run_gauge_push` shares the print pattern but is **not** a surface
  operation. Leave it printing.

### 4.5 `BatchResult` gains two additive fields

- **`settings_ok: bool`**: whether Settings re-assertion succeeded.
- **`unreachable: Optional[str]`**: set only when the connection never opened.

The `failed` count and control flow are unchanged, so `service.py`'s retry
behaviour (verified on hardware by #87) is untouched. This also closes #86's
known gap: `push` / `pair` against an absent Pi now exit 2 from the CLI, as
§9.6 intends, and render as Unreachable on the page, never as "every row
failed". `BleLinkBusy` still propagates as before.

### 4.6 Configuration per operation; operations on a worker thread

- **Only the config store's path** (`--config` / `ZEROPI_CONFIG`) is fixed when
  `zeropi-web` starts. **Values are resolved per operation**, so the page never
  says *not paired* straight after its own `pair`.
- **Each operation runs on a worker thread with its own `asyncio.run`**, exactly
  as a CLI invocation does. The operations contain blocking calls
  (`open_store`, log parsing, `systemctl` via `subprocess.run`) that would stall
  uvicorn's event loop. Bleak was verified to work this way.
- Concurrent operations across processes are serialised by the BLE `flock`, as
  two terminals already are. `flock` works between threads in one process,
  because each `os.open` gets its own open file description.

---

## 5. The process

*(Decided by [Process model](https://github.com/peterderkoala/zeropi.display/issues/93).)*

### 5.1 `zeropi-web`, resident

`zeropi-web` is a resident `systemd --user` unit running
**`python desktop/cli.py web`**. A foreground run for development takes the same
code path.

- **Bind: `127.0.0.1`, hardcoded. No `--host` flag.**
- **Port: `8737`**, overridable with `--port`. It is unprivileged, not in
  `/etc/services`, and below Linux's ephemeral range, so the kernel never hands
  it out. It is **not a Configuration key** (it would be the only key read at
  startup). If the port is taken, bind fails loudly; there is no fallback port.
- The process holds no state that matters between Operations (§6.5, §7.2 are in
  memory and may be lost). Stopping it loses nothing that a restart cannot
  explain to the page.
- **The web UI has no action that restarts `zeropi-web` itself.** Configuration
  never needs it (read per operation); a code update means restarting it by
  hand.

### 5.2 The unit file: `desktop/zeropi-web.service`

`Type=simple`, **`Environment=PYTHONUNBUFFERED=1`** (mandatory; without it
nothing reaches the journal), `Restart=on-failure`, `RestartSec=10s`,
`WantedBy=default.target`. `ExecStart` and `WorkingDirectory` are written by the
installer for the layout it detected (§9.2).

⚠ **No `Requires=`, `PartOf=`, `BindsTo=` or `After=` relationship to
`zeropi-push`.** The two are independent. A coupling would take the UI down at
exactly the moment it has to show that `zeropi-push` failed and offer to
restart it.

### 5.3 `zeropi-push`'s state is a banner, not a Verdict state

The page shows *zeropi-push is not running* / *has failed* / *is not installed*
as a **Desktop-side banner** above the tabs, linking to Setup's service step
(§9.4). It comes from `systemctl --user show` alone: local, cheap, no BLE, no
lock. *Pending restart* (management surface §3.7) is not shown while the
service is inactive.

Stopping `zeropi-web` mid-Operation needs nothing special. The kernel releases
the `flock`, a Batch is safe mid-way (`mark_pushed` runs per Ack), and
Commands are idempotent with the human as the retrier (ADR-0011). Shutdown does
not wait for an in-flight Operation.

---

## 6. Operations over HTTP

*(Decided by [How a long BLE action appears over HTTP](https://github.com/peterderkoala/zeropi.display/issues/94).)*

### 6.1 Which actions are Operations

**Every action that touches BLE is an Operation**: `status`, `push`, `pair`,
`redraw`, `wipe`, `discover`, and `config set` of a key marked `→ Pi` (a
Setting, which is written and then sent to the Pi as the CLI does). There is
no fast path: an absent Pi costs the 10 s scan whatever the verb.

Plain request/response covers everything else: `config` list and get,
`config set` of any other key, the Desktop facts, the last-status slot, and the
two unit actions (§9.4).

### 6.2 The handle

A `POST` starts the Operation on its worker thread and returns **`202` with its
handle**. `GET /api/operations/{id}/events` streams its progress, then its
result, over SSE. `EventSource` is GET-only and reconnects on its own, so a
reloaded page or a second tab reattaches.

⚠ **ADR-0011: the handle names an Operation that has already started when the
`POST` returns.** It is never a job that fires later. A table of handles looks
like a queue; it is not one, and nothing in this service may hold an action to
run later.

### 6.3 One at a time, for the whole Operation

**One BLE Operation at a time per `zeropi-web` process**, held across both
phases of `pair` (scan, then Batch) and `wipe` (Command, then re-push).

- A second request is refused **immediately** with `busy_here`, carrying the
  running Operation's handle so the page attaches to it. It is not queued and
  does not wait.
- `busy_here` (this surface is running something) is a different refusal from
  `unreachable: busy` (another process held the link).
- Between the two phases of `pair` / `wipe` the `flock` is free and
  `zeropi-push` can take it, exactly as with the CLI. That stays.
- **Lock wait is the CLI's 15 s (`CLI_LOCK_WAIT_S`)**, shown as `lock_wait`
  progress.

### 6.4 Runs to completion

An Operation runs to completion whatever the browser does. **There is no
cancel.** Every step is bounded, a Batch is safe to interrupt anyway, and a
half-cancelled `wipe` is worse than a finished one. The page says which phase is
running and how long it can take.

### 6.5 Lifetime: in memory only

- **Retention**: the running Operation plus the most recent finished one, until
  the next starts or the process exits. (The last *status* result is kept
  separately, §7.2.)
- **Ids** are random; an unknown id returns `404`.
- `GET /api/operations/current` finds the running or just-finished Operation
  without an id.
- The events stream **replays the whole event buffer**, then continues live.
- **After a `zeropi-web` restart**, the reconnect gets `404` and the page says:
  *"The web service restarted while `<verb>` was running. Its outcome is
  unknown. Nothing was queued. Check status."* It never guesses or retries.

### 6.6 The result

An Operation ends with **one `result` event**: the structured result from the
operations module (the single `status` builder for `status`) plus an
**`outcome`** named after management surface §9.6's exit-code categories. The
CLI maps `outcome` to its number; the web never sees numbers.

**Refusals decided before any BLE** (every row of §4.2 marked "before BLE")
come back **directly from the `POST`** as a structured refusal. **No Operation
is created.**

**A two-phase partial outcome reports each phase**, e.g.
`{"command": "ok", "repush": "busy"}`, with the headline *"Wiped. The re-push
hasn't happened yet: the link was busy. Run push."* The page offers *Push now*;
the human is the retrier. `outcome` follows the CLI's exit code for that case.

### 6.7 The HTTP surface

All under `/api`, all JSON. Every state change is a `POST`; **no `GET` has a
side effect**.

| Method and path | Does | Returns |
|---|---|---|
| `POST /api/operations` `{"verb": …, "args": {…}}` | starts `status` / `push` / `pair` / `redraw` / `wipe` / `discover` | `202 {id}`; pre-BLE refusal `422 {refusal}`; `409 {refusal: busy_here, running: id}` |
| `GET /api/operations/current` | the running or last-finished Operation | `200 {…}` or `204` |
| `GET /api/operations/{id}/events` | SSE: `progress` events, then one `result` | stream; `404` if unknown |
| `GET /api/status/last` | the last-status slot (§7.2) | `200 {result, checked_at}` or `204` |
| `GET /api/desktop` | Desktop facts: `zeropi-push` state and uptime, last Batch time, Readings sent, keys pending restart | `200` |
| `GET /api/config` | all three Tiers, with `→ Pi` and pending-restart marks | `200` |
| `POST /api/config/{key}` `{"value": "<string>"}` | `config set`; for a `→ Pi` key, starts an Operation | `200 {written}`; `202 {id}` for `→ Pi`; `422 {refusal}` |
| `POST /api/service/start` | `systemctl --user enable --now zeropi-push` (§9.4) | `200`; `422 {refusal: unit_missing}`; `502 {systemctl text}` |
| `POST /api/service/restart` | the CLI's `restart` | same as above |
| `GET /`, `/config`, `/setup` | the page (one HTML file, client-side tabs) | `200` |
| `GET /static/…` | the page's modules and `vendor/` | `200` |

Status codes and envelope field names beyond those named above are the
implementer's, as long as every refusal carries `{kind, message, rationale}`.

---

## 7. When the page asks for status

*(Decided by [When the page asks for status](https://github.com/peterderkoala/zeropi.display/issues/95).)*

### 7.1 No polling, ever

Status is requested only when a person clicks **Check now**, plus the one
automatic case in §7.3. **No page has a timer that touches BLE.**

The reason is in the code, not in taste. `GaugeGate.observe` clears its pending
flag and stamps `_last_push_at` *before* the push runs
(`desktop/service.py:148-150`), so a Gauge push that meets a busy link is **lost
until the Gauge next changes**, not retried. An absent Pi holds the lock for the
whole 10 s scan. A poll would also collide with the user's own actions under
§6.3. And polling would not solve ADR-0012's "stuck at 03:00" case, because it
only runs while a tab is open.

### 7.2 The last-status slot

`zeropi-web` keeps **the most recent status result** in memory, in its own slot
that later Operations do not replace, with **`checked_at`: the Desktop's wall
clock when the reply arrived**. It is lost on restart.

### 7.3 On load

If the slot is empty (the first visit after `zeropi-web` starts, or after a
check that failed), the page starts **one** status Operation automatically. A
second tab opening at the same moment gets `busy_here` and follows the running
handle. Otherwise the page shows the stored result with **Check now** and makes
no BLE connection.

### 7.4 How age is shown

- The **headline carries the check time**: `✓ Working · checked 14:04 (3m ago)`.
  Only the relative part ticks in the browser.
- **The Pi's durations are shown as they were at the check** (`drew 2m 23s
  ago`, `up 1d 6h`) and never advanced in the browser. The Pi may have redrawn
  or restarted since; ticking them would claim knowledge the page does not
  have. (The Pi only ever reports durations: ADR-0009, ADR-0012.)
- **There is no staleness threshold** that dims or downgrades an old Verdict,
  and so no new constant to inventory.

### 7.5 A result made out of date

If a `wipe`, `push`, `redraw` or `pair` succeeds after the check, or the Desktop
facts show a service Batch that landed after it, the result **stays visible and
is labelled**: `checked 14:04 · before the wipe at 14:10`. The Verdict keeps the
value it had when computed and is **never recomputed** against newer facts, and
no check is started automatically.

### 7.6 Desktop facts

The Desktop facts (`GET /api/desktop`) are local and lock-free. They refresh on
page load, when the tab becomes visible again, and after every Operation
finishes. There is no timer.

---

## 8. The localhost guard

*(Decided by [What stops another website from driving the localhost service](https://github.com/peterderkoala/zeropi.display/issues/96).)*

### 8.1 Threat model

**Browser-borne attacks only**: a website the user visits driving
`127.0.0.1:8737` through a cross-site request (CSRF), reading and driving it
through DNS rebinding, or framing it (clickjacking).

**Out of scope**: anything running as the user (it can already run `cli.py`),
and other local OS accounts on the same Desktop (`127.0.0.1` is reachable by
every account; this is a personal Desktop). LAN access and the shared token are
out of scope too (§13).

### 8.2 The guard, all in one module of the web layer

1. **Host allowlist on every request**, GETs and SSE included: Starlette's
   `TrustedHostMiddleware` with a **hardcoded** `["127.0.0.1", "localhost"]`,
   not a Configuration key. A rebound `Host` gets `400`. This also stops a
   rebound page *reading* status and Configuration.
2. **Every `POST` must be `Content-Type: application/json`, and its `Origin`,
   when present, must be `http://127.0.0.1:<port>` or
   `http://localhost:<port>`**, else `403`. A request with **no** `Origin` is
   accepted, so `curl` keeps working, which is harmless under §8.1.
3. **No CORS.** The service sends no CORS headers and never answers a preflight,
   so a cross-site JSON `fetch` dies at the preflight and a plain form cannot
   send JSON.
4. **Never framed**: every response carries
   `Content-Security-Policy: frame-ancestors 'none'` and
   `X-Frame-Options: DENY`.

**No token** of any kind: none embedded in the page, and
`~/.config/zeropi-display/token` (management surface §3.4) is not used. It
stays reserved for LAN access.

### 8.3 `wipe`'s confirmation is a slip guard, not a control

The page sends the typed short id in the `wipe` request's `args`; the operation
checks it (`confirm_mismatch`), with the same refusal path as the CLI. It is not
a security control in either surface.

The short id is the **last three octets** of the Pi's BLE address (`7c970f` on
the dev Pi). It used to be the first three, the Raspberry Pi manufacturer
prefix that every Pi shares, until
[#101](https://github.com/peterderkoala/zeropi.display/issues/101) fixed it in
the shared code. This spec says only "the typed short id, as the CLI defines it".

---

## 9. Install and first-run setup

*(Decided by [First-run setup](https://github.com/peterderkoala/zeropi.display/issues/100).)*

**Desktop side only.** Provisioning the Pi stays the curl bootstrap run on the
Pi; the Desktop holds no SSH or sudo for it.

### 9.1 One terminal step, then the browser

The UI cannot install the process that serves it, so **`install-desktop.sh`
(via `install.sh … desktop`) is the one non-graphical step**. It:

1. sets up the venv, installing `requirements.txt` **and
   `requirements-web.txt`**;
2. writes **both** unit files, `zeropi-push.service` and `zeropi-web.service`,
   to `~/.config/systemd/user/`, with `ExecStart` / `WorkingDirectory` for the
   detected layout, and runs `daemon-reload`;
3. runs `systemctl --user enable --now zeropi-web` — **and does not enable
   `zeropi-push`** (§9.4);
4. ends by printing `http://127.0.0.1:8737`, and opening it with `xdg-open`
   when a display is present.

There is no foreground `cli.py setup` server and no port handover.

### 9.2 Layout and packaging

- **Clone (in-place)**: units point at `<repo>/.venv/bin/python` and
  `<repo>/desktop/`.
- **Standalone**: deploys **the whole `desktop/` directory** (every `.py`, both
  requirements files, both unit templates, the web module's static files)
  under `~/.local/share/zeropi-display/`, with its venv at `…/venv`. This
  closes the gap #47 flagged: standalone mode copied only `push.py`, so it could
  never run `service.py` or `cli.py`.
- **No `pyproject.toml`**: flat imports, as `pytest.ini` already uses.

### 9.3 Discovery: a list, never first-heard

**`discover`** is an Operation (it takes the BLE lock) that scans for the full
10 s and returns **every** `zeropi-display` device seen, as
`{name, address, rssi}`. `find_pi()` returns the first match; that is not
acceptable here, because a second Pi in range (a reimaged board and the old
one, a neighbour's) is a real case.

- It starts automatically **once** when Setup opens on a Desktop with no
  `pi.address`, plus a **Scan again** button. This is one scan on the page
  whose purpose is finding a Pi; it is not polling (§7.1).
- One Pi found: preselected. Several: the user picks, and **Pair stays disabled
  until they do**. None: the page says so and shows the Pi's curl one-liner
  (`… | bash -s -- pi`), noting that it runs **on the Pi**.
- `pair` then targets the chosen address (§4.1).

### 9.4 `zeropi-push` is started by Setup, after a successful pair

The installer writes `zeropi-push.service` but never enables it. **Setup
enables it only after `pair` succeeds**, because an unpaired loop would push to
whatever Pi `find_pi()` heard first. That ordering is the safety property.

Starting it is a **Desktop-side action, not an Operation** (no BLE, no lock): a
synchronous guarded `POST` running `systemctl --user enable --now zeropi-push`,
in the same family as `restart`, with `systemctl`'s error text shown verbatim.
A missing unit file is the `unit_missing` refusal, whose message is *re-run the
installer* with the Desktop curl one-liner. **The web UI never writes unit
files**; only the installer knows the layout.

### 9.5 Setup is a checklist of real state

`/setup` is always reachable, and every step shows what is true now, derived
from `pi.address` and `systemctl --user show`. There is no stored wizard
progress.

1. **Find the Pi**: §9.3.
2. **Pair**: carries a static warning, *"If this Pi was paired with another
   computer, it clears its history and takes this computer's"* (ADR-0006).
   There is no detection: the advertisement carries no Desktop Id, and
   detecting it would cost a status round trip first.
3. **Start zeropi-push**: §9.4.
4. **First status**: the last-status slot is empty, so §7.3 starts the check;
   Setup shows the Verdict and links to the Status tab.

The Status tab **redirects to `/setup` when `pi.address` is unset**. The
banner of §5.3 links to step 3. On an already set-up Desktop every step shows as
done (*paired with `<name>` (`<address>`)*, *zeropi-push running*), and **Pair a
different Pi…** sits behind a confirm naming the current Pi. Re-entering Setup
after a reimage or on a new laptop is the same page.

---

## 10. The pages

*(Decided by [The pages: prototype the web UI on fake data](https://github.com/peterderkoala/zeropi.display/issues/97).
The prototype is on the throwaway branch
[`prototype/web-ui`](https://github.com/peterderkoala/zeropi.display/tree/prototype/web-ui);
run it with `python3 -m http.server 8737 -d desktop/prototype_web` and open
`?variant=B`. It is reference, not code to copy.)*

### 10.1 Inventory and navigation

**Three tabs, each its own route**: **Status** (`/`), **Configuration**
(`/config`), **Setup** (`/setup`). One HTML page; the tabs are client-side and
the routes are reload-stable.

- **The tab bar carries an Operation indicator**: `● <verb> running` while one
  runs, `last: <verb>` after. Clicking it opens the Operation.
- **The Operation opens in a modal** showing its `{kind, text}` log and the
  final result with its `outcome`. While running, the modal offers **Hide
  (keeps running)** and **no cancel**. Reloading the page reattaches (§6.5).
- **The banner** of §5.3 sits above the tabs.

### 10.2 Status

- **The hero**: the headline Verdict (`✓` / `✗` / `?` and the CLI's headline
  text) with its check time and any out-of-date label (§7.4, §7.5); one line of
  Desktop and Pi facts; and, beside them, **Check now**, **Push now**,
  **Redraw** and **Wipe…**. Wipe opens an inline typed-short-id confirmation.
- **The six checks as a grid of tiles**, always all six (management surface
  §8.3), each with its severity mark and the CLI's detail text.
- **Unreachable** (`absent` / `busy`) replaces the tiles with the unreachable
  block, what is still known from the Desktop's own records (management surface
  §9.2), and *Nothing was queued*.
- No status yet: the hero says so, and §7.3 applies.

### 10.3 Configuration

- **Tier 1 and Tier 2** as editable tables, each row showing its value, its
  range, and `→ Pi` where it is a Setting.
- **Tier 3 read-only**, each row beside the ADR or measurement that fixes it.
- **Refusals appear inline under the row**, with their kind:
  `out_of_range` with the derived-bound explanation, `not_a_setting`,
  `unknown_key`, `pair_only` (for `pi.address`, which points to Setup).
- A successful save of a non-Setting key shows **pending restart**, and one
  **Restart zeropi-push** button in a notice at the top of the tab. A `→ Pi`
  key's save is an Operation (§6.1) and shows in the modal.

### 10.4 Setup

§9.5, exactly.

### 10.5 Where the page deliberately differs from the CLI

Stated so the implementation does not "fix" these back:

- The checks are **tiles, not the CLI's aligned table**, and the Desktop and Pi
  facts are condensed to **one line**. The **words** of each check (`265  (47
  missing)`, `historic frame, drew 2m 23s ago`) stay **byte-identical** to the
  CLI's, from the shared builder (§4.3).
- `restart` is **not a top-level action**. It appears where it is needed: the
  pending-restart notice on Configuration and Setup's service step. The CLI
  keeps `restart` as a command.
- Operation progress is a **modal**, not inline output. The progress text is
  identical to the CLI's (§4.4).
- **The management surface §9.2 rule still holds**: no two-column
  Desktop-vs-Pi ledger.

### 10.6 Narrow screens

The prototype was not checked at phone width. The rule: below **640 px**, the
tile grid collapses to one column and the hero's buttons wrap under the
headline; the tab bar stays one row. No horizontal scroll at any width.

### 10.7 The states to render

The prototype's scenarios are the inventory. Every one must render, and each is
a fixture in §12.4: working; not working (a fault headline); Unreachable
`absent`; Unreachable `busy`; empty last-status slot (automatic check);
out of date after a mutating action; `zeropi-push` stopped / failed / not
installed; an Operation running; a second action refused with `busy_here` and
attached; *outcome unknown* after a `zeropi-web` restart; a two-phase partial
`wipe`; not paired (redirect to Setup); Setup with no Pi, one Pi, two Pis; the
unit missing.

---

## 11. The gap check: what an implementer would otherwise invent

Each of these has a wrong answer that looks reasonable. They are decided:

1. **The Operation handle is not a queue** (§6.2). Nothing runs later.
2. **Single-flight covers both phases** of `pair` and `wipe` (§6.3).
3. **`busy_here` ≠ `unreachable: busy`** (§6.3).
4. **Pre-BLE refusals create no Operation** and return from the `POST` (§6.6).
5. **Progress is `(kind, text)` with the CLI's exact text** (§4.4); CLI output
   stays byte-identical, pinned by the existing tests.
6. **One `status` JSON builder** for CLI and web (§4.3).
7. **Configuration is resolved per operation**; only the store path is fixed
   at startup (§4.6).
8. **`settings_ok` and `unreachable` are additive `BatchResult` fields**;
   `service.py`'s control flow does not change (§4.5).
9. **Pydantic never declares a key's type or range** (§3.3).
10. **No `fastapi` import outside the web module** (§3.1).
11. **No `GET` has a side effect**; every state change is a JSON `POST` (§6.7,
    §8.2).
12. **No BLE timer anywhere in the page** (§7.1); the only automatic BLE
    actions are §7.3's one status check and §9.3's one discover.
13. **The last-status slot is separate** from the current Operation (§7.2), and
    an out-of-date result is labelled, never recomputed (§7.5).
14. **Pi durations are never advanced** in the browser (§7.4).
15. **`zeropi-web` is not coupled to `zeropi-push`** in systemd (§5.2), and
    `zeropi-push`'s state is a banner, not a Verdict state (§5.3).
16. **The installer never enables `zeropi-push`**; Setup does, after a pair
    (§9.4). The web UI never writes unit files.
17. **`discover` returns a list**; Pair stays disabled until one of several is
    chosen (§9.3).
18. **A `→ Pi` key's `config set` is an Operation**, not a write that waits for
    the next connection (§6.1). (The prototype's message for this was wrong;
    this spec overrides it.)
19. **`run_gauge_push` keeps printing**; it is not a surface operation (§4.4).
20. **Vendored modules, no CDN at runtime**, with a sha256 `README.md` (§3.2).

---

## 12. Testing, without a Pi

The suite must stay green with **no panel, no SPI, no `bluezero`, no
`~/.claude`, no Pi, and no browser**. Everything below holds under those
constraints; what does not is §13.

### 12.1 The operations module

- **Every existing `test_cli.py` rendering test passes unchanged.** That is the
  byte-identical guarantee, and it is the first test of the extraction.
- Each operation is tested by its returned result against fakes, as the
  current `cmd_*` tests are; no output capture is needed for the logic.
- Each refusal kind of §4.2 has a test asserting its kind, and that nothing was
  written and nothing was sent.
- `BatchResult.unreachable` is set when the connection never opens and only
  then; `settings_ok` reflects Settings re-assertion. A `service.py` test
  asserts its retry behaviour is unchanged.
- `discover` returns every matching device from a faked scanner, not the
  first.
- The progress callback receives the closed set of kinds, and its default
  prints exactly the old lines.

### 12.2 The web layer

FastAPI's `TestClient` (with `httpx2`) over the app, the operations faked.

- **The guard** (§8.2), each as its own test: a cross-origin form `POST` gets
  `403`; a JSON `POST` with a mismatched `Origin` gets `403`; a non-JSON `POST`
  gets `403`; a rebound `Host` gets `400` on a `GET` and on the events stream; a
  `POST` with **no** `Origin` is accepted; every response carries both framing
  headers; no response carries a CORS header, and an `OPTIONS` preflight is not
  answered with one.
- **Operations** (§6): a `POST` returns `202` and a handle whose Operation has
  already started; a second `POST` while one runs returns `busy_here` with the
  running handle, **and the fake was called once**; a pre-BLE refusal returns
  `422` and creates no Operation (`/operations/current` unchanged); the events
  stream replays its buffer to a late subscriber, then ends with one `result`;
  an unknown id is `404`.
- **Status** (§7): the last-status slot survives a later `push` Operation; it
  is labelled out of date after a successful mutating Operation; `checked_at`
  comes from the Desktop clock at reply.
- **Configuration** (§3.3): a value is passed through as a string and the
  refusal text is `config.py`'s, byte for byte.
- **Import rule** (§3.1): `cli.py`, `push.py`, `service.py` and the operations
  module import with `fastapi` absent (a subprocess with it hidden).
- **Unit actions** (§9.4): `systemctl` is faked; `unit_missing` and a
  verbatim `systemctl` error are both rendered.

### 12.3 The installer

The installer stays a shell script with no test harness, as today. Its checks
move to §13.

### 12.4 The page

There is no JS test runner (no node, §3.2). The page is checked by hand against
§10.7's state list, driven from the real service with the operations faked or
against the dev Pi. Keep page logic thin: everything a test should pin lives in
Python.

---

## 13. Hardware verification the implementation map owes

On the dev Pi, from the Desktop, written up as
`docs/web-ui-verification.md` in the shape of the earlier verification
documents:

1. **From scratch**: a Desktop user with no units installed runs the curl
   bootstrap's Desktop role; the browser opens; Setup discovers the Pi, pairs,
   starts `zeropi-push`, and shows the first Verdict. Both layouts: clone and
   standalone.
2. **Two Pis in range**, if a second board is available: the list shows both
   and Pair waits for a choice. If not available, record that it was not run.
3. Every **state of §10.7** that real conditions can produce: Pi powered off
   (`absent`), the CLI holding the link (`busy`), a `push` running in one tab
   with a second tab attaching, `zeropi-web` restarted mid-`push` (*outcome
   unknown*), `zeropi-push` stopped (banner), a `wipe` with its re-push
   colliding with the service (two-phase partial).
4. **The guard against a real browser**: a page served from another local port
   attempts a form `POST` and a JSON `fetch` to `127.0.0.1:8737` and both fail;
   `curl -H 'Host: evil.example'` gets `400`; the page refuses to load in an
   `<iframe>`.
5. **A `→ Pi` Setting** saved from Configuration reaches the Pi, as #87 checked
   for the CLI.
6. **Reboot survival**: after a Desktop reboot and login, `zeropi-web` is up
   and `zeropi-push` is running, and `journalctl --user -u zeropi-web` shows
   output (the `PYTHONUNBUFFERED` check).

---

## 14. Out of scope, and known limitations

**Out of scope** for this spec:

- **LAN access, the shared token and its lifecycle.** Localhost only.
- **Attackers that are local processes or other OS accounts** (§8.1).
- **An HTTP service on the Pi**, **real auth or TLS**, and **panel refresh
  accounting** (inherited from management surface §12).
- **Pi provisioning from the browser.** The Pi's curl bootstrap stays.
- **Cancelling an Operation** (§6.4).
- **A JS test runner or any build step.**

**Known limitations this UI inherits** from the substrate. Both were on map
#89's fog and were ruled out of scope there, because neither is a web UI
question and the UI's design does not change when either is fixed underneath
it:

- **Push marks are not per-Pi** (raised on #87). A replacement Pi, or
  `push.py --resend-all`, produces a false *not working*, in the CLI and the
  page alike.
- **Status is requested, never pushed** (ADR-0012). A panel stuck at 03:00
  stays invisible until someone checks; an open page does not change that
  (§7.1).
