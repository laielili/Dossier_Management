/* ============================================================
   Dossier Search — /docs demo build (semi-static)

   Static replica of static/search-app.js. GitHub Pages has no Python
   backend, so this build never calls fetch(): every "server" response is
   canned in demo-data.js. Buttons stay live but only toggle state, reveal
   pre-filled cards, or write a log line — no simulated progress animation.

   Simulated data only — see demo-data.js.
   ============================================================ */

(function () {
  "use strict";

  const DEMO = window.DEMO;
  const $ = (sel) => document.querySelector(sel);

  // ---------------------------------------------------------------
  // Logging
  // ---------------------------------------------------------------
  function log(message, level) {
    const logArea = $("#log-area");
    const time = new Date().toLocaleTimeString();
    const line = document.createElement("div");
    line.className = "log-" + (level || "info");
    line.textContent = "[" + time + "] " + message;
    logArea.appendChild(line);
    logArea.scrollTop = logArea.scrollHeight;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  // ---------------------------------------------------------------
  // Step navigation
  // ---------------------------------------------------------------
  function goStep(n) {
    document.querySelectorAll(".step-page").forEach((p) => p.classList.remove("active"));
    $("#step-" + n).classList.add("active");
    $("#pill-1").classList.toggle("active", n === 1);
    $("#pill-1").classList.toggle("done", n === 2);
    $("#pill-2").classList.toggle("active", n === 2);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  $("#pill-1").addEventListener("click", () => goStep(1));
  $("#pill-2").addEventListener("click", () => goStep(2));
  $("#btn-back").addEventListener("click", () => goStep(1));

  // ---------------------------------------------------------------
  // Folder picker modal — navigates the canned tree in demo-data.js
  // ---------------------------------------------------------------
  let currentBrowsePath = "";
  let currentBrowseParent = null;

  function renderFolderList(data) {
    currentBrowseParent = data.parent || null;
    $("#folder-current").textContent = data.path
      ? data.path
      : (data.drives && data.drives.length ? "Select a drive" : "/");
    const list = $("#folder-list");
    list.innerHTML = "";
    if (data.parent) {
      const up = document.createElement("div");
      up.className = "folder-item folder-up";
      up.textContent = "..";
      up.addEventListener("click", () => folderBrowseNavigate(data.parent));
      list.appendChild(up);
    }
    (data.drives || []).concat(data.dirs || []).forEach((d) => {
      const item = document.createElement("div");
      item.className = "folder-item";
      item.textContent = d;
      item.addEventListener("click", () => folderBrowseNavigate(d));
      list.appendChild(item);
    });
  }

  function folderBrowseNavigate(path) {
    currentBrowsePath = path || "";
    const node = DEMO.folderTree[currentBrowsePath] || {
      path: currentBrowsePath, parent: null, drives: [], dirs: [],
    };
    renderFolderList(node);
  }

  function loadSearchPaths() {
    try {
      const data = JSON.parse(localStorage.getItem("demo_search_paths") || "null");
      if (Array.isArray(data) && data.length) return data;
    } catch (e) { /* fall through */ }
    return [DEMO.searchTarget, "D:\\Projects\\Dossiers\\2026-Q2"];
  }

  function renderModalSavedPaths() {
    const list = $("#saved-paths-list");
    const paths = loadSearchPaths();
    list.innerHTML = "";
    if (!paths.length) {
      list.innerHTML = '<div class="saved-path-empty muted">No saved paths yet.</div>';
      return;
    }
    paths.forEach((p) => {
      const row = document.createElement("div");
      row.className = "saved-path-item";
      const label = document.createElement("span");
      label.className = "saved-path-text";
      label.textContent = p;
      label.title = "Browse this folder";
      label.addEventListener("click", () => folderBrowseNavigate(p));
      const del = document.createElement("button");
      del.className = "saved-path-del";
      del.textContent = "\uD83D\uDDD1\uFE0F";
      del.title = "Remove this saved path";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        const next = loadSearchPaths().filter((x) => x !== p);
        try { localStorage.setItem("demo_search_paths", JSON.stringify(next)); } catch (err) { /* ignore */ }
        renderModalSavedPaths();
        log("Removed saved path.", "info");
      });
      row.appendChild(label);
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  function openFolderBrowser() {
    currentBrowsePath = $("#search-path").value.trim();
    folderBrowseNavigate(currentBrowsePath);
    renderModalSavedPaths();
    $("#folder-modal").classList.remove("hidden");
  }

  $("#btn-browse-search").addEventListener("click", openFolderBrowser);
  $("#folder-up").addEventListener("click", () => {
    if (currentBrowseParent) folderBrowseNavigate(currentBrowseParent);
  });
  $("#folder-modal-close").addEventListener("click", () =>
    $("#folder-modal").classList.add("hidden")
  );
  $("#folder-modal-backdrop").addEventListener("click", () =>
    $("#folder-modal").classList.add("hidden")
  );
  $("#folder-select").addEventListener("click", () => {
    if (!currentBrowsePath) {
      log("Navigate into a folder first, then select it.", "warn");
      return;
    }
    $("#search-path").value = currentBrowsePath;
    $("#folder-modal").classList.add("hidden");
    log("Search target path saved.", "success");
  });

  $("#btn-save-search").addEventListener("click", () => {
    const p = $("#search-path").value.trim();
    if (!p) { log("Enter or choose a folder path first.", "warn"); return; }
    log("Search target path saved.", "success");
  });

  // ---------------------------------------------------------------
  // Search — renders the canned result set
  // ---------------------------------------------------------------
  function syncDateFilter() {
    $("#date-inputs").hidden = !$("#date-enabled").checked;
  }
  $("#date-enabled").addEventListener("change", syncDateFilter);

  let currentFiles = [];

  function renderResults(files) {
    currentFiles = files;
    $("#result-count").textContent = files.length;
    const body = $("#results-body");
    body.innerHTML = "";
    $("#results-card").classList.remove("hidden");
    if (!files.length) {
      $("#results-empty").classList.remove("hidden");
      return;
    }
    $("#results-empty").classList.add("hidden");
    files.forEach((f, i) => {
      const ext = (f.ext || "").toLowerCase();
      const tr = document.createElement("tr");
      tr.innerHTML =
        '<td class="col-check"><input type="checkbox" class="file-check" data-idx="' + i + '"' +
          (f.keep ? " checked" : "") + "></td>" +
        '<td class="col-icon"><span class="ext-badge ext-' + ext + '">' + escapeHtml(f.ext) + "</span></td>" +
        '<td class="col-name" title="' + escapeHtml(f.name) + '">' + escapeHtml(f.name) + "</td>" +
        '<td class="col-type">' + f.size_kb + " KB</td>" +
        '<td class="col-mod">' + escapeHtml(f.modified) + "</td>";
      body.appendChild(tr);
    });
  }

  $("#select-all").addEventListener("change", (e) => {
    const checked = e.target.checked;
    document.querySelectorAll("#results-body .file-check").forEach((cb) => (cb.checked = checked));
  });

  $("#btn-search").addEventListener("click", () => {
    const target = $("#search-path").value.trim();
    if (!target) { log("Set the Search Target Path first.", "warn"); return; }
    const kw = [$("#kw1").value, $("#kw2").value, $("#kw3").value]
      .map((k) => k.trim()).filter(Boolean);
    log('Searching ' + target + " (recursive, 4 extensions) ...");
    renderResults(DEMO.sources);
    log('Search finished - ' + DEMO.sources.length + ' dossier file(s) matched keyword "' + (kw[0] || "") + '".', "success");
    if (kw[0]) $("#project-name").value = kw[0];
  });

  // ---------------------------------------------------------------
  // Next -> preprocess (pre-filled completed state)
  // ---------------------------------------------------------------
  function markAllStagesDone() {
    const t = $("#stage-track");
    t.querySelectorAll(".stage").forEach((el) => {
      el.classList.remove("running");
      el.classList.add("done");
    });
    t.querySelectorAll(".stage-sep").forEach((el) => el.classList.add("done"));
  }

  $("#btn-next").addEventListener("click", () => {
    const name = $("#project-name").value.trim();
    if (!name) { log("Enter a project name before proceeding.", "warn"); return; }
    const checked = document.querySelectorAll("#results-body .file-check:checked");
    if (!checked.length) { log("Select at least one file to preprocess.", "warn"); return; }

    ["#prep-project", "#prep-folder", "#prep-folder-2", "#prep-folder-3", "#prep-folder-4"]
      .forEach((sel) => { const el = $(sel); if (el) el.textContent = name; });

    log('Retrieval started for "' + name + '" - ' + checked.length + " file(s) selected.");
    goStep(2);

    const track = $("#stage-track");
    track.classList.remove("hidden");
    markAllStagesDone();

    $("#prep-result").classList.remove("hidden");
    const fw = DEMO.sources.filter((f) => f.keep).length;
    const rt = $("#prep-result-text");
    rt.className = "success";
    rt.textContent =
      "\u2713 Preprocess finished \u2014 " + fw + " file(s) written to " +
      "retrieved/" + name + "/AI_feed/" + name + "/" +
      " (drag the " + name + " folder into the downstream AI).";
    DEMO.log.index.filter(([lvl]) => lvl !== "info").forEach(([lvl, msg]) => log(msg, lvl));
  });

  // ---------------------------------------------------------------
  // Configuration modal
  // ---------------------------------------------------------------
  function bindModal(openBtnId, modalId, onOpen) {
    const btn = document.getElementById(openBtnId);
    const modal = document.getElementById(modalId);
    if (!btn || !modal) return;
    const close = () => modal.classList.add("hidden");
    btn.addEventListener("click", () => {
      modal.classList.remove("hidden");
      if (typeof onOpen === "function") onOpen();
    });
    modal.querySelector(".modal-close").addEventListener("click", close);
    modal.querySelector(".modal-backdrop").addEventListener("click", close);
  }

  function loadProfiles() {
    Object.entries(DEMO.config.profiles).forEach(([type, text]) => {
      const el = document.getElementById("profile-" + type);
      if (el) el.value = text;
    });
  }

  function loadNoiseConfig() {
    const cats = $("#noise-cats");
    cats.innerHTML = "";
    DEMO.config.noiseCategories.forEach((c) => {
      const cls = c.active ? "chip chip-on" : "chip chip-off";
      cats.insertAdjacentHTML("beforeend",
        '<span class="' + cls + '">' + escapeHtml(c.label) + (c.active ? "" : " (off)") + "</span>");
    });
    $("#query-txt").value = DEMO.config.queryTxt;
  }

  bindModal("btn-config", "config-modal", () => {
    loadProfiles();
    loadNoiseConfig();
  });

  document.querySelectorAll(".accordion-head").forEach((head) => {
    head.addEventListener("click", () => {
      const acc = head.closest(".accordion");
      const open = acc.classList.toggle("open");
      head.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    ["config-modal", "folder-modal"].forEach((id) => {
      const m = document.getElementById(id);
      if (m && !m.classList.contains("hidden")) m.classList.add("hidden");
    });
  });

  document.getElementById("btn-save-profiles").addEventListener("click", () =>
    log("Saved classification anchors: CLINS, FE, CE", "success")
  );
  document.getElementById("btn-save-query").addEventListener("click", () =>
    log("Saved query lexicon to queries/query.txt", "success")
  );

  // ---------------------------------------------------------------
  // Demo pre-fill — the page opens with a finished run already on screen
  // ---------------------------------------------------------------
  function prefill() {
    // Step 1 inputs
    $("#search-path").value = DEMO.searchTarget;
    $("#kw1").value = DEMO.project.name;
    $("#date-enabled").checked = true;
    syncDateFilter();

    // Step 1 results (pre-rendered)
    renderResults(DEMO.sources);
    $("#project-name").value = DEMO.project.name;

    // Step 2 completed state
    ["#prep-project", "#prep-folder", "#prep-folder-2", "#prep-folder-3", "#prep-folder-4"]
      .forEach((sel) => { const el = $(sel); if (el) el.textContent = DEMO.project.name; });
    $("#stage-track").classList.remove("hidden");
    markAllStagesDone();
    const fw = DEMO.sources.filter((f) => f.keep).length;
    const rt = $("#prep-result-text");
    rt.className = "success";
    rt.textContent =
      "\u2713 Preprocess finished \u2014 " + fw + " file(s) written to " +
      "retrieved/" + DEMO.project.name + "/AI_feed/" + DEMO.project.name + "/" +
      " (drag the " + DEMO.project.name + " folder into the downstream AI).";
    $("#prep-result").classList.remove("hidden");

    // Log
    DEMO.log.index.forEach(([lvl, msg]) => log(msg, lvl));
  }

  prefill();
})();
