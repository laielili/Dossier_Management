# `/docs` — static demo build

A **self-contained, backend-free replica of the front end** in `static/`, built for
flow demonstrations and published with GitHub Pages.

Every page here is a static copy: GitHub Pages cannot run FastAPI, PyMuPDF or Office
COM, so **all server calls are replaced by the canned dataset in `demo-data.js`**.
The pages stay clickable — steppers, modals, sliders and presets all work — but
nothing is computed, nothing is written to disk, and no `.pptx` is produced.

> **All data in this folder is simulated.** The project `P-TIOX`, its dossiers, its
> metrics and every percentage are invented for illustration. Nothing here is
> extracted from a real dossier and nothing here is evidence of anything.

---

## Publishing

The demo is designed to be served straight from the `/docs` folder of this repository:

1. Repository **Settings → Pages**
2. **Source**: *Deploy from a branch*
3. **Branch**: `main`, **Folder**: `/docs`
4. Save — the site appears at `https://<user>.github.io/Dossier_Management/`

`.nojekyll` is present so GitHub Pages serves the folder as-is without running Jekyll.
Asset paths are relative, so the site also works from a subpath (and from `file://`).

---

## What each page is

| File             | Mirrors                              | What the demo shows                                                                 |
| ---------------- | ------------------------------------ | ----------------------------------------------------------------------------------- |
| `index.html`     | `static/search.html` (`/`)           | Step 1 search & select → Step 2 preprocess, arriving with a finished run on screen.  |
| `ai-chat.html`   | *not in the repo* — the downstream AI | Mock L'Oréal GPT session: greeting, one-line project brief, three confirmations, then the `<deck>` XML. |
| `svg2ppt.html`   | `static/svg2ppt.html` (`/svg2ppt`)   | Deck XML → build → 3-page preview, with the Adjustments panel and presets live.      |

`file_listener.html` was intentionally **not** ported: it is the retired one-click entry
point, and its `app.js` calls 32 endpoints that no longer exist.

## The flow the demo tells

```
index.html  (search → select → preprocess)
      │  "L'Oréal GPT" button
      ▼
ai-chat.html  (greeting → brief → 3 confirmations → <deck> XML)
      │  auto-hand-off: XML written to localStorage, then redirect
      ▼
svg2ppt.html  (XML pre-loaded → build → preview)
```

`ai-chat.html` stays faithful to `prompt/system.md`: the assistant owns the visual
hierarchy and the project-level narrative, never computes coordinates or layout, and
the **front end** — not the AI — owns the output mode. The real client gates the
workflow behind `@extract` / `@summarize`; those exist only to work around the
hosting platform's routing limits, so this demo runs the conversation without them.

The hand-off is genuine: the XML the AI produces is written to
`localStorage.demo_deck_xml` and read back by `svg2ppt.html` on load. If storage is
unavailable (some browsers block it on `file://`), the converter falls back to its
built-in sample deck.

---

## Files

| File               | Role                                                                   |
| ------------------ | ---------------------------------------------------------------------- |
| `style.css`        | **Byte-identical copy** of `static/style.css` — the "Paper Workspace" design system. Do not edit here; edit the source and re-copy. |
| `demo.css`         | Demo-only chrome (footer note, AI transcript view), built from the same CSS variables. |
| `demo-data.js`     | Single source of the mock dataset: project, sources, logs, folder tree, config panels, `<deck>` XML, chat script, deck pages. |
| `search-app.js`    | `static/search-app.js` with every `fetch` replaced by canned state.     |
| `svg2ppt-app.js`   | `static/svg2ppt-app.js`, same treatment. Presets still use `localStorage` for real. |
| `ai-chat.js`       | The scripted AI session and the hand-off.                               |
| `demo/deck-page-*.svg` | The three pre-rendered deck pages shown in the preview strip (L'Oréal theme: banner `#e8c580`, summary band `#f5e6c6`, tab `#b8860b`, border `#d9a441`). |

### Changing the demo

Everything on screen traces back to `demo-data.js`. Changing the project name,
the source list, the log lines, the accepted chat replies or the deck XML there
updates the search results, the AI brief and the converter's pre-loaded input in
one place — no per-page edits.

The one exception is `demo/deck-page-*.svg`: those are hand-drawn mock renders, so
if you change the deck XML's content, re-draw them to match.
