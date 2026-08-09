/* Baby Tracker — in-app translation editor (SDD-004 §3.9).
 *
 * Issue #9's author said it plainly: "I'm not a programmer, but i can help
 * translate to dutch." A JSON file in a pull request is still a programmer's
 * workflow. This is the same job done inside the app, with an Export at the end
 * that produces a file the repo can take as-is.
 *
 * Edits are saved as an OVERRIDE layer under <data_dir>/i18n/, not into the
 * image, so they survive an add-on update. The device path reads the same
 * merged catalog, so correcting an overlong OLED string here reaches the Baby
 * Remote on its next refresh, with no rebuild and no release.
 */
(function () {
  "use strict";

  var LIMIT = 21;                 // Baby Remote OLED row width; see app/i18n.py
  var lang = "en";
  var rows = [];                  // [{key, en, shipped, override, effective, is_device}]
  var comments = {};              // "_"-prefixed notes, re-emitted on export
  var edits = {};                 // key -> new value (dirty set)
  var filter = "all";

  function el(id) { return document.getElementById(id); }

  function api(method, path, body) {
    var opts = { method: method, headers: { Accept: "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          var e = new Error(data.error || ("HTTP " + r.status));
          e.data = data;
          throw e;
        }
        return data;
      });
    });
  }

  /* Group headings name parts of the app, not raw dotted keys, so the list
   * reads as sections a parent recognises. The raw key stays visible in mono
   * for anyone filing a bug about a specific string. */
  var GROUPS = [
    ["editor.groupDevice", ["device."]],
    ["editor.groupTabs", ["tab.", "tabs."]],
    ["editor.groupButtons", ["btn.", "ctx.mild", "ctx.medium", "ctx.intense", "group.", "opt."]],
    ["editor.groupSummary", ["sum.", "stat.", "ai."]],
    ["editor.groupJournal", ["journal."]],
    ["editor.groupContractions", ["ctx."]],
    ["editor.groupReady", ["ready."]],
    ["editor.groupHealth", ["health."]],
    ["editor.groupGrowth", ["growth."]],
    ["editor.groupSupplies", ["supply.", "supplyCat.", "consume."]],
    ["editor.groupMessages", ["status.", "err.", "confirm.", "prompt.", "alert.", "manual.", "note.", "footer.", "settings.", "editor.", "unit.", "time."]],
  ];

  function groupOf(key) {
    for (var i = 0; i < GROUPS.length; i++) {
      var prefixes = GROUPS[i][1];
      for (var j = 0; j < prefixes.length; j++) {
        if (key.indexOf(prefixes[j]) === 0) return GROUPS[i][0];
      }
    }
    return "editor.groupOther";
  }

  function valueOf(r) {
    return Object.prototype.hasOwnProperty.call(edits, r.key) ? edits[r.key] : (r.effective || "");
  }
  function isMissing(r) { return !valueOf(r).trim(); }
  function isMachine(r) {
    // Shipped by the machine pass and not yet touched by a human here.
    return !r.override && !Object.prototype.hasOwnProperty.call(edits, r.key);
  }

  /* Folds like app/i18n.py ascii_fold(): strip accents, drop anything above
   * 0x7F (which also removes emoji), then collapse the whitespace that leaves.
   * This mirrors the server so the counter matches what actually gets sent;
   * the server re-checks on PUT regardless, since that is the guarantee. */
  function foldAscii(s) {
    var mapped = s
      .replace(/ß/g, "ss").replace(/ẞ/g, "SS")
      .replace(/ø/g, "o").replace(/Ø/g, "O")
      .replace(/æ/g, "ae").replace(/Æ/g, "AE")
      .replace(/œ/g, "oe").replace(/Œ/g, "OE")
      .replace(/đ/g, "d").replace(/Đ/g, "D")
      .replace(/ł/g, "l").replace(/Ł/g, "L");
    var stripped = mapped.normalize("NFKD").replace(/[̀-ͯ]/g, "");
    var ascii = stripped.replace(/[^\x20-\x7F]/g, "");
    return ascii.split(/\s+/).filter(Boolean).join(" ");
  }
  function deviceLen(s) { return foldAscii(s).length; }
  function overLimit(r) { return r.is_device && deviceLen(valueOf(r)) > LIMIT; }

  function anyOverLimit() {
    for (var i = 0; i < rows.length; i++) if (overLimit(rows[i])) return true;
    return false;
  }

  function renderProgress() {
    var total = rows.length, missing = 0, machine = 0;
    rows.forEach(function (r) {
      if (isMissing(r)) missing++;
      else if (isMachine(r)) machine++;
    });
    var done = total - missing - machine;
    el("ed-bar-done").style.width = (total ? (done / total) * 100 : 0) + "%";
    el("ed-bar-machine").style.width = (total ? (machine / total) * 100 : 0) + "%";
    var leg = el("ed-legend");
    leg.textContent = "";
    [["sw-done", t("editor.done", { n: done })],
     ["sw-machine", t("editor.machine", { n: machine })],
     ["sw-missing", t("editor.missing", { n: missing })]].forEach(function (p) {
      var s = document.createElement("span");
      var b = document.createElement("b"); b.className = p[0];
      s.appendChild(b); s.appendChild(document.createTextNode(p[1]));
      leg.appendChild(s);
    });
  }

  function renderFilters() {
    var wrap = el("ed-filters");
    wrap.textContent = "";
    var counts = {
      all: rows.length,
      missing: rows.filter(isMissing).length,
      machine: rows.filter(function (r) { return !isMissing(r) && isMachine(r); }).length,
      device: rows.filter(function (r) { return r.is_device; }).length,
    };
    [["all", "editor.filterAll"], ["missing", "editor.filterMissing"],
     ["machine", "editor.filterMachine"], ["device", "editor.filterDevice"]]
      .forEach(function (p) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "ed-chip" + (filter === p[0] ? " on" : "");
        b.textContent = t(p[1]) + " " + counts[p[0]];
        b.addEventListener("click", function () { filter = p[0]; renderList(); renderFilters(); });
        wrap.appendChild(b);
      });
  }

  function visible(r) {
    if (filter === "missing") return isMissing(r);
    if (filter === "machine") return !isMissing(r) && isMachine(r);
    if (filter === "device") return r.is_device;
    return true;
  }

  /* Highlights {placeholders} in the English source: dropping one is the
   * mistake a non-programmer will actually make. */
  function withPlaceholders(text) {
    var frag = document.createDocumentFragment();
    var parts = String(text).split(/(\{\w+\})/g);
    parts.forEach(function (p) {
      if (/^\{\w+\}$/.test(p)) {
        var em = document.createElement("em");
        em.textContent = p;
        frag.appendChild(em);
      } else if (p) {
        frag.appendChild(document.createTextNode(p));
      }
    });
    return frag;
  }

  function renderList() {
    var list = el("ed-list");
    list.textContent = "";
    var lastGroup = null;
    rows.filter(visible).forEach(function (r) {
      var g = groupOf(r.key);
      if (g !== lastGroup) {
        lastGroup = g;
        var h = document.createElement("div");
        h.className = "ed-group";
        h.appendChild(document.createTextNode(t(g)));
        if (g === "editor.groupDevice") {
          var pill = document.createElement("span");
          pill.className = "ed-pill";
          pill.textContent = t("editor.deviceNote");
          h.appendChild(pill);
        }
        list.appendChild(h);
      }

      var row = document.createElement("div");
      row.className = "ed-row" + (r.is_device ? " dev" : "");

      var k = document.createElement("div");
      k.className = "ed-key"; k.textContent = r.key;

      var src = document.createElement("div");
      src.className = "ed-src";
      src.appendChild(withPlaceholders(r.en));

      var wrap = document.createElement("div");
      wrap.className = "ed-input";
      var inp = document.createElement("input");
      inp.type = "text";
      inp.value = valueOf(r);
      inp.setAttribute("aria-label", r.key);

      var count = null, err = null;
      if (r.is_device) {
        count = document.createElement("span");
        count.className = "ed-count";
        err = document.createElement("div");
        err.className = "ed-err";
        err.textContent = t("editor.tooLong");
      }

      function sync() {
        var v = inp.value;
        if (v === (r.effective || "")) delete edits[r.key];
        else edits[r.key] = v;
        if (r.is_device) {
          var n = deviceLen(v);
          count.textContent = n + "/" + LIMIT;
          count.className = "ed-count" + (n > LIMIT ? " over" : " ok");
          row.classList.toggle("bad", n > LIMIT);
          err.hidden = n <= LIMIT;
        }
        el("ed-save").disabled = anyOverLimit();
        renderProgress();
      }

      inp.addEventListener("input", sync);
      wrap.appendChild(inp);
      if (count) wrap.appendChild(count);

      row.appendChild(k); row.appendChild(src); row.appendChild(wrap);
      list.appendChild(row);
      if (err) { list.appendChild(err); }
      sync();
    });
    el("ed-save").disabled = anyOverLimit();
  }

  function setMsg(text, isErr) {
    var m = el("ed-msg");
    m.textContent = text || "";
    m.classList.toggle("err", !!isErr);
  }

  function renderHeader() {
    var e = I18N.entry(lang) || { name: lang, flag: "", status: "" };
    var h = el("ed-lang");
    h.textContent = "";
    h.appendChild(document.createTextNode((e.flag ? e.flag + " " : "") + e.name));
    if (e.status === "machine") {
      var pill = document.createElement("span");
      pill.className = "ed-pill warn";
      pill.textContent = t("editor.filterMachine");
      h.appendChild(pill);
    }
  }

  function load(code) {
    lang = code || I18N.locale;
    edits = {};
    return api("GET", "api/i18n/catalog?lang=" + encodeURIComponent(lang))
      .then(function (d) {
        rows = d.rows || [];
        comments = d.comments || {};
        renderHeader();
        renderProgress();
        renderFilters();
        renderList();
        setMsg("");
      });
  }

  function save() {
    var payload = {};
    rows.forEach(function (r) {
      var v = valueOf(r);
      // Persist anything that differs from the shipped catalog, so an edit that
      // reverts to the shipped text drops the override instead of pinning it.
      if (v && v !== (r.shipped || "")) payload[r.key] = v;
    });
    el("ed-save").disabled = true;
    return api("PUT", "api/i18n/" + encodeURIComponent(lang), { overrides: payload })
      .then(function () {
        setMsg(t("editor.saved"));
        // Drop the memoised catalog so the reload picks up the new overrides.
        return I18N.load(I18N.locale === lang ? lang : I18N.locale);
      })
      .then(function () {
        if (window.BTApplyLanguage) window.BTApplyLanguage();
        return load(lang);
      })
      .catch(function (err) {
        var d = err.data || {};
        setMsg(d.key
          ? t("editor.saveFailed", { msg: d.error + ": " + d.key })
          : t("editor.saveFailed", { msg: err.message }), true);
        el("ed-save").disabled = anyOverLimit();
      });
  }

  function revert() {
    if (!window.confirm(t("editor.confirmRevert"))) return;
    api("DELETE", "api/i18n/" + encodeURIComponent(lang))
      .then(function () {
        setMsg(t("editor.reverted"));
        return I18N.load(I18N.locale);
      })
      .then(function () {
        if (window.BTApplyLanguage) window.BTApplyLanguage();
        return load(lang);
      })
      .catch(function (err) { setMsg(t("editor.saveFailed", { msg: err.message }), true); });
  }

  /* Downloads a complete catalog shaped exactly like the repo's files, so it
   * can be attached to the issue or dropped into a PR unchanged. */
  /* Downloads a complete catalog shaped exactly like the repo's files, so it
   * can be attached to the issue or dropped into a PR unchanged.
   *
   * Built from what is ON SCREEN, not from the server. Exporting over the wire
   * would return only SAVED edits, so someone who translated a whole language
   * and pressed Export without pressing Save first would silently send the old
   * file and believe they had sent their work. Export now always matches what
   * you can see, whether or not you saved. */
  function exportJson() {
    var data = {};
    Object.keys(comments).forEach(function (k) { data[k] = comments[k]; });
    rows.forEach(function (r) { data[r.key] = valueOf(r); });
    var blob = new Blob([JSON.stringify(data, null, 2) + "\n"],
      { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = lang + ".json";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setMsg(t("editor.exported", { lang: lang }));
  }

  function open(code) {
    I18N.applyDom(el("ed-modal"));
    el("ed-modal").hidden = false;
    load(code);
  }
  function close() { el("ed-modal").hidden = true; }

  function wire() {
    if (!el("ed-modal")) return;
    el("ed-close").addEventListener("click", close);
    el("ed-save").addEventListener("click", save);
    el("ed-revert").addEventListener("click", revert);
    el("ed-export").addEventListener("click", exportJson);
    el("ed-modal").addEventListener("click", function (e) {
      if (e.target === el("ed-modal")) close();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !el("ed-modal").hidden) close();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }

  window.BTEditor = { open: open, close: close };
})();
