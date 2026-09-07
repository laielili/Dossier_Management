/* ============================================================
   Dossier Search — homepage: search target folder by keyword,
   select files, then preprocess (scan -> classify -> ingest -> condense).

   Reuses the project's "Paper Workspace" design system (style.css).
   ============================================================ */

const $ = (sel) => document.querySelector(sel);

// Downstream AI client (L'Oréal GPT). Opened from the "L'Oréal GPT" button on
// the preprocessing result card. Empty for now — fill in the web URL here (or
// move to a backend config) when the client is available; the button is a
// no-op until then.
const LOREAL_GPT_URL = "";

// --- Logging (local console; mirrors the legacy page) -----------
function log(message, level = "info") {
  const logArea = $("#log-area");
  const time = new Date().toLocaleTimeString();
  const line = document.createElement("div");
  line.className = `log-${level}`;
  line.textContent = `[${time}] ${message}`;
  logArea.appendChild(line);
  logArea.scrollTop = logArea.scrollHeight;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// =================================================================
// Step navigation
// =================================================================
function goStep(n) {
  document.querySelectorAll(".step-page").forEach((p) => p.classList.remove("active"));
  $("#step-" + n).classList.add("active");
  $("#pill-1").classList.toggle("active", n === 1);
  $("#pill-2").classList.toggle("active", n === 2);
}
$("#pill-1").addEventListener("click", () => goStep(1));
$("#btn-back").addEventListener("click", () => goStep(1));

// =================================================================
// Folder browser modal (backed by /browse-folders)
// =================================================================
let currentBrowsePath = "";
let currentBrowseParent = null;

async function openFolderBrowser() {
  currentBrowsePath = $("#search-path").value.trim();
  await folderBrowseNavigate(currentBrowsePath);
  await renderModalSavedPaths();
  $("#folder-modal").classList.remove("hidden");
}

async function renderModalSavedPaths() {
  const list = $("#saved-paths-list");
  list.innerHTML = "";
  try {
    const res = await fetch("/config/search-paths");
    const data = await res.json();
    const paths = data.paths || [];
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
      del.textContent = "🗑️";
      del.title = "Remove this saved path";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteSearchPath(p);
      });
      row.appendChild(label);
      row.appendChild(del);
      list.appendChild(row);
    });
  } catch (err) {
    /* optional */
  }
}

async function folderBrowseNavigate(path) {
  currentBrowsePath = path || "";
  try {
    const url =
      "/browse-folders" +
      (currentBrowsePath ? "?path=" + encodeURIComponent(currentBrowsePath) : "");
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok) {
      log("Folder browse failed.", "error");
      return;
    }
    renderFolderList(data);
  } catch (err) {
    log("Folder browse error.", "error");
  }
}

function renderFolderList(data) {
  currentBrowseParent = data.parent || null;
  const cur = $("#folder-current");
  cur.textContent = data.path
    ? data.path
    : data.drives && data.drives.length
    ? "Select a drive"
    : "/";
  const list = $("#folder-list");
  list.innerHTML = "";
  if (data.parent) {
    const up = document.createElement("div");
    up.className = "folder-item folder-up";
    up.textContent = "..";
    up.addEventListener("click", () => folderBrowseNavigate(data.parent));
    list.appendChild(up);
  }
  (data.drives || []).forEach((d) => {
    const item = document.createElement("div");
    item.className = "folder-item";
    item.textContent = d;
    item.addEventListener("click", () => folderBrowseNavigate(d));
    list.appendChild(item);
  });
  (data.dirs || []).forEach((d) => {
    const item = document.createElement("div");
    item.className = "folder-item";
    item.textContent = d;
    item.addEventListener("click", () => folderBrowseNavigate(d));
    list.appendChild(item);
  });
}

$("#btn-browse-search").addEventListener("click", openFolderBrowser);

// =================================================================
// Search target path config (saved history, browse, delete)
// =================================================================
async function loadSearchPaths() {
  try {
    const res = await fetch("/config/search-paths");
    const data = await res.json();
    renderSavedSearchPaths(data.paths || []);
    if (data.active && !$("#search-path").value.trim()) {
      $("#search-path").value = data.active;
    }
  } catch (err) {
    /* optional */
  }
}

function renderSavedSearchPaths(paths) {
  const list = $("#saved-search-paths");
  if (!list) return; // main-page list hidden; modal list uses renderModalSavedPaths
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
    label.title = "Load this path";
    label.addEventListener("click", () => ($("#search-path").value = p));
    const del = document.createElement("button");
    del.className = "saved-path-del";
    del.textContent = "🗑️";
    del.title = "Remove this saved path";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSearchPath(p);
    });
    row.appendChild(label);
    row.appendChild(del);
    list.appendChild(row);
  });
}

async function saveSearchPath(p) {
  if (!p) return false;
  try {
    const res = await fetch("/config/search-paths", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: p }),
    });
    const data = await res.json();
    return !!data.ok;
  } catch (err) {
    return false;
  }
}

async function deleteSearchPath(p) {
  if (!confirm("Delete this saved path?\n" + p)) return;
  try {
    const res = await fetch(
      "/config/search-paths?path=" + encodeURIComponent(p),
      { method: "DELETE" }
    );
    const data = await res.json();
    if (data.ok) {
      loadSearchPaths();
    } else {
      log("Failed to delete path: " + (data.detail || ""), "error");
    }
  } catch (err) {
    log("Delete error: " + err.message, "error");
  }
}

$("#btn-save-search").addEventListener("click", async () => {
  const p = $("#search-path").value.trim();
  if (!p) {
    log("Enter or choose a folder path first.", "warn");
    return;
  }
  if (await saveSearchPath(p)) {
    log("Search target path saved.", "success");
    loadSearchPaths();
  }
});

$("#folder-select").addEventListener("click", async () => {
  if (!currentBrowsePath) {
    log("Navigate into a folder first, then select it.", "warn");
    return;
  }
  $("#search-path").value = currentBrowsePath;
  $("#folder-modal").classList.add("hidden");
  if (await saveSearchPath(currentBrowsePath)) log("Search target path saved.", "success");
});

$("#folder-up").addEventListener("click", () => {
  if (currentBrowseParent) folderBrowseNavigate(currentBrowseParent);
});
$("#folder-modal-close").addEventListener("click", () =>
  $("#folder-modal").classList.add("hidden")
);
$("#folder-modal-backdrop").addEventListener("click", () =>
  $("#folder-modal").classList.add("hidden")
);

// =================================================================
// Search
// =================================================================
// Last-modified date filter is opt-in: the date inputs only appear once the
// checkbox is ticked. When unchecked, modified_enabled stays false so no
// date window is sent to the backend.
function syncDateFilter() {
  $("#date-inputs").hidden = !$("#date-enabled").checked;
}
$("#date-enabled").addEventListener("change", syncDateFilter);
syncDateFilter();

$("#btn-search").addEventListener("click", async () => {
  const target = $("#search-path").value.trim();
  if (!target) {
    log("Set the Search Target Path first.", "warn");
    return;
  }
  const keywords = [$("#kw1").value, $("#kw2").value, $("#kw3").value];
  const modified_enabled = $("#date-enabled").checked;
  const modified_years = parseInt($("#date-years").value || "0", 10);
  const modified_months = parseInt($("#date-months").value || "0", 10);

  const btn = $("#btn-search");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Searching…';
  try {
    const res = await fetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_path: target,
        keywords,
        modified_enabled,
        modified_years,
        modified_months,
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      log("Search failed: " + (data.detail || ""), "error");
      return;
    }
    // Project name defaults to the first keyword (retrieved files land in
    // retrieved/<first_keyword>/). The user can still override it before Next.
    const firstKw = keywords.map((k) => k.trim()).find((k) => k) || "";
    if (firstKw) $("#project-name").value = firstKw;
    renderResults(data.files || []);
  } catch (err) {
    log("Search error: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "&#9654;&nbsp; Start";
  }
});

let currentFiles = [];

function renderResults(files) {
  currentFiles = files;
  $("#result-count").textContent = files.length;
  const body = $("#results-body");
  body.innerHTML = "";
  const empty = $("#results-empty");
  const card = $("#results-card");
  card.classList.remove("hidden");
  if (!files.length) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  files.forEach((f, i) => {
    const ext = (f.ext || "").toLowerCase();
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="col-check"><input type="checkbox" class="file-check" data-idx="${i}"></td>` +
      `<td class="col-icon"><span class="ext-badge ext-${ext}">${escapeHtml(f.ext)}</span></td>` +
      `<td class="col-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</td>` +
      `<td class="col-type">${f.size_kb} KB</td>` +
      `<td class="col-mod">${escapeHtml(f.modified)}</td>`;
    body.appendChild(tr);
  });
}

$("#select-all").addEventListener("change", (e) => {
  const checked = e.target.checked;
  document
    .querySelectorAll("#results-body .file-check")
    .forEach((cb) => (cb.checked = checked));
});

// =================================================================
// Next -> start preprocessing
// =================================================================
let retrievePollTimer = null;
const STAGES = ["scan", "classify", "ingest", "condense"];

function resetStageTracker() {
  const t = $("#stage-track");
  t.querySelectorAll(".stage").forEach((el) => el.classList.remove("running", "done"));
  t.querySelectorAll(".stage-sep").forEach((el) => el.classList.remove("done"));
}

function updateStageTracker(stage) {
  const t = $("#stage-track");
  const stageEls = t.querySelectorAll(".stage");
  const sepEls = t.querySelectorAll(".stage-sep");
  const idx = STAGES.indexOf(stage);
  stageEls.forEach((el, i) => {
    el.classList.toggle("done", idx >= 0 && i < idx);
    el.classList.toggle("running", i === idx);
    if (sepEls[i]) sepEls[i].classList.toggle("done", idx >= 0 && i < idx);
  });
}

function markAllStagesDone() {
  const t = $("#stage-track");
  t.querySelectorAll(".stage").forEach((el) => {
    el.classList.remove("running");
    el.classList.add("done");
  });
  t.querySelectorAll(".stage-sep").forEach((el) => el.classList.add("done"));
}

$("#btn-next").addEventListener("click", async () => {
  const name = $("#project-name").value.trim();
  if (!name) {
    log("Enter a project name before proceeding.", "warn");
    return;
  }
  const checked = [...document.querySelectorAll("#results-body .file-check:checked")];
  if (!checked.length) {
    log("Select at least one file to preprocess.", "warn");
    return;
  }
  const files = checked.map((cb) => currentFiles[parseInt(cb.dataset.idx, 10)].path);

  const btn = $("#btn-next");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Starting…';
  try {
    const res = await fetch("/retrieve/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_name: name, files }),
    });
    const data = await res.json();
    if (!data.ok) {
      log("Could not start: " + (data.detail || ""), "error");
      return;
    }
    const finalName = data.project_name || name;
    $("#prep-project").textContent = finalName;
    $("#prep-folder").textContent = finalName;
    log(`Retrieval started for "${finalName}" — ${files.length} file(s) selected.`);
    goStep(2);
    $("#stage-track").classList.remove("hidden");
    resetStageTracker();
    updateStageTracker("scan"); // light up the first step immediately
    $("#prep-progress").classList.remove("hidden");
    $("#prep-progress").textContent = "Stage: scan…";
    if (retrievePollTimer) clearInterval(retrievePollTimer);
    retrievePollTimer = setInterval(pollRetrieveStatus, 1000);
  } catch (err) {
    log("Start error: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "Next &rsaquo; Preprocess";
  }
});

async function pollRetrieveStatus() {
  try {
    const res = await fetch("/retrieve/status");
    const data = await res.json();
    if (!data.ok) return;

    const prog = $("#prep-progress");
    if (data.running) {
      if (data.current_stage) updateStageTracker(data.current_stage);
      prog.classList.remove("hidden");
      prog.textContent = data.current_stage
        ? `Stage: ${data.current_stage}…`
        : "Working…";
      return;
    }

    // Finished.
    clearInterval(retrievePollTimer);
    retrievePollTimer = null;
    prog.classList.add("hidden");
    markAllStagesDone();
    $("#prep-result").classList.remove("hidden");
    const rt = $("#prep-result-text");
    if (data.error) {
      rt.textContent = "Preprocess finished with errors — see log.";
      rt.className = "log-warn";
      return;
    }
    const fw = (data.result && data.result.files_written) || [];
    const folder = $("#prep-folder").textContent;
    rt.textContent =
      `✓ Preprocess finished — ${fw.length} file(s) written to ` +
      `retrieved/${folder}/AI_feed/${folder}/` +
      ` (drag the ${folder} folder into the downstream AI).`;
    rt.className = "success";
  } catch (err) {
    /* transient */
  }
}

// =================================================================
// L'Oréal GPT button — open the downstream AI client
// =================================================================
$("#btn-loreal-gpt").addEventListener("click", () => {
  if (!LOREAL_GPT_URL) {
    log("L'Oréal GPT URL not configured yet.", "warn");
    return;
  }
  window.open(LOREAL_GPT_URL, "_blank", "noopener");
});

// =================================================================
// Activity feed (server-side pipeline progress -> local log)
// =================================================================
let lastEventId = 0;
async function pollActivity() {
  try {
    const res = await fetch(`/activity?since=${lastEventId}`);
    const data = await res.json();
    if (data.ok) {
      (data.events || []).forEach((e) => log(e.message, e.level || "info"));
      lastEventId = data.last_id || lastEventId;
    }
  } catch (err) {
    /* transient */
  }
}

// =================================================================
// Configuration modal (copied from the File Listener page)
//   - classification vocabulary (classify/*.txt)
//   - noise filtering (deleted noise types + veto terms)
// =================================================================

function setButtonLoading(btn, loading) {
  if (loading) {
    btn.dataset.originalText = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span>' + btn.dataset.originalText;
    btn.disabled = true;
  } else {
    btn.textContent = btn.dataset.originalText || btn.textContent;
    btn.disabled = false;
  }
}

function bindConfigModal(rowId, modalId, onOpen) {
  const row = document.getElementById(rowId);
  const modal = document.getElementById(modalId);
  if (!row || !modal) return;
  const close = () => modal.classList.add("hidden");
  const open = () => {
    modal.classList.remove("hidden");
    if (typeof onOpen === "function") onOpen();
  };
  row.addEventListener("click", open);
  modal.querySelector(".modal-close").addEventListener("click", close);
  modal.querySelector(".modal-backdrop").addEventListener("click", close);
}
bindConfigModal("btn-config", "config-modal", () => {
  loadProfiles();
  loadNoiseConfig();
});

// Config modal: collapsible accordion sections
document.querySelectorAll(".accordion-head").forEach((head) => {
  head.addEventListener("click", () => {
    const acc = head.closest(".accordion");
    const open = acc.classList.toggle("open");
    head.setAttribute("aria-expanded", open ? "true" : "false");
  });
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const m = document.getElementById("config-modal");
  if (m && !m.classList.contains("hidden")) m.classList.add("hidden");
});

// --- Classification anchors (classify/*.txt) ---
function getProfilesFromUI() {
  return {
    CLINS: $("#profile-CLINS").value.trim(),
    FE: $("#profile-FE").value.trim(),
    CE: $("#profile-CE").value.trim(),
  };
}

async function loadProfiles() {
  try {
    const res = await fetch("/classify/profiles");
    const data = await res.json();
    if (data.ok && data.profiles) {
      for (const [type, text] of Object.entries(data.profiles)) {
        const el = document.getElementById(`profile-${type}`);
        if (el) el.value = text;
      }
      log("Loaded classification anchors from classify/*.txt", "info");
    }
  } catch (err) {
    log("Failed to load profiles: " + err.message, "warn");
  }
}

$("#btn-save-profiles").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  setButtonLoading(btn, true);
  try {
    const res = await fetch("/classify/profiles/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profiles: getProfilesFromUI() }),
    });
    const data = await res.json();
    if (data.ok) {
      log(`Saved classification anchors: ${data.saved.join(", ")}`, "success");
    } else {
      log("Failed to save profiles: " + (data.detail || ""), "error");
    }
  } catch (err) {
    log("Save profiles error: " + err.message, "error");
  }
  setButtonLoading(btn, false);
});

// --- Noise-based page filtering ---
const noiseCats = $("#noise-cats");
const queryTxt = $("#query-txt");

async function loadNoiseConfig() {
  try {
    const [paramsRes, qRes] = await Promise.all([
      fetch("/config/params"),
      fetch("/queries"),
    ]);
    const params = await paramsRes.json();
    const qData = await qRes.json();
    if (params.ok) {
      noiseCats.innerHTML = "";
      (params.noise_categories || []).forEach(c => {
        const cls = c.active ? "chip chip-on" : "chip chip-off";
        noiseCats.insertAdjacentHTML("beforeend", `<span class="${cls}">${c.label}${c.active ? "" : " (off)"}</span>`);
      });
    }
    if (qData.ok && qData.queries) {
      const txt = qData.queries.CLINS || qData.queries.FE || qData.queries.CE || "";
      queryTxt.value = txt;
    }
  } catch (err) {
    // config params / queries are optional — ignore network errors silently
  }
}

$("#btn-save-query").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  setButtonLoading(btn, true);
  try {
    const text = queryTxt.value;
    const res = await fetch("/queries/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ queries: { CLINS: text, FE: text, CE: text } }),
    });
    const data = await res.json();
    if (data.ok) {
      log("Saved query lexicon to queries/query.txt", "success");
    } else {
      log("Failed to save queries: " + (data.detail || ""), "error");
    }
  } catch (err) {
    log("Save queries error: " + err.message, "error");
  }
  setButtonLoading(btn, false);
});

// =================================================================
// Startup
// =================================================================
log("Dossier Search ready.", "info");
log(
  "1) Set the Search Target Path. 2) Enter 1–3 keywords + last-modified " +
  "date within, then Start. 3) Select files, name the project, click Next."
);
loadSearchPaths();
setInterval(pollActivity, 4000);
