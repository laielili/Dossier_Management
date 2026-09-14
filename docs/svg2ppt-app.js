/* ============================================================
   SVG -> PPTX front end — /docs demo build (semi-static)

   Static replica of static/svg2ppt-app.js. There is no layout engine on
   GitHub Pages, so the deck is pre-rendered (see demo/deck-page-*.svg) and
   this build never calls fetch(). Build / Re-render keep the page honest by
   re-revealing the pre-filled result, logging what the real server would
   have done, and leaving every control live.

   Simulated data only — see demo-data.js.
   ============================================================ */

(function () {
  "use strict";

  const DEMO = window.DEMO;
  const $ = (id) => document.getElementById(id);

  const xmlInput = $("xml-input");
  const codeStat = $("code-stat");
  const maxPages = $("max-pages");
  const maxPagesVal = $("max-pages-val");
  const resultCard = $("result-card");
  const resultText = $("result-text");
  const resultMeta = $("result-meta");
  const resultWarnings = $("result-warnings");
  const btnDownload = $("btn-download");
  const btnPreview = $("btn-preview");
  const previewCard = $("preview-card");
  const previewStrip = $("preview-strip");
  const btnPreviewHide = $("btn-preview-hide");
  const debugCard = $("debug-card");
  const outputFolderInput = $("output-folder");
  const folderNote = $("folder-note");
  const folderModal = $("folder-modal");
  const PRESET_KEY = "svg2ppt_presets";
  const PATHS_KEY = "demo_deck_output_paths";

  const R = DEMO.buildResult;

  function log(msg) {
    const area = $("log-area");
    if (!area) return;
    area.textContent += "[" + new Date().toLocaleTimeString() + "] " + msg + "\n";
    area.scrollTop = area.scrollHeight;
  }

  function updateStat() {
    const n = xmlInput.value.length;
    codeStat.textContent = n + " character" + (n === 1 ? "" : "s");
  }

  // ---------------------------------------------------------------
  // Result + preview
  // ---------------------------------------------------------------
  function renderPreview() {
    previewStrip.innerHTML = "";
    DEMO.deckPages.forEach((p) => {
      const wrap = document.createElement("div");
      wrap.className = "preview-page";
      const cap = document.createElement("div");
      cap.className = "page-cap";
      cap.textContent = p.caption;
      const img = document.createElement("img");
      img.src = p.img;
      img.alt = p.alt;
      img.loading = "lazy";
      wrap.appendChild(cap);
      wrap.appendChild(img);
      previewStrip.appendChild(wrap);
    });
  }

  function applyBuildResult(silent) {
    resultText.className = "success";
    resultText.textContent = "Built " + R.pageCount + " page(s).";
    resultMeta.textContent = R.filename + " \u00b7 " + R.sizeKb + " KB";
    if (R.warnings && R.warnings.length) {
      resultWarnings.classList.remove("hidden");
      resultWarnings.innerHTML = "<ul>" + R.warnings.map((w) => "<li>" + w + "</li>").join("") + "</ul>";
    } else {
      resultWarnings.classList.add("hidden");
      resultWarnings.innerHTML = "";
    }
    btnDownload.disabled = false;
    resultCard.classList.remove("hidden");
    debugCard.classList.remove("hidden");
    renderPreview();
    previewCard.classList.remove("hidden");
    if (!silent) {
      log("Done: " + R.pageCount + " page(s), " + R.sizeKb + " KB, 0 warnings.");
      resultCard.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  // ---------------------------------------------------------------
  // Editor controls
  // ---------------------------------------------------------------
  xmlInput.addEventListener("input", updateStat);

  $("btn-sample").addEventListener("click", () => {
    xmlInput.value = DEMO.deckXml;
    updateStat();
    log("Loaded the sample deck XML (" + DEMO.project.name + ").");
  });

  $("btn-clear").addEventListener("click", () => {
    xmlInput.value = "";
    updateStat();
    log("Editor cleared.");
  });

  $("btn-paste").addEventListener("click", async () => {
    try {
      xmlInput.value = await navigator.clipboard.readText();
      updateStat();
      log("Pasted from clipboard.");
    } catch (e) {
      log("Clipboard read blocked by the browser - paste manually into the box.");
    }
  });

  $("btn-build").addEventListener("click", () => {
    if (!xmlInput.value.trim()) {
      log("Nothing to build - paste or load deck XML first.");
      return;
    }
    log("Building deck \u2026");
    log("Routing: top_banner title/formula-ref/summary-block \u00b7 left_column 8 labelled cards \u00b7 core_content 6 evidence components.");
    log("Pagination: 6 core components \u2192 " + R.pageCount + " pages (banner + left column repeated).");
    applyBuildResult(false);
  });

  $("btn-rerender").addEventListener("click", () => {
    log("Re-rendering with overrides \u2026");
    applyBuildResult(false);
  });

  if (btnPreview) {
    btnPreview.addEventListener("click", () => previewCard.classList.toggle("hidden"));
  }
  if (btnPreviewHide) {
    btnPreviewHide.addEventListener("click", () => previewCard.classList.add("hidden"));
  }

  // ---------------------------------------------------------------
  // Sliders / colour pickers (labels stay live; the pages are pre-rendered)
  // ---------------------------------------------------------------
  function noteOverride(label, value) {
    log("Override: " + label + " = " + value + " (demo keeps the pre-rendered pages).");
  }

  function bindScaleControl(el, labelEl, label) {
    if (labelEl) {
      el.addEventListener("input", () => {
        labelEl.textContent = parseFloat(el.value).toFixed(1) + "\u00d7";
      });
    }
    el.addEventListener("change", () => noteOverride(label, parseFloat(el.value).toFixed(1) + "\u00d7"));
  }

  bindScaleControl($("ov-title-font"), $("ov-title-font-val"), "Title");
  bindScaleControl($("ov-left-font"), $("ov-left-font-val"), "Metadata column");
  bindScaleControl($("ov-mid-font"), $("ov-mid-font-val"), "Evidence area");
  bindScaleControl($("ov-right-font"), $("ov-right-font-val"), "Banner Summary");
  bindScaleControl($("ov-margin"), $("ov-margin-val"), "Card margin");

  [["ov-accent", "Accent"], ["ov-header-bg", "Header bg"], ["ov-meta-bg", "Summary bg"],
   ["ov-tab-bg", "Side tab bg"], ["ov-border", "Border"]].forEach(([id, label]) => {
    $(id).addEventListener("change", () => noteOverride(label, $(id).value));
  });

  maxPages.addEventListener("input", () => {
    const v = parseInt(maxPages.value, 10);
    maxPagesVal.textContent = v === 0 ? "0 (auto)" : String(v);
  });
  maxPages.addEventListener("change", () =>
    noteOverride("Max pages", maxPagesVal.textContent)
  );
  $("dpi").addEventListener("change", () => noteOverride("Render DPI", $("dpi").value));
  $("build-mode").addEventListener("change", () =>
    noteOverride("Build mode", $("build-mode").value)
  );

  document.querySelectorAll(".collapsible-header").forEach((btn) => {
    btn.addEventListener("click", () => {
      const wrap = btn.closest(".collapsible");
      const collapsed = wrap.classList.toggle("collapsed");
      btn.setAttribute("aria-expanded", String(!collapsed));
    });
  });

  // ---------------------------------------------------------------
  // Presets (localStorage — this part works for real in the demo)
  // ---------------------------------------------------------------
  function collectOverrides() {
    return {
      theme_overrides: {
        primary_accent: $("ov-accent").value,
        header_background: $("ov-header-bg").value,
        meta_background: $("ov-meta-bg").value,
        tab_background: $("ov-tab-bg").value,
        border_color: $("ov-border").value,
      },
      title_font_scale: parseFloat($("ov-title-font").value) || 1.0,
      left_font_scale: parseFloat($("ov-left-font").value) || 1.0,
      middle_font_scale: parseFloat($("ov-mid-font").value) || 1.0,
      right_font_scale: parseFloat($("ov-right-font").value) || 1.0,
      margin_scale: parseFloat($("ov-margin").value) || 1.0,
    };
  }

  function readPresets() {
    try { return JSON.parse(localStorage.getItem(PRESET_KEY) || "{}") || {}; }
    catch (e) { return {}; }
  }

  function loadPresetList() {
    const presets = readPresets();
    const sel = $("preset-select");
    sel.innerHTML = '<option value="">— saved presets —</option>';
    Object.keys(presets).forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    });
  }

  function applyPreset(name) {
    if (!name) return;
    const p = readPresets()[name];
    if (!p) return;
    const tov = p.theme_overrides || {};
    $("ov-accent").value = tov.primary_accent || "#c8860d";
    $("ov-header-bg").value = tov.header_background || "#e8c580";
    $("ov-meta-bg").value = tov.meta_background || "#f5e6c6";
    $("ov-tab-bg").value = tov.tab_background || "#b8860b";
    $("ov-border").value = tov.border_color || "#d9a441";
    $("ov-title-font").value = p.title_font_scale || 1.0;
    const legacy = p.content_font_scale || 1.0;
    $("ov-left-font").value = p.left_font_scale || legacy;
    $("ov-mid-font").value = p.middle_font_scale || legacy;
    $("ov-right-font").value = p.right_font_scale || legacy;
    $("ov-margin").value = p.margin_scale || 1.0;
    ["ov-title-font", "ov-left-font", "ov-mid-font", "ov-right-font", "ov-margin"].forEach((id) => {
      $(id + "-val").textContent = parseFloat($(id).value).toFixed(1) + "\u00d7";
    });
    log('Loaded preset "' + name + '".');
  }

  $("btn-preset-save").addEventListener("click", () => {
    const name = ($("preset-name").value || "").trim();
    if (!name) { log("Preset name required."); return; }
    const presets = readPresets();
    presets[name] = collectOverrides();
    try { localStorage.setItem(PRESET_KEY, JSON.stringify(presets)); } catch (e) { /* ignore */ }
    loadPresetList();
    $("preset-select").value = name;
    log('Saved preset "' + name + '".');
  });

  $("btn-preset-delete").addEventListener("click", () => {
    const name = $("preset-select").value;
    if (!name) { log("Select a preset to delete."); return; }
    const presets = readPresets();
    delete presets[name];
    try { localStorage.setItem(PRESET_KEY, JSON.stringify(presets)); } catch (e) { /* ignore */ }
    loadPresetList();
    log('Deleted preset "' + name + '".');
  });

  $("preset-select").addEventListener("change", () => applyPreset($("preset-select").value));
  loadPresetList();

  // ---------------------------------------------------------------
  // Output folder + folder picker (canned tree)
  // ---------------------------------------------------------------
  let currentBrowsePath = "";
  let currentBrowseParent = null;

  function readSavedPaths() {
    try {
      const v = JSON.parse(localStorage.getItem(PATHS_KEY) || "null");
      if (Array.isArray(v) && v.length) return v;
    } catch (e) { /* ignore */ }
    return [R.outputFolder, "D:\\Downloads"];
  }

  function writeSavedPaths(list) {
    try { localStorage.setItem(PATHS_KEY, JSON.stringify(list)); } catch (e) { /* ignore */ }
  }

  function renderDeckOutputPaths() {
    const list = $("saved-paths-list");
    const paths = readSavedPaths();
    const active = (outputFolderInput.value || "").trim();
    list.innerHTML = "";
    if (!paths.length) {
      list.innerHTML = '<div class="saved-path-empty muted">No saved paths yet.</div>';
      return;
    }
    paths.forEach((p) => {
      const row = document.createElement("div");
      row.className = "saved-path-item" + (p === active ? " active" : "");
      const label = document.createElement("span");
      label.className = "saved-path-text";
      label.textContent = p;
      label.title = "Load this path";
      label.addEventListener("click", () => {
        outputFolderInput.value = p;
        folderModal.classList.add("hidden");
        log("Loaded saved path: " + p);
      });
      const del = document.createElement("button");
      del.className = "saved-path-del";
      del.textContent = "\uD83D\uDDD1\uFE0F";
      del.title = "Remove this saved path";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        writeSavedPaths(readSavedPaths().filter((x) => x !== p));
        renderDeckOutputPaths();
      });
      row.appendChild(label);
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  function renderFolderList(data) {
    currentBrowseParent = data.parent || null;
    $("folder-current").textContent = data.path
      ? data.path
      : (data.drives && data.drives.length ? "Select a drive" : "/");
    const list = $("folder-list");
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

  function folderBrowseNavigate(path) {
    currentBrowsePath = path || "";
    const node = DEMO.folderTree[currentBrowsePath] || {
      path: currentBrowsePath, parent: null, drives: [], dirs: [],
    };
    renderFolderList(node);
  }

  $("btn-browse-folder").addEventListener("click", () => {
    currentBrowsePath = (outputFolderInput.value || "").trim();
    folderModal.classList.remove("hidden");
    renderDeckOutputPaths();
    folderBrowseNavigate(currentBrowsePath);
  });

  $("btn-save-folder").addEventListener("click", () => {
    const p = (outputFolderInput.value || "").trim();
    if (!p) { log("Enter or choose a folder first."); return; }
    writeSavedPaths([p].concat(readSavedPaths().filter((x) => x !== p)));
    folderNote.textContent = "Saved output folder.";
    log("Output folder saved.");
  });

  $("btn-download").addEventListener("click", () => {
    if (btnDownload.disabled) return;
    const st = $("save-status");
    st.classList.remove("hidden");
    st.textContent = "Saved to " + (outputFolderInput.value || R.outputFolder) + "\\" + R.filename + " (" + R.sizeKb + " KB).";
    log("Deck saved to " + (outputFolderInput.value || R.outputFolder));
  });

  $("folder-modal-close").addEventListener("click", () => folderModal.classList.add("hidden"));
  $("folder-modal-backdrop").addEventListener("click", () => folderModal.classList.add("hidden"));
  $("folder-up").addEventListener("click", () => {
    if (currentBrowseParent) folderBrowseNavigate(currentBrowseParent);
  });
  $("folder-select").addEventListener("click", () => {
    if (!currentBrowsePath) { log("Navigate into a folder first, then select it."); return; }
    outputFolderInput.value = currentBrowsePath;
    writeSavedPaths([currentBrowsePath].concat(readSavedPaths().filter((x) => x !== currentBrowsePath)));
    folderNote.textContent = "Saved output folder.";
    folderModal.classList.add("hidden");
    log("Output folder saved.");
  });

  // ---------------------------------------------------------------
  // Boot — arrive with a finished build already on screen
  // ---------------------------------------------------------------
  let incoming = null;
  try { incoming = localStorage.getItem("demo_deck_xml"); } catch (e) { incoming = null; }

  xmlInput.value = incoming || DEMO.deckXml;
  updateStat();

  $("file-name").value = R.filename.replace(/\.pptx$/, "");
  outputFolderInput.value = R.outputFolder;
  folderNote.textContent = "Saved output folder.";
  btnDownload.disabled = true;

  log("SVG -> PPTX ready. Load a sample or paste a deck XML, then Build.");
  if (incoming) {
    log("Deck XML received from the AI synthesis step (17 components).");
    log("Handing over from the AI session \u2014 nothing to paste.");
  } else {
    log("Pre-loading the sample deck (" + DEMO.project.name + ").");
  }
  log("Building deck \u2026");
  log("Routing: top_banner title/formula-ref/summary-block \u00b7 left_column 8 labelled cards \u00b7 core_content 6 evidence components.");
  log("Pagination: 6 core components \u2192 " + R.pageCount + " pages (banner + left column repeated).");
  applyBuildResult(true);
  log("Done: " + R.pageCount + " page(s), " + R.sizeKb + " KB, 0 warnings.");
})();
