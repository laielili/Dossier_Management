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

  const SAMPLE_XML = `<?xml version="1.0" encoding="UTF-8"?>
<deck project="P-TIOX" theme="loreal">
  <component type="title"><svg viewBox="0 0 800 40"><text x="0" y="30" font-size="28" font-weight="bold" fill="#333">P-TIOX</text></svg></component>
  <component type="meta"><svg viewBox="0 0 900 24"><text x="0" y="18" font-size="13" fill="#c8860d">Anti-aging facial serum · Audience: 35+ · Claim: -45% wrinkles</text></svg></component>
  <component type="info-card" label="Formulation"><svg viewBox="0 0 200 120"><rect width="200" height="120" fill="#fff" stroke="#d9a441"/><text x="10" y="24" font-size="13" font-weight="bold" fill="#333">Formulation</text><text x="10" y="50" font-size="11" fill="#333">Pure peptide complex</text><text x="10" y="70" font-size="11" fill="#333">Hyaluronic acid</text><text x="10" y="90" font-size="11" fill="#333">Niacinamide 4%</text></svg></component>
  <component type="efficacy-table"><svg viewBox="0 0 540 200"><rect width="540" height="200" fill="#fff" stroke="#2e7d32"/><text x="10" y="24" font-size="14" font-weight="bold" fill="#333">China T12W Clinical</text><text x="10" y="56" font-size="12" fill="#2e7d32">Wrinkle depth: -45.0%</text><text x="10" y="84" font-size="12" fill="#2e7d32">Firmness: +38.0%</text><text x="10" y="112" font-size="12" fill="#333">Hydration: +52.0%</text><text x="10" y="140" font-size="12" fill="#333">Elasticity: +29.0%</text><text x="10" y="168" font-size="12" fill="#333">Smoothness: +41.0%</text></svg></component>
  <component type="summary-block"><svg viewBox="0 0 200 120"><rect width="200" height="120" fill="#fff" stroke="#c8860d"/><text x="10" y="24" font-size="13" font-weight="bold" fill="#333">Summary</text><text x="10" y="52" font-size="11" fill="#333">Significant anti-aging</text><text x="10" y="72" font-size="11" fill="#333">benefit at 12 weeks</text><text x="10" y="96" font-size="11" fill="#333">Well tolerated</text></svg></component>
</deck>`;

  function stripFences(text) {
    // Remove a leading ```xml / ``` fenced block if the AI wrapped the XML.
    const m = text.match(/^```(?:xml)?\s*([\s\S]*?)\s*```$/i);
    return m ? m[1] : text;
  }

  // ---- wire up controls -------------------------------------------------
  xmlInput.addEventListener("input", updateStat);

  $("btn-sample").addEventListener("click", () => {
    xmlInput.value = SAMPLE_XML;
    updateStat();
    log("Loaded sample deck XML.");
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

  updateStat();
  log("SVG -> PPTX ready. Load a sample or paste a deck XML, then Build.");
})();
