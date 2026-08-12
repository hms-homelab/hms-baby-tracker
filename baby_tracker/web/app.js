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

  // --- Baby button definitions: [i18n key, color, {event_type, event_subtype?}, emoji, light?] ---
  // Position 0 is a CATALOG KEY, not a label. The single render site (makeTile)
  // resolves it with t(), so adding a language never touches this table.
  var GROUPS = {
    "grp-feed": [
      ["btn.breast", "#e8a0bf", { event_type: "feed", event_subtype: "breast" }, "🤱"],
      ["btn.bottle", "#a0c4e8", { event_type: "feed", event_subtype: "bottle" }, "🍼"],
      ["btn.solid", "#c4e8a0", { event_type: "feed", event_subtype: "solid" }, "🍎"],
    ],
    "grp-pump": [
      ["btn.pumpL", "#d4a0e8", { event_type: "pump", event_subtype: "left" }, "🫙"],
      ["btn.pumpR", "#d4a0e8", { event_type: "pump", event_subtype: "right" }, "🫙"],
    ],
    "grp-diaper": [
      ["btn.pee", "#f0e68c", { event_type: "diaper", event_subtype: "pee" }, "💧"],
      ["btn.poop", "#d2a679", { event_type: "diaper", event_subtype: "poop" }, "💩"],
      ["btn.both", "#e8c8a0", { event_type: "diaper", event_subtype: "both" }, "✅"],
      ["btn.change", "#c8b89a", { event_type: "diaper", event_subtype: "change" }, "🩲"],
    ],
    "grp-other": [
      ["btn.sleepStart", "#b0a0e8", { event_type: "sleep", event_subtype: "start" }, "😴", true],
      ["btn.sleepEnd", "#9a86d4", { event_type: "sleep", event_subtype: "end" }, "⏰", true],
      ["btn.bath", "#a0d8e8", { event_type: "bath" }, "🛁"],
      ["btn.medicine", "#e8a0a0", { event_type: "medicine" }, "💊"],
      ["btn.tummy", "#a0e8c4", { event_type: "tummy_time" }, "🤸"],
    ],
  };

  // Contractions tab: three big severity buttons. [i18n key, subtype, color, emoji]
  var CONTRACTIONS = [
    ["ctx.mild", "mild", "#7bc47f", "🟢"],
    ["ctx.medium", "medium", "#e8a84e", "🟠"],
    ["ctx.intense", "intense", "#e06b6b", "🔴"],
  ];

  var SUPPLY_CATEGORIES = ["formula", "diapers", "wipes", "cream", "other"];

  // Growth tab metrics. [event_type, i18n key, [metric_unit, imperial_unit], emoji]
  var GROWTH_METRICS = [
    ["weight", "growth.weight", ["kg", "lb"], "⚖️"],
    ["length", "growth.length", ["cm", "in"], "📏"],
    ["head_circumference", "growth.head", ["cm", "in"], "🧢"],
  ];

  // Which event a supply auto-counts down on. [i18n key, type, subtype?]
  var CONSUME_OPTIONS = [
    ["consume.bottleFeed", "feed", "bottle"],
    ["consume.anyFeed", "feed", null],
    ["consume.anyDiaper", "diaper", null],
    ["consume.pump", "pump", null],
    ["consume.bath", "bath", null],
  ];

  // Flattened Baby type list for the manual-entry dropdown (key + payload).
  // Labels are resolved at build time so a language switch rebuilds them.
  // Container id -> hideable module id (SDD-005).
  var GROUP_MODULE = {
    "grp-feed": "group.feed",
    "grp-pump": "group.pump",
    "grp-diaper": "group.diaper",
    "grp-other": "group.other",
  };

  var EVENT_OPTIONS = [];
  Object.keys(GROUPS).forEach(function (gid) {
    GROUPS[gid].forEach(function (def) {
      EVENT_OPTIONS.push({ key: def[0], emoji: def[3], payload: def[2], group: GROUP_MODULE[gid] });
    });
  });
  EVENT_OPTIONS.push({ key: "opt.note", emoji: "📝", payload: { event_type: "note" } });

  var statusEl = document.getElementById("status");
  var pollTimer = null;
  var editingId = null;   // id of the journal row whose inline editor is open
  var currentTab = "baby";
  var lastEntries = [];   // cache of the latest journal entries (for the ctx readout)
  var lastSummaryData = null; // cache of the latest /api/log payload (re-render on pin toggle)
  var feverThresholdC = 38.0;  // from /api/config
  var imperial = true;          // from /api/config measurement_system
  var noteSpecial = false;      // shared note bar ⭐ toggle state
  var generatingSummary = false; // AI summary in flight (don't clobber the button)
  var addonSlug = "";           // this add-on's Supervisor slug (config deep link)
  var appTz = "";               // add-on's IANA timezone (anchors the datetime pickers)
  var PANELS = { get_ready: 1, baby: 1, contractions: 1, health: 1, growth: 1, supplies: 1 };

  // --- Module visibility (SDD-005) ----------------------------------------
  // `hidden_modules` from /api/config, as a lookup. Presentation only: hidden
  // event types are still accepted by the API, so the Baby Remote and any
  // automations keep logging them and past entries stay in the journal.
  var hidden = {};

  function visible(id) { return !hidden[id]; }

  // Module id for a button payload. Sleep start and end share one id: a
  // half-visible sleep pair cannot be used to log anything meaningful.
  function moduleOf(payload) {
    if (payload.event_type === "sleep") return "sleep";
    return payload.event_subtype
      ? payload.event_type + "." + payload.event_subtype
      : payload.event_type;
  }

  // Manual/backfill dropdown contents. Must stay the single source of truth for
  // both the <option> list and the click handler, which pairs them by index.
  function visibleEventOptions() {
    return EVENT_OPTIONS.filter(function (o) {
      if (o.payload.event_type === "note") return true;   // notes are never hidden
      return visible(o.group) && visible(moduleOf(o.payload));
    });
  }

  // Drops hidden tabs from the bar and their panels from navigation, and hides
  // the standalone summary rows and cards. Group and tile filtering happens in
  // buildGrids, which reruns on a language switch.
  function applyHiddenModules() {
    Object.keys(PANELS).forEach(function (name) {
      if (name === "baby") return;                        // home tab, never hidden
      if (visible("tab." + name)) return;
      delete PANELS[name];
      var btn = document.querySelector('.tab[data-tab="' + name + '"]');
      var panel = document.querySelector('.panel[data-panel="' + name + '"]');
      if (btn) btn.hidden = true;
      if (panel) panel.hidden = true;
    });
    var sleepRow = document.getElementById("sum-sleep");
    if (sleepRow) sleepRow.hidden = !visible("sleep");
    var manualCard = document.querySelector(".card.manual");
    if (manualCard) manualCard.hidden = !visible("card.manual");
  }

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
  // Issue #1 follow-up: hours + minutes past the 1h mark so users don't have to
  // do the math themselves, e.g. "15m", "2h", "1h 35m" (matches the "Xh Ym"
  // convention used elsewhere for durations, e.g. sleep_total_today).
  function fmtAgo(min) {
    if (min === null || min === undefined) return "—";
    if (min < 60) return min + t("unit.m");
    var hrs = Math.floor(min / 60), rem = min % 60;
    return hrs + t("unit.h") + (rem ? " " + rem + t("unit.m") : "");
  }
  function fmtAgoSuffix(min) {
    return (min === null || min === undefined) ? "" : " " + t("time.ago");
  }
  function fmtType(t) { return t ? " (" + t + ")" : ""; }

  // Any auxiliary stat (pumps, baths, contractions, temp, ...) can be tapped
  // to "pin" it up into the same big stat-card format as Last feed / Last
  // diaper / Asleep, or tapped again to send it back down into the chip tray.
  // Persisted in localStorage so the layout survives a refresh.
  var PIN_KEY = "babytracker_pinned_stats";
  function loadPinned() {
    try { return JSON.parse(localStorage.getItem(PIN_KEY) || "[]"); } catch (e) { return []; }
  }
  function savePinned(arr) {
    try { localStorage.setItem(PIN_KEY, JSON.stringify(arr)); } catch (e) {}
  }
  function togglePinned(key) {
    var p = loadPinned();
    var i = p.indexOf(key);
    if (i >= 0) p.splice(i, 1); else p.push(key);
    savePinned(p);
    if (lastSummaryData) renderSummary(lastSummaryData);
  }

  function statCard(item) {
    var el = document.createElement("div");
    el.className = "stat clickable";
    el.title = t("stat.unpin");
    el.addEventListener("click", function () { togglePinned(item.key); });
    var ico = document.createElement("div");
    ico.className = "stat-ico";
    ico.style.setProperty("--accent", item.accent);
    ico.textContent = item.icon;
    var text = document.createElement("div");
    text.className = "stat-text";
    var label = document.createElement("div");
    label.className = "stat-label";
    label.textContent = item.label;
    var val = document.createElement("div");
    val.className = "stat-value";
    val.textContent = item.value;
    text.appendChild(label);
    text.appendChild(val);
    el.appendChild(ico);
    el.appendChild(text);
    return el;
  }

  function statChip(item) {
    var chip = document.createElement("span");
    chip.className = "chip" + (item.warn ? " warn" : "");
    chip.title = t("stat.pin");
    chip.addEventListener("click", function () { togglePinned(item.key); });
    chip.appendChild(document.createTextNode(item.icon + " " + item.label + " "));
    var b = document.createElement("b");
    b.textContent = item.value;
    chip.appendChild(b);
    return chip;
  }

  // Renders each aux item as a pinned stat-card or an unpinned chip, per the
  // saved pin set.
  // Which module each aux stat belongs to, so hiding a tab or a button also
  // drops its roll-up figure instead of leaving a permanent "—".
  var STAT_MODULE = {
    pumps: "group.pump", baths: "bath", meds: "medicine", tummy: "tummy_time",
    contractions: "tab.contractions", ready: "tab.get_ready",
    temp: "tab.health", weight: "tab.growth",
  };

  function renderAuxStats(items) {
    items = items.filter(function (it) {
      return !STAT_MODULE[it.key] || visible(STAT_MODULE[it.key]);
    });
    var pinned = loadPinned();
    var extraEl = document.getElementById("sum-extra");
    var chipsEl = document.getElementById("sum-chips");
    extraEl.textContent = "";
    chipsEl.textContent = "";
    items.forEach(function (it) {
      if (pinned.indexOf(it.key) >= 0) extraEl.appendChild(statCard(it));
      else chipsEl.appendChild(statChip(it));
    });
  }

  function renderSummary(data) {
    lastSummaryData = data;
    var stats = (data && data.stats) || {};
    var extras = (data && data.summary_extras) || {};
    var entries = (data && data.entries) || lastEntries || [];
    var now = Date.now();

    document.getElementById("sum-feed-val").textContent =
      fmtAgo(stats.last_feed_min) + fmtAgoSuffix(stats.last_feed_min);
    document.getElementById("sum-feed-sub").textContent =
      (stats.last_feed_type ? stats.last_feed_type + " · " : "") + t("sum.today", { n: stats.feeds_today });
    document.getElementById("sum-diaper-val").textContent =
      fmtAgo(stats.last_diaper_min) + fmtAgoSuffix(stats.last_diaper_min);
    document.getElementById("sum-diaper-sub").textContent =
      (stats.last_diaper_type ? stats.last_diaper_type + " · " : "") + t("sum.today", { n: stats.diapers_today });

    // Sleep: state (asleep/awake) + how long that state has lasted, computed
    // from the most recent sleep start/end in the journal (entries are
    // newest-first), plus the running total for today as a caption.
    var lastSleep = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].event_type === "sleep") { lastSleep = entries[i]; break; }
    }
    var sinceMin = lastSleep ? Math.round((now - new Date(lastSleep.logged_at).getTime()) / 60000) : null;
    document.getElementById("sum-sleep-ico").textContent = stats.is_sleeping ? "😴" : "🌙";
    document.getElementById("sum-sleep-label").textContent =
      stats.is_sleeping ? t("sum.asleep") : t("sum.awake");
    document.getElementById("sum-sleep-val").textContent = sinceMin === null ? "—" : fmtAgo(sinceMin);
    document.getElementById("sum-sleep-sub").textContent =
      t("sum.slept", { total: stats.sleep_total_today });

    // Contractions / Get Ready / Health / Growth roll-up
    var win = 2 * 3600 * 1000;
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

    var fever = false, tempStr = "—";
    if (lastTemp) {
      tempStr = fmtNum(lastTemp.value) + (lastTemp.value_unit ? " " + lastTemp.value_unit : "");
      fever = isFever(lastTemp.value, lastTemp.value_unit || "");
    }
    var wStr = lastWeight ? fmtMeasure(lastWeight.value, lastWeight.value_unit) : "—";

    renderAuxStats([
      { key: "pumps", icon: "🫙", label: t("stat.pumps"), value: stats.pumps_today, accent: "#B19CD9" },
      { key: "baths", icon: "🛁", label: t("stat.baths"), value: stats.baths_today, accent: "#6FD1D1" },
      { key: "meds", icon: "💊", label: t("stat.meds"), value: stats.medicines_today, accent: "#E06B6B" },
      { key: "tummy", icon: "🤸", label: t("stat.tummy"), value: stats.tummy_times_today, accent: "#5BD6A0" },
      { key: "contractions", icon: "⏱️", label: t("stat.contractions"),
        value: ctxToday + (ctx2h ? " " + t("stat.ctx2h", { n: ctx2h }) : ""), accent: "#E06B6B" },
      { key: "ready", icon: "🎒", label: t("stat.ready"), value: cl.done + "/" + cl.total, accent: "#9A86D4" },
      { key: "temp", icon: "🌡️", label: t("stat.temp"), value: tempStr, warn: fever, accent: "#E8A84E" },
      { key: "weight", icon: "📈", label: t("stat.weight"), value: wStr, accent: "#6FB1C9" },
    ]);

    // Notifications / alerts strip (fever + supply low/refill-due)
    var sup = extras.supplies || { low: [], due: [] };
    var alerts = [];
    if (fever) alerts.push("⚠ " + t("alert.fever"));
    if (sup.low && sup.low.length) alerts.push("🧴 " + t("alert.low", { names: sup.low.join(", ") }));
    if (sup.due && sup.due.length) alerts.push("🔔 " + t("alert.refill", { names: sup.due.join(", ") }));
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
      if (sub === "change") return "🩲 " + t("journal.diaperChange");
      if (sub === "both") return "🧷 " + t("journal.peePoop");
      if (sub === "pee") return "💧 " + t("journal.pee");
      if (sub === "poop") return "💩 " + t("journal.poop");
      return "🧷 " + t("journal.diaper") + fmtType(sub);
    }
    if (type === "sleep") {
      if (sub === "start") return "😴 " + t("journal.sleepStart");
      if (sub === "end") return "⏰ " + t("journal.sleepEnd");
      return "😴 " + t("journal.sleep") + fmtType(sub);
    }
    if (type === "pump") {
      if (sub === "left") return "🫙 " + t("journal.pumpL");
      if (sub === "right") return "🫙 " + t("journal.pumpR");
      return "🫙 " + t("journal.pump") + fmtType(sub);
    }
    if (type === "feed") {
      if (sub === "breast") return "🤱 " + t("journal.breastFeed");
      if (sub === "bottle") return "🍼 " + t("journal.bottleFeed");
      if (sub === "solid") return "🍎 " + t("journal.solidFood");
      return "🍼 " + t("journal.feed") + fmtType(sub);
    }
    if (type === "contraction") return "⏱️ " + t("journal.contraction") + fmtType(sub);
    if (type === "supply") return "🧴 " + t("journal.supply") + fmtType(sub);
    if (type === "temperature") return "🌡️ " + t("journal.temperature") + fmtValue(e.value, e.value_unit);
    if (type === "weight") return "⚖️ " + t("journal.weight") + " " + fmtMeasure(e.value, e.value_unit);
    if (type === "length") return "📏 " + t("journal.length") + fmtValue(e.value, e.value_unit);
    if (type === "head_circumference") return "🧢 " + t("journal.head") + fmtValue(e.value, e.value_unit);
    if (type === "symptom") return "🤒 " + t("journal.symptom");
    if (type === "note") return "📝 " + t("journal.note");
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
      empty.textContent = t("journal.empty");
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
    note.placeholder = t("manual.notePlaceholder");
    note.value = entry.note || "";

    var save = document.createElement("button");
    save.className = "j-save";
    save.textContent = t("journal.save");
    save.addEventListener("click", function () {
      var iso = localInputToIso(time.value);
      if (!iso) { setStatus(t("err.invalidDateTime"), true); return; }
      apiPatch("api/event/" + entry.id, { logged_at: iso, note: note.value.trim() })
        .then(function () { editingId = null; setStatus(t("status.updated")); return refresh(); })
        .catch(function (err) { setStatus(t("err.failed", { msg: err.message }), true); });
    });

    var del = document.createElement("button");
    del.className = "j-del";
    del.textContent = t("journal.delete");
    del.addEventListener("click", function () {
      if (!window.confirm(t("confirm.deleteEvent"))) return;
      apiDelete("api/event/" + entry.id)
        .then(function () { editingId = null; setStatus(t("status.deleted")); return refresh(); })
        .catch(function (err) { setStatus(t("err.failed", { msg: err.message }), true); });
    });

    var cancel = document.createElement("button");
    cancel.className = "j-cancel";
    cancel.textContent = t("journal.cancel");
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
    if (!recent.length) { el.textContent = t("ctx.none2h"); return; }
    // entries are most-recent-first
    var lastMin = Math.round((now - new Date(recent[0].logged_at).getTime()) / 60000);
    var gap = "";
    if (recent.length >= 2) {
      var diffs = [];
      for (var i = 0; i < recent.length - 1; i++) {
        diffs.push(new Date(recent[i].logged_at).getTime() - new Date(recent[i + 1].logged_at).getTime());
      }
      var avg = diffs.reduce(function (a, b) { return a + b; }, 0) / diffs.length;
      gap = t("ctx.avgGap", { n: Math.round(avg / 60000) });
    }
    el.textContent = t("ctx.recent",
      { n: recent.length, ago: fmtAgo(lastMin) }, recent.length) + gap;
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
    if (!data || !data.enabled || !visible("card.summary")) {
      box.hidden = true;
      if (notice) notice.hidden = true;
      return;
    }
    box.hidden = false;
    var txt = document.getElementById("ai-text");
    var meta = document.getElementById("ai-meta");
    if (data.latest && data.latest.text) {
      txt.textContent = "🤖 " + data.latest.text;
      meta.textContent = t("ai.generated", { time: fmtClock(data.latest.generated_at) })
        + " · " + t("ai.usage", { used: data.used_today, cap: data.cap });
    } else {
      txt.textContent = "🤖 " + t("ai.noSummary");
      meta.textContent = t("ai.usage", { used: data.used_today, cap: data.cap });
    }
    if (!generatingSummary) {
      var btn = document.getElementById("ai-generate");
      btn.disabled = !data.can_generate;
      btn.textContent = data.can_generate ? t("ai.generate") : t("ai.capReached");
    }
    // First-run privacy notice (once per device)
    var seen = true;
    try { seen = localStorage.getItem("bt_ai_notice_seen"); } catch (e) {}
    if (!seen) notice.hidden = false;
  }
  function generateSummary() {
    var btn = document.getElementById("ai-generate");
    generatingSummary = true;
    btn.disabled = true; btn.textContent = t("ai.thinking");
    apiPost("api/summary", {})
      .then(function () { generatingSummary = false; setStatus(t("status.summaryReady")); return loadSummary(); })
      .catch(function (err) {
        generatingSummary = false;
        setStatus(err.message.indexOf("429") >= 0
          ? t("err.capReached") : t("err.summaryFailed", { msg: err.message }), true);
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
      if (!addonSlug) { setStatus(t("ai.openSettings")); return; }
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
      .catch(function (err) { setStatus(t("err.offline", { msg: err.message }), true); });
  }

  // --- Baby actions -------------------------------------------------------
  function sendEvent(payload, tileEl) {
    if (tileEl) {
      tileEl.classList.add("pressed");
      setTimeout(function () { tileEl.classList.remove("pressed"); }, 150);
    }
    apiPost("api/event", payload)
      .then(function () { setStatus(t("status.logged")); return refresh(); })
      .catch(function (err) { setStatus(t("err.failedLog", { msg: err.message }), true); });
  }
  function addManual() {
    var sel = document.getElementById("manual-type");
    var opt = visibleEventOptions()[sel.selectedIndex];
    if (!opt) return;
    var timeVal = document.getElementById("manual-time").value;
    var noteVal = (document.getElementById("manual-note").value || "").trim();
    var iso = timeVal ? localInputToIso(timeVal) : null;
    if (timeVal && !iso) { setStatus(t("err.invalidDateTime"), true); return; }
    var payload = { event_type: opt.payload.event_type };
    if (opt.payload.event_subtype) payload.event_subtype = opt.payload.event_subtype;
    if (noteVal) payload.note = noteVal;
    if (iso) payload.logged_at = iso;
    apiPost("api/event", payload)
      .then(function () {
        document.getElementById("manual-note").value = "";
        document.getElementById("manual-time").value = nowLocalInput();
        setStatus(t("status.added")); return refresh();
      })
      .catch(function (err) { setStatus(t("err.failedAdd", { msg: err.message }), true); });
  }
  function resetAll() {
    if (!window.confirm(t("confirm.resetAll"))) return;
    apiPost("api/reset", {})
      .then(function () { setStatus(t("status.resetDone")); return refresh(); })
      .catch(function (err) { setStatus(t("err.failedReset", { msg: err.message }), true); });
  }

  // --- Backup / restore (issue #5) ---------------------------------------
  function backupData() {
    setStatus(t("status.preparingBackup"));
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
      setStatus(t("status.backupDownloaded"));
    }).catch(function (err) { setStatus(t("err.backupFailed", { msg: err.message }), true); });
  }
  function restoreData(file) {
    if (!file) return;
    if (!window.confirm(t("confirm.restore"))) return;
    var reader = new FileReader();
    reader.onload = function () {
      var payload;
      try { payload = JSON.parse(reader.result); }
      catch (e) { setStatus(t("err.badBackup"), true); return; }
      apiPost("api/import", payload)
        .then(function (r) {
          var n = r.restored ? (r.restored.baby_events || 0) : 0;
          setStatus(t("status.restored", { n: n }, n));
          return refresh();
        })
        .then(function () { loadSupplies(); loadChecklist(); })
        .catch(function (err) { setStatus(t("err.restoreFailed", { msg: err.message }), true); });
    };
    reader.readAsText(file);
  }

  // --- Contractions -------------------------------------------------------
  function addBackfillContraction() {
    var sel = document.getElementById("ctx-backfill-intensity");
    var sub = sel.value;
    var timeVal = document.getElementById("ctx-backfill-time").value;
    var iso = timeVal ? localInputToIso(timeVal) : null;
    if (!iso) { setStatus(t("err.pickDateTime"), true); return; }
    apiPost("api/event", { event_type: "contraction", event_subtype: sub, logged_at: iso })
      .then(function () {
        document.getElementById("ctx-backfill-time").value = nowLocalInput();
        setStatus(t("status.contractionAdded")); return refresh();
      })
      .catch(function (err) { setStatus(t("err.failed", { msg: err.message }), true); });
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
      empty.textContent = t("supply.empty");
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
      if (s.is_low) { var b1 = document.createElement("span"); b1.className = "badge low"; b1.textContent = t("supply.low"); badges.appendChild(b1); }
      if (s.is_due) { var b2 = document.createElement("span"); b2.className = "badge due"; b2.textContent = t("supply.refillDue"); badges.appendChild(b2); }
      if (s.low_threshold != null) { var b3 = document.createElement("span"); b3.className = "badge muted"; b3.textContent = "≤ " + fmtNum(s.low_threshold); badges.appendChild(b3); }
      if (s.refill_days != null) { var b4 = document.createElement("span"); b4.className = "badge muted"; b4.textContent = t("supply.everyDays", { n: s.refill_days }); badges.appendChild(b4); }
      if (badges.children.length) li.appendChild(badges);

      var actions = document.createElement("div");
      actions.className = "supply-actions";
      actions.appendChild(supplyBtn("−", "s-minus", function () { adjustSupply(s.id, -1); }));
      actions.appendChild(supplyBtn("+", "s-plus", function () { adjustSupply(s.id, 1); }));
      actions.appendChild(supplyBtn(t("supply.refillBtn"), "s-refill", function () { refillSupply(s); }));
      actions.appendChild(supplyBtn(t("supply.deleteBtn"), "s-del", function () {
        if (window.confirm(t("confirm.deleteSupply", { name: s.name }))) {
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
      .then(loadSupplies).catch(function (err) { setStatus(t("err.failed", { msg: err.message }), true); });
  }
  function refillSupply(s) {
    var ans = window.prompt(t("prompt.refill", { name: s.name, unit: s.unit || t("supply.units") }),
      s.low_threshold != null ? "" : fmtNum(s.quantity));
    if (ans === null) return;
    var body = {};
    var q = parseFloat(ans);
    if (!isNaN(q)) body.quantity = q;
    apiPost("api/supplies/" + s.id + "/refill", body)
      .then(function () { setStatus(t("status.refilled")); return refresh().then(loadSupplies); })
      .catch(function (err) { setStatus(t("err.failed", { msg: err.message }), true); });
  }
  function addSupply() {
    var name = (document.getElementById("sup-name").value || "").trim();
    if (!name) { setStatus(t("err.supplyName"), true); return; }
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
        setStatus(t("status.supplyAdded")); return loadSupplies();
      })
      .catch(function (err) { setStatus(t("err.failedSupply", { msg: err.message }), true); });
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
      del.setAttribute("aria-label", t("ready.deleteAria"));
      del.addEventListener("click", function () {
        apiDelete("api/checklist/" + it.id).then(loadChecklist).catch(function () {});
      });
      li.appendChild(cb); li.appendChild(label); li.appendChild(del);
      ul.appendChild(li);
    });
    var prog = document.getElementById("checklist-progress");
    if (prog) prog.textContent = items.length ? t("ready.progress", { done: done, total: items.length }) : "";
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
      if (!temp) { tEl.textContent = t("health.tempEmpty"); tEl.className = "hx-readout"; }
      else {
        var fever = isFever(temp.value, temp.value_unit || "");
        tEl.textContent = t("health.lastTemp", {
          value: fmtNum(temp.value) + (temp.value_unit ? " " + temp.value_unit : ""),
          time: temp.time || "",
        }) + (fever ? "   ⚠ " + t("alert.fever") : "");
        tEl.className = "hx-readout" + (fever ? " fever" : "");
      }
    }
    var mEl = document.getElementById("med-readout");
    if (mEl) {
      var meds = lastEntries.filter(function (e) { return e.event_type === "medicine"; });
      var today = meds.filter(function (e) { return sameLocalDay(e.logged_at); });
      if (!meds.length) mEl.textContent = t("health.medEmpty");
      else mEl.textContent = t("health.lastDose", {
        time: (meds[0].time || "") + (meds[0].note ? " (" + meds[0].note + ")" : ""),
        n: today.length,
      });
    }
  }
  function logTemperature() {
    var v = parseFloat(document.getElementById("temp-value").value);
    if (isNaN(v)) { setStatus(t("err.enterTemp"), true); return; }
    var u = document.getElementById("temp-unit").value;
    apiPost("api/event", { event_type: "temperature", value: v, value_unit: u })
      .then(function () { document.getElementById("temp-value").value = ""; setStatus(t("status.tempLogged")); return refresh(); })
      .catch(function (err) { setStatus(t("err.failed", { msg: err.message }), true); });
  }
  function logSymptom() {
    var inp = document.getElementById("symptom-input");
    var msg = (inp.value || "").trim();
    if (!msg) return;
    apiPost("api/event", { event_type: "symptom", note: msg })
      .then(function () { inp.value = ""; setStatus(t("status.symptomLogged")); return refresh(); })
      .catch(function (err) { setStatus(t("err.failed", { msg: err.message }), true); });
  }
  function logMedicine() {
    var inp = document.getElementById("med-input");
    var msg = (inp.value || "").trim();
    var body = { event_type: "medicine" };
    if (msg) body.note = msg;
    apiPost("api/event", body)
      .then(function () { inp.value = ""; setStatus(t("status.medicineLogged")); return refresh(); })
      .catch(function (err) { setStatus(t("err.failed", { msg: err.message }), true); });
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
      name.className = "metric-name"; name.textContent = m[3] + " " + t(m[1]);
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
        hint.textContent = series.length ? t("growth.logAnother") : t("growth.noEntries");
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
    document.getElementById("growth-value").placeholder = isLb ? "lb" : t("growth.valuePlaceholder");
  }
  function logGrowth() {
    var type = growthType(), unit = growthUnit();
    var vEl = document.getElementById("growth-value");
    var v = parseFloat(vEl.value);
    if (type === "weight" && unit === "lb") {
      var oz = parseFloat(document.getElementById("growth-oz").value) || 0;
      var lb = isNaN(v) ? 0 : v;
      if (lb === 0 && oz === 0) { setStatus(t("err.enterLbOz"), true); return; }
      v = lb + oz / 16;
    } else if (isNaN(v)) { setStatus(t("err.enterValue"), true); return; }
    var timeVal = document.getElementById("growth-time").value;
    var iso = timeVal ? localInputToIso(timeVal) : null;
    var body = { event_type: type, value: v, value_unit: unit };
    if (iso) body.logged_at = iso;
    apiPost("api/event", body)
      .then(function () {
        vEl.value = "";
        document.getElementById("growth-oz").value = "";
        document.getElementById("growth-time").value = nowLocalInput();
        setStatus(t("status.logged"));
        return refresh().then(loadGrowth);
      })
      .catch(function (err) { setStatus(t("err.failed", { msg: err.message }), true); });
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
  // Rebuilt from scratch on a language switch, so every tile label follows the
  // active catalog. `def[0]` is a catalog key, resolved here via t().
  function buildGrids() {
    Object.keys(GROUPS).forEach(function (gid) {
      var container = document.getElementById(gid);
      container.textContent = "";
      var groupOn = visible(GROUP_MODULE[gid]);
      GROUPS[gid].forEach(function (def) {
        if (!groupOn || !visible(moduleOf(def[2]))) return;
        var btn = makeTile(t(def[0]), def[1], def[3], def[4]);
        btn.addEventListener("click", function () { sendEvent(def[2], btn); });
        container.appendChild(btn);
      });
      // A group whose tiles are all hidden loses its heading too, otherwise the
      // Baby tab keeps a title with nothing under it.
      var empty = !container.firstChild;
      container.hidden = empty;
      var title = container.previousElementSibling;
      if (title && title.classList.contains("group-title")) title.hidden = empty;
    });
    // Contraction severity tiles (bigger, in their own grid).
    var cg = document.getElementById("grp-contraction");
    cg.textContent = "";
    CONTRACTIONS.forEach(function (def) {
      var btn = makeTile(t(def[0]), def[2], def[3], false);
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
  // Repopulates every <select> whose options carry translated text. Split out
  // from the wiring below so a language switch can re-run it without
  // double-binding listeners. Preserves the current selection by index.
  function fillOptions() {
    function refill(id, items, make) {
      var sel = document.getElementById(id);
      if (!sel) return;
      var idx = sel.selectedIndex;
      sel.textContent = "";
      items.forEach(function (it) { sel.appendChild(make(it)); });
      if (idx >= 0 && idx < sel.options.length) sel.selectedIndex = idx;
    }
    refill("manual-type", visibleEventOptions(), function (o) {
      var opt = document.createElement("option");
      opt.textContent = o.emoji + " " + t(o.key);
      return opt;
    });
    refill("ctx-backfill-intensity", CONTRACTIONS, function (def) {
      var opt = document.createElement("option");
      opt.value = def[1]; opt.textContent = def[3] + " " + t(def[0]);
      return opt;
    });
    refill("sup-category", SUPPLY_CATEGORIES, function (c) {
      var opt = document.createElement("option");
      opt.value = c; opt.textContent = t("supplyCat." + c);
      return opt;
    });
    refill("sup-consume-type", CONSUME_OPTIONS, function (c) {
      var opt = document.createElement("option");
      opt.textContent = t(c[0]);
      return opt;
    });
    refill("growth-type", GROWTH_METRICS, function (m) {
      var o = document.createElement("option");
      o.value = m[0]; o.textContent = m[3] + " " + t(m[1]);
      return o;
    });
  }
  function buildManual() {
    document.getElementById("manual-time").value = nowLocalInput();
    document.getElementById("manual-add").addEventListener("click", addManual);
  }
  function buildContractionsPanel() {
    document.getElementById("ctx-backfill-time").value = nowLocalInput();
    document.getElementById("ctx-backfill-add").addEventListener("click", addBackfillContraction);
  }
  function buildSuppliesPanel() {
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
    document.getElementById("growth-type").addEventListener("change", syncGrowthUnits);
    document.getElementById("growth-unit").addEventListener("change", toggleOz);
    document.getElementById("growth-time").value = nowLocalInput();
    document.getElementById("growth-log").addEventListener("click", logGrowth);
  }
  // Apply the configured unit system to the pickers (after /api/config loads).
  // NB: the temp-unit element is held in `tu`, not `t` — `t` is the global
  // translate function and shadowing it here would break every lookup below.
  function applyMeasurementDefaults() {
    var tu = document.getElementById("temp-unit");
    if (tu) tu.value = imperial ? "°F" : "°C";
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
        setStatus(t("status.noteSaved"));
        return refresh();
      })
      .catch(function (err) { setStatus(t("err.failedNote", { msg: err.message }), true); });
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

  // --- Language picker (SDD-004 §3.5.1) -----------------------------------
  // Every entry is flag + endonym: a flag denotes a country, not a language
  // (nl-NL vs nl-BE), and regional-indicator emoji fall back to bare letter
  // pairs on some platforms. The endonym keeps both cases readable.
  function renderLangMenu() {
    var menu = document.getElementById("lang-menu");
    var flagEl = document.getElementById("lang-flag");
    if (!menu) return;
    var reg = I18N.registry();
    var active = I18N.entry(I18N.locale);
    if (flagEl) flagEl.textContent = (active && active.flag) || "🌐";
    menu.textContent = "";

    function row(label, sub, selected, onClick) {
      var d = document.createElement("button");
      d.type = "button";
      d.className = "lang-item" + (selected ? " sel" : "");
      d.setAttribute("role", "menuitem");
      d.appendChild(document.createTextNode(label));
      if (sub) {
        var s = document.createElement("span");
        s.className = "lang-sub"; s.textContent = sub;
        d.appendChild(s);
      }
      d.addEventListener("click", onClick);
      menu.appendChild(d);
      return d;
    }

    // "Automatic" clears the per-device override. Without it a user who picks a
    // language they cannot read has no way back.
    var autoEntry = I18N.entry(I18N.autoLocale);
    row("🌐 " + t("settings.languageAuto", { lang: (autoEntry && autoEntry.name) || "English" }),
      null, !I18N.hasOverride(), function () { switchLanguage(null); });

    reg.forEach(function (e) {
      row((e.flag ? e.flag + " " : "") + e.name,
        e.status === "machine" ? t("editor.filterMachine") : null,
        I18N.hasOverride() && e.code === I18N.locale,
        function () { switchLanguage(e.code); });
    });

    var edit = row("✎ " + t("settings.editTranslations"), null, false, function () {
      closeLangMenu();
      if (window.BTEditor) window.BTEditor.open(I18N.locale);
    });
    edit.classList.add("lang-edit");
  }

  function openLangMenu() {
    renderLangMenu();
    document.getElementById("lang-menu").hidden = false;
    document.getElementById("lang-btn").setAttribute("aria-expanded", "true");
  }
  function closeLangMenu() {
    var m = document.getElementById("lang-menu");
    if (m) m.hidden = true;
    var b = document.getElementById("lang-btn");
    if (b) b.setAttribute("aria-expanded", "false");
  }

  // Re-render in place rather than reloading, so an open journal editor or an
  // unsaved note survives a language switch.
  function applyLanguage() {
    I18N.applyDom(document);
    buildGrids();
    fillOptions();
    syncGrowthUnits();
    renderLangMenu();
    if (lastSummaryData) renderSummary(lastSummaryData);
    renderJournal(lastEntries);
    renderContractionReadout();
    renderHealthReadout();
    if (currentTab === "supplies") loadSupplies();
    if (currentTab === "growth") loadGrowth();
    if (currentTab === "get_ready") loadChecklist();
    loadSummary();
  }

  function switchLanguage(code) {
    I18N.setOverride(code);
    closeLangMenu();
    I18N.load(code || I18N.autoLocale).then(applyLanguage);
  }

  function wireLangPicker() {
    var btn = document.getElementById("lang-btn");
    if (!btn) return;
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var m = document.getElementById("lang-menu");
      if (m.hidden) openLangMenu(); else closeLangMenu();
    });
    document.addEventListener("click", function (e) {
      var m = document.getElementById("lang-menu");
      if (!m || m.hidden) return;
      if (!m.contains(e.target)) closeLangMenu();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeLangMenu();
    });
  }
  // Exposed so editor.js can re-apply after a save without a reload.
  window.BTApplyLanguage = applyLanguage;

  function init() {
    // Wire listeners once. Anything that renders translated text is built
    // later, in applyLanguage(), once a catalog is actually loaded.
    buildManual();
    buildContractionsPanel();
    buildHealthPanel();
    buildGrowthPanel();
    buildSuppliesPanel();
    buildChecklistPanel();
    wireCommonNote();
    wireAiSummary();
    wireTabs();
    wireLangPicker();

    document.getElementById("reset").addEventListener("click", resetAll);
    document.getElementById("backup").addEventListener("click", backupData);
    document.getElementById("restore-btn").addEventListener("click", function () {
      document.getElementById("restore-file").click();
    });
    document.getElementById("restore-file").addEventListener("change", function (e) {
      restoreData(e.target.files && e.target.files[0]);
      e.target.value = ""; // allow re-selecting the same file
    });

    // Boot order matters (SDD-004): the tile labels and <option> text come from
    // the catalog at build time, so /api/config (which carries `language`) and
    // the catalog must both land BEFORE anything translated is rendered.
    var conf = null;
    apiGet("api/config")
      .catch(function () { return null; })
      .then(function (c) {
        conf = c;
        if (c && typeof c.fever_threshold_c === "number") feverThresholdC = c.fever_threshold_c;
        if (c && c.measurement_system) imperial = (c.measurement_system === "imperial");
        if (c && c.addon_slug) addonSlug = c.addon_slug;
        if (c && c.timezone) appTz = c.timezone;
        // Must land before applyLanguage(), which rebuilds the tiles and the
        // manual-entry dropdown from the (now filtered) catalogs.
        if (c && c.hidden_modules) {
          c.hidden_modules.forEach(function (id) { hidden[id] = 1; });
        }
        return I18N.boot(c && c.language);
      })
      .catch(function () { /* catalogs unreachable: fall through on English */ })
      .then(function () {
        applyHiddenModules();
        applyLanguage();
        // The add/backfill pickers were pre-filled with "now" before appTz
        // arrived; refresh them so their default is in the add-on's timezone.
        ["manual-time", "growth-time", "ctx-backfill-time"].forEach(function (id) {
          var el = document.getElementById(id);
          if (el) el.value = nowLocalInput();
        });
        applyMeasurementDefaults();
        activateTab(pickInitialTab(conf && conf.default_tab));
        refresh();
        pollTimer = setInterval(refresh, 10000);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
