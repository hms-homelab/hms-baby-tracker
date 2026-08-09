/* Baby Tracker — i18n runtime (SDD-004).
 *
 * Zero dependencies, no bundler: the add-on serves the SPA as plain files.
 * Catalogs live in `i18n/<code>.json` and are fetched with RELATIVE paths so
 * they resolve under the Home Assistant Ingress path prefix, same rule as the
 * api/ calls in app.js.
 *
 * Lookup order for a key: active locale -> en -> the key itself.
 * `en` is always loaded, so a partial translation degrades to English rather
 * than to blanks.
 */
(function () {
  "use strict";

  var LS_KEY = "babytracker_lang";
  var catalogs = {};      // code -> {key: value}
  var registry = [];      // index.json
  var warned = {};        // key -> true (one console.warn per missing key)

  function fetchJson(path) {
    return fetch(path, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  /* Pick the best available catalog for a browser/user tag: exact match first
   * (nl-NL), then the base tag (nl). Returns null when nothing matches. */
  function match(tag) {
    if (!tag) return null;
    var lower = String(tag).toLowerCase();
    var codes = registry.map(function (e) { return e.code; });
    for (var i = 0; i < codes.length; i++) {
      if (codes[i].toLowerCase() === lower) return codes[i];
    }
    var base = lower.split("-")[0];
    for (var j = 0; j < codes.length; j++) {
      if (codes[j].toLowerCase().split("-")[0] === base) return codes[j];
    }
    return null;
  }

  function stored() {
    try { return localStorage.getItem(LS_KEY) || null; } catch (e) { return null; }
  }

  var I18N = {
    locale: "en",
    /* The language the picker's "Automatic" entry would resolve to, i.e. what
     * you get with no per-device override. Shown as a hint in the menu. */
    autoLocale: "en",

    registry: function () { return registry; },
    entry: function (code) {
      for (var i = 0; i < registry.length; i++) {
        if (registry[i].code === code) return registry[i];
      }
      return null;
    },

    /* Resolution order (SDD-004 §3.5): the per-device override, then the add-on
     * option when it is not "auto", then the browser, then English. */
    resolve: function (configLang) {
      var auto = null;
      if (configLang && configLang !== "auto") auto = match(configLang);
      if (!auto) {
        var langs = navigator.languages || [navigator.language];
        for (var i = 0; i < langs.length && !auto; i++) auto = match(langs[i]);
      }
      I18N.autoLocale = auto || "en";
      var override = match(stored());
      return override || I18N.autoLocale;
    },

    setOverride: function (code) {
      try {
        if (code) localStorage.setItem(LS_KEY, code);
        else localStorage.removeItem(LS_KEY);
      } catch (e) {}
    },
    hasOverride: function () { return !!stored(); },

    /* Loads index.json (once) plus `en` and the requested locale. Any failure
     * falls back to English rather than leaving the UI blank. */
    boot: function (configLang) {
      return fetchJson("i18n/index.json")
        .then(function (idx) { registry = idx || []; })
        .catch(function () { registry = [{ code: "en", name: "English", english_name: "English", flag: "", status: "source", credit: "" }]; })
        .then(function () { return I18N.load(I18N.resolve(configLang)); });
    },

    load: function (code) {
      code = code || "en";
      var need = ["en"];
      if (code !== "en") need.push(code);
      return Promise.all(need.map(function (c) {
        if (catalogs[c]) return null;
        return fetchJson("i18n/" + c + ".json")
          .then(function (d) { catalogs[c] = d || {}; })
          .catch(function () { catalogs[c] = catalogs[c] || {}; });
      })).then(function () {
        I18N.locale = catalogs[code] && Object.keys(catalogs[code]).length ? code : "en";
        document.documentElement.lang = I18N.locale;
        return I18N.locale;
      });
    },

    /* Raw lookup with the en fallback chain. Returns null when the key is
     * unknown everywhere, so t() can warn once and echo the key. */
    lookup: function (key) {
      var active = catalogs[I18N.locale];
      if (active && typeof active[key] === "string") return active[key];
      var en = catalogs.en;
      if (en && typeof en[key] === "string") return en[key];
      return null;
    },

    /* t("sum.slept", {total: "3h 12m"})
     * t("ctx.recent", {n: 4, ago: "12m"}, 4)   <- plural form via count
     *
     * Plurals use Intl.PluralRules categories as a key suffix
     * (key_one / key_other / key_few / ...), falling back to key_other then the
     * bare key. Built into the browser, so it stays correct for languages with
     * more than two forms without shipping a plural library.
     */
    t: function (key, vars, count) {
      var val = null;
      if (count !== undefined && count !== null) {
        var cat = "other";
        try { cat = new Intl.PluralRules(I18N.locale).select(count); } catch (e) {}
        val = I18N.lookup(key + "_" + cat);
        if (val === null) val = I18N.lookup(key + "_other");
      }
      if (val === null || val === undefined) val = I18N.lookup(key);
      if (val === null || val === undefined) {
        if (!warned[key]) { warned[key] = true; console.warn("[i18n] missing key:", key); }
        return key;
      }
      if (vars) {
        val = val.replace(/\{(\w+)\}/g, function (m, name) {
          return (vars[name] === undefined || vars[name] === null) ? m : String(vars[name]);
        });
      }
      return val;
    },

    /* Rewrites every marked node under `root`. The English text stays inline in
     * index.html as readable source and as the fallback if this never runs. */
    applyDom: function (root) {
      root = root || document;
      var map = [
        ["data-i18n", null],
        ["data-i18n-placeholder", "placeholder"],
        ["data-i18n-title", "title"],
        ["data-i18n-aria", "aria-label"],
      ];
      map.forEach(function (pair) {
        var attr = pair[0], target = pair[1];
        var nodes = root.querySelectorAll("[" + attr + "]");
        for (var i = 0; i < nodes.length; i++) {
          var key = nodes[i].getAttribute(attr);
          var text = I18N.t(key);
          if (target) nodes[i].setAttribute(target, text);
          else nodes[i].textContent = text;
        }
      });
    },
  };

  window.I18N = I18N;
  window.t = function (key, vars, count) { return I18N.t(key, vars, count); };
})();
