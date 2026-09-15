# Research: candidate stacks for the web UI

Ticket: [#90](https://github.com/peterderkoala/zeropi.display/issues/90), part of map [#89](https://github.com/peterderkoala/zeropi.display/issues/89).
Gathered 2026-09-15. **Facts only; this document does not pick a stack.** The pick belongs to the next grilling ticket.

The question: which small Python web frameworks and which no-build SPA approaches are realistic for a localhost-only (`127.0.0.1`) Desktop service that skins the Management Surface? The criteria are dependency footprint, asyncio fit with `bleak`, long requests and SSE, vendoring without npm, maintenance, licence, and whether it can be tested without a browser.

## Method and sources

- **Versions, licences, `requires_dist`, upload dates**: the PyPI JSON API (`https://pypi.org/pypi/<name>/json`) and the npm registry (`https://registry.npmjs.org/<name>`), queried on 2026-09-15.
- **Transitive footprint**: `uv pip compile` resolved against this repo's own baseline (`bleak`) under Python 3.12.3, the version of the repo's `.venv`. Package counts include `bleak==3.0.2` and `dbus-fast==5.0.22`, the two packages already installed.
- **Release cadence, archive state**: `gh release list` and `gh api repos/<owner>/<repo>`.
- **Vendorable file sizes**: jsDelivr's package listing API (`data.jsdelivr.com/v1/packages/npm/<pkg>@<ver>`) and `curl | wc -c` / `gzip -9 | wc -c` on the exact files.
- **Behaviour claims**: the official docs, linked where each claim is made.

## Facts about this repo that matter

- **Baseline Desktop dependencies**: `desktop/requirements.txt` is `bleak`, which resolves to `bleak` plus `dbus-fast`. Dev dependencies are `pytest` and `Pillow`. `pytest-asyncio` is not currently a dependency.
- **How the BLE code is called**: it is `async` throughout (`push.py`: `find_pi`, `BleConnection.__aenter__`, `send_one`, `ble_lock`, `write_settings`, `run_batch_pass`; `cli.py`: `_send_command`). Each entry point runs it with its own `asyncio.run(...)`: `cli.py:969`, `push.py:931`, `service.py:354`.
  - So a web process has two ways in. It can await these coroutines on the server's event loop (ASGI or aiohttp). Or, on a sync or WSGI server, it can call `asyncio.run(...)` per request in a worker thread, the way the CLI does.

## Part 1: Python web frameworks

### Summary table

| Candidate | Latest (PyPI upload) | Licence | Direct deps | Resolved set with `bleak` (total pkgs) | Server model | SSE | Test client without a browser |
|---|---|---|---|---|---|---|---|
| **Starlette** + uvicorn | starlette 1.6.0 (2026-08-08); uvicorn 0.53.0 (2026-09-14) | BSD-3-Clause (both) | starlette: `anyio`, `typing-extensions` (<3.13); uvicorn: `click`, `h11` | **9**: anyio, bleak, click, dbus-fast, h11, idna, starlette, typing-extensions, uvicorn | ASGI, async-native | `StreamingResponse` in core; `sse-starlette` 3.4.11 as an add-on | `starlette.testclient.TestClient`, a sync client on httpx or httpx2 (see note) |
| **FastAPI** + uvicorn | fastapi 0.141.1 (2026-07-29) | MIT | `starlette>=0.46`, `pydantic>=2.9`, `typing-extensions`, `typing-inspection`, `annotated-doc` | **15**: the Starlette set plus fastapi, pydantic, pydantic-core, annotated-types, annotated-doc, typing-inspection | ASGI (Starlette underneath) | Built in since 0.135.0: `fastapi.sse.EventSourceResponse` | `fastapi.testclient.TestClient` (re-export of Starlette's) |
| **Flask** `[async]` | flask 3.1.3 (2026-02-19); werkzeug 3.1.8 | BSD-3-Clause | blinker, click, itsdangerous, jinja2, markupsafe, werkzeug; `[async]` adds asgiref | **10**: asgiref, bleak, blinker, click, dbus-fast, flask, itsdangerous, jinja2, markupsafe, werkzeug | WSGI; each async view gets a fresh event loop in a thread | Streamed generator response (WSGI); no SSE helper | `app.test_client()` (Werkzeug), sync |
| **Quart** | 0.23.1 (2026-08-29) | MIT | aiofiles, blinker, click, **flask>=3.0**, hypercorn, itsdangerous, jinja2, markupsafe, werkzeug | **17** (includes all of Flask, plus hypercorn, h11, h2, hpack, hyperframe, priority, wsproto, aiofiles) | ASGI reimplementation of the Flask API | Streaming responses (async generators) | `app.test_client()`, async |
| **Litestar** + uvicorn | litestar 2.24.0 (2026-06-11) | MIT | anyio, click, httpx, litestar-htmx, msgspec, multidict, multipart, polyfactory, pyyaml, rich, rich-click, sniffio, typing-extensions, ... | **24**, including faker, polyfactory, rich, pygments, markdown-it-py, httpx, msgspec | ASGI | `ServerSentEvent` in core | `TestClient`, `AsyncTestClient`, `create_test_client` (httpx) |
| **aiohttp** (server) | 3.14.3 (2026-07-23) | Apache-2.0 AND MIT | aiohappyeyeballs, aiosignal, attrs, frozenlist, multidict, propcache, yarl | **11**: aiohappyeyeballs, aiohttp, aiosignal, attrs, bleak, dbus-fast, frozenlist, idna, multidict, propcache, yarl | Own asyncio server, no separate ASGI server | `web.StreamResponse` in core; `aiohttp-sse` 2.2.0 (2024-02-29) as an add-on | `pytest-aiohttp` 1.1.1 (`aiohttp_client` fixture; needs `pytest-asyncio`) |
| **stdlib** `http.server` | ships with Python | PSF | none | **2** (baseline) | Sync `HTTPServer` / `ThreadingHTTPServer`; no asyncio integration | Hand-written `wfile` writes | None built in: a real server on a port plus `urllib` |
| *(reference)* bottle | 0.13.4 (2025-06-15) | MIT | none (single file) | 3 | WSGI, sync | none | none built in (WebTest is third-party) |

Resolved sets are the `uv pip compile` output under Python 3.12.3. Compiled extension wheels in those sets: `pydantic-core` (FastAPI), `msgspec` (Litestar), `multidict`/`frozenlist`/`propcache`/`yarl` (aiohttp, Litestar via multidict), and `dbus-fast` (already present).

### Starlette

- **Dependencies**: `anyio<5,>=3.6.2`, plus `typing-extensions` below Python 3.13 ([PyPI](https://pypi.org/project/starlette/)). Starlette is a toolkit and needs a separate ASGI server; uvicorn's own dependencies are `click` and `h11` ([PyPI](https://pypi.org/project/uvicorn/)).
- **Maintenance**: the repository has moved to `github.com/Kludex/starlette`. Version 1.4.1 through 1.6.0 were released between 2026-08-05 and 2026-08-08. Python >= 3.10.
- **asyncio**: ASGI handlers are `async def` and run on the server's event loop, so `push.py` coroutines can be awaited directly.
- **Test client**: the current Starlette docs ([`docs/testclient.md`](https://github.com/Kludex/starlette/blob/main/docs/testclient.md)) say:
  - "The `TestClient` is built on `httpx2`. Plain `httpx` is still supported, but deprecated - install `httpx2` (included in `starlette[full]`) instead."
  - Calls "are just standard function calls, not awaitables".
  - The lifespan handler runs only when `TestClient` is used as a context manager.
  - It defaults to the `asyncio` backend through `anyio.start_blocking_portal()`.
  - The `full` extra lists both `httpx2>=2.0.0` and `httpx<0.29.0,>=0.27.0`. `httpx2` 2.13.0 is on PyPI (uploaded 2026-09-14). `httpx` 0.28.1 was last uploaded 2024-12-06.
  - **Note**: FastAPI's testing tutorial still says "install `httpx`" (see below). The two docs disagree about which client package is preferred.

### FastAPI

- **Dependencies**: `starlette>=0.46.0`, `pydantic>=2.9.0`, `typing-extensions`, `typing-inspection`, `annotated-doc` ([PyPI](https://pypi.org/project/fastapi/)). `pydantic` brings `pydantic-core`, a compiled wheel. Latest is 0.141.1 (2026-07-29). Releases are frequent: 0.140.12, 0.140.13, 0.141.0 and 0.141.1 all came out on 2026-07-28 and 2026-07-29. The version is still 0.x.
- **SSE**: built in since **0.135.0** ([docs](https://fastapi.tiangolo.com/tutorial/server-sent-events/)).
  - `from fastapi.sse import EventSourceResponse, ServerSentEvent`.
  - A path operation declared with `response_class=EventSourceResponse` that yields items sends each one JSON-encoded in the `data:` field.
  - The docs say FastAPI "handles keep-alive pings, cache prevention, and proxy buffering prevention automatically".
- **Test client**: `from fastapi.testclient import TestClient`, which the docs say is "the same `starlette.testclient`". Tests are plain `def` functions, "not using `await`" ([docs](https://fastapi.tiangolo.com/tutorial/testing/)).

### Flask (with the `async` extra)

From [Using async and await](https://flask.palletsprojects.com/en/stable/async-await/):

- **Install**: `pip install flask[async]` (adds `asgiref`). Routes and handlers may be `async def`.
- **How async views run**: "Flask, as a WSGI application, uses one worker to handle one request/response cycle. When a request comes in to an async view, Flask will start an event loop in a thread, run the view function there, then return the result." And: "Each request still ties up one worker, even for async views."
- **Background tasks**: "Async functions will run in an event loop until they complete, at which stage the event loop will stop ... Therefore you cannot spawn background tasks, for example via `asyncio.create_task`." The same page says tasks can be spawned by serving Flask with an ASGI server and the asgiref `WsgiToAsgi` adapter.
- **Pointer to Quart**: "If you have a mainly async codebase it would make sense to consider Quart ... This allows it to handle many concurrent requests, long running requests, and websockets without requiring multiple worker processes or threads."
- **Maintenance**: 3.1.3 (2026-02-19), 3.1.2 (2025-08-19), 3.1.1 (2025-05-13). Python >= 3.9.

### Quart

- **What it is**: an ASGI reimplementation of Flask by Pallets ([docs](https://quart.palletsprojects.com)). It **depends on `flask>=3.0`** plus `hypercorn`, `aiofiles` and the Pallets libraries ([PyPI](https://pypi.org/project/quart/)).
- **Python version**: 0.23.1 (2026-08-29) declares **`requires_python >=3.13`**. This repo's `.venv` is Python 3.12.3. `uv pip compile` still resolved `quart==0.23.1` here, so that resolution is not evidence it installs on 3.12; the metadata says it does not support it.

### Litestar

- **Footprint**: 2.24.0 (2026-06-11), MIT, Python >=3.8,<4.0 ([PyPI](https://pypi.org/project/litestar/)).
  - Its unconditional dependencies include `httpx`, `msgspec`, `polyfactory` (which pulls `faker`), `rich`, `rich-click`, `pyyaml` and `litestar-htmx`.
  - It has the largest resolved set here: 24 packages with uvicorn and bleak.
- **SSE**: core `ServerSentEvent` class, fed from an async generator that yields ints, strings, bytes, dicts or `ServerSentEventMessage` ([responses docs](https://docs.litestar.dev/latest/usage/responses.html)).
- **Testing** ([testing docs](https://docs.litestar.dev/latest/usage/testing.html)):
  - `TestClient`, `AsyncTestClient` and `create_test_client`, "built on top of the httpx library".
  - Documented caveat: for SSE with infinite generators, the standard test client "will lock up since HTTPX attempts to consume the full response before returning". Litestar provides `subprocess_sync_client()` and `subprocess_async_client()` for this case.
- **Maintenance**: v2.21.1 (2026-03-07), v2.22.0 (2026-05-20), v2.23.0 (2026-05-29), v2.24.0 (2026-06-11).

### aiohttp

- **What it is**: an asyncio HTTP client and server with its own server, so no uvicorn is needed ([PyPI](https://pypi.org/project/aiohttp/)). 3.14.3 (2026-07-23). Licence expression `Apache-2.0 AND MIT`. Python >= 3.10.
- **Dependencies**: seven direct dependencies, several of them compiled (`multidict`, `frozenlist`, `propcache`, `yarl`).
- **Streaming and SSE**: `web.StreamResponse` is core. The add-on `aiohttp-sse` 2.2.0 was last uploaded 2024-02-29.
- **Testing** ([docs](https://docs.aiohttp.org/en/stable/testing.html)):
  - The `pytest-aiohttp` plugin (1.1.1, 2026-06-07) provides the `aiohttp_client` fixture. It requires `pytest-asyncio>=0.17.2`, which is currently 1.4.0.
  - Tests are `async def`.
  - `TestServer` and `TestClient` can also be used without pytest.
  - The testing page gives no SSE-specific guidance.

### stdlib `http.server`

From the [Python docs](https://docs.python.org/3/library/http.server.html):

- **Warning**: "`http.server` is not recommended for production. It only implements basic security checks." The same page lists these risks:
  - `SimpleHTTPRequestHandler` follows symlinks.
  - `send_header()` does not validate input for CRLF sequences.
- **Concurrency**: `ThreadingHTTPServer` (added in 3.7) handles each request in a thread.
- **asyncio**: none. Handlers are synchronous, so BLE calls would need `asyncio.run(...)` per request, as `cli.py` already does.
- **Dependencies and tests**: no new dependencies. No test client; tests would bind a real port.

## Part 2: no-build SPA approaches

Every candidate below ships prebuilt files in its npm tarball. Any of those files can be downloaded once, over HTTPS from jsDelivr or the registry, and committed under a static directory without node or npm. Sizes are for the exact files named.

### Summary table

| Candidate | Latest (npm `latest`, date) | Licence | File(s) to vendor | Raw bytes | gzip -9 | Model | Streaming / SSE story | Maintenance signal |
|---|---|---|---|---|---|---|---|---|
| **Preact + htm** | preact 10.29.8 (2026-08-01); htm 3.1.1 (2022-04-26) | MIT; htm Apache-2.0 | `preact/dist/preact.module.js` + `preact/hooks/dist/hooks.module.js` + `htm/dist/htm.module.js`, or the single `htm/preact/standalone.module.js` | 11 693 + 3 647 + 1 207; standalone 13 194 | preact 4 835; htm 646 | Client-side components (VDOM) with tagged-template "JSX" | None built in; use the browser `EventSource` / `fetch` streams | preact: pushed 2026-09-15, 11.0.0-rc.2 on 2026-09-08. htm: last release 2022-04, last push 2024-02-01 |
| **@preact/signals** (optional add-on) | 2.11.2 (2026-09-04) | MIT | `signals.module.js` + `@preact/signals-core` `signals-core.module.js` | 4 299 + 5 533 | n/m | Reactive state for Preact | n/a | Active |
| **Alpine.js** | 3.17.3 (2026-09-14) | MIT | `dist/cdn.min.js` (script tag) or `dist/module.esm.min.js` | 55 886 / 55 870 | 19 896 | HTML attributes (`x-data`, `x-on`), client state | None built in | Weekly releases (3.17.1 to 3.17.3, 2026-08-31 to 09-14) |
| **htmx 2** | 2.0.10 (2026-04-21) | 0BSD | `dist/htmx.min.js` (+ `htmx-ext-sse` 2.2.4 `dist/sse.min.js`) | 51 238 (+ 2 853) | 16 527 | Server returns HTML fragments; attributes drive requests | Separate `htmx-ext-sse` extension (`hx-ext="sse"`, `sse-connect`, `sse-swap`) | See htmx 4 below |
| **htmx 4** | 4.0.0 (2026-08-28), npm dist-tag **`next`**, not `latest` | 0BSD (`BSD-0-Clause` in registry) | `dist/htmx.min.js` or `dist/htmx.esm.min.js` (+ `dist/ext/hx-sse.min.js`) | 36 716 (+ 6 225) | n/m | Same model, `fetch()`-based | `hx-sse` extension bundled in the package's `dist/ext/` | GitHub marks v4.0.0 "Latest". Migration from 2 has breaking changes |
| **petite-vue** | 0.4.1 (2022-01-18) | MIT | `dist/petite-vue.es.js` or `.iife.js` | 17 033 | 7 318 | Vue-template subset over existing DOM | None built in | Last push 2024-07-13; README: "the issue list is intentionally disabled" |
| **Lit** | 3.3.3 (2026-05-14) | BSD-3-Clause | Prebuilt bundles `lit-core.min.js` or `lit-all.min.js` from `lit/dist` | 15 734 / 29 370 | 6 104 / 10 210 | Web Components (custom elements, shadow DOM) | None built in | Pushed 2026-09-14 |

n/m = not measured.

### Preact + htm

- **No-build usage**: Preact's [No-Build Workflows guide](https://preactjs.com/guide/v10/no-build-workflows/) covers import maps to resolve bare specifiers (`preact`, `preact/hooks`, `@preact/signals`) and HTM "Tagged Templates" in place of JSX.
  - It warns that "duplication of preact and some other libraries will cause (often subtle and unexpected) issues".
  - The guide's examples point at esm.sh. For vendored files, the import map points at local paths instead.
  - `hooks.module.js` begins `import{options as n}from"preact"`, a bare specifier, so it needs an import map, or the file edited to a relative path.
- **Single file option**: `htm/preact/standalone.module.js` (13 194 bytes) bundles Preact, hooks and htm into one ES module with no imports to resolve.
- **Versions**: Preact 11 is at release candidate (`rc` dist-tag 11.0.0-rc.2, 2026-09-08). `latest` is still 10.29.8. htm has had no release since 3.1.1 (2022-04-26) and is not archived.

### Alpine.js

- **Install without npm**: the [installation docs](https://alpinejs.dev/essentials/installation) give a `<script defer src=".../alpinejs@3.x.x/dist/cdn.min.js">` tag and recommend pinning an exact version in production. Self-hosting means downloading that file.
- **ES module**: an ESM build `dist/module.esm.min.js` exists. The npm package declares `@vue/reactivity ~3.5.40` as a dependency. The built ESM file embeds `@vue/reactivity` v3.5.41 (its licence banner is inside the file), so it has no external imports to resolve.

### htmx (2.x and 4.x)

- **htmx 2 SSE**: SSE is a separate extension, `htmx-ext-sse` ([docs](https://htmx.org/extensions/sse/)).
  - It is enabled with `hx-ext="sse"` and uses `sse-connect="<url>"` and `sse-swap="<event>"`.
  - It installs by script tag, direct download, or npm.
- **Which major is current**:
  - On GitHub, **v4.0.0 is marked Latest** (2026-08-28).
  - On npm, `latest` is still **2.0.10** and 4.0.0 carries the `next` tag.
  - The [htmx 4 docs](https://four.htmx.org/docs/) say SSE is "not in core" and is handled by the `hx-sse` extension, that requests are `fetch()`-based, and that htmx 4 "is a single JavaScript file with no dependencies. No build step is required", with self-hosting recommended for production.
  - The docs describe "three major behavioral changes" from 2 and an upgrade checker run via `npx` (that tool needs node; the library does not).
- **Server shape**: htmx expects the server to return HTML fragments. That puts templating on the Python side: Jinja2 is already a Flask/Quart dependency, and an optional extra in Starlette's `full`.

### petite-vue

- **Install**: the [README](https://github.com/vuejs/petite-vue) installs via `<script src=".../petite-vue" defer init>` or an ES module import of `createApp`, and claims "Only ~6kb".
- **Maintenance**:
  - The README says "the issue list is intentionally disabled" and "Feature requests are unlikely to be accepted".
  - The last npm release is 0.4.1 (2022-01-18). The last push to the repository was 2024-07-13. The repository is not archived.

### Lit

- **Bundles**: the [getting started docs](https://lit.dev/docs/getting-started/) say Lit is "available as pre-built, single-file bundles" (`lit-core.min.js`, `lit-all.min.js`) that are "standard JavaScript modules with no dependencies".
  - The docs say that "if you're using npm for client-side dependencies, you should use the `lit` package, not these bundles".
  - The npm `lit` package itself depends on `lit-element`, `lit-html` and `@lit/reactive-element`, which resolve as bare specifiers. That is why the bundles, not the npm files, are the vendoring path.

## Cross-cutting facts

- **Transport for a 10 to 25 s BLE action**: any framework in Part 1 can hold a plain request open that long. Browsers' `EventSource` and `fetch` put no short timeout on a same-origin localhost request.
  - SSE helpers: FastAPI (`EventSourceResponse`) and Litestar (`ServerSentEvent`) have one in core.
  - Starlette and aiohttp have streaming responses in core and an SSE helper as a separate package.
  - Flask streams from a generator, but each open stream holds one WSGI worker thread (Flask docs quoted above).
- **Testing streams**: Litestar documents that httpx-based in-process test clients lock up on infinite SSE generators. The Starlette and FastAPI test clients are also httpx-based. Their docs quoted here do not state the same caveat, but nor do they document streaming tests.
- **Test style**:
  - Sync `def` tests, which match the current pytest harness: Starlette/FastAPI `TestClient`, Flask `test_client()`, Litestar `TestClient`.
  - Async tests that add `pytest-asyncio`: aiohttp (`pytest-aiohttp`), Quart `test_client()`, Litestar `AsyncTestClient`.
- **Binding to localhost**: uvicorn and hypercorn take `--host 127.0.0.1` (uvicorn's default host is `127.0.0.1`). Flask's dev server, aiohttp `web.run_app(host=...)` and `http.server` take the bind address as an argument.

## Open questions this research did not settle

- **Streaming tests**: whether Starlette/FastAPI's `TestClient` can read a *finite* SSE stream to completion in-process. This is expected, since a finite generator ends, but it was not exercised here. A prototype would answer it.
- **`httpx` vs `httpx2`**: which one Starlette's `TestClient` resolves to at the currently pinned 1.6.0, given that the Starlette and FastAPI docs disagree.
- **Quart on Python 3.12**: whether 0.23.1 actually installs on the repo's Python 3.12, given `requires_python >=3.13`.
