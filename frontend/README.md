# LegalAI Bangladesh: web UI

Vite, React 18, strict TypeScript and Tailwind CSS v3, with Radix primitives. The design system is in [`DESIGN.md`](DESIGN.md).

## Develop

```bash
npm ci
npm run dev        # http://localhost:5173, proxies /api to http://localhost:8001
```

The proxy needs an API on port 8001: start one with `python evals/serve_live.py` from the repo root. It takes about 3 minutes to load. Set `VITE_PROXY_TARGET` to point the proxy somewhere else.

## Build and serve

```bash
npm run build                     # typechecks, then builds to dist/
python ../evals/serve_live.py     # serves dist/ and the real pipeline at http://localhost:8001
```

`serve_live.py` caches hashed files in `assets/` for a year and revalidates `index.html`, `sw.js` and the manifest, with an SPA fallback. `--ui legacy` serves the old `legal_ui_v2.html`.

`api_v3.py` doesn't serve a UI. To host the build elsewhere, build with `VITE_API_BASE` set to the API's address, since the API allows any origin. How production will serve it hasn't been decided.

## Check

```bash
npm run typecheck
npm run lint
npm run format:check
npm run qa:unit                 # shared logic, on plain Node
npm run qa:contrast             # WCAG contrast for every text token on every surface
node qa/unknown-classes.mjs     # Tailwind classes that produce no CSS (after a build)
node qa/bundle-size.mjs         # first-load JS against the 120 KB budget (after a build)
```

The browser checks use your installed Chrome. Start `node qa/static-server.mjs` first: it serves `dist/` with the same cache rules and no pipeline. They use made-up responses and make no LLM calls.

```bash
npm run qa:parity               # request bodies against the old UI's (qa/payloads.before.json)
npm run qa:after                # every tab and state, at six sizes, in both themes, with layout checks
npm run qa:axe                  # axe-core on every tab, state and open dialog
npm run qa:lighthouse -- http://localhost:4173/ after
```

`node qa/live.mjs` runs each feature against the real pipeline on port 8001, and makes real LLM calls.

Screenshots and full reports stay local and gitignored, because real responses show judgement text and names. Only scripts, made-up fixtures and number-only summaries are committed. Open `qa/index.html` for the before-and-after contact sheet.

## Rules that are easy to break

- **API payloads are frozen.** Run `qa:parity` after touching anything that sends a request.
- **Judgement and LLM text is untrusted.** Render it as text only. ESLint forbids `dangerouslySetInnerHTML`, and links go through `safeUrl`.
- **Bangla is never below 14px.**
- **Don't present a number as more than it is.** See "Honest numbers" in `DESIGN.md`.
