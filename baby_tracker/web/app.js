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
      ["Change", "#c8b89a", { event_type: "diaper", event_subtype: "change" }, "🩲"],
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

  // Growth tab metrics. [event_type, label, [metric_unit, imperial_unit]]
  var GROWTH_METRICS = [
    ["weight", "⚖️ Weight", ["kg", "lb"]],
    ["length", "📏 Length", ["cm", "in"]],
    ["head_circumference", "🧢 Head", ["cm", "in"]],
  ];

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
  var feverThresholdC = 38.0;  // from /api/config
  var imperial = true;          // from /api/config measurement_system
  var noteSpecial = false;      // shared note bar ⭐ toggle state
  var generatingSummary = false; // AI summary in flight (don't clobber the button)
  var addonSlug = "";           // this add-on's Supervisor slug (config deep link)
  var appTz = "";               // add-on's IANA timezone (anchors the datetime pickers)
  var PANELS = { get_ready: 1, baby: 1, contractions: 1, health: 1, growth: 1, supplies: 1 };

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

  // --- Date/time helpers (UTC ISO <-> <input type=datetime-local>) ---------
  // The datetime-local widget is a naive wall clock. We anchor it to the
  // ADD-ON's configured timezone (appTz), NOT the viewing device's, so the
  // picker always matches the server-formatted journal times (issue #2). Falls
  // back to the browser's local time when appTz is unknown/unsupported.
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function browserInput(d) {
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }
  // Wall-clock "YYYY-MM-DDTHH:MM" of instant `d` in the given IANA tz.
  function tzInput(d, tz) {
    try {
      var f = new Intl.DateTimeFormat("en-CA", {
        timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false,
      });
      var p = {};
      f.formatToParts(d).forEach(function (x) { p[x.type] = x.value; });
      var h = (p.hour === "24") ? "00" : p.hour;   // some engines emit 24 at midnight
      return p.year + "-" + p.month + "-" + p.day + "T" + h + ":" + p.minute;
    } catch (e) { return browserInput(d); }
  }
  // Minutes tz is ahead of UTC at `date` (DST-correct).
  function tzOffsetMin(date, tz) {
    var f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hour12: false, year: "numeric", month: "2-digit",
      day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
    var p = {};
    f.formatToParts(date).forEach(function (x) { p[x.type] = x.value; });
    var h = (p.hour === "24") ? "00" : p.hour;
    var asUTC = Date.UTC(+p.year, +p.month - 1, +p.day, +h, +p.minute, +p.second);
    return Math.round((asUTC - date.getTime()) / 60000);
  }
  function toLocalInput(d) { return appTz ? tzInput(d, appTz) : browserInput(d); }
  function nowLocalInput() { return toLocalInput(new Date()); }
  function isoToLocalInput(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? nowLocalInput() : toLocalInput(d);
  }
  function localInputToIso(val) {
    if (!val) return null;
    if (!appTz) { var d = new Date(val); return isNaN(d.getTime()) ? null : d.toISOString(); }
    var m = val.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    if (!m) return null;
    // Treat the picker value as wall-clock in appTz, then convert to UTC.
    var asUtc = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    try {
      var off = tzOffsetMin(new Date(asUtc), appTz);
      return new Date(asUtc - off * 60000).toISOString();
    } catch (e) {
      var b = new Date(val); return isNaN(b.getTime()) ? null : b.toISOString();
    }
  }

  // --- Summary + journal rendering ---------------------------------------
  function fmtAgo(min) { return (min === null || min === undefined) ? "—" : min + "min ago"; }
  function fmtType(t) { return t ? " (" + t + ")" : ""; }

  function renderSummary(data) {
    var stats = (data && data.stats) || {};
    var extras = (data && data.summary_extras) || {};
    var entries = (data && data.entries) || lastEntries || [];
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

    // Contractions / Get Ready / Health / Growth roll-up
    var now = Date.now(), win = 2 * 3600 * 1000;
    var ctxToday = 0, ctx2h = 0, lastTemp = null, lastWeight = null;
    entries.forEach(function (e) {
      if (e.event_type === "contraction") {
        if (sameLocalDay(e.logged_at)) ctxToday++;
        if (now - new Date(e.logged_at).getTime() <= win) ctx2h++;
      }
      if (!lastTemp && e.event_type === "temperature" && e.value != null) lastTemp = e;
      if (!lastWeight && e.event_type === "weight" && e.value != null) lastWeight = e;
    });
    var cl = extras.checklist || { done: 0, total: 0 };
    document.getElementById("sum-track").textContent =
      "⏱️ Contractions: " + ctxToday + (ctx2h ? " (" + ctx2h + " in 2h)" : "")
      + " | 🎒 Ready: " + cl.done + "/" + cl.total;

    var fever = false, tempStr = "—";
    if (lastTemp) {
      tempStr = fmtNum(lastTemp.value) + (lastTemp.value_unit ? " " + lastTemp.value_unit : "");
      fever = isFever(lastTemp.value, lastTemp.value_unit || "");
    }
    var wStr = lastWeight ? fmtMeasure(lastWeight.value, lastWeight.value_unit) : "—";
    document.getElementById("sum-vitals").textContent =
      "🌡️ Temp: " + tempStr + (fever ? " ⚠" : "") + " | 📈 Weight: " + wStr;

    // Notifications / alerts strip (fever + supply low/refill-due)
    var sup = extras.supplies || { low: [], due: [] };
    var alerts = [];
    if (fever) alerts.push("⚠ Fever");
    if (sup.low && sup.low.length) alerts.push("🧴 Low: " + sup.low.join(", "));
    if (sup.due && sup.due.length) alerts.push("🔔 Refill: " + sup.due.join(", "));
    var aEl = document.getElementById("sum-alert");
    if (alerts.length) { aEl.textContent = alerts.join("   ·   "); aEl.hidden = false; }
    else { aEl.textContent = ""; aEl.hidden = true; }
  }

  function fmtValue(v, u) {
    if (v === null || v === undefined) return "";
    return " " + String(v) + (u ? " " + u : "");
  }
  // Weight stored as decimal lb -> "X lb Y oz"; everything else -> "value unit".
  function fmtWeightLb(v) {
    if (v === null || v === undefined) return "";
    var lb = Math.floor(v), oz = Math.round((v - lb) * 16);
    if (oz === 16) { lb += 1; oz = 0; }
    return lb + " lb " + oz + " oz";
  }
  function fmtMeasure(v, u) {
    if (v === null || v === undefined) return "";
    return (u === "lb") ? fmtWeightLb(v) : (String(v) + (u ? " " + u : ""));
  }
  function journalLabel(e) {
    var type = e.event_type, sub = e.event_subtype;
    if (type === "diaper") {
      if (sub === "change") return "🩲 Diaper change";
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
    if (type === "temperature") return "🌡️ Temperature" + fmtValue(e.value, e.value_unit);
    if (type === "weight") return "⚖️ Weight " + fmtMeasure(e.value, e.value_unit);
    if (type === "length") return "📏 Length" + fmtValue(e.value, e.value_unit);
    if (type === "head_circumference") return "🧢 Head" + fmtValue(e.value, e.value_unit);
    if (type === "symptom") return "🤒 Symptom";
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

  // --- AI daily summary ---------------------------------------------------
  function fmtClock(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var m = (d.getMinutes() < 10 ? "0" : "") + d.getMinutes();
    return (d.getHours() % 12 || 12) + ":" + m + (d.getHours() >= 12 ? " PM" : " AM");
  }
  function loadSummary() {
    return apiGet("api/summary").then(renderAiSummary).catch(function () {});
  }
  function renderAiSummary(data) {
    var box = document.getElementById("sum-ai");
    var notice = document.getElementById("ai-notice");
    if (!box) return;
    if (!data || !data.enabled) { box.hidden = true; if (notice) notice.hidden = true; return; }
    box.hidden = false;
    var txt = document.getElementById("ai-text");
    var meta = document.getElementById("ai-meta");
    if (data.latest && data.latest.text) {
      txt.textContent = "🤖 " + data.latest.text;
      meta.textContent = "generated " + fmtClock(data.latest.generated_at)
        + " · " + data.used_today + "/" + data.cap + " today";
    } else {
      txt.textContent = "🤖 No summary yet today.";
      meta.textContent = data.used_today + "/" + data.cap + " today";
    }
    if (!generatingSummary) {
      var btn = document.getElementById("ai-generate");
      btn.disabled = !data.can_generate;
      btn.textContent = data.can_generate ? "Summarize now" : "Cap reached";
    }
    // First-run privacy notice (once per device)
    var seen = true;
    try { seen = localStorage.getItem("bt_ai_notice_seen"); } catch (e) {}
    if (!seen) notice.hidden = false;
  }
  function generateSummary() {
    var btn = document.getElementById("ai-generate");
    generatingSummary = true;
    btn.disabled = true; btn.textContent = "Thinking…";
    apiPost("api/summary", {})
      .then(function () { generatingSummary = false; setStatus("Summary ready ✓"); return loadSummary(); })
      .catch(function (err) {
        generatingSummary = false;
        setStatus(err.message.indexOf("429") >= 0
          ? "Daily summary cap reached" : "Summary failed (" + err.message + ")", true);
        return loadSummary();
      });
  }
  function wireAiSummary() {
    document.getElementById("ai-generate").addEventListener("click", generateSummary);
    document.getElementById("ai-notice-dismiss").addEventListener("click", function () {
      try { localStorage.setItem("bt_ai_notice_seen", "1"); } catch (e) {}
      document.getElementById("ai-notice").hidden = true;
    });
    document.getElementById("ai-config-link").addEventListener("click", function (e) {
      e.preventDefault();
      if (!addonSlug) { setStatus("Open Settings → the add-on → Configuration"); return; }
      // The Ingress iframe is same-origin with HA; navigate the parent frame to
      // the add-on's Configuration tab.
      var url = "/hassio/addon/" + addonSlug + "/config";
      try { window.top.location.href = url; }
      catch (err) { window.open(url, "_blank"); }
    });
  }

  // --- Data refresh (log + summary + journal) -----------------------------
  function refresh() {
    return apiGet("api/log")
      .then(function (data) {
        lastEntries = data.entries || [];
        renderSummary(data);
        if (editingId === null) renderJournal(lastEntries);
        renderContractionReadout();
        renderHealthReadout();
        loadSummary();
        if (currentTab === "supplies") loadSupplies();
        if (currentTab === "growth") loadGrowth();
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

  // --- Backup / restore (issue #5) ---------------------------------------
  function backupData() {
    setStatus("Preparing backup…");
    apiGet("api/export").then(function (data) {
      var blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      var d = new Date();
      function p(n) { return (n < 10 ? "0" : "") + n; }
      a.href = url;
      a.download = "baby-tracker-backup-" + d.getFullYear() + p(d.getMonth() + 1) +
        p(d.getDate()) + "-" + p(d.getHours()) + p(d.getMinutes()) + ".json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setStatus("Backup downloaded ✓");
    }).catch(function (err) { setStatus("Backup failed (" + err.message + ")", true); });
  }
  function restoreData(file) {
    if (!file) return;
    if (!window.confirm("Restore from this file? It REPLACES all current data in the add-on.")) return;
    var reader = new FileReader();
    reader.onload = function () {
      var payload;
      try { payload = JSON.parse(reader.result); }
      catch (e) { setStatus("That is not a valid JSON backup", true); return; }
      apiPost("api/import", payload)
        .then(function (r) {
          var n = r.restored ? (r.restored.baby_events || 0) : 0;
          setStatus("Restored ✓ (" + n + " events)");
          return refresh();
        })
        .then(function () { loadSupplies(); loadChecklist(); })
        .catch(function (err) { setStatus("Restore failed (" + err.message + ")", true); });
    };
    reader.readAsText(file);
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

  // --- Health -------------------------------------------------------------
  function sameLocalDay(iso) {
    var d = new Date(iso), n = new Date();
    return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth()
      && d.getDate() === n.getDate();
  }
  function isFever(v, unit) {
    if (v === null || v === undefined) return false;
    var thr = (unit && unit.indexOf("F") >= 0) ? (feverThresholdC * 9 / 5 + 32) : feverThresholdC;
    return v >= thr;
  }
  function renderHealthReadout() {
    var tEl = document.getElementById("temp-readout");
    if (tEl) {
      var temp = null;
      for (var i = 0; i < lastEntries.length; i++) {
        if (lastEntries[i].event_type === "temperature" && lastEntries[i].value != null) {
          temp = lastEntries[i]; break;
        }
      }
      if (!temp) { tEl.textContent = "No temperature logged yet."; tEl.className = "hx-readout"; }
      else {
        var fever = isFever(temp.value, temp.value_unit || "");
        tEl.textContent = "Last: " + fmtNum(temp.value) + (temp.value_unit ? " " + temp.value_unit : "")
          + " · " + (temp.time || "") + (fever ? "   ⚠ Fever" : "");
        tEl.className = "hx-readout" + (fever ? " fever" : "");
      }
    }
    var mEl = document.getElementById("med-readout");
    if (mEl) {
      var meds = lastEntries.filter(function (e) { return e.event_type === "medicine"; });
      var today = meds.filter(function (e) { return sameLocalDay(e.logged_at); });
      if (!meds.length) mEl.textContent = "No medicine logged today.";
      else mEl.textContent = "Last dose: " + (meds[0].time || "")
        + (meds[0].note ? " (" + meds[0].note + ")" : "") + " · " + today.length + " today";
    }
  }
  function logTemperature() {
    var v = parseFloat(document.getElementById("temp-value").value);
    if (isNaN(v)) { setStatus("Enter a temperature", true); return; }
    var u = document.getElementById("temp-unit").value;
    apiPost("api/event", { event_type: "temperature", value: v, value_unit: u })
      .then(function () { document.getElementById("temp-value").value = ""; setStatus("Temp logged ✓"); return refresh(); })
      .catch(function (err) { setStatus("Failed (" + err.message + ")", true); });
  }
  function logSymptom() {
    var inp = document.getElementById("symptom-input");
    var msg = (inp.value || "").trim();
    if (!msg) return;
    apiPost("api/event", { event_type: "symptom", note: msg })
      .then(function () { inp.value = ""; setStatus("Symptom logged ✓"); return refresh(); })
      .catch(function (err) { setStatus("Failed (" + err.message + ")", true); });
  }
  function logMedicine() {
    var inp = document.getElementById("med-input");
    var msg = (inp.value || "").trim();
    var body = { event_type: "medicine" };
    if (msg) body.note = msg;
    apiPost("api/event", body)
      .then(function () { inp.value = ""; setStatus("Medicine logged ✓"); return refresh(); })
      .catch(function (err) { setStatus("Failed (" + err.message + ")", true); });
  }

  // --- Growth -------------------------------------------------------------
  function loadGrowth() {
    return apiGet("api/growth").then(renderGrowth).catch(function () {});
  }
  function round2(n) { return Math.round(n * 100) / 100; }
  function renderGrowth(data) {
    var wrap = document.getElementById("growth-metrics");
    if (!wrap) return;
    wrap.textContent = "";
    GROWTH_METRICS.forEach(function (m) {
      var series = (data && data[m[0]]) || [];
      var card = document.createElement("div");
      card.className = "metric-row";
      var head = document.createElement("div");
      head.className = "metric-head";
      var name = document.createElement("span");
      name.className = "metric-name"; name.textContent = m[1];
      head.appendChild(name);
      var val = document.createElement("span");
      val.className = "metric-val";
      if (series.length) {
        var last = series[series.length - 1];
        var txt = fmtMeasure(last.value, last.value_unit);
        if (series.length >= 2) {
          var raw = last.value - series[series.length - 2].value;
          var arrow = raw > 0 ? "▲" : (raw < 0 ? "▼" : "·");
          var dtxt;
          if (last.value_unit === "lb") {
            var oz = Math.round(raw * 16);
            dtxt = (oz > 0 ? "+" : "") + oz + " oz";
          } else {
            var d = round2(raw);
            dtxt = (d > 0 ? "+" : "") + d + (last.value_unit ? " " + last.value_unit : "");
          }
          txt += "   " + arrow + " " + dtxt;
        }
        val.textContent = txt;
      } else { val.textContent = "—"; }
      head.appendChild(val);
      card.appendChild(head);
      if (series.length >= 2) {
        card.appendChild(sparkline(series.map(function (r) { return r.value; })));
      } else {
        var hint = document.createElement("div");
        hint.className = "metric-hint";
        hint.textContent = series.length ? "Log another to see the trend" : "No entries yet";
        card.appendChild(hint);
      }
      wrap.appendChild(card);
    });
  }
  function sparkline(vals) {
    var NS = "http://www.w3.org/2000/svg";
    var w = 240, h = 42, p = 5;
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var range = (max - min) || 1, n = vals.length;
    var pts = vals.map(function (v, i) {
      var x = p + (w - 2 * p) * (n === 1 ? 0.5 : i / (n - 1));
      var y = h - p - (h - 2 * p) * ((v - min) / range);
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("preserveAspectRatio", "none");
    var poly = document.createElementNS(NS, "polyline");
    poly.setAttribute("points", pts.join(" "));
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", "currentColor");
    poly.setAttribute("stroke-width", "2");
    poly.setAttribute("stroke-linejoin", "round");
    poly.setAttribute("stroke-linecap", "round");
    svg.appendChild(poly);
    var lp = pts[pts.length - 1].split(",");
    var dot = document.createElementNS(NS, "circle");
    dot.setAttribute("cx", lp[0]); dot.setAttribute("cy", lp[1]); dot.setAttribute("r", "3");
    dot.setAttribute("fill", "currentColor");
    svg.appendChild(dot);
    return svg;
  }
  function metricDef(type) {
    for (var i = 0; i < GROWTH_METRICS.length; i++) {
      if (GROWTH_METRICS[i][0] === type) return GROWTH_METRICS[i];
    }
    return null;
  }
  function growthType() { return document.getElementById("growth-type").value; }
  function growthUnit() { return document.getElementById("growth-unit").value; }
  // Repopulate the unit picker for the selected metric, defaulting per system,
  // and reveal the extra oz field only for imperial weight.
  function syncGrowthUnits() {
    var def = metricDef(growthType());
    if (!def) return;
    var usel = document.getElementById("growth-unit");
    var prev = usel.value;
    usel.textContent = "";
    def[2].forEach(function (u) {
      var o = document.createElement("option"); o.value = u; o.textContent = u; usel.appendChild(o);
    });
    usel.value = (def[2].indexOf(prev) >= 0) ? prev : (imperial ? def[2][1] : def[2][0]);
    toggleOz();
  }
  function toggleOz() {
    var isLb = growthType() === "weight" && growthUnit() === "lb";
    document.getElementById("growth-oz").hidden = !isLb;
    document.getElementById("growth-value").placeholder = isLb ? "lb" : "Value";
  }
  function logGrowth() {
    var type = growthType(), unit = growthUnit();
    var vEl = document.getElementById("growth-value");
    var v = parseFloat(vEl.value);
    if (type === "weight" && unit === "lb") {
      var oz = parseFloat(document.getElementById("growth-oz").value) || 0;
      var lb = isNaN(v) ? 0 : v;
      if (lb === 0 && oz === 0) { setStatus("Enter lb / oz", true); return; }
      v = lb + oz / 16;
    } else if (isNaN(v)) { setStatus("Enter a value", true); return; }
    var timeVal = document.getElementById("growth-time").value;
    var iso = timeVal ? localInputToIso(timeVal) : null;
    var body = { event_type: type, value: v, value_unit: unit };
    if (iso) body.logged_at = iso;
    apiPost("api/event", body)
      .then(function () {
        vEl.value = "";
        document.getElementById("growth-oz").value = "";
        document.getElementById("growth-time").value = nowLocalInput();
        setStatus("Logged ✓");
        return refresh().then(loadGrowth);
      })
      .catch(function (err) { setStatus("Failed (" + err.message + ")", true); });
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
    if (name === "growth") loadGrowth();
    if (name === "health") renderHealthReadout();
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
  function buildHealthPanel() {
    document.getElementById("temp-log").addEventListener("click", logTemperature);
    document.getElementById("temp-value").addEventListener("keydown", function (e) {
      if (e.key === "Enter") logTemperature();
    });
    document.getElementById("symptom-log").addEventListener("click", logSymptom);
    document.getElementById("symptom-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") logSymptom();
    });
    document.getElementById("med-log").addEventListener("click", logMedicine);
  }
  function buildGrowthPanel() {
    var sel = document.getElementById("growth-type");
    GROWTH_METRICS.forEach(function (m) {
      var o = document.createElement("option"); o.value = m[0]; o.textContent = m[1];
      sel.appendChild(o);
    });
    sel.addEventListener("change", syncGrowthUnits);
    document.getElementById("growth-unit").addEventListener("change", toggleOz);
    syncGrowthUnits();
    document.getElementById("growth-time").value = nowLocalInput();
    document.getElementById("growth-log").addEventListener("click", logGrowth);
  }
  // Apply the configured unit system to the pickers (after /api/config loads).
  function applyMeasurementDefaults() {
    var t = document.getElementById("temp-unit");
    if (t) t.value = imperial ? "°F" : "°C";
    syncGrowthUnits();
  }
  // Shared note bar (works on any tab), with the ⭐ special toggle.
  function wireCommonNote() {
    var star = document.getElementById("note-star");
    var inp = document.getElementById("common-note");
    star.addEventListener("click", function () {
      noteSpecial = !noteSpecial;
      star.textContent = noteSpecial ? "⭐" : "☆";
      star.classList.toggle("on", noteSpecial);
    });
    document.getElementById("common-note-save").addEventListener("click", function () { saveCommonNote(inp); });
    inp.addEventListener("keydown", function (e) { if (e.key === "Enter") saveCommonNote(inp); });
  }
  function saveCommonNote(inp) {
    var msg = (inp.value || "").trim();
    if (!msg) return;
    apiPost("api/note", { message: msg, special: noteSpecial })
      .then(function () {
        inp.value = "";
        noteSpecial = false;
        var star = document.getElementById("note-star");
        star.textContent = "☆"; star.classList.remove("on");
        setStatus("Note saved ✓");
        return refresh();
      })
      .catch(function (err) { setStatus("Failed to save note (" + err.message + ")", true); });
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
    buildHealthPanel();
    buildGrowthPanel();
    buildSuppliesPanel();
    buildChecklistPanel();
    wireCommonNote();
    wireAiSummary();
    wireTabs();

    document.getElementById("reset").addEventListener("click", resetAll);
    document.getElementById("backup").addEventListener("click", backupData);
    document.getElementById("restore-btn").addEventListener("click", function () {
      document.getElementById("restore-file").click();
    });
    document.getElementById("restore-file").addEventListener("change", function (e) {
      restoreData(e.target.files && e.target.files[0]);
      e.target.value = ""; // allow re-selecting the same file
    });

    apiGet("api/config")
      .then(function (c) {
        if (c && typeof c.fever_threshold_c === "number") feverThresholdC = c.fever_threshold_c;
        if (c && c.measurement_system) imperial = (c.measurement_system === "imperial");
        if (c && c.addon_slug) addonSlug = c.addon_slug;
        if (c && c.timezone) appTz = c.timezone;
        // The add/backfill pickers were pre-filled with "now" before appTz
        // arrived; refresh them so their default is in the add-on's timezone.
        ["manual-time", "growth-time", "ctx-backfill-time"].forEach(function (id) {
          var el = document.getElementById(id);
          if (el) el.value = nowLocalInput();
        });
        applyMeasurementDefaults();
        activateTab(pickInitialTab(c && c.default_tab));
      })
      .catch(function () { applyMeasurementDefaults(); activateTab(pickInitialTab("baby")); });

    refresh();
    pollTimer = setInterval(refresh, 10000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
