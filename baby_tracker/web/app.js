/* Baby Tracker — Ingress SPA (vanilla JS, relative fetch URLs).
 *
 * All API calls use RELATIVE paths ("api/log", "api/event", ...) so they
 * resolve correctly under the Home Assistant Ingress path prefix.
 */
(function () {
  "use strict";

  // --- Event type -> emoji (matches app/ingest.py ICONS) ---
  var ICONS = {
    feed: "🍼",
    diaper: "🧷",
    sleep: "😴",
    bath: "🛁",
    medicine: "💊",
    tummy_time: "🤸",
    weight: "⚖️",
    pump: "🤱",
    note: "📝",
  };

  // --- Button definitions: [label, color, {event_type, event_subtype?}, emoji, light?] ---
  var GROUPS = {
    "grp-feed": [
      ["Breast", "#e8a0bf", { event_type: "feed", event_subtype: "breast" }, "🍼"],
      ["Bottle", "#a0c4e8", { event_type: "feed", event_subtype: "bottle" }, "🍼"],
      ["Solid", "#c4e8a0", { event_type: "feed", event_subtype: "solid" }, "🍎"],
    ],
    "grp-pump": [
      ["Pump L", "#d4a0e8", { event_type: "pump", event_subtype: "left" }, "🤱"],
      ["Pump R", "#d4a0e8", { event_type: "pump", event_subtype: "right" }, "🤱"],
    ],
    "grp-diaper": [
      ["Pee", "#f0e68c", { event_type: "diaper", event_subtype: "pee" }, "💧"],
      ["Poop", "#d2a679", { event_type: "diaper", event_subtype: "poop" }, "💩"],
      ["Both", "#e8c8a0", { event_type: "diaper", event_subtype: "both" }, "✅"],
      ["Change", "#c8b89a", { event_type: "diaper", event_subtype: "change" }, "🍼"],
    ],
    "grp-other": [
      ["Sleep Start", "#b0a0e8", { event_type: "sleep", event_subtype: "start" }, "😴", true],
      ["Sleep End", "#9a86d4", { event_type: "sleep", event_subtype: "end" }, "⏰", true],
      ["Bath", "#a0d8e8", { event_type: "bath" }, "🛁"],
      ["Medicine", "#e8a0a0", { event_type: "medicine" }, "💊"],
      ["Tummy", "#a0e8c4", { event_type: "tummy_time" }, "🤸"],
    ],
  };

  var statusEl = document.getElementById("status");
  var pollTimer = null;

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

  function apiPost(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  // --- Rendering ----------------------------------------------------------
  function fmtAgo(min) {
    if (min === null || min === undefined) return "—";
    return min + "min ago";
  }

  function fmtType(t) {
    return t ? " (" + t + ")" : "";
  }

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
      "🫙 Pumps: " + stats.pumps_today +
      " | 🛁 Baths: " + stats.baths_today +
      " | 💊 Medicine: " + stats.medicines_today +
      " | 🤸 Tummy time: " + stats.tummy_times_today;
  }

  // Human label + emoji for a journal entry.
  function journalLabel(e) {
    var type = e.event_type;
    var sub = e.event_subtype;

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
      if (sub === "breast") return "🍼 Breast feed";
      if (sub === "bottle") return "🍼 Bottle feed";
      if (sub === "solid") return "🍎 Solid food";
      return "🍼 Feed" + fmtType(sub);
    }
    if (type === "note") return "📝 Note";

    // Generic: emoji + capitalized "type (sub)"
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
      // Notes carry their text in .note; show it (but not for the title duplication).
      if (e.note && e.event_type !== "note") {
        var n = document.createElement("span");
        n.className = "j-note";
        n.textContent = e.note;
        left.appendChild(n);
      } else if (e.note && e.event_type === "note") {
        var nn = document.createElement("span");
        nn.className = "j-note";
        nn.textContent = e.note;
        left.appendChild(nn);
      }

      var time = document.createElement("span");
      time.className = "j-time";
      time.textContent = e.time || "";

      li.appendChild(left);
      li.appendChild(time);
      ul.appendChild(li);
    });
  }

  // --- Data refresh -------------------------------------------------------
  function refresh() {
    return apiGet("api/log")
      .then(function (data) {
        renderSummary(data.stats || {});
        renderJournal(data.entries || []);
        setStatus("");
      })
      .catch(function (err) {
        setStatus("Offline — retrying… (" + err.message + ")", true);
      });
  }

  // --- Actions ------------------------------------------------------------
  function sendEvent(payload, tileEl) {
    if (tileEl) {
      tileEl.classList.add("pressed");
      setTimeout(function () {
        tileEl.classList.remove("pressed");
      }, 150);
    }
    apiPost("api/event", payload)
      .then(function () {
        setStatus("Logged ✓");
        return refresh();
      })
      .catch(function (err) {
        setStatus("Failed to log (" + err.message + ")", true);
      });
  }

  function saveNote(inputEl, special) {
    var msg = (inputEl.value || "").trim();
    if (!msg) return;
    apiPost("api/note", { message: msg, special: !!special })
      .then(function () {
        inputEl.value = "";
        setStatus("Note saved ✓");
        return refresh();
      })
      .catch(function (err) {
        setStatus("Failed to save note (" + err.message + ")", true);
      });
  }

  function resetAll() {
    if (!window.confirm("Reset ALL events? This cannot be undone.")) return;
    apiPost("api/reset", {})
      .then(function () {
        setStatus("Reset done ✓");
        return refresh();
      })
      .catch(function (err) {
        setStatus("Failed to reset (" + err.message + ")", true);
      });
  }

  // --- Build button grids -------------------------------------------------
  function buildGrids() {
    Object.keys(GROUPS).forEach(function (gid) {
      var container = document.getElementById(gid);
      GROUPS[gid].forEach(function (def) {
        var label = def[0],
          color = def[1],
          payload = def[2],
          emoji = def[3],
          light = def[4];

        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "tile" + (light ? " light" : "");
        btn.style.background = color;

        var ico = document.createElement("span");
        ico.className = "ico";
        ico.textContent = emoji;

        var lbl = document.createElement("span");
        lbl.className = "lbl";
        lbl.textContent = label;

        btn.appendChild(ico);
        btn.appendChild(lbl);
        btn.addEventListener("click", function () {
          sendEvent(payload, btn);
        });
        container.appendChild(btn);
      });
    });
  }

  // --- Wire up ------------------------------------------------------------
  function init() {
    buildGrids();

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

    refresh();
    pollTimer = setInterval(refresh, 10000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
