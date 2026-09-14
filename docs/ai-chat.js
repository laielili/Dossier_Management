/* ============================================================
   Mock AI synthesis client — /docs demo build

   Static replica of the downstream "L'Oréal GPT" step, which in the real
   product lives outside this repo. The scripted conversation follows
   prompt/system.md: the assistant states the project in one line, confirms
   subject + formula numbers, output context, and outline + narrative
   spine — then emits the <deck> XML and hands it to the converter.

   The real client gates the workflow behind @extract / @summarize; those
   exist to work around the platform's routing limits, so this demo runs the
   conversation without them.

   Simulated data only — see demo-data.js.
   ============================================================ */

(function () {
  "use strict";

  const DEMO = window.DEMO;
  const $ = (sel) => document.querySelector(sel);

  const stream = $("#chat-stream");
  const chipsBox = $("#chat-chips");
  const composer = $("#chat-composer");
  const input = $("#chat-input");
  const sendBtn = $("#chat-send");
  const statusEl = $("#chat-status");
  const HANDOFF_SECONDS = 3;
  let handoffTimer = null;
  let lastRole = null;

  // ---------------------------------------------------------------
  // Utilities
  // ---------------------------------------------------------------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function log(message, level) {
    const area = $("#log-area");
    const line = document.createElement("div");
    line.className = "log-" + (level || "info");
    line.textContent = "[" + new Date().toLocaleTimeString() + "] " + message;
    area.appendChild(line);
    area.scrollTop = area.scrollHeight;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  // Minimal inline markup: `code`, **bold**, *italic*, "• " bullet lines.
  function richText(text) {
    const lines = String(text).split("\n");
    let html = "";
    let inList = false;
    lines.forEach((raw) => {
      let line = escapeHtml(raw)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
      const bullet = line.match(/^\s*\u2022\s+(.*)$/);
      if (bullet) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += "<li>" + bullet[1] + "</li>";
        return;
      }
      if (inList) { html += "</ul>"; inList = false; }
      if (line.trim()) html += "<p>" + line + "</p>";
    });
    if (inList) html += "</ul>";
    return html;
  }

  function scrollDown() {
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  }

  // ---------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------
  function bubble(role, html, sender) {
    const msg = document.createElement("div");
    msg.className = "chat-msg " + role;
    const avatar = document.createElement("div");
    avatar.className = "chat-avatar";
    avatar.textContent = role === "ai" ? "AI" : "YOU";
    const body = document.createElement("div");
    body.className = "chat-body";
    // Only label the first bubble of a consecutive run by the same speaker.
    if (role !== lastRole) {
      const who = document.createElement("div");
      who.className = "chat-sender";
      who.textContent = sender || (role === "ai" ? "L'Oréal GPT" : "You");
      body.appendChild(who);
    }
    lastRole = role;
    const bub = document.createElement("div");
    bub.className = "chat-bubble";
    bub.innerHTML = html;
    body.appendChild(bub);
    msg.appendChild(avatar);
    msg.appendChild(body);
    stream.appendChild(msg);
    scrollDown();
    return bub;
  }

  function showTyping() {
    const msg = document.createElement("div");
    msg.className = "chat-msg ai";
    msg.id = "typing-row";
    msg.innerHTML =
      '<div class="chat-avatar">AI</div>' +
      '<div class="chat-body"><div class="chat-bubble">' +
      '<span class="chat-typing"><span></span><span></span><span></span></span>' +
      "</div></div>";
    stream.appendChild(msg);
    scrollDown();
  }

  function hideTyping() {
    const el = document.getElementById("typing-row");
    if (el) el.remove();
  }

  // One assistant turn: typing indicator, then the lines, line by line.
  async function aiTurn(title, lines, thinkMs) {
    statusEl.textContent = "Thinking\u2026";
    showTyping();
    await sleep(thinkMs == null ? 620 : thinkMs);
    hideTyping();
    let first = true;
    for (const text of lines) {
      bubble("ai", (first && title ? '<span class="chat-step-title">' + escapeHtml(title) + "</span>" : "") + richText(text));
      first = false;
      if (lines.length > 1) await sleep(280);
    }
  }

  function renderChips(chips, onPick) {
    chipsBox.innerHTML = "";
    chips.forEach((chip) => {
      const btn = document.createElement("button");
      btn.className = "btn btn-sm btn-outline";
      btn.type = "button";
      btn.textContent = chip.label;
      btn.addEventListener("click", () => {
        chipsBox.classList.add("hidden");
        chipsBox.innerHTML = "";
        onPick(chip);
      });
      chipsBox.appendChild(btn);
    });
    chipsBox.classList.remove("hidden");
    statusEl.textContent = "Waiting for your confirmation";
    scrollDown();
  }

  function setComposerEnabled(on) {
    composer.style.display = on ? "" : "none";
    input.disabled = !on;
    sendBtn.disabled = !on;
  }

  // ---------------------------------------------------------------
  // Conversation
  // ---------------------------------------------------------------
  async function start() {
    setComposerEnabled(false);
    $("#src-count").textContent = DEMO.sources.filter((s) => s.keep).length;
    DEMO.sources.filter((s) => s.keep).forEach((f) => {
      $("#src-strip").insertAdjacentHTML(
        "beforeend",
        '<span class="chip">' + escapeHtml(f.name) + "</span>"
      );
    });

    await aiTurn(null, DEMO.chat.brief, 700);
    await runStep(0);
  }

  async function runStep(i) {
    const step = DEMO.chat.steps[i];
    if (!step) { await finish(); return; }

    await aiTurn(step.title, step.ai, 680);

    const advance = async (chip) => {
      await aiTurn(null, [chip.reply], 480);
      await sleep(220);
      await runStep(i + 1);
    };

    renderChips(step.chips, advance);
    setComposerEnabled(true);
    input.focus();

    // Free-text path: any typed reply is accepted, then we advance.
    const onSend = async () => {
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      bubble("user", richText(text));
      chipsBox.classList.add("hidden");
      chipsBox.innerHTML = "";
      setComposerEnabled(false);
      await aiTurn(null, [
        "Noted \u2014 I'll apply that to the " +
          (i === 0 ? "formula index" : i === 1 ? "output context" : "band") +
          ". Moving to the next check.",
      ], 480);
      await sleep(200);
      await runStep(i + 1);
    };

    sendBtn.onclick = onSend;
    input.onkeydown = (e) => { if (e.key === "Enter") onSend(); };
  }

  // ---------------------------------------------------------------
  // Final turn: the deck XML + hand-off
  // ---------------------------------------------------------------
  async function finish() {
    setComposerEnabled(false);
    statusEl.textContent = "Writing the deck\u2026";
    log("All three confirmations approved.", "success");
    await aiTurn(null, ["Writing the `<deck>` XML from the confirmed scope \u2014 17 components across 3 pages."], 900);

    const bub = bubble("ai", richText(DEMO.chat.closing));
    const block = document.createElement("div");
    block.className = "xml-block";
    block.textContent = DEMO.deckXml;
    bub.appendChild(block);

    const actions = document.createElement("div");
    actions.className = "chat-chips";
    const copyBtn = document.createElement("button");
    copyBtn.className = "btn btn-sm btn-outline";
    copyBtn.type = "button";
    copyBtn.textContent = "Copy XML";
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(DEMO.deckXml);
        copyBtn.textContent = "Copied";
      } catch (e) {
        copyBtn.textContent = "Select & copy manually";
      }
    });
    const restartBtn = document.createElement("button");
    restartBtn.className = "btn btn-sm btn-outline";
    restartBtn.type = "button";
    restartBtn.textContent = "Restart session";
    restartBtn.addEventListener("click", () => {
      try { localStorage.removeItem("demo_deck_xml"); } catch (e) { /* ignore */ }
      window.location.reload();
    });
    actions.appendChild(copyBtn);
    actions.appendChild(restartBtn);
    bub.appendChild(actions);

    // Scope table
    const table = document.createElement("table");
    table.className = "chat-kv";
    DEMO.chat.deckSummary.forEach(([k, v]) => {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      th.textContent = k;
      const td = document.createElement("td");
      td.textContent = v;
      tr.appendChild(th);
      tr.appendChild(td);
      table.appendChild(tr);
    });
    bub.appendChild(table);

    log("Deck XML emitted \u2014 17 components.", "success");
    handoff(bub);
  }

  function handoff(bub) {
    let stored = false;
    try {
      localStorage.setItem("demo_deck_xml", DEMO.deckXml);
      localStorage.setItem("demo_deck_source", "ai-chat");
      stored = true;
    } catch (e) {
      stored = false;
    }

    const box = document.createElement("div");
    box.className = "handoff";
    const text = document.createElement("span");
    text.className = "handoff-text";
    text.innerHTML = stored
      ? "Deck XML placed in the <strong>SVG &rarr; PPTX</strong> input box. Opening the converter in <span class=\"handoff-count\">" + HANDOFF_SECONDS + "</span>s\u2026"
      : "Opening the <strong>SVG &rarr; PPTX</strong> converter in <span class=\"handoff-count\">" + HANDOFF_SECONDS + "</span>s\u2026";
    const openBtn = document.createElement("button");
    openBtn.className = "btn btn-sm btn-primary";
    openBtn.type = "button";
    openBtn.textContent = "Open now";
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn btn-sm btn-outline";
    cancelBtn.type = "button";
    cancelBtn.textContent = "Cancel";
    box.appendChild(text);
    box.appendChild(openBtn);
    box.appendChild(cancelBtn);
    bub.appendChild(box);
    scrollDown();

    let left = HANDOFF_SECONDS;
    const go = () => { window.location.href = "svg2ppt.html"; };
    openBtn.addEventListener("click", go);
    cancelBtn.addEventListener("click", () => {
      if (handoffTimer) clearInterval(handoffTimer);
      handoffTimer = null;
      box.classList.remove("handoff");
      box.className = "meta-line";
      box.textContent = "Auto-open cancelled \u2014 the XML is ready in the SVG \u2192 PPTX input box whenever you are.";
    });

    statusEl.textContent = "Handing off to SVG \u2192 PPTX";
    log("Sending the deck to the SVG \u2192 PPTX converter.");
    handoffTimer = setInterval(() => {
      left -= 1;
      const c = box.querySelector(".handoff-count");
      if (c) c.textContent = String(Math.max(left, 0));
      if (left <= 0) {
        clearInterval(handoffTimer);
        handoffTimer = null;
        go();
      }
    }, 1000);
  }

  // ---------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------
  log("Dossier Search ready.");
  log("Source folder retrieved/" + DEMO.project.name + "/AI_feed/" + DEMO.project.name + "/ \u2014 7 document(s).");
  log("Connected to the downstream AI client (demo).");
  start();
})();
