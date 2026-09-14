/* ============================================================
   Dossier_Management — /docs demo build: single source of mock data
   ------------------------------------------------------------
   Every page in this folder is a static replica of the real front end
   (static/*.html). GitHub Pages has no Python/Office backend, so all
   server calls are replaced by the canned dataset below.

   ALL DATA IN THIS FILE IS SIMULATED, for illustration only.
   It is not extracted from any real dossier and must not be read as
   evidence of any kind.
   ============================================================ */
window.DEMO = (function () {
  "use strict";

  /* ---------- The demo project ---------- */
  const project = {
    name: "P-TIOX",
    type: "Launch - DEV",
    audience: ["Female, 25-55 y.o.", "all skin types incl. sensitive"],
    claims: [
      "Inspired by BOTOX",
      "Treats areas Botox cannot reach",
      "Visible results in 4 weeks",
      "Tested under dermatological control",
    ],
    formulation: ["2% SYN-AKE tripeptide", "0.5% HEPES buffer", "milky lotion"],
    fragrance: "N/A",
    packaging: "Airless pump bottle 30 ml",
    sustainability: "Refillable cartridge, 78% recycled glass",
    safety: "Dermatologically tested, 0 serious adverse events",
    formulaChain: ["866420 11", "866420 12"],
    latestFormula: "866420 12",
    outputMode: "DECK",
  };

  /* ---------- Search target + canned result set ---------- */
  const searchTarget = "D:\\Projects\\Dossiers\\2026-Q3";
  const sources = [
    { name: "P-TIOX_Clinical_China_12W_CLINS.pdf", ext: "PDF", size_kb: 2481.3, modified: "2026-05-12 09:41", keep: true },
    { name: "P-TIOX_Instrumental_US_8W_CLINS.pdf", ext: "PDF", size_kb: 1902.7, modified: "2026-05-12 09:44", keep: true },
    { name: "P-TIOX_Clinical_Brazil_12W_CLINS.pdf", ext: "PDF", size_kb: 1673.9, modified: "2026-05-13 15:02", keep: true },
    { name: "P-TIOX_Sensory_France_4W_FE.pdf", ext: "PDF", size_kb: 1244.1, modified: "2026-05-13 15:07", keep: true },
    { name: "P-TIOX_Consumer_UK_12W_CE.pdf", ext: "PDF", size_kb: 3120.5, modified: "2026-05-14 08:26", keep: true },
    { name: "P-TIOX_Consumer_Brazil_8W_CE.pptx", ext: "PPTX", size_kb: 4668.0, modified: "2026-05-14 08:31", keep: true },
    { name: "P-TIOX_Formulation_Brief.docx", ext: "DOCX", size_kb: 288.4, modified: "2026-05-08 11:19", keep: true },
    { name: "P-TIOX_Raw_Metrics.xlsx", ext: "XLSX", size_kb: 812.6, modified: "2026-05-14 09:02", keep: false },
  ];

  /* ---------- Pipeline log (both pages) ---------- */
  const log = {
    index: [
      ["info", "Dossier Search ready."],
      ["info", "1) Set the Search Target Path. 2) Enter 1-3 keywords + last-modified date within, then Start. 3) Select files, name the project, click Next."],
      ["info", "Search target path loaded from history."],
      ["info", "Searching " + searchTarget + " (recursive, 4 extensions) ..."],
      ["success", "Search finished - 8 dossier file(s) matched keyword \"P-TIOX\"."],
      ["info", "Project name defaulted to \"P-TIOX\" (first keyword)."],
      ["info", "Retrieval started for \"P-TIOX\" - 7 file(s) selected."],
      ["info", "Stage: scan ..."],
      ["success", "scan - 7 source dossier(s), 412 pages total."],
      ["info", "Stage: classify ..."],
      ["success", "classify - CLINS 3 (231 p) | FE 1 (48 p) | CE 2 (133 p)."],
      ["info", "Stage: ingest ..."],
      ["success", "ingest - pptx/docx converted to PDF via Office; xlsx left unprocessed."],
      ["info", "Stage: condense ..."],
      ["success", "condense - 68 noise page(s) dropped (cover 7, toc 3, boilerplate 41, blank 12, closing 5)."],
      ["success", "Preprocess finished - 7 file(s) written to retrieved/P-TIOX/AI_feed/P-TIOX/."],
    ],
  };

  /* ---------- Fake folder tree for the picker modal ---------- */
  const folderTree = {
    "": { path: null, parent: null, drives: ["C:\\", "D:\\"], dirs: [] },
    "C:\\": { path: "C:\\", parent: null, dirs: ["C:\\Users"] },
    "C:\\Users": { path: "C:\\Users", parent: "C:\\", dirs: ["C:\\Users\\Public"] },
    "C:\\Users\\Public": { path: "C:\\Users\\Public", parent: "C:\\Users", dirs: ["C:\\Users\\Public\\Downloads"] },
    "C:\\Users\\Public\\Downloads": {
      path: "C:\\Users\\Public\\Downloads", parent: "C:\\Users\\Public",
      dirs: ["C:\\Users\\Public\\Downloads\\P-TIOX", "C:\\Users\\Public\\Downloads\\P-RETINOL B3"],
    },
    "C:\\Users\\Public\\Downloads\\P-TIOX": {
      path: "C:\\Users\\Public\\Downloads\\P-TIOX", parent: "C:\\Users\\Public\\Downloads", dirs: [],
    },
    "C:\\Users\\Public\\Downloads\\P-RETINOL B3": {
      path: "C:\\Users\\Public\\Downloads\\P-RETINOL B3", parent: "C:\\Users\\Public\\Downloads", dirs: [],
    },
    "D:\\": { path: "D:\\", parent: null, dirs: ["D:\\Projects", "D:\\Downloads"] },
    "D:\\Projects": { path: "D:\\Projects", parent: "D:\\", dirs: ["D:\\Projects\\Dossiers"] },
    "D:\\Projects\\Dossiers": { path: "D:\\Projects\\Dossiers", parent: "D:\\Projects", dirs: ["D:\\Projects\\Dossiers\\2026-Q2", "D:\\Projects\\Dossiers\\2026-Q3"] },
    "D:\\Projects\\Dossiers\\2026-Q2": { path: "D:\\Projects\\Dossiers\\2026-Q2", parent: "D:\\Projects\\Dossiers", dirs: [] },
    "D:\\Projects\\Dossiers\\2026-Q3": { path: "D:\\Projects\\Dossiers\\2026-Q3", parent: "D:\\Projects\\Dossiers", dirs: [] },
    "D:\\Downloads": { path: "D:\\Downloads", parent: "D:\\", dirs: [] },
  };

  /* ---------- Configuration panel content (mirrors classify/*.txt, queries/query.txt) ---------- */
  const config = {
    profiles: {
      CLINS: "Clinical report. First page identifies a clinical study, clinical trial,\nclinical evaluation, clinical investigation, or instrumental measurement.\nContains terms: clinical, investigator, dermatological, efficacy, tolerance,\nsafety, before/after, grader assessment, instrumental clinical measurement,\nendpoints, adverse event, corneometer, tewameter, primos, skin hydration,\ntransepidermal water loss, TEWL, barrier function, roughness, elasticity,\nfirmness, biophysical, instrumental, objective measurement, skin properties.\nMeasured clinical outcomes and skin biophysical parameters under controlled\nconditions. \u89d2\u8d28\u5c42\u6c34\u5206, \u7ecf\u76ae\u6c34\u5206\u6d41\u5931, \u76ae\u80a4\u5c4f\u969c\u529f\u80fd, \u7c97\u7cd9\u5ea6, \u5f39\u6027, \u575a\u97e7\u5ea6,\n\u751f\u7269\u7269\u7406, \u4eea\u5668\u6d4b\u91cf, \u5ba2\u89c2\u6d4b\u91cf, \u76ae\u80a4\u751f\u7269\u7269\u7406\u53c2\u6570, \u89d2\u8d28\u8ba1, \u7ecf\u8868\u76ae\u6c34\u5206\u6d41\u5931\u4eea, \u76ae\u80a4\u6210\u50cf\u5206\u6790",
      FE: "Sensory report. First page identifies sensory evaluation, sensory analysis,\nsensory panel, or sensory assessment. Contains terms: sensory, panel,\ntexture, fragrance, odor, appearance, feel, sensory attributes,\ndescriptive analysis, hedonic, liking, touch, color. Product assessed\nthrough human sensory perception.",
      CE: "Consumer Evaluation report. First page identifies a consumer test,\nconsumer evaluation, consumer study, or consumer panel. Contains terms:\nconsumer, evaluation, self-assessment, usage test, home-use test,\nquestionnaire, satisfaction, perception, consumer feedback, claim\nsubstantiation, panelist, user journey, usage journey, journey. Measures consumer opinion and behavior at scale.",
    },
    noiseCategories: [
      { label: "Cover / section divider", active: true },
      { label: "Table of contents", active: true },
      { label: "Boilerplate / template", active: true },
      { label: "Blank / near-empty", active: true },
      { label: "Closing (thank-you, etc.)", active: true },
    ],
    queryTxt: "# Title Anchors\nCONSUMER EVALUATION\nCONSUMER STUDY\nUSAGE TEST\nHOME-USE TEST\nSATISFACTION ANALYSIS\nSTATISTICAL ANALYSIS\nRESULTS\nSUMMARY\nCONCLUSION\n\n# Metric Keywords\nconsumer\nsatisfaction\nself-assessment\ntolerance\nefficacy\nsafety\ndermatological\ninstrumental measurement\nperception\nquestionnaire\npanelist\nclaim substantiation\n\n# Table Features\np-value\nconfidence interval\nmean score\nstatistical significance\nprimary endpoint\nbaseline\npercentage of subjects\n\n# Other\n# Add target-formula / study-code / product-specific vocabulary here (anti-miss).",
  };

  /* ---------- Deck XML emitted by the mock AI (spec-conformant: prompt/system.md) ---------- */
  const deckXml = `<deck project="P-TIOX" theme="loreal">
  <!-- ===== top_banner (repeated on every page) ===== -->
  <component type="title"><svg viewBox="0 0 1000 22" width="1000" height="22"><text x="0" y="18" font-size="18" font-weight="bold" fill="#333" font-family="Arial, sans-serif">P-TIOX</text></svg></component>
  <component type="formula-ref" formula="866420 11|866420 12"><svg viewBox="0 0 1000 14" width="1000" height="14"><text x="0" y="11" font-size="11" fill="#c8860d" font-family="Arial, sans-serif">866420 12</text></svg></component>

  <!-- ===== left_column: eight labelled cards (viewBox 200 wide, font 10) ===== -->
  <component type="meta" label="Type"><svg viewBox="0 0 200 20" width="200" height="20"><text x="8" y="12" font-size="10" fill="#333" font-family="Arial, sans-serif">Launch - DEV</text></svg></component>
  <component type="meta" label="Audience"><svg viewBox="0 0 200 35" width="200" height="35"><text x="8" y="12" font-size="10" fill="#333" font-family="Arial, sans-serif">Female, 25-55 y.o.</text><text x="8" y="27" font-size="10" fill="#333" font-family="Arial, sans-serif">all skin types incl. sensitive</text></svg></component>
  <component type="meta" label="Communication"><svg viewBox="0 0 200 75" width="200" height="75"><text x="8" y="12" font-size="10" fill="#333" font-family="Arial, sans-serif">&#8226; Inspired by BOTOX</text><text x="8" y="27" font-size="10" fill="#333" font-family="Arial, sans-serif">&#8226; Treats areas Botox cannot reach</text><text x="8" y="42" font-size="10" fill="#333" font-family="Arial, sans-serif">&#8226; Visible results in 4 weeks</text><text x="8" y="57" font-size="10" fill="#333" font-family="Arial, sans-serif">&#8226; Tested under dermatological control</text></svg></component>
  <component type="info-card" label="Formulation"><svg viewBox="0 0 200 36" width="200" height="36"><text x="8" y="12" font-size="10" fill="#333" font-family="Arial, sans-serif">2% SYN-AKE tripeptide &#183; 0.5% HEPES</text><text x="8" y="27" font-size="10" fill="#333" font-family="Arial, sans-serif">milky lotion</text></svg></component>
  <component type="info-card" label="Fragrance"><svg viewBox="0 0 200 36" width="200" height="36"><text x="8" y="12" font-size="10" fill="#333" font-family="Arial, sans-serif">N/A</text></svg></component>
  <component type="info-card" label="Packaging"><svg viewBox="0 0 200 36" width="200" height="36"><text x="8" y="12" font-size="10" fill="#333" font-family="Arial, sans-serif">Airless pump bottle 30 ml</text></svg></component>
  <component type="info-card" label="Sustainability"><svg viewBox="0 0 200 36" width="200" height="36"><text x="8" y="12" font-size="10" fill="#333" font-family="Arial, sans-serif">Refillable cartridge &#183; 78% recycled glass</text></svg></component>
  <component type="info-card" label="Safety"><svg viewBox="0 0 200 36" width="200" height="36"><text x="8" y="12" font-size="10" fill="#333" font-family="Arial, sans-serif">Dermatologically tested</text></svg></component>

  <!-- ===== summary-block: single-column PERFORMANCE SUMMARY band (viewBox 760x88) ===== -->
  <component type="summary-block"><svg viewBox="0 0 760 88" width="760" height="88" font-family="Arial, sans-serif">
      <text x="4" y="12" font-size="9" font-weight="bold" fill="#333" font-family="Arial, sans-serif">PERFORMANCE SUMMARY:</text>
      <text x="4" y="25" font-size="8.5" fill="#333" font-family="Arial, sans-serif">P-TIOX is a DEV anti-wrinkle project targeting expression lines that Botox</text>
      <text x="4" y="38" font-size="8.5" fill="#333" font-family="Arial, sans-serif">cannot reach. Assessed across 3 dermatologist-graded clinical tests (N=112),</text>
      <text x="4" y="51" font-size="8.5" fill="#333" font-family="Arial, sans-serif">one facial sensory test and 2 consumer tests (N=192): wrinkle depth <tspan font-weight="bold" fill="#2e7d32">-32.4% at T12W</tspan></text>
      <text x="4" y="64" font-size="8.5" fill="#333" font-family="Arial, sans-serif">and consumer acceptance <tspan font-weight="bold" fill="#2e7d32">89.0%</tspan>, while barrier function improved only</text>
      <text x="4" y="77" font-size="8.5" fill="#333" font-family="Arial, sans-serif">moderately (<tspan font-weight="bold" fill="#e07b00">TEWL -15.6% at T8W</tspan>) - validated, now in optimization.</text>
    </svg></component>

  <!-- ===== core_content: one component per study (viewBox 1000 wide) ===== -->
  <component type="efficacy-table" formula="866420 12"><svg viewBox="0 0 1000 200" width="1000" height="200">
      <rect x="0" y="0" width="1000" height="30" fill="#f5f5f5" />
      <text x="15" y="20" font-size="14" font-weight="bold" fill="#333" font-family="Arial, sans-serif">China T12W clinical test [cn]</text>
      <text x="15" y="45" font-size="10" fill="#666" font-family="Arial, sans-serif">N=42, female 25-55 &#183; vs 866420 11</text>
      <line x1="0" y1="60" x2="1000" y2="60" stroke="#ddd" stroke-width="1" />
      <text x="15" y="80" font-size="12" fill="#333" font-family="Arial, sans-serif">Forehead wrinkle depth</text>
      <text x="560" y="80" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T4W: -18.5%</text>
      <text x="780" y="80" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T12W: -32.4%</text>
      <line x1="0" y1="95" x2="1000" y2="95" stroke="#eee" stroke-width="1" />
      <text x="15" y="115" font-size="12" fill="#333" font-family="Arial, sans-serif">Crow's feet wrinkle depth</text>
      <text x="560" y="115" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T4W: -12.1%</text>
      <text x="780" y="115" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T12W: -24.7%</text>
      <line x1="0" y1="130" x2="1000" y2="130" stroke="#eee" stroke-width="1" />
      <text x="15" y="150" font-size="12" fill="#333" font-family="Arial, sans-serif">Skin firmness</text>
      <text x="560" y="150" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T4W: +9.3%</text>
      <text x="780" y="150" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T12W: +16.8%</text>
      <text x="15" y="183" font-size="8" fill="#666666" font-family="Arial, sans-serif">&#8226; Primary endpoint met on every graded wrinkle site &#8226; vs 866420 11 bench &#8226; all p &lt; 0.05 vs baseline</text>
    </svg></component>
  <component type="efficacy-table" formula="866420 12"><svg viewBox="0 0 1000 160" width="1000" height="160">
      <rect x="0" y="0" width="1000" height="30" fill="#f5f5f5" />
      <text x="15" y="20" font-size="14" font-weight="bold" fill="#333" font-family="Arial, sans-serif">US instrumental 8W study [us]</text>
      <text x="15" y="45" font-size="10" fill="#666" font-family="Arial, sans-serif">N=30, female 30-55 &#183; Corneometer / Tewameter</text>
      <line x1="0" y1="60" x2="1000" y2="60" stroke="#ddd" stroke-width="1" />
      <text x="15" y="80" font-size="12" fill="#333" font-family="Arial, sans-serif">Corneometer - skin hydration</text>
      <text x="560" y="80" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T1h: +146.9%</text>
      <text x="780" y="80" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T8W: +38.2%</text>
      <line x1="0" y1="95" x2="1000" y2="95" stroke="#eee" stroke-width="1" />
      <text x="15" y="115" font-size="12" fill="#333" font-family="Arial, sans-serif">TEWL - barrier function</text>
      <text x="560" y="115" font-size="12" font-weight="bold" fill="#e07b00" font-family="Arial, sans-serif">T1h: -21.4%</text>
      <text x="780" y="115" font-size="12" font-weight="bold" fill="#e07b00" font-family="Arial, sans-serif">T8W: -15.6%</text>
      <line x1="0" y1="130" x2="1000" y2="130" stroke="#eee" stroke-width="1" />
      <text x="15" y="150" font-size="12" fill="#333" font-family="Arial, sans-serif">Skin elasticity (R2)</text>
      <text x="560" y="150" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T4W: +7.9%</text>
      <text x="780" y="150" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T8W: +14.2%</text>
    </svg></component>
  <component type="efficacy-table" formula="866420 11"><svg viewBox="0 0 1000 160" width="1000" height="160">
      <rect x="0" y="0" width="1000" height="30" fill="#f5f5f5" />
      <text x="15" y="20" font-size="14" font-weight="bold" fill="#333" font-family="Arial, sans-serif">Brazil T12W clinical test [br]</text>
      <text x="15" y="45" font-size="10" fill="#666" font-family="Arial, sans-serif">N=40, female 25-50 &#183; vs marketed reference</text>
      <line x1="0" y1="60" x2="1000" y2="60" stroke="#ddd" stroke-width="1" />
      <text x="15" y="80" font-size="12" fill="#333" font-family="Arial, sans-serif">Periorbital wrinkle depth</text>
      <text x="560" y="80" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T4W: -9.8%</text>
      <text x="780" y="80" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T12W: -19.4%</text>
      <line x1="0" y1="95" x2="1000" y2="95" stroke="#eee" stroke-width="1" />
      <text x="15" y="115" font-size="12" fill="#333" font-family="Arial, sans-serif">Skin smoothness</text>
      <text x="560" y="115" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T4W: +11.2%</text>
      <text x="780" y="115" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T12W: +22.6%</text>
      <line x1="0" y1="130" x2="1000" y2="130" stroke="#eee" stroke-width="1" />
      <text x="15" y="150" font-size="12" fill="#333" font-family="Arial, sans-serif">Skin radiance (L*)</text>
      <text x="560" y="150" font-size="12" font-weight="bold" fill="#333333" font-family="Arial, sans-serif">T4W: +3.1%</text>
      <text x="780" y="150" font-size="12" font-weight="bold" fill="#e07b00" font-family="Arial, sans-serif">T12W: +6.4%</text>
    </svg></component>
  <component type="efficacy-table" formula="866420 12"><svg viewBox="0 0 1000 160" width="1000" height="160">
      <rect x="0" y="0" width="1000" height="30" fill="#f5f5f5" />
      <text x="15" y="20" font-size="14" font-weight="bold" fill="#333" font-family="Arial, sans-serif">France 4W sensory test [fr]</text>
      <text x="15" y="45" font-size="10" fill="#666" font-family="Arial, sans-serif">N=24, trained panel &#183; descriptive analysis</text>
      <line x1="0" y1="60" x2="1000" y2="60" stroke="#ddd" stroke-width="1" />
      <text x="15" y="80" font-size="12" fill="#333" font-family="Arial, sans-serif">Skin smoothness (after feel)</text>
      <text x="780" y="80" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T4W: +22.5%</text>
      <line x1="0" y1="95" x2="1000" y2="95" stroke="#eee" stroke-width="1" />
      <text x="15" y="115" font-size="12" fill="#333" font-family="Arial, sans-serif">Spreadability</text>
      <text x="780" y="115" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T4W: +14.0%</text>
      <line x1="0" y1="130" x2="1000" y2="130" stroke="#eee" stroke-width="1" />
      <text x="15" y="150" font-size="12" fill="#333" font-family="Arial, sans-serif">Residue on skin</text>
      <text x="780" y="150" font-size="12" font-weight="bold" fill="#e07b00" font-family="Arial, sans-serif">T4W: +8.6%</text>
    </svg></component>
  <component type="consumer-block" formula="866420 12"><svg viewBox="0 0 1000 130" width="1000" height="130">
      <rect x="0" y="0" width="1000" height="30" fill="#e3f2fd" />
      <text x="15" y="20" font-size="14" font-weight="bold" fill="#1565c0" font-family="Arial, sans-serif">UK consumer perception test [gb]</text>
      <text x="15" y="45" font-size="10" fill="#666" font-family="Arial, sans-serif">N=104, 12-week home-use &#183; vs 866420 11</text>
      <text x="15" y="70" font-size="12" fill="#333" font-family="Arial, sans-serif">&#8226; Wrinkles appear less visible</text>
      <text x="670" y="70" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T12W: 89.0%</text>
      <text x="15" y="95" font-size="12" fill="#333" font-family="Arial, sans-serif">&#8226; Skin feels firmer</text>
      <text x="670" y="95" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T12W: 84.6%</text>
      <text x="15" y="120" font-size="12" fill="#333" font-family="Arial, sans-serif">&#8226; Product absorbs quickly</text>
      <text x="670" y="120" font-size="12" font-weight="bold" fill="#333333" font-family="Arial, sans-serif">T12W: 76.0%</text>
    </svg></component>
  <component type="consumer-block" formula="866420 11"><svg viewBox="0 0 1000 130" width="1000" height="130">
      <rect x="0" y="0" width="1000" height="30" fill="#e3f2fd" />
      <text x="15" y="20" font-size="14" font-weight="bold" fill="#1565c0" font-family="Arial, sans-serif">Brazil consumer perception test [br]</text>
      <text x="15" y="45" font-size="10" fill="#666" font-family="Arial, sans-serif">N=88, 8-week home-use</text>
      <text x="15" y="70" font-size="12" fill="#333" font-family="Arial, sans-serif">&#8226; Complexion looks more radiant</text>
      <text x="670" y="70" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T8W: 81.8%</text>
      <text x="15" y="95" font-size="12" fill="#333" font-family="Arial, sans-serif">&#8226; Wrinkles look reduced</text>
      <text x="670" y="95" font-size="12" font-weight="bold" fill="#2e7d32" font-family="Arial, sans-serif">T8W: 74.2%</text>
      <text x="15" y="120" font-size="12" fill="#333" font-family="Arial, sans-serif">&#8226; Texture too rich for oily skin</text>
      <text x="670" y="120" font-size="12" font-weight="bold" fill="#e07b00" font-family="Arial, sans-serif">T8W: 22.7%</text>
    </svg></component>
</deck>`;

  /* ---------- Mock AI conversation ----------
     Mirrors prompt/system.md: the AI owns the project-level narrative, never
     computes layout, and the front end (not the AI) owns the output mode. */
  const chat = {
    brief: [
      "\ud83d\udc4b Hi \u2014 I'm the **L'Or\u00e9al GPT** synthesis assistant. I've read the 7 documents in your AI-feed folder.",
      "**P-TIOX** \u2014 a DEV-stage anti-wrinkle project evaluated on two formula codes (**866420 11** \u2192 **866420 12**), carrying 3 clinical, 1 sensory and 2 consumer tests.",
      "Before I write the deck, three quick confirmations. Nothing is generated until you approve them.",
    ],
    steps: [
      {
        title: "1 / 3 \u00b7 Project subject & formula numbers",
        ai: [
          "I'll treat **P-TIOX** as the single synthesis subject \u2014 every overall claim and the Performance Summary will be written in project-level voice, not in the voice of a formula code.",
          "Formula chain: **866420 11 \u2192 866420 12**. **866420 12** is the evaluated formula; **866420 11** appears only as its benchmark. Both stay in the deck regardless, and each study keeps `formula=\"<code>\"` so the local renderer can re-filter later.",
        ],
        chips: [
          {
            label: "Confirmed \u2014 12 is the evaluated formula",
            reply: "Locked. Formula index = `866420 11|866420 12` (latest = **866420 12**), subject = **P-TIOX**.",
          },
          {
            label: "Also register a third code",
            reply: "Understood \u2014 noting a third code for the registry. For this demo run I'll still build from the two codes already present in the documents; you can add it before the next extraction.",
          },
          {
            label: "Use 866420 11 as the subject",
            reply: "Noted. I'll keep the subject at **P-TIOX** (project level) and surface **866420 11** as the comparison anchor instead \u2014 formula codes never take over the project-level conclusion.",
          },
        ],
      },
      {
        title: "2 / 3 \u00b7 Output context",
        ai: [
          "The front end pre-flagged **DECK** mode for this project, so I'll write SVG components for a paginated deck \u2014 not a one-page summary.",
          "Output context: English, project-level voice, for the **R&D evaluation committee** review pack. Target **\u2264 6 pages**, one slide per study or consumer test, regional coverage left to the `region-bar` chart rather than the summary band.",
        ],
        chips: [
          {
            label: "Confirmed \u2014 DECK, R&D committee",
            reply: "Understood: DECK mode, \u2264 6 pages, English, committee audience.",
          },
          {
            label: "Lead with the clinical evidence",
            reply: "Fine \u2014 I'll order the evidence area clinical \u2192 sensory \u2192 consumer so the strongest endpoints land on page 1.",
          },
          {
            label: "Shorten to 3 pages",
            reply: "I'll keep one component per study \u2014 that is not negotiable for data fidelity \u2014 but I'll tighten the summary band so the deck lands in fewer pages.",
          },
        ],
      },
      {
        title: "3 / 3 \u00b7 Synthesis outline & project narrative",
        ai: [
          "**Outline** I'll render:\n\u2022 **top banner** \u2014 title + formula-ref + one `PERFORMANCE SUMMARY` band (repeats on every page)\n\u2022 **left column** \u2014 8 labelled cards: Type \u00b7 Audience \u00b7 Communication \u00b7 Formulation \u00b7 Fragrance \u00b7 Packaging \u00b7 Sustainability \u00b7 Safety\n\u2022 **core content** \u2014 3 clinical tables + 1 sensory table + 2 consumer blocks, one component per study",
          "**Project narrative** \u2014 the fixed three beats: \u2460 *project subject & objective* \u2192 \u2461 *evidence & testing* (streams, cohorts, timepoints) \u2192 \u2462 *results & takeaway*, colour-coded green / orange / red straight from the extracted `color_code`. I never recompute a percentage and never invent a colour.",
          "Approve and I'll emit the `<deck>` XML and hand it straight to the SVG \u2192 PPTX converter.",
        ],
        chips: [
          {
            label: "Confirmed \u2014 write the deck",
            reply: "Confirmed. Writing the `<deck>` XML now \u2014 17 components, then handing it to the converter.",
          },
          {
            label: "Adjust the outline first",
            reply: "Happy to iterate on wording \u2014 for this demo run I'll proceed with the outline above so you can see the full hand-off, and you can revise any component afterwards in the converter.",
          },
        ],
      },
    ],
    closing: "Done \u2014 the deck XML has been placed in the **SVG \u2192 PPTX** input box. Opening the converter\u2026",
    deckSummary: [
      ["Project", project.name],
      ["Formula index", project.formulaChain.join(" \u2192 ")],
      ["Output mode", project.outputMode],
      ["Components", "17 \u00b7 1 title, 1 formula-ref, 3 meta, 5 info-card, 1 summary-block, 4 efficacy-table, 2 consumer-block"],
      ["Evidence", "3 CLINS \u00b7 1 FE \u00b7 2 CE"],
    ],
  };

  /* ---------- Rendered deck pages (static mock; see demo/deck-page-*.svg) ---------- */
  const deckPages = [
    { caption: "Page 1 / 3", img: "demo/deck-page-1.svg", alt: "deck page 1 - banner, metadata column, China and US clinical tables" },
    { caption: "Page 2 / 3", img: "demo/deck-page-2.svg", alt: "deck page 2 - Brazil clinical table, France sensory table, UK consumer block" },
    { caption: "Page 3 / 3", img: "demo/deck-page-3.svg", alt: "deck page 3 - Brazil consumer block" },
  ];

  const buildResult = {
    filename: "P-TIOX_synthesis_deck.pptx",
    pageCount: 3,
    sizeKb: 1284.6,
    warnings: [],
    outputFolder: "C:\\Users\\Public\\Downloads",
  };

  return {
    project: project,
    searchTarget: searchTarget,
    sources: sources,
    log: log,
    folderTree: folderTree,
    config: config,
    deckXml: deckXml,
    chat: chat,
    deckPages: deckPages,
    buildResult: buildResult,
  };
})();
