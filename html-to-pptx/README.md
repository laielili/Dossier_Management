# html-to-pptx (vendored)

Vendored copy of the `html-to-pptx` library, used by the **HTML → PPTX** page of
Dossier_Management (`/html2pptx`).

Upstream: <https://github.com/joker-duzhong/html-to-pptx> (MIT, see `LICENSE`).
Vendored on 2026-08-10 from the locally-fixed fork at
`A:\Projects\OpenSourceProjects\html-to-pptx\html-to-pptx`
(the upstream `rollup@2` toolchain no longer builds; the build script was
replaced with `esbuild`).

---

## Why it runs in the browser, not in Python

The converter is **not** an HTML parser. It walks the live DOM and reads the
*rendered* geometry of every element:

| Source                     | API used                             |
| -------------------------- | ------------------------------------ |
| `src/analyze.ts:114 / 142` | `window.getComputedStyle(el)`        |
| `src/analyze.ts:247 / 388` | `el.getBoundingClientRect()`         |
| `src/analyze.ts:377`       | `document.querySelectorAll('.'+cls)` |
| `src/analyze.ts:386`       | `el.offsetWidth / offsetHeight`      |

Those values only exist once a real CSS layout engine has laid the page out.
BeautifulSoup / lxml cannot produce them, and jsdom does no layout — so any
server-side execution would require a headless browser (Playwright/Puppeteer).

**Chosen design:** the user's browser *is* the layout engine. `static/html2pptx.js`
renders the pasted HTML in an off-screen same-origin `<iframe>`, loads this
bundle inside it, and calls `exportHtmlToPpt(pageClass, 'base64')`. The base64
result is POSTed to `POST /html2pptx/save`, and **the backend writes the .pptx**
to the user-configured output folder. Zero new Python dependencies.

---

## Files

```
dist/html-to-pptx.min.js   the ONLY runtime artifact — IIFE bundle, global
                           `HtmlToPptx`, pptxgenjs inlined (~374 KB, offline)
src/*.ts                   TypeScript sources, kept so the bundle is rebuildable
package.json               esbuild-based build script
tsconfig.json              compiler options
LICENSE                    MIT
```

`node_modules/` is intentionally NOT vendored — nothing at runtime needs it.

## Reference paths inside Dossier_Management

| Where                | Path                                                            |
| -------------------- | --------------------------------------------------------------- |
| FastAPI static mount | `src/api.py` → `app.mount("/html-to-pptx", …PROJECT_ROOT/"html-to-pptx")` |
| Browser (in iframe)  | `/html-to-pptx/dist/html-to-pptx.min.js`                          |
| Injected by          | `static/html2pptx.js` (`BUNDLE_URL`)                              |

## Public API (global `HtmlToPptx`)

```js
exportHtmlToPpt(pageClassName = "page", outputType = "blob")  // → Promise<base64|Blob|…>
downloadHtmlToPpt(pageClassName = "page", fileName = "presentation")  // → browser download
```

Dossier_Management uses `exportHtmlToPpt(cls, "base64")` so the bytes can be
handed to the backend instead of going to the browser's download folder.

## Hard constraints on the pasted HTML

1. Every slide must be an element carrying the **page class** (default `page`);
   `document.querySelectorAll('.page')` → one slide per match. If the HTML has
   no such wrapper, the UI's auto-detect will look for `slide` / `h-ppt-page` /
   `ppt-page` / `section`, or the whole body is wrapped in one `.page`.
2. A page with `offsetWidth === 0 || offsetHeight === 0` is **silently skipped**
   (`analyze.ts:386`) — `display:none` pages never export.
3. Layout is 16:9 (`LAYOUT_16x9`); the page element's own width sets the scale.
4. Cross-origin images need CORS, otherwise they are dropped.

## Rebuilding the bundle

```bash
cd html-to-pptx
npm install          # esbuild + typescript + pptxgenjs
npm run build        # regenerates dist/html-to-pptx.min.js (+ cjs/esm builds)
```

Only `dist/html-to-pptx.min.js` needs to be committed back here.
