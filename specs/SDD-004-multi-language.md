# SDD-004 — Multi-language (i18n)

Status: **DRAFT** for review. Scope and §7 decisions resolved 2026-08-09. No code written yet.
Date: 2026-08-09
Component: `baby_tracker/` (web SPA + `app/display.py`, `app/scheduler.py`)
Origin: GitHub issue [#9](https://github.com/hms-homelab/hms-baby-tracker/issues/9)
(mkampstra / Merte): "is there any plans to make it multi language? I searched
for language files but found it is all in the code. I'm not a programmer, but i
can help translate to dutch (NL_nl)"
Ships as: **2026.4.12** (patch, staying on the 4.x line)

## 1. Goal

Make every string a parent actually reads translatable, and make contributing a
language a **single JSON file with zero code changes**, so a non-programmer can
open one PR.

Two surfaces are in scope:

1. **The Ingress web UI** (`web/index.html` + `web/app.js`). This is what issue
   #9 is about.
2. **The Baby Remote device text** (`app/display.py` OLED rows,
   `app/scheduler.py` reminder banners and alerts).

Language is chosen by a new add-on option `language`, defaulting to `auto`.

## 2. Background: what exists today

Every string is a literal in source. Inventory:

| Surface | Count | Shape |
|---|---|---|
| `web/index.html` text nodes | 47 | headings, tab labels, buttons, static copy |
| `web/index.html` attributes | 27 | `placeholder`, `aria-label`, `title` |
| `web/app.js` | ~110 | `GROUPS`/`CONTRACTIONS`/`GROWTH_METRICS`/`CONSUME_OPTIONS` labels, stat labels, journal labels, empty states, `window.confirm()` text, status toasts, unit hints |
| `app/display.py` | 7 | `build_rows()` OLED rows |
| `app/scheduler.py` | 5 | `_fire_pump` / `_fire_feed` titles, messages, OLED banner |

Roughly **185 web keys + 12 device keys**.

Relevant existing plumbing we reuse rather than replace:

- `app/config.py` is a dataclass loaded from `/data/options.json` via
  `_load_options()`. Adding an option is one field plus one `config.yaml` entry.
- `GET /api/config` (`app/main.py:366`) already ships `timezone`,
  `measurement_system`, `fever_threshold_c`, `default_tab`, `addon_slug` to the
  SPA on boot. It is the natural carrier for the resolved language.
- The SPA is **plain vanilla JS with no bundler and no build step**. Any solution
  must be dependency-free and work off a plain `fetch()`.

### 2.1 The device font is a hard constraint

`baby-remote-esp/firmware/main/font5x7.h` declares:

```c
#define FONT_FIRST 0x20
#define FONT_LAST  0x7F
```

and `oled.c:fb_char()` does `if (ch < FONT_FIRST || ch > FONT_LAST) ch = '?';`.

Consequences that drive the design:

- The OLED renders **ASCII only**. A non-ASCII character becomes `?`.
- Payloads are UTF-8, so a single accented letter is **2 bytes**, and each byte
  independently fails the range check. Dutch `"beëindigd"` would render as
  `"be??indigd"`, not `"be?indigd"`.
- The row is **21 characters** (128px / 6px cell), and overflow is **silently
  clipped** (`if (x + col >= OLED_W) return;`). German and Dutch phrasings are
  routinely longer than their English source.

So device strings need an ASCII fold and a width budget. Web strings need
neither. This is why §4 splits the namespace.

## 3. Design

### 3.1 One catalog directory, shared by both surfaces

```
baby_tracker/i18n/          shipped in the image, read-only
  index.json                registry of available locales
  en.json                   source of truth, always complete
  nl.json  es.json  fr.json machine first pass, see §3.8

/data/i18n/                 user overrides from the editor, see §3.9
  nl.json  es.json  fr.json survives add-on updates
```

A translator copies `en.json`, translates the values, adds one line to
`index.json`, opens a PR. They never touch `.js`, `.py`, or `.html`.

`index.json`:

```json
[
  { "code": "en", "name": "English",    "english_name": "English", "flag": "🇬🇧", "status": "source",  "credit": "" },
  { "code": "nl", "name": "Nederlands", "english_name": "Dutch",   "flag": "🇳🇱", "status": "machine", "credit": "@mkampstra" },
  { "code": "es", "name": "Español",    "english_name": "Spanish", "flag": "🇪🇸", "status": "machine", "credit": "" },
  { "code": "fr", "name": "Français",   "english_name": "French",  "flag": "🇫🇷", "status": "machine", "credit": "" }
]
```

- `name` is the **endonym** (the language's own name), so a language list is
  readable to the person who needs it.
- `flag` is a regional-indicator emoji for the header picker (§3.5.1). It is
  data, so a contributor picks their own, and a flag is always shown next to its
  endonym rather than alone.
- `english_name` is what the AI summary prompt asks for (§3.7). The LLM gets
  "respond in Dutch", not "respond in Nederlands".
- `status` is `source`, `machine` (first-pass machine translation, not yet
  reviewed by a speaker) or `human`. See §3.8.
- `credit` is optional attribution.

FastAPI mounts the directory as static at `/i18n` for the SPA, and
`app/i18n.py` reads the same files from disk for the device strings. One file per
language serves both surfaces.

### 3.2 Catalog format

Flat dotted keys, values are strings:

```json
{
  "app.title": "Baby Tracker",
  "tab.baby": "Baby",
  "btn.breast": "Breast",
  "sum.lastFeed": "Last feed",
  "sum.slept": "Slept {total} today",
  "journal.empty": "No events yet.",
  "confirm.resetAll": "Reset ALL events? This cannot be undone.",
  "ctx.recent_one":   "{n} in last 2h",
  "ctx.recent_other": "{n} in last 2h",
  "device.feedAgo": "Feed {ago} ago",
  "device.pumpDue": "Pump due now"
}
```

- **Interpolation** is `{name}`, substituted positionally by name. No expressions.
- **Plurals** use an `Intl.PluralRules` category suffix: `key_zero`, `key_one`,
  `key_two`, `key_few`, `key_many`, `key_other`. `t()` calls
  `new Intl.PluralRules(locale).select(n)` and looks up `key_<category>`, falling
  back to `key_other`, then `key`. This is built into every browser and into
  Python via a small static table on the server side, so it costs nothing and it
  is correct for Polish, Russian and Arabic later, not just Dutch.
- **No nesting.** Flat keys keep diffs readable for a non-programmer and make the
  completeness test trivial.

### 3.3 Web: marking up the DOM

`index.html` gets declarative attributes. No text stays in markup:

```html
<h1 data-i18n="app.title">👶 Baby Tracker</h1>
<button class="tab" data-tab="baby" data-i18n="tab.baby">Baby</button>
<input id="common-note" data-i18n-placeholder="note.placeholder" />
<button id="note-star" data-i18n-title="note.star" data-i18n-aria="note.star">☆</button>
```

The English text stays inline as a **fallback and as readable source**, and is
overwritten on boot by `applyDom()`, which walks
`[data-i18n], [data-i18n-placeholder], [data-i18n-title], [data-i18n-aria]`.

Emoji stay in the markup, outside the translated span where they are decorative,
so a translator never has to handle them.

### 3.4 Web: the runtime

New `web/i18n.js`, loaded before `app.js`:

```js
window.I18N = {
  locale: "en",
  load: function (code) { /* fetch i18n/<code>.json, fall back to en */ },
  t: function (key, vars, count) { /* plural select, interpolate, fallback */ },
  applyDom: function (root) { /* rewrite data-i18n* attributes */ }
};
```

Resolution rules:

- Missing key in the active locale falls back to `en`, then to the key string
  itself, and logs one `console.warn` per missing key. A half-finished
  translation degrades to English, it never shows blanks.
- `en.json` is always fetched, so fallback needs no second round trip on miss.
- `document.documentElement.lang` is set to the resolved locale.

`app.js` changes are mechanical: literals become `t("...")`, and the definition
tables carry keys instead of labels:

```js
// before
["Breast", "#e8a0bf", { event_type: "feed", event_subtype: "breast" }, "🤱"],
// after
["btn.breast", "#e8a0bf", { event_type: "feed", event_subtype: "breast" }, "🤱"],
```

with the single render site calling `t(def[0])`.

`fmtAgo()` currently concatenates `"h"` / `"m"` / `"ago"` literals. Those become
`unit.h`, `unit.m`, `time.ago` keys so a language can reorder them.

### 3.5 Language selection

New add-on option:

```yaml
language: auto
```

`config.yaml` schema `language: str`, `app/config.py` field `language: str = "auto"`.
`GET /api/config` gains `"language": cfg.language`.

Client resolution order, first match wins:

1. `localStorage.babytracker_lang`, set by the in-UI picker (§3.5.1).
2. `cfg.language` from `/api/config`, when it is not `auto`.
3. `navigator.languages`, best match against `index.json`: exact tag (`nl-NL`),
   then base tag (`nl`).
4. `en`.

`auto` is the default so a Dutch household gets Dutch with no configuration, and
an explicit value exists for the case where the browser language and the desired
UI language differ.

### 3.5.1 In-UI language picker

A flag button in the **header**, on the `app-head` row that currently holds
`<h1>👶 Baby Tracker</h1>` and the status span. It sits top-right, shows the
active language's flag, and opens a dropdown on tap.

Header placement means the control is visible on every tab without scrolling,
and a returning user who landed in the wrong language finds it immediately
instead of scrolling past six tabs of untranslated buttons to reach a footer.

- `index.json` gains a `flag` field holding a regional-indicator emoji
  (`"🇳🇱"`). It is data, not code, so a contributor picks their own.
- **Every entry pairs the flag with its endonym** (`🇳🇱 Nederlands`), for two
  reasons. Flags denote countries, not languages: Dutch is spoken in NL and BE,
  and a Belgian user seeing only 🇳🇱 reads it as the wrong locale. And regional
  indicator pairs do not render as flags on Windows Chrome, which falls back to
  the letters `NL` and would otherwise leave the picker looking broken. With the
  endonym present, both cases still read correctly.
- The collapsed button shows flag alone to stay compact at 360px; the open menu
  shows flag plus endonym.
- A first entry, `Automatic`, clears the override and falls back to step 2.
  Without it a user could strand themselves in a language they cannot read.
  It carries the resolved language as a hint: `Automatic (English)`.
- Selecting writes `localStorage.babytracker_lang` and re-renders **in place**:
  `I18N.load(code)` then `applyDom(document)` plus a re-render of the dynamic
  panels. No page reload, so an open journal edit or unsaved note is not lost.
- `localStorage` means the choice is **per browser**, which is the intent: two
  parents sharing one HA install can read different languages on their own
  phones.
- The picker itself is translated (`settings.language`, `settings.languageAuto`).

**Scope boundary worth stating in DOCS.md:** the picker changes the **web UI
only**. The OLED and reminder text are produced server-side from `cfg.language`,
which no browser can override, since one device serves the whole household.
Changing the picker and expecting the remote to follow is the obvious wrong
assumption, so the `Automatic` label and DOCS both name the add-on option as the
place that controls the device.

### 3.6 Device strings

New `app/i18n.py`:

```python
def load(lang: str) -> dict          # read i18n/<lang>.json, merge over en.json
def t(key: str, **vars) -> str       # interpolate {name}
def device(key: str, **vars) -> str  # t() + ascii_fold() + width guard
```

`ascii_fold()`:

1. `unicodedata.normalize("NFKD", s)` and drop combining marks. Handles
   `ë -> e`, `á -> a`, `ü -> u`.
2. An explicit map for characters NFKD does not decompose:
   `ß -> ss`, `ø -> o`, `æ -> ae`, `œ -> oe`, `đ -> d`, `ł -> l`, `å -> a`.
3. Drop anything still above `0x7F`. This also strips emoji, which the OLED
   cannot render at all.

Width guard: if the folded string exceeds **21 characters**, log one warning per
key per process and **return the English string for that key on the device
only**. The web UI still shows the full translation. A too-long translation
degrades to readable English rather than to a silently clipped word.

`app.display.build_rows()` and `app.scheduler._fire_feed` / `_fire_pump` take the
resolved language and call `device(...)`. `build_rows()` stays a pure function
with the language passed in, so its existing parity tests keep working.

`publish_alert()` titles and messages go to Home Assistant, **not** to the OLED,
so they use `t()` (full Unicode, emoji preserved), not `device()`.

Language for device strings is `cfg.language`, resolved at startup. When it is
`auto` the server has no browser to ask, so it uses `en`. Documented in DOCS.md:
set `language` explicitly to translate the device.

### 3.7 AI daily summary language

The AI recap renders inside the summary card, so a Dutch UI showing an English
recap reads as a bug. `app/summary.py` builds its prompt from
`cfg.summary_prompt` (`DEFAULT_SUMMARY_PROMPT`). We append one line when the
resolved language is not English:

```
Respond in {english_name}.
```

`english_name` comes from `index.json`, so adding a language needs no prompt
code change. Rules:

- Appended, never substituted. The existing prompt body is untouched, including
  its "do not use em-dashes" instruction, which has been shown to matter.
- Applies to **every** provider (hosted, ollama, anthropic, openai, gemini).
- The de-identified digest payload is unchanged. This adds no new data to what
  the hosted relay receives, only an output-language instruction.
- English resolves to no appended line at all, so the default install sends a
  byte-identical prompt to what ships today.

Known limitation to state in DOCS.md: non-English summary quality depends on the
configured model and is unverified. A user who dislikes it can set the UI
language and leave `summary_enabled` off, or override `summary_prompt`.

### 3.8 Machine-translated first pass, and who reviews each language

2026.4.12 ships **Dutch, Spanish and French** as machine translations, so each is
usable immediately and each has a real file to correct rather than a blank one.
That means shipping text no native speaker has read, in an app parents use at
3am, so it is labelled rather than passed off as finished:

- `index.json` carries `"status": "machine"` for those locales.
- `en.json` and DOCS.md state that `machine` locales are unreviewed and that
  corrections are welcome, either by PR or via the editor's Export (§3.9).
- The `device.*` keys are the risk concentration, since they are the ones a
  clipped or mistranslated word makes actively misleading on a glanceable
  screen. They are 12 strings per language, 36 total. **These get hand-checked
  against the ASCII and 21-character tests before the release, not just
  machine-produced.**
- `status` flips to `human` when a speaker has been through a language, and
  `credit` records who.

The three are not in the same position, and the plan should not pretend they are:

| Locale | Reviewer | Note |
|---|---|---|
| `nl` | Merte (`@mkampstra`) | Volunteered on issue #9. Reviews via the editor or a PR. |
| `es` | **You** | Native reviewer already on the project. |
| `fr` | none yet | Ships `machine` and stays that way until a speaker turns up. |

**Spanish is the one to get right at source.** Your Spanish is Latin American
(South Florida): `el control` not `el mando`, and `el app` masculine with
agreement following. A generic machine pass will produce neither. So the Spanish
first pass is explicitly a **draft for you to rewrite**, and §3.9's editor is the
mechanism: fix the wording in the app, Export, commit. Where your Spanish
diverges from the English phrasing, that is the correct outcome, not a mismatch
to reconcile.

French has no reviewer, so it carries the `machine` badge indefinitely. That is
honest rather than ideal, and it is still better than no French at all given the
editor lets any French-speaking user fix a string and send it back.

### 3.9 In-app translation editor

Issue #9's author says plainly: *"I'm not a programmer, but i can help translate
to dutch."* A JSON file in a PR is still a programmer's workflow. The editor
closes that gap: translate in the app, hit **Export**, attach the file to the
issue. He never opens a code editor or a terminal.

It also pays back on the maintenance side. A too-long `device.*` string can be
fixed on the running install in seconds, with no rebuild, no ghcr push and no
release.

#### Where it lives

A **full-screen modal**, opened from an `Edit translations…` entry at the bottom
of the language dropdown. Deliberately **not** a seventh tab: the tab bar
already scrolls horizontally at six, and translating is a rare focused sitting,
not something a parent switches to mid-feed. Opening from the picker also puts it
exactly where someone unhappy with the wording is already looking.

#### Two catalog layers

```
baby_tracker/i18n/<lang>.json    shipped in the image, read-only
/data/i18n/<lang>.json           user overrides, survives add-on updates
```

Effective value resolves as `en shipped` < `<lang> shipped` < `<lang> override`.
The overrides live in `/data` precisely because the image is replaced on every
add-on update, and hand-typed translations must not be.

Both consumers read the merged result, so correcting an OLED string in the editor
changes what the device prints on its next 60s refresh.

#### API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/i18n/catalog?lang=nl` | rows of `{key, en, shipped, override, effective, is_device, limit}` |
| `PUT` | `/api/i18n/nl` | save `{overrides: {...}}` |
| `DELETE` | `/api/i18n/nl?key=` | revert one key, or all when `key` is omitted |
| `GET` | `/api/i18n/nl/export` | download a complete merged `nl.json` |

Server-side validation on `PUT`, not just in the browser:

- **Unknown keys are rejected.** An override may only set a key that exists in
  `en.json`. A typo cannot inject junk into the catalog.
- **`device.*` values are re-checked** for ASCII and the 21-character budget
  after folding. The live counter in the UI is a convenience; this is the
  guarantee. A violation returns 400 naming the key and its length.

#### The editing surface

- A **progress bar** per language: translated / machine / missing out of ~199.
- **Filter chips**: All, Missing, Machine, Device. `Missing` is the one that
  matters to a contributor working through a fresh language.
- Rows **grouped by key prefix** with human headings (Tabs, Buttons, Summary,
  Journal, Health, Supplies, Device) rather than raw dotted keys, so the list
  reads as sections of the app instead of a symbol dump. The raw key stays
  visible in mono for anyone filing a bug about it.
- Each row shows the **English source** above an input holding the current
  value. Placeholder tokens like `{ago}` are highlighted, with a note that they
  must survive translation.
- `device.*` rows carry a **live `n/21` counter** that turns red past the limit,
  and **Save is blocked** while any device row is over. That is the whole point:
  catch it here rather than as a silently clipped word on the remote.
- Footer actions: **Save**, **Export JSON**, **Revert to shipped**.

#### Export

`Export JSON` downloads the merged catalog as `<lang>.json`, byte-identical in
shape to what the repo expects. That file can be attached to issue #9, dropped
into a PR, or mailed. It is the handoff that turns a parent's afternoon of
typing into a contribution the repo can accept.

## 4. Key namespace

| Prefix | Surface | Constraints |
|---|---|---|
| `app.` `tab.` `btn.` `sum.` `journal.` `ctx.` `health.` `growth.` `supply.` `ready.` `ai.` `status.` `confirm.` `unit.` `time.` | web SPA | full Unicode, emoji fine |
| `alert.` | HA notifications via MQTT | full Unicode, emoji fine |
| `device.` | Baby Remote OLED | **ASCII after fold, <= 21 chars** |

The `device.` prefix is the contract that makes the constraint testable, and it
tells a translator exactly which dozen strings need care. `en.json` carries a
`_comment` note above the `device.` block stating the 21-character budget.

## 5. Tests

New `tests/test_i18n.py`, pytest, no new dependencies:

1. **Catalog completeness.** For every locale in `index.json`, its key set equals
   `en.json`'s key set. Missing keys fail, extra keys fail. This is the check that
   makes a partial translation a red CI run instead of a blank UI.
2. **Device safety.** For every locale, every `device.*` key rendered with
   worst-case sample vars is pure ASCII (`s.isascii()`) and `len(s) <= 21`.
3. **Fold correctness.** `ascii_fold()` unit cases including the multi-byte trap:
   `"beëindigd" -> "beeindigd"`, `"straße" -> "strasse"`, emoji stripped.
4. **Width fallback.** An over-long device translation returns the English string
   and logs once.
5. **`t()` behavior.** Interpolation, missing-key fallback to `en`, plural
   category selection.
6. **Source/catalog sync.** Parse `index.html` for `data-i18n*` attribute values
   and `app.js` for `t("...")` literals, assert every key exists in `en.json`,
   and assert no `en.json` key is orphaned. This catches a typo'd key without
   standing up a JS test runner, which the repo does not currently have.
7. **Override merge precedence.** `en shipped` < `<lang> shipped` <
   `<lang> override`, and an override for one key never disturbs its neighbours.
8. **Override rejection.** `PUT /api/i18n/<lang>` returns 400 for a key absent
   from `en.json`, and 400 naming the key and length for a `device.*` value that
   fails ASCII or exceeds 21 characters after folding. Both asserted
   server-side, since the browser's counter is convenience and this is the
   guarantee.
9. **Export round-trip.** `GET /api/i18n/<lang>/export` output, dropped into
   `i18n/` as `<lang>.json`, passes the completeness test in check 1. This is the
   contributor's whole path, so it should be the thing CI proves.
10. **Registry integrity.** Every `index.json` entry has `code`, `name`,
    `english_name`, `flag` and a `status` in `source|machine|human`, and a
    catalog file exists for each.

Existing 50 tests must stay green. `display.py`'s parity tests are the ones most
likely to need touching, since `build_rows()` gains a language parameter. It
keeps a default so existing call sites and tests are unaffected.

## 6. Out of scope, and why

- **MQTT discovery entity names** (`Last Feed`, `Sleeping`, `Daily Summary`).
  Translating these renames Home Assistant entities and **breaks every existing
  user's automations and dashboards**. HA already lets a user rename an entity
  locally. Explicitly excluded.
- **The add-on Configuration panel.** HA has a native
  `baby_tracker/translations/<lang>.yaml` mechanism for option names and
  descriptions. Cheap and zero-risk, but not selected for this pass. Easy
  follow-up.
- **README, DOCS.md, CHANGELOG.** English only.
- **The suite's ESPHome OLED build.** It consumes the same
  `baby/remote/display` payload contract, so the ASCII fold protects it too, but
  its font was not audited here.

## 7. Decisions (resolved 2026-08-09)

1. **Version: 2026.4.12.** Patch, staying on the 4.x line, consistent with
   SDD-003's "no minors" note.
2. **AI summary follows the UI language.** Implemented as §3.7. Chosen over
   leaving it English and over a separate `summary_language` option, so there is
   one language knob rather than two.
3. **Dutch, Spanish and French ship in 2026.4.12 as machine translations.**
   Extended from Dutch alone on 2026-08-09. Labelling, the hand-checked
   `device.*` carve-out and the per-language reviewer map are in §3.8. Chosen
   over waiting on a volunteer's turnaround.
4. **In-UI language picker: build it.** Reversed 2026-08-09 after the design
   review, then moved from the footer to the **header** with **flags** in the
   same round. Per-browser via `localStorage`, flag paired with endonym, with an
   `Automatic` entry so nobody can strand themselves. Specced in §3.5.1. It
   overrides the add-on option for the **web UI only**; the device stays on
   `cfg.language`.
5. **In-app translation editor: build it.** Added 2026-08-09. Full-screen modal
   off the language dropdown, overrides persisted to `/data/i18n/<lang>.json`,
   server-validated, with a JSON export. Specced in §3.9. It is what makes a
   non-programmer's contribution possible without a PR, and it doubles as the
   fastest path for your own Spanish rewrite.

Scope was fixed the same day: web UI plus device OLED and reminders. The HA
Configuration panel and MQTT entity names were both explicitly declined (§6).

## 8. Acceptance criteria

- A fresh install with `language: auto` and a Dutch browser shows a Dutch UI.
- `language: nl` forces Dutch regardless of browser, including OLED rows.
- Picking a language from the header re-renders the UI in place, with no reload
  and no loss of an open journal edit, and survives a refresh.
- Picking `Automatic` clears the override and returns to the add-on option.
- Two browsers against the same install can sit on different languages, and
  neither changes what the OLED shows.
- Every picker entry shows a flag **and** an endonym, and the menu is still
  usable where regional-indicator emoji fall back to letter pairs.
- Editing a string in the editor and saving changes the UI immediately and
  survives an add-on restart and an add-on **update**, since it lives in `/data`.
- Editing a `device.*` string to 22 characters blocks Save in the UI and is
  rejected with a 400 if posted directly.
- Correcting an OLED string in the editor changes what the remote prints on its
  next refresh, with no rebuild and no release.
- Export produces a file that drops straight into `i18n/` and passes CI.
- Spanish is reviewable end to end by you in the editor without a code change.
- Deleting a random key from `nl.json` fails CI on the completeness test.
- Adding an accented character to a `device.*` value fails CI on the ASCII test.
- With `nl.json` absent, everything renders English and nothing errors.
- Adding a new language requires **zero** changes to `.js`, `.py`, or `.html`.
- With `language: en`, the summary prompt sent to the relay is byte-identical to
  2026.4.11's. With `language: nl`, it carries exactly one appended line.
- All 12 `device.*` Dutch strings pass the ASCII and 21-character tests and have
  been read by a human before tagging.
- Existing 50 tests stay green.
