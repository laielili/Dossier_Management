/* ============================================================
   Dossier_Management — HTML → PPTX page

   Why the conversion happens here and not in Python
   -------------------------------------------------
   The vendored `html-to-pptx` library is NOT an HTML parser: it walks a
   *rendered* DOM and reads getComputedStyle() / getBoundingClientRect() /
   offsetWidth to place every element on the slide. Those values only exist
   after a real CSS layout engine has laid the page out, so the pasted markup
   is rendered in an off-screen, same-origin <iframe> — the user's browser IS
   the layout engine. The bundle is injected into that iframe, produces the
   pptx as base64, and the bytes are POSTed to the backend, which writes the
   file into the user-chosen folder (a plain browser download cannot target a
   specific directory).

   Flow:
     textarea → strip fences → iframe.write() → wait for layout/fonts/images
     → detect page class → inject /html-to-pptx/dist/html-to-pptx.min.js
     → HtmlToPptx.exportHtmlToPpt(cls, "base64") → POST /html2pptx/save
   ============================================================ */

const $ = (sel) => document.querySelector(sel);

// The vendored bundle (IIFE, global `HtmlToPptx`, pptxgenjs inlined).
// Served by the `/html-to-pptx` static mount in src/api.py.
const BUNDLE_URL = window.location.origin + "/html-to-pptx/dist/html-to-pptx.min.js";

// Class names AI tools commonly use for a slide container, tried in order.
const KNOWN_PAGE_CLASSES = [
  "page", "slide", "h-ppt-page", "ppt-page", "pptx-page", "ppt-slide", "sld",
];
// Class injected when nothing known is found and we tag blocks ourselves.
const GENERATED_PAGE_CLASS = "h2p-auto-page";

// Off-screen stage size. Pages with their own fixed width are unaffected —
// the converter scales each page by its own offsetWidth — but responsive
// layouts need a sane 16:9 viewport to lay out against.
const STAGE_W = 1280;
const STAGE_H = 720;

// ---------------------------------------------------------------- logging

function log(message, level = "info") {
  const area = $("#log-area");
  const span = document.createElement("span");
  span.className = `log-${level}`;
  span.textContent = `[${new Date().toLocaleTimeString()}] ${message}\n`;
  area.appendChild(span);
  area.scrollTop = area.scrollHeight;
}

function setButtonLoading(btn, loading, label) {
  if (loading) {
    btn.dataset.originalText = btn.dataset.originalText || btn.textContent;
    btn.innerHTML = '<span class="spinner"></span>' + (label || btn.dataset.originalText);
    btn.disabled = true;
  } else {
    btn.textContent = btn.dataset.originalText || btn.textContent;
    btn.disabled = false;
  }
}

function showResult(text, ok = true) {
  const box = $("#run-result");
  const p = $("#run-result-text");
  p.className = ok ? "success" : "log-error";
  p.style.color = ok ? "" : "var(--danger)";
  p.textContent = text;
  box.classList.remove("hidden");
}

// ------------------------------------------------------- output folder cfg

function outputFolder() {
  return ($("#output-folder").value || "").trim();
}

function syncDestPath() {
  const base = outputFolder().replace(/[\\/]+$/, "");
  const name = fileNameBase();
  $("#dest-path").textContent = base
    ? `${base}\\${name}.pptx`
    : `<Output Folder>\\${name}.pptx`;
}

async function loadOutputFolder() {
  try {
    const res = await fetch("/config/pptx-output");
    const data = await res.json();
    if (data.ok && data.path) {
      $("#output-folder").value = data.path;
      $("#folder-note").textContent = data.is_default
        ? "Using your system Downloads folder. Pick another folder and click Save Path to change it."
        : "Saved output folder.";
      if (!data.exists) {
        log("Output folder does not exist yet — it will be created on first save.", "warn");
      }
    }
  } catch (err) {
    log("Could not read the output-folder setting: " + err.message, "warn");
  }
  syncDestPath();
}

async function saveOutputFolder(path) {
  try {
    const res = await fetch("/config/pptx-output", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      log("Failed to save output folder: " + (data.detail || res.status), "error");
      return false;
    }
    $("#output-folder").value = data.path;
    $("#folder-note").textContent = "Saved output folder.";
    syncDestPath();
    return true;
  } catch (err) {
    log("Save output folder error: " + err.message, "error");
    return false;
  }
}

// -------------------------------------------------- folder picker (modal)

let currentBrowsePath = "";
let currentBrowseParent = null;

async function folderBrowseNavigate(path) {
  currentBrowsePath = path || "";
  try {
    const url = "/browse-folders" +
      (currentBrowsePath ? "?path=" + encodeURIComponent(currentBrowsePath) : "");
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) { log("Folder browse failed.", "error"); return; }
    renderFolderList(data);
  } catch (err) {
    log("Folder browse error: " + err.message, "error");
  }
}

function renderFolderList(data) {
  currentBrowseParent = data.parent || null;
  $("#folder-current").textContent = data.path
    ? data.path
    : (data.drives && data.drives.length ? "Select a drive" : "/");

  const list = $("#folder-list");
  list.innerHTML = "";
  const add = (label, target, extraClass) => {
    const item = document.createElement("div");
    item.className = "folder-item" + (extraClass ? " " + extraClass : "");
    item.textContent = label;
    item.addEventListener("click", () => folderBrowseNavigate(target));
    list.appendChild(item);
  };
  if (data.parent) add("..", data.parent, "folder-up");
  (data.drives || []).forEach((d) => add(d, d));
  (data.dirs || []).forEach((d) => add(d, d));
}

// ------------------------------------------------------------ input utils

/** Strip Markdown code fences an LLM may have wrapped the markup in. */
function stripCodeFences(text) {
  let s = (text || "").trim();
  const fence = /^```[a-zA-Z]*\s*\n([\s\S]*?)\n?```$/;
  const m = s.match(fence);
  if (m) return m[1].trim();
  // Tolerate an unterminated opening fence.
  if (/^```[a-zA-Z]*\s*\n/.test(s)) s = s.replace(/^```[a-zA-Z]*\s*\n/, "").trim();
  if (/\n```$/.test(s)) s = s.replace(/\n```$/, "").trim();
  return s;
}

function fileNameBase() {
  const raw = ($("#file-name").value || "").trim();
  return raw ? raw.replace(/\.pptx$/i, "") : "presentation";
}

function updateCodeStat() {
  const n = $("#html-input").value.length;
  $("#code-stat").textContent = n.toLocaleString() + " characters";
}

// ------------------------------------------------------- off-screen render

function isContentElement(el) {
  return !["SCRIPT", "STYLE", "LINK", "META", "TEMPLATE", "NOSCRIPT", "TITLE", "BASE"]
    .includes(el.tagName);
}

function isVisible(el) {
  return el.offsetWidth > 0 && el.offsetHeight > 0;
}

/** Create the off-screen iframe and write the markup into it. */
function createStage(html) {
  const stage = $("#render-stage");
  stage.innerHTML = "";
  const iframe = document.createElement("iframe");
  // Kept in the layout tree (NOT display:none) — the converter skips any page
  // whose offsetWidth/offsetHeight is 0, so the stage must really be laid out.
  iframe.setAttribute("title", "html-to-pptx render stage");
  iframe.style.cssText = [
    "position:fixed", "left:-20000px", "top:0",
    `width:${STAGE_W}px`, `height:${STAGE_H}px`,
    "border:0", "opacity:0", "pointer-events:none", "z-index:-1",
  ].join(";");
  stage.appendChild(iframe);

  const doc = iframe.contentDocument || iframe.contentWindow.document;
  doc.open();
  doc.write(html);
  doc.close();
  return iframe;
}

function waitFor(predicate, timeoutMs, everyMs = 60) {
  return new Promise((resolve) => {
    const started = Date.now();
    const tick = () => {
      let ok = false;
      try { ok = predicate(); } catch (e) { ok = false; }
      if (ok || Date.now() - started > timeoutMs) return resolve(ok);
      setTimeout(tick, everyMs);
    };
    tick();
  });
}

/** Resolve once every <img> has settled (loaded or failed), or on timeout. */
function waitForImages(doc, timeoutMs) {
  const imgs = Array.from(doc.images || []);
  if (!imgs.length) return Promise.resolve();
  const all = Promise.all(imgs.map((img) =>
    img.complete
      ? Promise.resolve()
      : new Promise((res) => { img.addEventListener("load", res, { once: true });
                               img.addEventListener("error", res, { once: true }); })
  ));
  return Promise.race([all, new Promise((r) => setTimeout(r, timeoutMs))]);
}

const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Wait until the staged document is fully laid out and stable. */
async function waitForLayout(iframe) {
  const doc = iframe.contentDocument;
  await waitFor(() => doc.readyState === "complete", 20000);
  try { if (doc.fonts && doc.fonts.ready) await Promise.race([doc.fonts.ready, sleep(8000)]); }
  catch (e) { /* fonts API unavailable — not fatal */ }
  await waitForImages(doc, 20000);
  // Runtime CSS generators (Tailwind CDN) need a tick after scripts finish.
  await nextFrame();
  await sleep(350);
  // Grow the stage so tall multi-slide decks are never clipped mid-layout.
  try {
    const h = Math.max(doc.documentElement.scrollHeight, doc.body.scrollHeight, STAGE_H);
    iframe.style.height = Math.min(h, 60000) + "px";
    await nextFrame();
  } catch (e) { /* ignore */ }
}

/**
 * Work out which CSS class marks a slide.
 * 1) Try the class the user typed (when not "auto").
 * 2) Try well-known slide class names.
 * 3) Fall back to tagging the document's top-level blocks as slides.
 */
function resolvePageClass(doc, requested) {
  const wanted = (requested || "").trim().replace(/^\./, "");

  if (wanted && wanted.toLowerCase() !== "auto") {
    const els = Array.from(doc.querySelectorAll("." + CSS.escape(wanted))).filter(isVisible);
    return { cls: wanted, count: els.length, how: `class .${wanted} (manual)` };
  }

  for (const c of KNOWN_PAGE_CLASSES) {
    const els = Array.from(doc.getElementsByClassName(c)).filter(isVisible);
    if (els.length) return { cls: c, count: els.length, how: `detected class .${c}` };
  }

  // Nothing known: descend through single-child wrappers to the level that
  // actually holds the slides, then tag those blocks ourselves.
  let level = Array.from(doc.body ? doc.body.children : []).filter(isContentElement);
  let guard = 0;
  while (level.length === 1 && guard++ < 12) {
    const kids = Array.from(level[0].children).filter(isContentElement);
    if (kids.length <= 1) break;
    level = kids;
  }
  level = level.filter(isVisible);
  if (!level.length) return { cls: null, count: 0, how: "no visible block found" };

  level.forEach((el) => el.classList.add(GENERATED_PAGE_CLASS));
  return {
    cls: GENERATED_PAGE_CLASS,
    count: level.length,
    how: `auto-tagged ${level.length} top-level block(s) as slides`,
  };
}

/** Load the converter bundle inside the staged document. */
function injectBundle(iframe) {
  return new Promise((resolve, reject) => {
    const doc = iframe.contentDocument;
    const win = iframe.contentWindow;
    if (win.HtmlToPptx) return resolve(win.HtmlToPptx);
    const s = doc.createElement("script");
    s.src = BUNDLE_URL;              // absolute — the staged doc has no useful base URL
    s.onload = () => win.HtmlToPptx
      ? resolve(win.HtmlToPptx)
      : reject(new Error("Bundle loaded but window.HtmlToPptx is missing"));
    s.onerror = () => reject(new Error("Could not load " + BUNDLE_URL));
    (doc.head || doc.documentElement).appendChild(s);
  });
}

// ------------------------------------------------------------- main action

async function convert() {
  const btn = $("#btn-run");
  const raw = $("#html-input").value;
  const html = stripCodeFences(raw);

  if (!html) { log("Paste some HTML code first.", "warn"); return; }
  if (!/</.test(html)) { log("That does not look like HTML markup.", "warn"); return; }
  const folder = outputFolder();
  if (!folder) { log("Choose an output folder first.", "warn"); return; }

  $("#run-result").classList.add("hidden");
  setButtonLoading(btn, true, "Rendering…");
  let iframe = null;

  try {
    log(`Rendering ${html.length.toLocaleString()} characters off-screen…`);
    iframe = createStage(html);
    await waitForLayout(iframe);
    const doc = iframe.contentDocument;

    const found = resolvePageClass(doc, $("#page-class").value);
    if (!found.cls || found.count === 0) {
      throw new Error(
        `No slide element found (${found.how}). Wrap each slide in an element ` +
        `with a shared class, e.g. <div class="page">…</div>, or type that class above.`
      );
    }
    $("#class-status").textContent = `.${found.cls} × ${found.count}`;
    log(`Slides: ${found.count} — ${found.how}`, "success");

    // A default file name from the deck's own <title> beats "presentation".
    if (!($("#file-name").value || "").trim()) {
      const t = (doc.title || "").trim();
      if (t) { $("#file-name").value = t; syncDestPath(); }
    }

    setButtonLoading(btn, true, "Converting…");
    log("Loading converter bundle into the render stage…");
    const api = await injectBundle(iframe);

    const base64 = await api.exportHtmlToPpt(found.cls, "base64");
    if (!base64 || typeof base64 !== "string") {
      throw new Error("Converter returned no data — check the browser console for details.");
    }
    log(`Deck built in the browser (${Math.round(base64.length * 0.75 / 1024)} KB).`, "success");

    setButtonLoading(btn, true, "Saving…");
    const res = await fetch("/html2pptx/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: fileNameBase(),
        data_base64: base64,
        output_dir: folder,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error(data.detail || `Server refused the file (HTTP ${res.status})`);
    }

    log(`Saved: ${data.path} (${data.size_kb} KB)`, "success");
    showResult(`✓ ${found.count} slide(s) → ${data.path}  ·  ${data.size_kb} KB`, true);
  } catch (err) {
    log("Conversion failed: " + err.message, "error");
    showResult("✕ " + err.message, false);
  } finally {
    setButtonLoading(btn, false);
    // Tear the stage down so a stale render can never leak into the next run.
    if (iframe) $("#render-stage").innerHTML = "";
  }
}

// ------------------------------------------------------------------ sample

const SAMPLE_HTML = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Sample Deck</title>
<style>
  body { margin:0; font-family: "Segoe UI", Arial, sans-serif; }
  .page { width:1280px; height:720px; position:relative; background:#FDFDFA;
          border-bottom:1px solid #ddd; overflow:hidden; }
  .page h1 { position:absolute; left:90px; top:230px; font-size:64px; color:#26261F; }
  .page h2 { position:absolute; left:90px; top:80px; font-size:44px; color:#2F6E5E; }
  .page p  { position:absolute; left:90px; top:340px; font-size:26px; color:#54534A; }
  .card    { position:absolute; top:200px; width:320px; height:220px; border-radius:14px;
             background:#E4EFEA; padding:24px; box-sizing:border-box; }
  .card b  { font-size:22px; color:#235A4C; }
  .c1 { left:90px; } .c2 { left:470px; } .c3 { left:850px; }
</style>
</head>
<body>
  <div class="page">
    <h1>Sample Deck</h1>
    <p>Generated from pasted HTML by Dossier Management</p>
  </div>
  <div class="page">
    <h2>Three Signals</h2>
    <div class="card c1"><b>CLINS</b><br>Clinical evidence</div>
    <div class="card c2"><b>FE</b><br>Sensory evaluation</div>
    <div class="card c3"><b>CE</b><br>Consumer evaluation</div>
  </div>
</body>
</html>`;

// ------------------------------------------------------------------- wiring

$("#html-input").addEventListener("input", updateCodeStat);
$("#file-name").addEventListener("input", syncDestPath);
$("#output-folder").addEventListener("input", syncDestPath);
$("#page-class").addEventListener("input", () => { $("#class-status").textContent = ""; });

$("#btn-clear").addEventListener("click", () => {
  $("#html-input").value = "";
  updateCodeStat();
  $("#class-status").textContent = "";
  $("#run-result").classList.add("hidden");
});

$("#btn-sample").addEventListener("click", () => {
  $("#html-input").value = SAMPLE_HTML;
  updateCodeStat();
  log("Loaded the 2-slide sample deck. Press Convert to verify the pipeline.");
});

$("#btn-paste").addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (!text) { log("Clipboard is empty.", "warn"); return; }
    $("#html-input").value = text;
    updateCodeStat();
    log("Pasted from clipboard.");
  } catch (err) {
    log("Clipboard blocked by the browser — use Ctrl+V in the editor instead.", "warn");
  }
});

$("#btn-save-folder").addEventListener("click", async () => {
  const p = outputFolder();
  if (!p) { log("Enter or choose a folder path first.", "warn"); return; }
  if (await saveOutputFolder(p)) log("Output folder saved.", "success");
});

$("#btn-browse-folder").addEventListener("click", () => {
  $("#folder-modal").classList.remove("hidden");
  folderBrowseNavigate(outputFolder());
});

$("#folder-select").addEventListener("click", async () => {
  if (!currentBrowsePath) { log("Navigate into a folder first, then select it.", "warn"); return; }
  $("#output-folder").value = currentBrowsePath;
  syncDestPath();
  $("#folder-modal").classList.add("hidden");
  if (await saveOutputFolder(currentBrowsePath)) log("Output folder saved.", "success");
});

$("#folder-up").addEventListener("click", () => {
  if (currentBrowseParent) folderBrowseNavigate(currentBrowseParent);
});
$("#folder-modal-close").addEventListener("click", () =>
  $("#folder-modal").classList.add("hidden"));
$("#folder-modal-backdrop").addEventListener("click", () =>
  $("#folder-modal").classList.add("hidden"));

$("#btn-run").addEventListener("click", convert);

// Config panel — hidden by default; the Config button reveals it and
// re-reads the configured output path so the user can adjust it there.
$("#btn-config").addEventListener("click", () => {
  $("#output-panel").classList.remove("hidden");
  loadOutputFolder();
  log("Opened output settings — configure the destination folder.", "info");
});
$("#btn-config-close").addEventListener("click", () => {
  $("#output-panel").classList.add("hidden");
});
$("#output-panel-backdrop").addEventListener("click", () => {
  $("#output-panel").classList.add("hidden");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#output-panel").classList.contains("hidden")) {
    $("#output-panel").classList.add("hidden");
  }
});

// --- init ---
updateCodeStat();
loadOutputFolder();
log("Ready. Paste the HTML source of your deck, then press Convert.");
