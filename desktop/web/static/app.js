// The page (web-ui spec §10): three client-side tabs over the JSON the
// operations module produces. Kept thin on purpose (§12.4): every rule and
// every result sentence a test should pin lives in Python. ⚠ No timer here
// ever touches BLE (§7.1); the one interval re-renders "3m ago", nothing else.
import { h, render } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);

const TABS = [
  { path: "/", label: "Status" },
  { path: "/config", label: "Configuration" },
  { path: "/setup", label: "Setup" },
];

// ------------------------------------------------------------------ helpers

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const resp = await fetch(path, opts);
  const data = resp.status === 204 ? null : await resp.json().catch(() => null);
  return { status: resp.status, data };
}

// A failed request as a refusal, so nothing fails silently: the service's own
// refusal when it sent one, else the bare status.
function failure({ status, data }) {
  return (data && data.refusal) || { kind: `http_${status}`, message: `The web service answered ${status}. Nothing was done.`, rationale: null };
}

// §9.4's Desktop-side actions on zeropi-push: null when done, else a refusal
// with systemctl's own text verbatim.
async function unitAction(action) {
  const resp = await api(`/api/service/${action}`, {}).catch(() => ({ status: 0, data: null }));
  if (resp.status === 200) return null;
  if (resp.data && resp.data.systemctl !== undefined) {
    return { kind: "systemctl", message: `✗ Failed to ${action} zeropi-push: ${resp.data.systemctl}`, rationale: null };
  }
  return failure(resp);
}

// cli.py's `_dur`, for the durations the page shows as they were (§7.4).
function dur(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (d) return `${d}d ${hh}h`;
  if (hh) return `${hh}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}
const ago = (seconds) => (seconds === null || seconds === undefined ? "never" : `${dur(seconds)} ago`);
const hhmm = (date) => date.toTimeString().slice(0, 5);
const sinceIso = (iso) => (Date.now() - new Date(iso)) / 1000;

// Glyphs for the Verdict's states, the checks' severities and an outcome.
const HEAD = { ok: ["✓", "ok"], not_working: ["✗", "fail"], cant_tell: ["?", "unk"], not_paired: ["?", "unk"] };
const SEV = { ok: ["✓", "ok"], fail: ["✗", "fail"], note: ["·", "note"] };
const OUTCOME = { ok: ["✓", "ok"], fault: ["✗", "fail"], cant_tell: ["?", "unk"], refused: ["✗", "fail"] };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// The Operation this page started or attached to, kept so a reload can tell
// a `zeropi-web` restart from a finished Operation (§6.5).
const OP_KEY = "zeropi-web.op";
function remember(op) {
  try {
    op ? localStorage.setItem(OP_KEY, JSON.stringify(op)) : localStorage.removeItem(OP_KEY);
  } catch {}
}
function remembered() {
  try {
    return JSON.parse(localStorage.getItem(OP_KEY));
  } catch {
    return null;
  }
}

function useTick() {
  const [, set] = useState(0);
  useEffect(() => {
    const i = setInterval(() => set((x) => x + 1), 15000);
    return () => clearInterval(i);
  }, []);
}

// ------------------------------------------------------------------ the surface

function useSurface() {
  const [desktop, setDesktop] = useState(null);
  const [slot, setSlot] = useState(undefined); // undefined: not loaded; null: empty (§7.2)
  const [lastCheck, setLastCheck] = useState(null); // this page's latest status result, failed ones included
  const [lastDiscover, setLastDiscover] = useState(null); // Setup's list (§9.3)
  const [loaded, setLoaded] = useState(false); // nothing starts by itself before load() reattached (§6.5)
  const autoChecked = useRef(false);
  const autoDiscovered = useRef(false);
  const [op, setOp] = useState(null);
  const [showOp, setShowOp] = useState(false);
  const [refusal, setRefusal] = useState(null);
  const [configVersion, setConfigVersion] = useState(0);
  const source = useRef(null);

  const refreshDesktop = async () => {
    const d = (await api("/api/desktop")).data;
    setDesktop(d);
    return d;
  };
  const refreshSlot = async () => {
    const s = (await api("/api/status/last")).data;
    setSlot(s);
    return s;
  };

  function attach(id, verb) {
    if (source.current) source.current.close();
    remember({ id, verb });
    setOp({ id, verb, running: true, events: [], result: null });
    const es = (source.current = new EventSource(`/api/operations/${id}/events`));
    es.addEventListener("progress", (e) => setOp((o) => (o.id === id ? { ...o, events: [...o.events, JSON.parse(e.data)] } : o)));
    es.addEventListener("result", (e) => {
      es.close();
      remember(null);
      const result = JSON.parse(e.data);
      setOp((o) => (o.id === id ? { ...o, running: false, result } : o));
      if (verb === "status") setLastCheck(result);
      if (verb === "discover") setLastDiscover(result);
      // §7.6: Desktop facts refresh after every Operation.
      refreshDesktop();
      refreshSlot();
      setConfigVersion((v) => v + 1);
    });
    es.onerror = async () => {
      // EventSource would reconnect and replay the buffer on its own; decide here instead.
      es.close();
      const current = await api("/api/operations/current").then((r) => r.data, () => null); // down: as restarted
      if (source.current !== es) return; // another Operation was attached meanwhile
      if (current && current.id === id) attach(id, verb);
      else {
        remember(null);
        setOp({ id, verb, running: false, unknown: true, events: [] }); // §6.5: never guess, never retry
      }
    };
  }

  // `quiet`: §7.3's automatic check shows in the hero and the tab bar, not a modal.
  async function start(verb, args = {}, quiet = false) {
    setRefusal(null);
    const resp = await api("/api/operations", { verb, args }).catch(() => ({ status: 0, data: null }));
    if (resp.status === 202) {
      attach(resp.data.id, verb);
      setShowOp(!quiet);
    } else if (resp.status === 409) {
      follow(resp.data.running, verb, quiet);
    } else {
      setRefusal(failure(resp));
    }
  }

  // busy_here: follow the running Operation instead (§6.3).
  async function follow(running, verb, quiet = false) {
    const current = (await api("/api/operations/current")).data;
    attach(running, current ? current.verb : verb);
    setShowOp(!quiet);
  }

  async function load() {
    const [desk, s] = await Promise.all([refreshDesktop(), refreshSlot()]);
    const current = (await api("/api/operations/current")).data;
    const mine = remembered();
    if (current && current.running) attach(current.id, current.verb);
    else if (mine && (!current || current.id !== mine.id)) {
      // §6.5: a 404 for the one this page was following means zeropi-web restarted under it.
      const probe = await fetch(`/api/operations/${mine.id}/events`).then((r) => (r.body && r.body.cancel(), r.status), () => 0);
      if (probe === 404) {
        remember(null);
        setOp({ ...mine, running: false, unknown: true, events: [] });
      } else attach(mine.id, mine.verb);
    } else if (current) attach(current.id, current.verb);
    if (!(current && current.running)) autoCheck(desk, s);
    setLoaded(true);
  }

  // §7.3: one automatic check when the slot is empty -- on load, or once
  // Setup has paired (§9.5 step 4) -- and never again on this page.
  function autoCheck(desk, s) {
    if (autoChecked.current || s !== null || !desk || !desk.pi_address) return;
    autoChecked.current = true;
    start("status", {}, true);
  }

  // §9.3: one scan, automatically, when Setup opens with no Pi paired -- once per page.
  function autoDiscover(desk) {
    if (autoDiscovered.current || !desk || desk.pi_address) return;
    autoDiscovered.current = true;
    start("discover", {}, true);
  }

  useEffect(() => {
    load();
    const onVisible = () => document.visibilityState === "visible" && refreshDesktop();
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  return {
    desktop, slot, lastCheck, lastDiscover, loaded, op, showOp, setShowOp, refusal, setRefusal,
    start, follow, attach, autoCheck, autoDiscover, refreshDesktop, configVersion,
  };
}

// ------------------------------------------------------------------ pieces

function Banner({ desktop, go }) {
  // Not while unpaired: Setup starts zeropi-push only after a pair (§9.4).
  if (!desktop || !desktop.pi_address) return null;
  const what = { failed: "has failed", stopped: "is not running", missing: "is not installed" }[desktop.service.unit];
  if (!what) return null;
  return html`<div class="banner" role="status">
    ⚠ zeropi-push ${what} — nothing is pushing usage to the Pi. This is a Desktop fact, not part of the Verdict.${" "}
    <a href="/setup#service" onClick=${(e) => go(e, "/setup#service")}>Fix in Setup →</a>
  </div>`;
}

function RefusalBox({ refusal, onDismiss }) {
  if (!refusal) return null;
  return html`<div class="refusal" role="alert">
    ${refusal.message}${refusal.rationale && html`<div class="rationale">${refusal.rationale}</div>`}
    <div class="small mute">refusal: <code>${refusal.kind}</code>${onDismiss && html` · <button class="link" onClick=${onDismiss}>dismiss</button>`}</div>
  </div>`;
}

function OpModal({ sv }) {
  const { op, setShowOp, start } = sv;
  const closeButton = useRef(null);
  const open = op && sv.showOp;
  const close = () => setShowOp(false);

  useEffect(() => {
    if (!open) return;
    closeButton.current && closeButton.current.focus();
    const onKey = (e) => e.key === "Escape" && close();
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [open]);

  if (!open) return null;
  const r = op.result;
  const glyph = r && OUTCOME[r.outcome];
  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && close()}>
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="op-title">
      <h2 id="op-title">${op.verb} ${op.running ? html`<span class="unk small">● running</span>` : html`<span class="mute small">${op.unknown ? "outcome unknown" : "finished"}</span>`}</h2>
      ${op.unknown
        ? html`<div class="refusal">The web service restarted while <b>${op.verb}</b> was running. Its outcome is unknown. Nothing was queued. Check status.</div>`
        : html`<div class="log mono" aria-live="polite">${op.events.map((e) => html`<div><span class="k">${e.kind}</span>${e.text}</div>`)}${op.running && html`<div class="mute">…</div>`}</div>`}
      ${r && html`<div class=${r.outcome === "ok" ? "info" : "refusal"}>
        <span class=${glyph[1]}>${glyph[0]}</span> ${r.summary}
        <div class="small mute">outcome: <code>${r.outcome}</code></div>
      </div>`}
      <div class="actions">
        ${r && r.phases && r.phases.repush && r.phases.repush !== "ok" && html`<button onClick=${() => start("push")}>Push now</button>`}
        <button ref=${closeButton} onClick=${close}>${op.running ? "Hide (keeps running)" : "Close"}</button>
      </div>
      ${op.running && html`<p class="small mute">Runs to completion; there is no cancel. A scan takes up to 10s, a Batch about two minutes.</p>`}
    </div>
  </div>`;
}

function WipeConfirm({ desktop, onWipe, onCancel }) {
  const [typed, setTyped] = useState("");
  return html`<div class="confirm">
    <b>Wipe the Pi's Readings?</b> This deletes all ${desktop.readings_sent} Readings on the Pi, then re-pushes them from here.
    The Pi will show the empty frame until the first Batch lands.
    <div class="row">
      <label>Type the Pi's short id <code>${desktop.short_id}</code>: <input value=${typed} onInput=${(e) => setTyped(e.target.value)} size="8" autofocus /></label>
      <button class="danger" onClick=${() => onWipe(typed)}>Wipe</button>
      <button onClick=${onCancel}>Cancel</button>
    </div>
  </div>`;
}

// ------------------------------------------------------------------ Status (§10.2)

function CheckedAt({ shown, desktop }) {
  useTick(); // only the relative part ticks (§7.4)
  const at = new Date(shown.checked_at);
  let label = null;
  if (shown.outdated_by) label = `before the ${shown.outdated_by.verb} at ${hhmm(new Date(shown.outdated_by.at))}`;
  else if (desktop && desktop.last_batch_at && new Date(desktop.last_batch_at) > at) {
    label = `before a Batch at ${hhmm(new Date(desktop.last_batch_at))}`; // §7.5: labelled, never recomputed
  }
  return html`<span class="when"> · checked ${hhmm(at)} (${ago(sinceIso(shown.checked_at))})</span>${label && html` <span class="stale">${label}</span>`}`;
}

function Status({ sv }) {
  const { desktop, slot, lastCheck, op, start } = sv;
  const [wiping, setWiping] = useState(false);
  const running = op && op.running;

  if (slot === undefined || !desktop) return html`<p class="mute">Loading…</p>`;
  // A check with no reply empties the slot (§7.3), but its result is still
  // the last thing known: show it until a newer check replaces it.
  const shown = slot || (lastCheck && { result: lastCheck, checked_at: lastCheck.checked_at, outdated_by: null });
  const r = shown && shown.result;
  const head = r && HEAD[r.verdict.state];
  const svc = desktop.service;
  const desktopLine = `zeropi-push ${svc.state}${svc.uptime_s !== null ? `, up ${dur(svc.uptime_s)}` : ""}, last Batch ${ago(desktop.seconds_since_last_batch)}, ${desktop.readings_sent} Readings sent`;
  const piLine = !r
    ? null
    : r.unreachable
      ? `last seen ${ago(r.seconds_since_last_push)}, holding ${r.desktop.readings_sent} Readings then`
      : r.pi
        ? `replied in ${r.round_trip_s !== null ? r.round_trip_s.toFixed(1) + "s" : "?"}${r.pi.uptime_s !== undefined ? `, up ${dur(r.pi.uptime_s)}` : ""}`
        : null;

  return html`<div>
    <section class="hero">
      <div class="headline">
        ${r
          ? html`<h1><span class=${head[1]}>${head[0]}</span> ${r.verdict.headline}<${CheckedAt} shown=${shown} desktop=${desktop} /></h1>`
          : html`<h1 class="mute">No status yet</h1><span class="mute small">${running ? (op.verb === "status" ? "Checking…" : `${op.verb} running…`) : "Nothing checked since zeropi-web started."}</span>`}
        <div class="small mute facts">Desktop: ${desktopLine}${piLine && html` · Pi: ${piLine}`}</div>
      </div>
      <div class="buttons">
        <button class="primary" disabled=${running} onClick=${() => start("status")}>Check now</button>
        <button disabled=${running} onClick=${() => start("push")}>Push now</button>
        <button disabled=${running} onClick=${() => start("redraw")}>Redraw</button>
        <button class="danger" disabled=${running} onClick=${() => setWiping(true)}>Wipe…</button>
      </div>
    </section>
    ${wiping && html`<${WipeConfirm} desktop=${desktop} onCancel=${() => setWiping(false)} onWipe=${(typed) => (setWiping(false), start("wipe", { confirm: typed }))} />`}
    <${RefusalBox} refusal=${sv.refusal} onDismiss=${() => sv.setRefusal(null)} />
    ${r && r.unreachable
      ? html`<div class="refusal">
          ${r.unreachable.detail && html`<div>${r.unreachable.detail}.</div>`}
          ${r.unreachable.reason === "absent" && html`<div>Normal when it is powered off or out of range. Not a fault.</div>`}
          ${r.advice && html`<div class="rationale">${r.advice}</div>`}
        </div>`
      : r && html`${r.advice && html`<div class="info">${r.advice}</div>`}
        <div class="grid">${r.checks.map((c) => html`<div class="tile">
          <div class=${"tile-head " + SEV[c.severity][1]}>${SEV[c.severity][0]} ${cap(c.name)}</div>
          <div class="small mono">${c.detail}</div>
        </div>`)}</div>`}
  </div>`;
}

// ------------------------------------------------------------------ Configuration (§10.3)

const show = (v) => (v !== null && typeof v === "object" ? JSON.stringify(v) : String(v));

function ConfigRow({ row, sv, onSaved, go }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [outcome, setOutcome] = useState(null);

  async function save() {
    const resp = await api(`/api/config/${encodeURIComponent(row.key)}`, { value }).catch(() => ({ status: 0, data: null }));
    const { status, data } = resp;
    if (status === 200) {
      setEditing(false);
      setOutcome({ ok: true, text: `Set ${row.key} = ${show(data.written)}. Pending restart.` });
      onSaved();
    } else if (status === 202 || status === 409) {
      // → Pi: an Operation (§6.1); or busy_here, so follow what is running (§6.3).
      setEditing(false);
      setOutcome(status === 409 ? { refusal: failure(resp) } : null);
      status === 202 ? (sv.attach(data.id, "config_set"), sv.setShowOp(true)) : sv.follow(data.running, "config_set");
      onSaved();
    } else {
      setOutcome({ refusal: failure(resp) });
    }
  }

  const range = row.lo !== null || row.hi !== null ? `${row.lo ?? ""} .. ${row.hi ?? ""}` : "";
  return html`
    <tr>
      <td class="mono">${row.key}${row.pending_restart && html` <span class="stale">pending restart</span>`}</td>
      <td class="mono value">${editing
        ? html`<input aria-label=${row.key} value=${value} onInput=${(e) => setValue(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && save()} />`
        : show(row.value)}</td>
      <td class="mono mute">${range}</td>
      <td>${row.to_pi && html`<b>→ Pi</b>`}</td>
      <td class="edit">
        ${row.key === "pi.address"
          ? html`<a href="/setup" onClick=${(e) => go(e, "/setup")}>Pair in Setup…</a>`
          : editing
            ? html`<button class="primary" onClick=${save}>Save</button> <button aria-label="Cancel" onClick=${() => (setEditing(false), setOutcome(null))}>×</button>`
            : html`<button onClick=${() => (setEditing(true), setValue(show(row.value)), setOutcome(null))}>Edit</button>`}
      </td>
    </tr>
    ${outcome && html`<tr><td colspan="5">${outcome.refusal ? html`<${RefusalBox} refusal=${outcome.refusal} />` : html`<div class="info">${outcome.text}</div>`}</td></tr>`}`;
}

function Configuration({ sv, go }) {
  const { desktop } = sv;
  const [cfg, setCfg] = useState(null);
  const [notice, setNotice] = useState(null);
  const refresh = async () => {
    setCfg((await api("/api/config")).data);
    sv.refreshDesktop();
  };
  useEffect(() => {
    refresh();
  }, [sv.configVersion]);

  async function restart() {
    const refusal = await unitAction("restart");
    setNotice(refusal ? { refusal } : { ok: true, text: "Restarted zeropi-push." });
    refresh();
  }

  if (!cfg || !desktop) return html`<p class="mute">Loading…</p>`;
  const pending = desktop.pending_restart;
  const table = (rows) => html`<table><tbody>${rows.map((row) => html`<${ConfigRow} key=${row.key} row=${row} sv=${sv} onSaved=${refresh} go=${go} />`)}</tbody></table>`;
  return html`<div>
    ${pending.length > 0 && html`<div class="info">${pending.length} change(s) pending restart: <span class="mono">${pending.join(", ")}</span>.
      <button class="primary" onClick=${restart}>Restart zeropi-push</button></div>`}
    ${notice && (notice.refusal ? html`<${RefusalBox} refusal=${notice.refusal} onDismiss=${() => setNotice(null)} />` : html`<div class="info">${notice.text}</div>`)}
    <h3>Tier 1 — deployment facts (freely editable)</h3>
    ${table(cfg.tier1)}
    <h3>Tier 2 — policy (editable within a validated range)</h3>
    ${table(cfg.tier2)}
    <h3>Tier 3 — verified invariants. Not settings; shown so you can see them.</h3>
    <table><tbody>${cfg.tier3.map((t) => html`<tr>
      <td class="mono">${t.key}</td><td class="mono">${t.value}</td><td class="mono mute">${t.fixed_by}</td><td class="small mute">${t.why}</td>
    </tr>`)}</tbody></table>
  </div>`;
}

// ------------------------------------------------------------------ Setup (§9.5)

// One step of the checklist: what is true now, never stored progress.
function Step({ n, title, done, id, children }) {
  return html`<section class="step" id=${id}>
    <h3><span class=${done ? "ok" : "mute"}>${done ? "✓" : `${n}.`}</span> ${title}</h3>
    ${children}
  </section>`;
}

// Step 1 while choosing a Pi: the last discover's own summary (§9.3), and its list.
function PiList({ result, scanning, pick, setChosen }) {
  if (scanning) return html`<p class="mute">Scanning for 10s…</p>`;
  if (!result) return null;
  const devices = result.devices || [];
  if (devices.length === 0) return html`<div class="refusal">${result.summary}</div>`;
  return html`<fieldset class="pis">
    <legend class="small mute">${result.summary}</legend>
    ${devices.map((d) => html`<label><input type="radio" name="pi" checked=${pick === d.address} onChange=${() => setChosen(d.address)} />
      ${" "}${d.name} <code>${d.address}</code> <span class="small mute">${d.rssi} dBm</span></label>`)}
  </fieldset>`;
}

function Setup({ sv, go }) {
  const { desktop, slot, lastCheck, lastDiscover, op, start } = sv;
  const [chosen, setChosen] = useState(null);
  const [repairing, setRepairing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState(null);
  const running = op && op.running;
  const paired = desktop && desktop.pi_address;

  useEffect(() => {
    if (sv.loaded && !running) sv.autoDiscover(desktop);
  }, [sv.loaded, desktop, running]);

  // §9.5 step 4: after a pair, the empty slot's check -- once the pair's result has been read.
  useEffect(() => {
    if (sv.loaded && !running && !sv.showOp && !lastCheck) sv.autoCheck(desktop, slot);
  }, [sv.loaded, running, sv.showOp, desktop, slot]);

  // A successful pair ends the re-pair, also onto the same Pi, where `paired` never changes (#109).
  const pairedOk = op && op.verb === "pair" && op.result && op.result.outcome === "ok";
  useEffect(() => setRepairing(false), [paired]);
  useEffect(() => { if (pairedOk) setRepairing(false); }, [pairedOk]);

  // The banner's link to step 3 (§5.3), also on a reload: the steps render only once loaded.
  useEffect(() => {
    if (desktop && location.hash) document.querySelector(location.hash)?.scrollIntoView();
  }, [!!desktop]);

  async function startPush() {
    setNotice(await unitAction("start"));
    sv.refreshDesktop();
  }

  function scan() {
    setChosen(null);
    start("discover", {}, true);
  }

  if (!desktop) return html`<p class="mute">Loading…</p>`;
  const choosingPi = !paired || repairing;
  const devices = (lastDiscover && lastDiscover.devices) || [];
  const pick = chosen || (devices.length === 1 ? devices[0].address : null);
  const unit = desktop.service.unit;
  const checked = slot ? slot.result : lastCheck;
  const verdictResult = checked && checked.verdict ? checked : null; // a refused check carries no Verdict

  return html`<div class="setup">
    <${RefusalBox} refusal=${sv.refusal} onDismiss=${() => sv.setRefusal(null)} />

    <${Step} n="1" title="Find the Pi" done=${!choosingPi}>
      ${choosingPi
        ? html`<${PiList} result=${lastDiscover} scanning=${running && op.verb === "discover"} pick=${pick} setChosen=${setChosen} />
            <button disabled=${running} onClick=${scan}>Scan again</button>`
        : html`<p>Paired with <code>${desktop.pi_address}</code> (short id <code>${desktop.short_id}</code>).
            ${!confirming && html` <button class="link" onClick=${() => setConfirming(true)}>Pair a different Pi…</button>`}</p>
          ${confirming && html`<div class="confirm">
            <b>Pair a different Pi?</b> This Desktop is paired with <code>${desktop.pi_address}</code>. Pairing another replaces it.
            <div class="row">
              <button class="danger" onClick=${() => { setConfirming(false); setRepairing(true); scan(); }}>Find another Pi</button>
              <button onClick=${() => setConfirming(false)}>Cancel</button>
            </div>
          </div>`}`}
    <//>

    <${Step} n="2" title="Pair" done=${!choosingPi}>
      ${choosingPi
        ? html`<p class="small">⚠ If this Pi was paired with another computer, it clears its history and takes this computer's.</p>
            <button class="primary" disabled=${running || !pick} onClick=${() => start("pair", { address: pick })}>Pair${pick ? ` with ${pick}` : ""}</button>
            ${!pick && devices.length > 1 && html` <span class="small mute">Choose a Pi first.</span>`}`
        : html`<p class="mute small">Paired. This Desktop's Readings go to this Pi.</p>`}
    <//>

    <${Step} n="3" id="service" title="Start zeropi-push" done=${paired && unit === "running"}>
      ${!paired
        ? html`<p class="mute small">After pairing: an unpaired zeropi-push would push to whichever Pi it heard first.</p>`
        : unit === "running"
          ? html`<p>zeropi-push running${desktop.service.uptime_s !== null ? `, up ${dur(desktop.service.uptime_s)}` : ""}.</p>`
          : html`<p>zeropi-push ${{ failed: "has failed", missing: "is not installed" }[unit] || "is not running"}. It pushes usage to the Pi.</p>
              <button class="primary" onClick=${startPush}>Start zeropi-push</button>`}
      <${RefusalBox} refusal=${notice} onDismiss=${() => setNotice(null)} />
    <//>

    <${Step} n="4" title="First status" done=${verdictResult && verdictResult.verdict.state === "ok"}>
      ${!paired
        ? html`<p class="mute small">After pairing.</p>`
        : verdictResult
          ? html`<p><span class=${HEAD[verdictResult.verdict.state][1]}>${HEAD[verdictResult.verdict.state][0]}</span> ${verdictResult.verdict.headline}
              ${" "}<a href="/" onClick=${(e) => go(e, "/")}>Status →</a></p>`
          : html`<p class="mute">${running && op.verb === "status" ? "Checking…" : "No status yet."}</p>`}
    <//>
  </div>`;
}

// ------------------------------------------------------------------ the app

function App() {
  const [path, setPath] = useState(location.pathname);
  const sv = useSurface();

  useEffect(() => {
    const onPop = () => setPath(location.pathname);
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);

  const go = (e, to) => {
    if (e) e.preventDefault();
    history.pushState(null, "", to);
    setPath(location.pathname);
    if (location.hash) requestAnimationFrame(() => document.querySelector(location.hash)?.scrollIntoView());
  };

  // §9.5: Status redirects to Setup while no Pi is paired.
  useEffect(() => {
    if (path === "/" && sv.desktop && !sv.desktop.pi_address) {
      history.replaceState(null, "", "/setup");
      setPath("/setup");
    }
  }, [path, sv.desktop]);

  const tab = TABS.find((t) => t.path === path) || TABS[0];
  const op = sv.op;
  return html`
    <${Banner} desktop=${sv.desktop} go=${go} />
    <nav class="tabs">
      ${TABS.map((t) => html`<a href=${t.path} class=${t === tab ? "on" : ""} aria-current=${t === tab ? "page" : undefined} onClick=${(e) => go(e, t.path)}>${t.label}</a>`)}
      <span class="spacer"></span>
      ${op && html`<button class="link op" onClick=${() => sv.setShowOp(true)}>
        ${op.running ? html`<span class="unk">● ${op.verb} running</span>` : `last: ${op.verb}${op.unknown ? " (outcome unknown)" : ""}`}</button>`}
    </nav>
    <main>
      ${tab.path === "/" && html`<${Status} sv=${sv} />`}
      ${tab.path === "/config" && html`<${Configuration} sv=${sv} go=${go} />`}
      ${tab.path === "/setup" && html`<${Setup} sv=${sv} go=${go} />`}
    </main>
    <${OpModal} sv=${sv} />
  `;
}

render(html`<${App} />`, document.getElementById("app"));
