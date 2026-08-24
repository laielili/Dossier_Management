/* SVG -> PPTX frontend — drives POST /svg2ppt/build.
 *
 * The server owns all layout; the browser only sends the deck XML + options
 * and receives download/preview URLs for the generated artifact.
 */
(function () {
  "use strict";

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

  // ---- Output mode (editable Beta / classic single-image) ----
  const modeEditable = document.querySelector(
    'input[name="output-mode"][value="editable"]'
  );
  const modeClassic = document.querySelector(
    'input[name="output-mode"][value="classic"]'
  );

  function currentEditable() {
    return !!(modeEditable && modeEditable.checked);
  }

  // ---- Debug / theme override controls ----
  const debugCard = $("debug-card");
  const ovAccent = $("ov-accent");
  const ovHeaderBg = $("ov-header-bg");
  const ovMetaBg = $("ov-meta-bg");
  const ovTabBg = $("ov-tab-bg");
  const ovBorder = $("ov-border");
  const ovTitleFont = $("ov-title-font");
  const ovTitleFontVal = $("ov-title-font-val");
  const ovMetaFont = $("ov-meta-font");
  const ovMetaFontVal = $("ov-meta-font-val");
const ovLeftFont = $("ov-left-font");
const ovLeftFontVal = $("ov-left-font-val");
const ovMidFont = $("ov-mid-font");
const ovMidFontVal = $("ov-mid-font-val");
const ovRightFont = $("ov-right-font");
const ovRightFontVal = $("ov-right-font-val");
  const ovMargin = $("ov-margin");
  const ovMarginVal = $("ov-margin-val");
  const btnRerender = $("btn-rerender");
  const presetName = $("preset-name");
  const presetSelect = $("preset-select");
  const btnPresetSave = $("btn-preset-save");
  const btnPresetDelete = $("btn-preset-delete");

  // Defaults mirror templates/deck_5region.json (loreal theme).
  const THEME_DEFAULTS = {
    primary_accent: "#c8860d",
    header_background: "#e8c580",
    meta_background: "#f5e6c6",
    tab_background: "#b8860b",
    border_color: "#d9a441",
  };
  const PRESET_KEY = "svg2ppt_presets";

  // Last successful build — re-render reuses its XML + base options.
  let lastBuild = null;
  let rerenderTimer = null;

  function log(msg) {
    const area = $("log-area");
    if (!area) return;
    const ts = new Date().toLocaleTimeString();
    area.textContent += `[${ts}] ${msg}\n`;
    area.scrollTop = area.scrollHeight;
  }

  function updateStat() {
    const n = xmlInput.value.length;
    codeStat.textContent = `${n} character${n === 1 ? "" : "s"}`;
  }

  const SAMPLE_XML_URL = "/static/sample.xml";

  function stripFences(text) {
    // Remove a leading ```xml / ``` fenced block if the AI wrapped the XML.
    const m = text.match(/^```(?:xml)?\s*([\s\S]*?)\s*```$/i);
    return m ? m[1] : text;
  }

  // ---- wire up controls -------------------------------------------------
  xmlInput.addEventListener("input", updateStat);

  $("btn-sample").addEventListener("click", async () => {
    try {
      const resp = await fetch(SAMPLE_XML_URL);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      xmlInput.value = await resp.text();
      updateStat();
      log("Loaded sample deck XML.");
    } catch (e) {
      log("Failed to load sample XML: " + e.message);
    }
  });

  $("btn-clear").addEventListener("click", () => {
    xmlInput.value = "";
    updateStat();
  });

  $("btn-paste").addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      xmlInput.value = text;
      updateStat();
      log("Pasted from clipboard.");
    } catch (e) {
      log("Clipboard read blocked by browser: " + e.message);
    }
  });

  maxPages.addEventListener("input", () => {
    const v = parseInt(maxPages.value, 10);
    maxPagesVal.textContent = v === 0 ? "0 (auto)" : String(v);
  });
  maxPages.addEventListener("change", scheduleRerender);

  $("btn-build").addEventListener("click", buildDeck);

  async function buildDeck() {
    const raw = xmlInput.value.trim();
    const xml = stripFences(raw).trim();
    if (!xml) {
      log("Nothing to build — paste or load deck XML first.");
      return;
    }

    const payload = {
      xml: xml,
      filename: $("file-name").value.trim() || "synthesis_deck",
      max_pages: parseInt(maxPages.value, 10) || 0,
      dpi: parseInt($("dpi").value, 10) || 150,
      editable: currentEditable(),
    };

    resultCard.classList.add("hidden");
    log("Building deck…");
    $("btn-build").disabled = true;

    try {
      const resp = await fetch("/svg2ppt/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();

      if (!resp.ok || !data.ok) {
        const msg = data.detail || data.message || "Build failed";
        log("ERROR: " + msg);
        resultCard.classList.remove("hidden");
        resultText.className = "error";
        resultText.textContent = "Build failed: " + msg;
        return;
      }

      lastBuild = {
        xml: xml,
        filename: payload.filename,
        max_pages: payload.max_pages,
        dpi: payload.dpi,
        editable: payload.editable,
      };
      applyBuildResult(data);
      debugCard.classList.remove("hidden");
    } catch (e) {
      log("Network error: " + e.message);
      resultCard.classList.remove("hidden");
      resultText.className = "error";
      resultText.textContent = "Network error: " + e.message;
    } finally {
      $("btn-build").disabled = false;
    }
  }

  function renderPreview(data) {
    if (!previewStrip) return;
    previewStrip.innerHTML = "";
    const n = data.page_count || 0;
    for (let i = 1; i <= n; i++) {
      const pageNo = String(i).padStart(3, "0");
      const url = `/svg2ppt/files/${encodeURIComponent(data.run_id)}/page_${pageNo}.png`;
      const wrap = document.createElement("div");
      wrap.className = "preview-page";
      const cap = document.createElement("div");
      cap.className = "page-cap";
      cap.textContent = `Page ${i} / ${n}`;
      const img = document.createElement("img");
      img.src = url;
      img.alt = `deck page ${i}`;
      img.loading = "lazy";
      wrap.appendChild(cap);
      wrap.appendChild(img);
      previewStrip.appendChild(wrap);
    }
    if (previewCard) previewCard.classList.remove("hidden");
    log(`Preview: ${n} page(s) rendered inline.`);
  }

  if (btnPreview) {
    btnPreview.addEventListener("click", () => {
      if (previewCard) previewCard.classList.toggle("hidden");
    });
  }
  if (btnPreviewHide) {
    btnPreviewHide.addEventListener("click", () => {
      if (previewCard) previewCard.classList.add("hidden");
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // ------------------------------------------------------------------
  // Build-result application (shared by Build and Re-render)
  // ------------------------------------------------------------------
  function applyBuildResult(data) {
    resultText.className = "success";
    resultText.textContent = `Built ${data.page_count} page(s).`;
    resultMeta.textContent = `${data.filename} · ${data.size_kb} KB`;
    btnDownload.href = data.pptx_url;
    btnDownload.setAttribute("download", data.filename);
    renderPreview(data);
    resultCard.classList.remove("hidden");

    if (data.warnings && data.warnings.length) {
      resultWarnings.classList.remove("hidden");
      resultWarnings.innerHTML =
        "<ul>" + data.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("") + "</ul>";
    } else {
      resultWarnings.classList.add("hidden");
      resultWarnings.innerHTML = "";
    }
    log(`Done: ${data.page_count} page(s), ${data.size_kb} KB.`);
  }

  // ------------------------------------------------------------------
  // Debug / theme overrides — live re-render (the Download always matches Preview)
  // ------------------------------------------------------------------
  function collectOverrides() {
    return {
      theme_overrides: {
        primary_accent: ovAccent.value,
        header_background: ovHeaderBg.value,
        meta_background: ovMetaBg.value,
        tab_background: ovTabBg.value,
        border_color: ovBorder.value,
      },
      title_font_scale: parseFloat(ovTitleFont.value) || 1.0,
      meta_font_scale: parseFloat(ovMetaFont.value) || 1.0,
      left_font_scale: parseFloat(ovLeftFont.value) || 1.0,
      middle_font_scale: parseFloat(ovMidFont.value) || 1.0,
      right_font_scale: parseFloat(ovRightFont.value) || 1.0,
      margin_scale: parseFloat(ovMargin.value) || 1.0,
    };
  }

  async function rerender() {
    if (!lastBuild) {
      log("Build the deck first, then use the debug panel.");
      return;
    }
    const payload = {
      xml: lastBuild.xml,
      filename: lastBuild.filename,
      max_pages: parseInt(maxPages.value, 10) || 0,
      dpi: parseInt($("dpi").value, 10) || 150,
      editable: currentEditable(),
      ...collectOverrides(),
    };
    log("Re-rendering with overrides…");
    btnRerender.disabled = true;
    try {
      const resp = await fetch("/svg2ppt/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        const msg = data.detail || data.message || "Re-render failed";
        log("ERROR: " + msg);
        return;
      }
      applyBuildResult(data);
    } catch (e) {
      log("Network error: " + e.message);
    } finally {
      btnRerender.disabled = false;
    }
  }

  function scheduleRerender() {
    clearTimeout(rerenderTimer);
    rerenderTimer = setTimeout(rerender, 400);
  }

  function bindScaleControl(el, labelEl) {
    if (labelEl) {
      el.addEventListener("input", () => {
        labelEl.textContent = parseFloat(el.value).toFixed(1) + "×";
      });
    }
    el.addEventListener("change", scheduleRerender);
  }
  bindScaleControl(ovTitleFont, ovTitleFontVal);
  bindScaleControl(ovMetaFont, ovMetaFontVal);
  bindScaleControl(ovLeftFont, ovLeftFontVal);
  bindScaleControl(ovMidFont, ovMidFontVal);
  bindScaleControl(ovRightFont, ovRightFontVal);
  bindScaleControl(ovMargin, ovMarginVal);
  [ovAccent, ovHeaderBg, ovTabBg, ovBorder].forEach((el) =>
    el.addEventListener("change", scheduleRerender)
  );
  $("dpi").addEventListener("change", scheduleRerender);

  // Collapsible panels in the Adjustments card
  document.querySelectorAll(".collapsible-header").forEach((btn) => {
    btn.addEventListener("click", () => {
      const wrap = btn.closest(".collapsible");
      const collapsed = wrap.classList.toggle("collapsed");
      btn.setAttribute("aria-expanded", String(!collapsed));
    });
  });

  if (btnRerender) btnRerender.addEventListener("click", rerender);

  // ------------------------------------------------------------------
  // Presets (browser localStorage — not written to server files)
  // ------------------------------------------------------------------
  function readPresets() {
    try {
      return JSON.parse(localStorage.getItem(PRESET_KEY) || "{}") || {};
    } catch (e) {
      return {};
    }
  }

  function loadPresetList() {
    const presets = readPresets();
    presetSelect.innerHTML = '<option value="">— saved presets —</option>';
    Object.keys(presets).forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      presetSelect.appendChild(opt);
    });
  }

  function savePreset() {
    const name = (presetName.value || "").trim();
    if (!name) {
      log("Preset name required.");
      return;
    }
    const presets = readPresets();
    presets[name] = collectOverrides();
    localStorage.setItem(PRESET_KEY, JSON.stringify(presets));
    loadPresetList();
    presetSelect.value = name;
    log(`Saved preset "${name}".`);
  }

  function applyPreset(name) {
    if (!name) return;
    const p = readPresets()[name];
    if (!p) return;
    const tov = p.theme_overrides || {};
    ovAccent.value = tov.primary_accent || THEME_DEFAULTS.primary_accent;
    ovHeaderBg.value = tov.header_background || THEME_DEFAULTS.header_background;
    ovMetaBg.value = tov.meta_background || THEME_DEFAULTS.meta_background;
    ovTabBg.value = tov.tab_background || THEME_DEFAULTS.tab_background;
    ovBorder.value = tov.border_color || THEME_DEFAULTS.border_color;
    ovTitleFont.value = p.title_font_scale || 1.0;
    ovMetaFont.value = p.meta_font_scale || 1.0;
    // Old presets stored a single content_font_scale for all three columns.
    const legacy = p.content_font_scale || 1.0;
    ovLeftFont.value = p.left_font_scale || legacy;
    ovMidFont.value = p.middle_font_scale || legacy;
    ovRightFont.value = p.right_font_scale || legacy;
    ovMargin.value = p.margin_scale || 1.0;
    ovTitleFontVal.textContent = parseFloat(ovTitleFont.value).toFixed(1) + "×";
    ovMetaFontVal.textContent = parseFloat(ovMetaFont.value).toFixed(1) + "×";
    ovLeftFontVal.textContent = parseFloat(ovLeftFont.value).toFixed(1) + "×";
    ovMidFontVal.textContent = parseFloat(ovMidFont.value).toFixed(1) + "×";
    ovRightFontVal.textContent = parseFloat(ovRightFont.value).toFixed(1) + "×";
    ovMarginVal.textContent = parseFloat(ovMargin.value).toFixed(1) + "×";
    scheduleRerender();
  }

  function deletePreset() {
    const name = presetSelect.value;
    if (!name) {
      log("Select a preset to delete.");
      return;
    }
    const presets = readPresets();
    delete presets[name];
    localStorage.setItem(PRESET_KEY, JSON.stringify(presets));
    loadPresetList();
    log(`Deleted preset "${name}".`);
  }

  if (btnPresetSave) btnPresetSave.addEventListener("click", savePreset);
  if (btnPresetDelete) btnPresetDelete.addEventListener("click", deletePreset);
  if (presetSelect) presetSelect.addEventListener("change", () => applyPreset(presetSelect.value));
  loadPresetList();

  updateStat();
  log("SVG -> PPTX ready. Load a sample or paste a deck XML, then Build.");
})();
