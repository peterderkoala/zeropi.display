// The page shell (web-ui spec §10.1): three client-side tabs on
// reload-stable routes. The tabs' content arrives with #107 and #108.
import { h, render } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);

const TABS = [
  { path: "/", label: "Status" },
  { path: "/config", label: "Configuration" },
  { path: "/setup", label: "Setup" },
];

function App() {
  const [path, setPath] = useState(location.pathname);

  useEffect(() => {
    const onPop = () => setPath(location.pathname);
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);

  const go = (e, to) => {
    e.preventDefault();
    history.pushState(null, "", to);
    setPath(to);
  };

  const tab = TABS.find((t) => t.path === path) || TABS[0];
  return html`
    <nav class="tabs">
      ${TABS.map(
        (t) => html`<a href=${t.path} class=${t === tab ? "active" : ""} onClick=${(e) => go(e, t.path)}>${t.label}</a>`
      )}
    </nav>
    <main><h1>${tab.label}</h1></main>
  `;
}

render(html`<${App} />`, document.getElementById("app"));
