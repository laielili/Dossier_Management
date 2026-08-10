/* ============================================================
   Dossier_Management - Simplified one-page frontend

   Layout:
     1. Listen Folder  — base directory config (input + Browse + Save),
                         with the Auto-Watch switch directly beneath it
     2. Configuration  — collapsed editors: classification anchors
                         (classify/*.txt) + page-selection queries
                         (queries/*.txt)
     3. Run Pipeline   — ONE button: scan → classify → ingest → package
                         → export for every project folder in the listen
                         folder; PDFs land in <listen>/Dossier_condensed/
   ============================================================ */

// --- DOM helpers ---
const $ = (sel) => document.querySelector(sel);

// --- Logging ---
function log(message, level = "info") {
  const logArea = $("#log-area");
  const time = new Date().toLocaleTimeString();
  const span = document.createElement("span");
  span.className = `log-${level}`;
  span.textContent = `[${time}] ${message}\n`;
  logArea.appendChild(span);
  logArea.scrollTop = logArea.scrollHeight;
}

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

// =================================================================
// Listen-folder config (persisted to listen_folder.txt, NOT logged)
// =================================================================

function getListenFolder() {
  return ($("#listen-folder") ? $("#listen-folder").value : "").trim();
}

// Keep the "Output: .../Dossier_condensed/" hint in sync with the input.
function syncDestPath() {
  const base = getListenFolder().replace(/[\\/]+$/, "");
  $("#dest-path").textContent = base
    ? base + "\\Dossier_condensed\\"
    : "<Listen Folder>\\Dossier_condensed\\";
}

async function loadListenFolder() {
  try {
    const res = await fetch("/config/listen-folder");
    const data = await res.json();
    if (data.ok && data.path) {
      $("#listen-folder").value = data.path;
      if (data.is_default) {
        log("No saved listen folder yet — defaulted to your Documents folder. Click 'Save Path' to keep it.", "warn");
      }
    }
  } catch (err) {
    // config is optional — ignore network errors silently
  }
  syncDestPath();
}

async function saveListenFolder(path) {
  if (!path) return false;
  try {
    const res = await fetch("/config/listen-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!data.ok) {
      log("Failed to save listen folder: " + (data.detail || ""), "error");
      return false;
    }
    return true;
  } catch (err) {
    log("Save listen folder error: " + err.message, "error");
    return false;
  }
}

$("#listen-folder").addEventListener("input", syncDestPath);

$("#btn-save-folder").addEventListener("click", async () => {
  const p = getListenFolder();
  if (!p) {
    log("Enter or choose a folder path first.", "warn");
    return;
  }
  if (await saveListenFolder(p)) {
    log("Listen folder path saved.", "success");
  }
});

// --- Folder browser (directory picker backed by /browse-folders) ---
let currentBrowsePath = "";
let currentBrowseParent = null;

async function openFolderBrowser() {
  const start = getListenFolder();
  currentBrowsePath = start;
  await folderBrowseNavigate(start);
  await renderSavedPaths();
  $("#folder-modal").classList.remove("hidden");
}

async function renderSavedPaths() {
  const list = $("#saved-paths-list");
  if (!list) return;
  list.innerHTML = "";
  let active = "";
  try {
    const res = await fetch("/config/listen-folders");
    const data = await res.json();
    active = data.active || "";
    const paths = data.paths || [];
    if (!paths.length) {
      const empty = document.createElement("div");
      empty.className = "saved-path-empty muted";
      empty.textContent = "No saved paths yet.";
      list.appendChild(empty);
      return;
    }
    paths.forEach((p) => {
      const row = document.createElement("div");
      row.className = "saved-path-item" + (p === active ? " active" : "");
      const label = document.createElement("span");
      label.className = "saved-path-text";
      label.textContent = p;
      label.title = "Click to browse this folder";
      label.addEventListener("click", () => folderBrowseNavigate(p));
      const del = document.createElement("button");
      del.className = "saved-path-del";
      del.type = "button";
      del.title = "Delete this saved path";
      del.textContent = "🗑️";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteSavedPath(p);
      });
      row.appendChild(label);
      row.appendChild(del);
      list.appendChild(row);
    });
  } catch (err) {
    // history is optional — ignore network errors silently
  }
}

async function deleteSavedPath(path) {
  if (!confirm("Delete this saved path?\n" + path)) return;
  try {
    const res = await fetch(
      "/config/listen-folder?path=" + encodeURIComponent(path),
      { method: "DELETE" }
    );
    const data = await res.json();
    if (data.ok) {
      const input = $("#listen-folder");
      if (input && input.value.trim() === path) {
        input.value = data.active || "";
        syncDestPath();
      }
      renderSavedPaths();
    } else {
      log("Failed to delete path: " + (data.detail || ""), "error");
    }
  } catch (err) {
    log("Delete path error: " + err.message, "error");
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

$("#btn-browse-folder").addEventListener("click", openFolderBrowser);

$("#folder-select").addEventListener("click", async () => {
  if (!currentBrowsePath) {
    log("Navigate into a folder first, then select it.", "warn");
    return;
  }
  $("#listen-folder").value = currentBrowsePath;
  syncDestPath();
  $("#folder-modal").classList.add("hidden");
  if (await saveListenFolder(currentBrowsePath)) {
    log("Listen folder path saved.", "success");
  }
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
// Configuration editors (collapsed panels)
// =================================================================

function bindConfigModal(rowId, modalId, onOpen) {
  const row = document.getElementById(rowId);
  const modal = document.getElementById(modalId);
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
  loadQueries();
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

// --- Page-selection queries (queries/*.txt) ---
function getQueriesFromUI() {
  return {
    CLINS: $("#query-CLINS").value.trim(),
    FE: $("#query-FE").value.trim(),
    CE: $("#query-CE").value.trim(),
  };
}

async function loadQueries() {
  try {
    const res = await fetch("/queries");
    const data = await res.json();
    if (data.ok && data.queries) {
      for (const [type, text] of Object.entries(data.queries)) {
        const el = document.querySelector(`#query-${type}`);
        if (el) el.value = text;
      }
      log("Loaded page-selection queries from queries/*.txt", "info");
    }
  } catch (err) {
    log("Failed to load queries: " + err.message, "warn");
  }
}

$("#btn-save-queries").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  setButtonLoading(btn, true);
  try {
    const res = await fetch("/queries/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ queries: getQueriesFromUI() }),
    });
    const data = await res.json();
    if (data.ok) {
      log(`Saved queries to disk: ${data.saved.join(", ")}`, "success");
    } else {
      log("Failed to save queries: " + (data.detail || ""), "error");
    }
  } catch (err) {
    log("Save queries error: " + err.message, "error");
  }
  setButtonLoading(btn, false);
});

// --- Deletion Floor (page-deletion threshold, persisted on the backend) ---
const floorInput = $("#deletion-floor");
const floorStatus = $("#floor-status");

async function loadDeleteFloor() {
  try {
    const res = await fetch("/config/params");
    const data = await res.json();
    if (data.ok && data.delete_floor != null) {
      floorInput.value = data.delete_floor;
      floorStatus.textContent = `(default ${data.default_delete_floor})`;
    }
  } catch (err) {
    // config params are optional — ignore network errors silently
  }
}

$("#btn-save-floor").addEventListener("click", async () => {
  const raw = floorInput.value.trim();
  const v = parseFloat(raw);
  if (raw === "" || isNaN(v) || v < 0) {
    log("Deletion Floor must be a number ≥ 0.", "warn");
    return;
  }
  try {
    const res = await fetch("/config/params", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delete_floor: v }),
    });
    const data = await res.json();
    if (data.ok) {
      floorStatus.textContent = ` saved (${data.delete_floor})`;
      log(`Deletion Floor set to ${data.delete_floor}.`, "success");
    } else {
      log("Failed to save Deletion Floor: " + (data.detail || ""), "error");
    }
  } catch (err) {
    log("Save Deletion Floor error: " + err.message, "error");
  }
});

// =================================================================
// Activity feed (server-side events → frontend log)
// =================================================================

let lastEventId = 0;
let activityTimer = null;

async function pollActivity() {
  try {
    const res = await fetch(`/activity?since=${lastEventId}`);
    const data = await res.json();
    if (data.ok) {
      (data.events || []).forEach((e) => log(e.message, e.level || "info"));
      lastEventId = data.last_id || lastEventId;
    }
  } catch (err) {
    // transient poll errors are non-fatal
  }
}

function setActivityPolling(fast) {
  if (activityTimer) clearInterval(activityTimer);
  activityTimer = setInterval(pollActivity, fast ? 1500 : 5000);
}

// =================================================================
// One-click full pipeline (run-all + stage tracker)
// =================================================================

const STAGES = ["scan", "classify", "ingest", "package", "export"];
const runBtn = $("#btn-run-all");
let runPollTimer = null;

function resetStageTracker() {
  const track = $("#stage-track");
  track.querySelectorAll(".stage").forEach((el) =>
    el.classList.remove("running", "done")
  );
  track.querySelectorAll(".stage-sep").forEach((el) =>
    el.classList.remove("done")
  );
}

function updateStageTracker(stage) {
  const track = $("#stage-track");
  const stageEls = track.querySelectorAll(".stage");
  const sepEls = track.querySelectorAll(".stage-sep");
  const idx = STAGES.indexOf(stage);
  stageEls.forEach((el, i) => {
    el.classList.toggle("done", idx >= 0 && i < idx);
    el.classList.toggle("running", i === idx);
    if (sepEls[i]) sepEls[i].classList.toggle("done", idx >= 0 && i < idx);
  });
}

function markAllStagesDone() {
  const track = $("#stage-track");
  track.querySelectorAll(".stage").forEach((el) => {
    el.classList.remove("running");
    el.classList.add("done");
  });
  track.querySelectorAll(".stage-sep").forEach((el) => el.classList.add("done"));
}

async function pollRunStatus() {
  try {
    const res = await fetch("/run-all/status");
    const data = await res.json();
    if (!data.ok) return;

    const progressEl = $("#run-progress");
    if (data.running) {
      if (data.current_project) {
        const total = (data.projects || []).length;
        const done = (data.done || []).length;
        progressEl.textContent =
          `Processing ${data.current_project} (${done + 1}/${total}) — stage: ${data.current_stage || "…"}`;
        progressEl.classList.remove("hidden");
        updateStageTracker(data.current_stage);
      }
      return;
    }

    // Finished — stop polling, flush remaining activity, show summary.
    clearInterval(runPollTimer);
    runPollTimer = null;
    await pollActivity();
    setActivityPolling(false);

    const okCount = (data.results || []).length;
    const errCount = (data.errors || []).length;
    if (okCount + errCount > 0) {
      markAllStagesDone();
      const resultDiv = $("#run-result");
      const resultText = $("#run-result-text");
      if (errCount === 0) {
        resultText.textContent =
          `\u2713 Pipeline finished — ${okCount} PDF(s) saved to Dossier_condensed/`;
        resultText.className = "success";
      } else {
        resultText.textContent =
          `Pipeline finished with errors — ${okCount} succeeded, ${errCount} failed (see log)`;
        resultText.className = "log-warn";
      }
      resultDiv.classList.remove("hidden");
    }
    progressEl.classList.add("hidden");
    setButtonLoading(runBtn, false);
    runBtn.innerHTML = "&#9654;&nbsp; Run Full Pipeline";
    runBtn.disabled = false;
  } catch (err) {
    // transient poll errors are non-fatal
  }
}

runBtn.addEventListener("click", async () => {
  const lf = getListenFolder();
  if (!lf) {
    log("Set the Listen Folder first.", "warn");
    return;
  }
  // Persist the listen folder so the backend resolves the same base dir.
  await saveListenFolder(lf);

  runBtn.disabled = true;
  runBtn.innerHTML = '<span class="spinner"></span>&nbsp; Running…';
  $("#run-result").classList.add("hidden");
  $("#stage-track").classList.remove("hidden");
  resetStageTracker();

  try {
    const res = await fetch("/run-all", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      log("Could not start run: " + (data.detail || "unknown error"), "error");
      runBtn.disabled = false;
      runBtn.innerHTML = "&#9654;&nbsp; Run Full Pipeline";
      return;
    }
    log("Full pipeline started…");
    setActivityPolling(true);
    if (runPollTimer) clearInterval(runPollTimer);
    runPollTimer = setInterval(pollRunStatus, 1000);
  } catch (err) {
    log("Run error: " + err.message, "error");
    runBtn.disabled = false;
    runBtn.innerHTML = "&#9654;&nbsp; Run Full Pipeline";
  }
});

// =================================================================
// Auto-watch toggle
// =================================================================

const watchToggle = $("#watch-toggle");

function renderWatchState(enabled) {
  watchToggle.checked = enabled;
  $("#watch-state").textContent = enabled ? "ON" : "OFF";
  $("#watch-state").classList.toggle("on", enabled);
  $("#live-dot").classList.toggle("on", enabled);
  // Poll the activity feed faster while watching so auto-triggered
  // pipeline events show up promptly.
  setActivityPolling(enabled);
}

async function loadWatchState() {
  try {
    const res = await fetch("/watch");
    const data = await res.json();
    if (data.ok) renderWatchState(!!data.enabled);
  } catch (err) {
    // watch state is optional at load time
  }
}

watchToggle.addEventListener("change", async () => {
  const want = watchToggle.checked;
  if (want) {
    const lf = getListenFolder();
    if (!lf) {
      log("Set the Listen Folder first.", "warn");
      renderWatchState(false);
      return;
    }
    await saveListenFolder(lf);
  }
  try {
    const res = await fetch("/watch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: want }),
    });
    const data = await res.json();
    if (data.ok) {
      renderWatchState(!!data.enabled);
    } else {
      log("Watch toggle failed: " + (data.detail || ""), "error");
      renderWatchState(!want);
    }
  } catch (err) {
    log("Watch toggle error: " + err.message, "error");
    renderWatchState(!want);
  }
});

// =================================================================
// Startup
// =================================================================

loadListenFolder();
loadProfiles();
loadQueries();
loadDeleteFloor();
loadWatchState();
pollActivity();
setActivityPolling(false);

log("Dossier_Management ready.", "info");
log("1) Confirm the Listen Folder. 2) (Optional) tune configs. 3) Click 'Run Full Pipeline' — or switch on Auto-Watch and just drop project folders in.");
