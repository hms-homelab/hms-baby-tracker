/* Baby Tracker — Ingress SPA (vanilla JS, relative fetch URLs).
 *
 * All API calls use RELATIVE paths ("api/log", "api/event", ...) so they
 * resolve correctly under the Home Assistant Ingress path prefix.
 *
 * Layout (SDD-002): a pinned summary card, a tab bar (Get Ready / Baby /
 * Contractions / Health* / Growth* / Supplies — *Phase 2), then a pinned
 * journal below that logs every tab's events. The open tab comes from
 * /api/config (default_tab), overridable per-browser via localStorage.
 */
(function () {
  "use strict";

  // --- Event type -> emoji (matches app/ingest.py ICONS) ---
  var ICONS = {
    feed: "🍼", diaper: "🧷", sleep: "😴", bath: "🛁", medicine: "💊",
    tummy_time: "🤸", weight: "⚖️", pump: "🤱", note: "📝",
    contraction: "⏱️", supply: "🧴",
  };

  // --- Baby button definitions: [label, color, {event_type, event_subtype?}, emoji, light?] ---
  var GROUPS = {
    "grp-feed": [
      ["Breast", "#e8a0bf", { event_type: "feed", event_subtype: "breast" }, "🤱"],
      ["Bottle", "#a0c4e8", { event_type: "feed", event_subtype: "bottle" }, "🍼"],
      ["Solid", "#c4e8a0", { event_type: "feed", event_subtype: "solid" }, "🍎"],
    ],
    "grp-pump": [
      ["Pump L", "#d4a0e8", { event_type: "pump", event_subtype: "left" }, "🫙"],
      ["Pump R", "#d4a0e8", { event_type: "pump", event_subtype: "right" }, "🫙"],
    ],
    "grp-diaper": [
      ["Pee", "#f0e68c", { event_type: "diaper", event_subtype: "pee" }, "💧"],
      ["Poop", "#d2a679", { event_type: "diaper", event_subtype: "poop" }, "💩"],
      ["Both", "#e8c8a0", { event_type: "diaper", event_subtype: "both" }, "✅"],
      ["Change", "#c8b89a", { event_type: "diaper", event_subtype: "change" }, "🔄"],
    ],
    "grp-other": [
      ["Sleep Start", "#b0a0e8", { event_type: "sleep", event_subtype: "start" }, "😴", true],
      ["Sleep End", "#9a86d4", { event_type: "sleep", event_subtype: "end" }, "⏰", true],
      ["Bath", "#a0d8e8", { event_type: "bath" }, "🛁"],
      ["Medicine", "#e8a0a0", { event_type: "medicine" }, "💊"],
      ["Tummy", "#a0e8c4", { event_type: "tummy_time" }, "🤸"],
    ],
  };

  // Contractions tab: three big severity buttons. [label, subtype, color, emoji]
  var CONTRACTIONS = [
    ["Mild", "mild", "#7bc47f", "🟢"],
    ["Medium", "medium", "#e8a84e", "🟠"],
    ["Intense", "intense", "#e06b6b", "🔴"],
  ];

  var SUPPLY_CATEGORIES = ["formula", "diapers", "wipes", "cream", "other"];

  // Which event a supply auto-counts down on. [label, type, subtype?]
  var CONSUME_OPTIONS = [
    ["Bottle feed", "feed", "bottle"],
    ["Any feed", "feed", null],
    ["Diaper (any)", "diaper", null],
    ["Pump", "pump", null],
    ["Bath", "bath", null],
  ];

  // Flattened Baby type list for the manual-entry dropdown (label + payload).
  var EVENT_OPTIONS = [];
  Object.keys(GROUPS).forEach(function (gid) {
    GROUPS[gid].forEach(function (def) {
      EVENT_OPTIONS.push({ label: def[3] + " " + def[0], payload: def[2] });
    });
  });
  EVENT_OPTIONS.push({ label: "📝 Note", payload: { event_type: "note" } });

  var statusEl = document.getElementById("status");
  var pollTimer = null;
  var editingId = null;   // id of the journal row whose inline editor is open
  var currentTab = "baby";
  var lastEntries = [];   // cache of the latest journal entries (for the ctx readout)
  var PANELS = { get_ready: 1, baby: 1, contractions: 1, supplies: 1 };

  // --- Networking ---------------------------------------------------------
  function setStatus(msg, isErr) {
    statusEl.textContent = msg || "";
    statusEl.classList.toggle("err", !!isErr);
  }
  function apiGet(path) {
    return fetch(path, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function apiSend(method, path, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function apiPost(path, body) { return apiSend("POST", path, body || {}); }
  function apiPatch(path, body) { return apiSend("PATCH", path, body || {}); }
  function apiDelete(path) { return apiSend("DELETE", path); }

  // --- Date/time helpers (UTC ISO <-> <input type=datetime-local> local) --
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function toLocalInput(d) {
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }
  function nowLocalInput() { return toLocalInput(new Date()); }
  function isoToLocalInput(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? nowLocalInput() : toLocalInput(d);
  }
  function localInputToIso(val) {
    var d = new Date(val);
    return isNaN(d.getTime()) ? null : d.toISOString();
  }

  // --- Summary + journal rendering ---------------------------------------
  function fmtAgo(min) { return (min === null || min === undefined) ? "—" : min + "min ago"; }
  function fmtType(t) { return t ? " (" + t + ")" : ""; }

  function renderSummary(stats) {
    document.getElementById("sum-feed").textContent =
      "🍼 Last feed: " + fmtAgo(stats.last_feed_min) + fmtType(stats.last_feed_type) +
      " | Today: " + stats.feeds_today;
    document.getElementById("sum-diaper").textContent =
      "🧷 Last diaper: " + fmtAgo(stats.last_diaper_min) + fmtType(stats.last_diaper_type) +
      " | Today: " + stats.diapers_today;
    document.getElementById("sum-sleep").textContent =
      "😴 Sleep today: " + stats.sleep_total_today + " | " +
      (stats.is_sleeping ? "💤 Currently sleeping" : "🌙 Awake");
    document.getElementById("sum-other").textContent =
      "🫙 Pumps: " + stats.pumps_today + " | 🛁 Baths: " + stats.baths_today +
      " | 💊 Medicine: " + stats.medicines_today + " | 🤸 Tummy time: " + stats.tummy_times_today;
  }

  function journalLabel(e) {
    var type = e.event_type, sub = e.event_subtype;
    if (type === "diaper") {
      if (sub === "change") return "🔄 Diaper change";
      if (sub === "both") return "🧷 Pee+Poop";
      if (sub === "pee") return "💧 Pee";
      if (sub === "poop") return "💩 Poop";
      return "🧷 Diaper" + fmtType(sub);
    }
    if (type === "sleep") {
      if (sub === "start") return "😴 Sleep start";
      if (sub === "end") return "⏰ Sleep end";
      return "😴 Sleep" + fmtType(sub);
    }
    if (type === "pump") {
      if (sub === "left") return "🫙 Pump L";
      if (sub === "right") return "🫙 Pump R";
      return "🫙 Pump" + fmtType(sub);
    }
    if (type === "feed") {
      if (sub === "breast") return "🤱 Breast feed";
      if (sub === "bottle") return "🍼 Bottle feed";
      if (sub === "solid") return "🍎 Solid food";
      return "🍼 Feed" + fmtType(sub);
    }
    if (type === "contraction") return "⏱️ Contraction" + fmtType(sub);
    if (type === "supply") return "🧴 Supply" + fmtType(sub);
    if (type === "note") return "📝 Note";
    var icon = ICONS[type] || "📝";
    var display = type.replace(/_/g, " ");
    display = display.charAt(0).toUpperCase() + display.slice(1);
    return icon + " " + display + fmtType(sub);
  }

  function renderJournal(entries) {
    var ul = document.getElementById("journal");
    ul.textContent = "";
    if (!entries || !entries.length) {
      var empty = document.createElement("li");
      empty.className = "journal-empty";
      empty.textContent = "No events yet.";
      ul.appendChild(empty);
      return;
    }
    entries.forEach(function (e) {
      var li = document.createElement("li");
      var left = document.createElement("span");
      left.className = "j-label";
      left.textContent = journalLabel(e);
      if (e.note) {
        var n = document.createElement("span");
        n.className = "j-note";
        n.textContent = e.note;
        left.appendChild(n);
      }
      var time = document.createElement("span");
      time.className = "j-time";
      time.textContent = e.time || "";
      li.appendChild(left);
      li.appendChild(time);
      if (e.id !== null && e.id !== undefined) {
        li.classList.add("editable");
        li.addEventListener("click", function () {
          // Toggle: tapping an open row collapses it; tapping a closed one
          // opens it (closing any other open editor first).
          var wasOpen = li.classList.contains("open");
          collapseEditors();
          if (wasOpen) { refresh(); } else { openEditor(li, e); }
        });
      }
      ul.appendChild(li);
    });
  }

  // Close any open inline editor(s) and reset editing state (no re-render).
  function collapseEditors() {
    editingId = null;
    var boxes = document.querySelectorAll("#journal .j-edit");
    for (var i = 0; i < boxes.length; i++) boxes[i].remove();
    var opens = document.querySelectorAll("#journal li.open");
    for (var j = 0; j < opens.length; j++) opens[j].classList.remove("open");
  }

  // Inline editor: fix the time, edit the note, or delete the event.
  function openEditor(li, entry) {
    if (li.querySelector(".j-edit")) return;
    editingId = entry.id;
    li.classList.add("open");
    var box = document.createElement("div");
    box.className = "j-edit";
    box.addEventListener("click", function (ev) { ev.stopPropagation(); });

    var time = document.createElement("input");
    time.type = "datetime-local";
    time.value = entry.logged_at ? isoToLocalInput(entry.logged_at) : nowLocalInput();

    var note = document.createElement("input");
    note.type = "text";
    note.className = "j-note-edit";
    note.placeholder = "Note (optional)";
    note.value = entry.note || "";

    var save = document.createElement("button");
    save.className = "j-save";
    save.textContent = "Save";
    save.addEventListener("click", function () {
      var iso = localInputToIso(time.value);
      if (!iso) { setStatus("Invalid date/time", true); return; }
      apiPatch("api/event/" + entry.id, { logged_at: iso, note: note.value.trim() })
        .then(function () { editingId = null; setStatus("Updated ✓"); return refresh(); })
        .catch(function (err) { setStatus("Failed (" + err.message + ")", true); });
    });

    var del = document.createElement("button");
    del.className = "j-del";
    del.textContent = "Delete";
    del.addEventListener("click", function () {
      if (!window.confirm("Delete this event?")) return;
      apiDelete("api/event/" + entry.id)
        .then(function () { editingId = null; setStatus("Deleted ✓"); return refresh(); })
        .catch(function (err) { setStatus("Failed (" + err.message + ")", true); });
    });

    var cancel = document.createElement("button");
    cancel.className = "j-cancel";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", function () {
      editingId = null; li.classList.remove("open"); box.remove(); refresh();
    });

    box.appendChild(time); box.appendChild(note);
    box.appendChild(save); box.appendChild(del); box.appendChild(cancel);
    li.appendChild(box);
  }

  // --- Contraction readout (computed from journal entries) ----------------
  function renderContractionReadout() {
    var el = document.getElementById("ctx-readout");
    if (!el) return;
    var now = Date.now();
    var win = 2 * 3600 * 1000;
    var recent = (lastEntries || []).filter(function (e) {
      if (e.event_type !== "contraction") return false;
      var t = new Date(e.logged_at).getTime();
      return !isNaN(t) && now - t <= win;
    });
    if (!recent.length) { el.textContent = "No contractions in the last 2h."; return; }
    // entries are most-recent-first
    var lastMin = Math.round((now - new Date(recent[0].logged_at).getTime()) / 60000);
    var gap = "";
    if (recent.length >= 2) {
      var diffs = [];
      for (var i = 0; i < recent.length - 1; i++) {
        diffs.push(new Date(recent[i].logged_at).getTime() - new Date(recent[i + 1].logged_at).getTime());
      }
      var avg = diffs.reduce(function (a, b) { return a + b; }, 0) / diffs.length;
      gap = " · avg gap " + Math.round(avg / 60000) + " min";
    }
    el.textContent = recent.length + " in last 2h · last " + lastMin + " min ago" + gap;
  }

  // --- Data refresh (log + summary + journal) -----------------------------
  function refresh() {
    return apiGet("api/log")
      .then(function (data) {
        lastEntries = data.entries || [];
        renderSummary(data.stats || {});
        if (editingId === null) renderJournal(lastEntries);
        renderContractionReadout();
        if (currentTab === "supplies") loadSupplies();
        setStatus("");
      })
      .catch(function (err) { setStatus("Offline — retrying… (" + err.message + ")", true); });
  }

  // --- Baby actions -------------------------------------------------------
  function sendEvent(payload, tileEl) {
    if (tileEl) {
      tileEl.classList.add("pressed");
      setTimeout(function () { tileEl.classList.remove("pressed"); }, 150);
    }
    apiPost("api/event", payload)
      .then(function () { setStatus("Logged ✓"); return refresh(); })
      .catch(function (err) { setStatus("Failed to log (" + err.message + ")", true); });
  }
  function saveNote(inputEl, special) {
    var msg = (inputEl.value || "").trim();
    if (!msg) return;
    apiPost("api/note", { message: msg, special: !!special })
      .then(function () { inputEl.value = ""; setStatus("Note saved ✓"); return refresh(); })
      .catch(function (err) { setStatus("Failed to save note (" + err.message + ")", true); });
  }
  function addManual() {
    var sel = document.getElementById("manual-type");
    var opt = EVENT_OPTIONS[sel.selectedIndex];
    if (!opt) return;
    var timeVal = document.getElementById("manual-time").value;
    var noteVal = (document.getElementById("manual-note").value || "").trim();
    var iso = timeVal ? localInputToIso(timeVal) : null;
    if (timeVal && !iso) { setStatus("Invalid date/time", true); return; }
    var payload = { event_type: opt.payload.event_type };
    if (opt.payload.event_subtype) payload.event_subtype = opt.payload.event_subtype;
    if (noteVal) payload.note = noteVal;
    if (iso) payload.logged_at = iso;
    apiPost("api/event", payload)
      .then(function () {
        document.getElementById("manual-note").value = "";
        document.getElementById("manual-time").value = nowLocalInput();
        setStatus("Added ✓"); return refresh();
      })
      .catch(function (err) { setStatus("Failed to add (" + err.message + ")", true); });
  }
  function resetAll() {
    if (!window.confirm("Reset ALL events? This cannot be undone.")) return;
    apiPost("api/reset", {})
      .then(function () { setStatus("Reset done ✓"); return refresh(); })
      .catch(function (err) { setStatus("Failed to reset (" + err.message + ")", true); });
  }

  // --- Contractions -------------------------------------------------------
  function addBackfillContraction() {
    var sel = document.getElementById("ctx-backfill-intensity");
    var sub = sel.value;
    var timeVal = document.getElementById("ctx-backfill-time").value;
    var iso = timeVal ? localInputToIso(timeVal) : null;
    if (!iso) { setStatus("Pick a date/time", true); return; }
    apiPost("api/event", { event_type: "contraction", event_subtype: sub, logged_at: iso })
      .then(function () {
        document.getElementById("ctx-backfill-time").value = nowLocalInput();
        setStatus("Contraction added ✓"); return refresh();
      })
      .catch(function (err) { setStatus("Failed (" + err.message + ")", true); });
  }

  // --- Supplies -----------------------------------------------------------
  function fmtNum(n) {
    if (n === null || n === undefined) return "0";
    return (n === Math.round(n)) ? String(n) : String(n);
  }
  function loadSupplies() {
    return apiGet("api/supplies").then(function (d) { renderSupplies(d.supplies || []); })
      .catch(function () {});
  }
  function renderSupplies(list) {
    var ul = document.getElementById("supply-list");
    if (!ul) return;
    ul.textContent = "";
    if (!list.length) {
      var empty = document.createElement("li");
      empty.className = "journal-empty";
      empty.textContent = "No supplies yet — add one below.";
      ul.appendChild(empty);
      return;
    }
    list.forEach(function (s) {
      var li = document.createElement("li");
      li.className = "supply-row";

      var head = document.createElement("div");
      head.className = "supply-head";
      var title = document.createElement("span");
      title.className = "supply-name";
      var meta = [s.brand, s.type].filter(Boolean).join(" · ");
      title.textContent = "🧴 " + s.name + (meta ? " (" + meta + ")" : "");
      head.appendChild(title);

      var qty = document.createElement("span");
      qty.className = "supply-qty";
      qty.textContent = fmtNum(s.quantity) + (s.unit ? " " + s.unit : "");
      head.appendChild(qty);
      li.appendChild(head);

      var badges = document.createElement("div");
      badges.className = "supply-badges";
      if (s.is_low) { var b1 = document.createElement("span"); b1.className = "badge low"; b1.textContent = "Low"; badges.appendChild(b1); }
      if (s.is_due) { var b2 = document.createElement("span"); b2.className = "badge due"; b2.textContent = "Refill due"; badges.appendChild(b2); }
      if (s.low_threshold != null) { var b3 = document.createElement("span"); b3.className = "badge muted"; b3.textContent = "≤ " + fmtNum(s.low_threshold); badges.appendChild(b3); }
      if (s.refill_days != null) { var b4 = document.createElement("span"); b4.className = "badge muted"; b4.textContent = "every " + s.refill_days + "d"; badges.appendChild(b4); }
      if (badges.children.length) li.appendChild(badges);

      var actions = document.createElement("div");
      actions.className = "supply-actions";
      actions.appendChild(supplyBtn("−", "s-minus", function () { adjustSupply(s.id, -1); }));
      actions.appendChild(supplyBtn("+", "s-plus", function () { adjustSupply(s.id, 1); }));
      actions.appendChild(supplyBtn("Refill", "s-refill", function () { refillSupply(s); }));
      actions.appendChild(supplyBtn("Delete", "s-del", function () {
        if (window.confirm("Delete " + s.name + "?")) {
          apiDelete("api/supplies/" + s.id).then(loadSupplies).catch(function () {});
        }
      }));
      li.appendChild(actions);
      ul.appendChild(li);
    });
  }
  function supplyBtn(text, cls, fn) {
    var b = document.createElement("button");
    b.className = "supply-btn " + cls;
    b.textContent = text;
    b.addEventListener("click", fn);
    return b;
  }
  function adjustSupply(id, delta) {
    apiPost("api/supplies/" + id + "/adjust", { delta: delta })
      .then(loadSupplies).catch(function (err) { setStatus("Failed (" + err.message + ")", true); });
  }
  function refillSupply(s) {
    var ans = window.prompt("Refill " + s.name + " to how many " + (s.unit || "units") + "?",
      s.low_threshold != null ? "" : fmtNum(s.quantity));
    if (ans === null) return;
    var body = {};
    var q = parseFloat(ans);
    if (!isNaN(q)) body.quantity = q;
    apiPost("api/supplies/" + s.id + "/refill", body)
      .then(function () { setStatus("Refilled ✓"); return refresh().then(loadSupplies); })
      .catch(function (err) { setStatus("Failed (" + err.message + ")", true); });
  }
  function addSupply() {
    var name = (document.getElementById("sup-name").value || "").trim();
    if (!name) { setStatus("Give the supply a name", true); return; }
    var payload = {
      category: document.getElementById("sup-category").value,
      name: name,
      brand: (document.getElementById("sup-brand").value || "").trim() || null,
      type: (document.getElementById("sup-type").value || "").trim() || null,
      quantity: parseFloat(document.getElementById("sup-qty").value) || 0,
      unit: (document.getElementById("sup-unit").value || "").trim() || null,
      low_threshold: numOrNull(document.getElementById("sup-low").value),
      refill_days: intOrNull(document.getElementById("sup-days").value),
    };
    if (document.getElementById("sup-autodec").checked) {
      var c = CONSUME_OPTIONS[document.getElementById("sup-consume-type").selectedIndex];
      if (c) {
        payload.consume_event_type = c[1];
        if (c[2]) payload.consume_event_subtype = c[2];
        payload.consume_amount = parseFloat(document.getElementById("sup-consume-amt").value) || 1;
      }
    }
    apiPost("api/supplies", payload)
      .then(function () {
        ["sup-name", "sup-brand", "sup-type", "sup-qty", "sup-unit", "sup-low", "sup-days"]
          .forEach(function (id) { document.getElementById(id).value = ""; });
        setStatus("Supply added ✓"); return loadSupplies();
      })
      .catch(function (err) { setStatus("Failed to add supply (" + err.message + ")", true); });
  }
  function numOrNull(v) { var n = parseFloat(v); return isNaN(n) ? null : n; }
  function intOrNull(v) { var n = parseInt(v, 10); return isNaN(n) ? null : n; }

  // --- Get Ready checklist ------------------------------------------------
  function loadChecklist() {
    return apiGet("api/checklist").then(function (d) { renderChecklist(d.items || []); })
      .catch(function () {});
  }
  function renderChecklist(items) {
    var ul = document.getElementById("checklist");
    if (!ul) return;
    ul.textContent = "";
    var done = 0;
    items.forEach(function (it) {
      if (it.done) done++;
      var li = document.createElement("li");
      li.className = "check-row" + (it.done ? " done" : "");
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!it.done;
      cb.addEventListener("change", function () {
        apiPatch("api/checklist/" + it.id, { done: cb.checked }).then(loadChecklist).catch(function () {});
      });
      var label = document.createElement("span");
      label.className = "check-label";
      label.textContent = it.label;
      var del = document.createElement("button");
      del.className = "check-del";
      del.textContent = "×";
      del.setAttribute("aria-label", "Delete item");
      del.addEventListener("click", function () {
        apiDelete("api/checklist/" + it.id).then(loadChecklist).catch(function () {});
      });
      li.appendChild(cb); li.appendChild(label); li.appendChild(del);
      ul.appendChild(li);
    });
    var prog = document.getElementById("checklist-progress");
    if (prog) prog.textContent = items.length ? "— " + done + " / " + items.length + " ready" : "";
  }
  function addChecklistItem() {
    var inp = document.getElementById("checklist-input");
    var label = (inp.value || "").trim();
    if (!label) return;
    apiPost("api/checklist", { label: label })
      .then(function () { inp.value = ""; return loadChecklist(); }).catch(function () {});
  }

  // --- Tabs ---------------------------------------------------------------
  function activateTab(name) {
    if (!PANELS[name]) name = "baby";
    currentTab = name;
    var tabs = document.querySelectorAll(".tab");
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].classList.toggle("active", tabs[i].getAttribute("data-tab") === name);
    }
    var panels = document.querySelectorAll(".panel");
    for (var j = 0; j < panels.length; j++) {
      panels[j].hidden = panels[j].getAttribute("data-panel") !== name;
    }
    try { localStorage.setItem("bt_tab", name); } catch (e) {}
    if (name === "supplies") loadSupplies();
    if (name === "get_ready") loadChecklist();
  }

  function wireTabs() {
    var tabs = document.querySelectorAll(".tab");
    for (var i = 0; i < tabs.length; i++) {
      (function (t) {
        if (t.disabled) return;
        t.addEventListener("click", function () { activateTab(t.getAttribute("data-tab")); });
      })(tabs[i]);
    }
  }

  function pickInitialTab(defaultTab) {
    var stored = null;
    try { stored = localStorage.getItem("bt_tab"); } catch (e) {}
    if (stored && PANELS[stored]) return stored;
    if (defaultTab && PANELS[defaultTab]) return defaultTab;
    return "baby";
  }

  // --- Build UI -----------------------------------------------------------
  function buildGrids() {
    Object.keys(GROUPS).forEach(function (gid) {
      var container = document.getElementById(gid);
      GROUPS[gid].forEach(function (def) {
        var btn = makeTile(def[0], def[1], def[3], def[4]);
        btn.addEventListener("click", function () { sendEvent(def[2], btn); });
        container.appendChild(btn);
      });
    });
    // Contraction severity tiles (bigger, in their own grid).
    var cg = document.getElementById("grp-contraction");
    CONTRACTIONS.forEach(function (def) {
      var btn = makeTile(def[0], def[2], def[3], false);
      btn.classList.add("ctx-tile");
      btn.addEventListener("click", function () {
        sendEvent({ event_type: "contraction", event_subtype: def[1] }, btn);
      });
      cg.appendChild(btn);
    });
  }
  function makeTile(label, color, emoji, light) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tile" + (light ? " light" : "");
    btn.style.setProperty("--accent", color);
    var ico = document.createElement("span"); ico.className = "ico"; ico.textContent = emoji;
    var lbl = document.createElement("span"); lbl.className = "lbl"; lbl.textContent = label;
    btn.appendChild(ico); btn.appendChild(lbl);
    return btn;
  }
  function buildManual() {
    var sel = document.getElementById("manual-type");
    EVENT_OPTIONS.forEach(function (o) {
      var opt = document.createElement("option"); opt.textContent = o.label; sel.appendChild(opt);
    });
    document.getElementById("manual-time").value = nowLocalInput();
    document.getElementById("manual-add").addEventListener("click", addManual);
  }
  function buildContractionsPanel() {
    var sel = document.getElementById("ctx-backfill-intensity");
    CONTRACTIONS.forEach(function (def) {
      var opt = document.createElement("option");
      opt.value = def[1]; opt.textContent = def[3] + " " + def[0];
      sel.appendChild(opt);
    });
    document.getElementById("ctx-backfill-time").value = nowLocalInput();
    document.getElementById("ctx-backfill-add").addEventListener("click", addBackfillContraction);
    document.getElementById("ctx-note-save").addEventListener("click", function () {
      var inp = document.getElementById("ctx-note");
      var msg = (inp.value || "").trim();
      if (!msg) return;
      apiPost("api/note", { message: msg })
        .then(function () { inp.value = ""; setStatus("Note saved ✓"); return refresh(); })
        .catch(function (err) { setStatus("Failed (" + err.message + ")", true); });
    });
  }
  function buildSuppliesPanel() {
    var cat = document.getElementById("sup-category");
    SUPPLY_CATEGORIES.forEach(function (c) {
      var opt = document.createElement("option"); opt.value = c;
      opt.textContent = c.charAt(0).toUpperCase() + c.slice(1); cat.appendChild(opt);
    });
    var cons = document.getElementById("sup-consume-type");
    CONSUME_OPTIONS.forEach(function (c) {
      var opt = document.createElement("option"); opt.textContent = c[0]; cons.appendChild(opt);
    });
    document.getElementById("sup-autodec").addEventListener("change", function (e) {
      document.getElementById("sup-consume-row").hidden = !e.target.checked;
    });
    document.getElementById("sup-add").addEventListener("click", addSupply);
  }
  function buildChecklistPanel() {
    document.getElementById("checklist-add").addEventListener("click", addChecklistItem);
    document.getElementById("checklist-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") addChecklistItem();
    });
    document.getElementById("checklist-reset").addEventListener("click", function () {
      apiPost("api/checklist/reset", {}).then(loadChecklist).catch(function () {});
    });
  }

  function init() {
    buildGrids();
    buildManual();
    buildContractionsPanel();
    buildSuppliesPanel();
    buildChecklistPanel();
    wireTabs();

    document.getElementById("note-save").addEventListener("click", function () {
      saveNote(document.getElementById("note-input"), false);
    });
    document.getElementById("special-save").addEventListener("click", function () {
      saveNote(document.getElementById("special-input"), true);
    });
    document.getElementById("note-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") saveNote(e.target, false);
    });
    document.getElementById("special-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") saveNote(e.target, true);
    });
    document.getElementById("reset").addEventListener("click", resetAll);

    apiGet("api/config")
      .then(function (c) { activateTab(pickInitialTab(c && c.default_tab)); })
      .catch(function () { activateTab(pickInitialTab("baby")); });

    refresh();
    pollTimer = setInterval(refresh, 10000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
