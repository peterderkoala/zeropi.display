# Vendored ES modules

The page's three runtime modules (web-ui spec §3.2), committed as published
on npm and served from here. **No npm, node, bundler or `dist/` is involved**,
and the page loads nothing from a CDN at runtime: `index.html`'s import map
points at these files. Preact is pinned to the 10.x line on purpose (never 11).

To update one, download the new file from its URL below, replace it here and
update its version and sha256. `tests/test_web.py` checks every `.js` file in
this directory against the sha256 values in this README.

| File | Version | Source URL | sha256 |
|---|---|---|---|
| `preact.module.js` | preact 10.29.8 | https://unpkg.com/preact@10.29.8/dist/preact.module.js | `c30e721ebfdc6e2ad4c18c14d2dfb82667829c8aec27de1207774e3fc16858a8` |
| `hooks.module.js` | preact 10.29.8 (`preact/hooks`) | https://unpkg.com/preact@10.29.8/hooks/dist/hooks.module.js | `a6ee626f2d01570592dd569a792e3f050154aa02890eead8c223fa3ed5aa3d5a` |
| `htm.module.js` | htm 3.1.1 | https://unpkg.com/htm@3.1.1/dist/htm.module.js | `ab33dd3f38059b9be4d5f5350128eefb2356639c4e0bbe9d9e8b3ba75847e9e4` |
