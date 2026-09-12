# SDD-005: OpenAI-compatible base URL + module visibility

Status: IMPLEMENTED, unreleased (version not yet assigned)
Origin: discussion #8 (saitheexplorer)
Baseline: 2026.4.12

## 1. Problem

Two requests from a user who tracked labor with the contraction module and now
uses the app for feeds and diapers at home.

**1.1 No way to point the OpenAI provider at an OpenAI-compatible service.**
`app/llm.py::_openai` hardcodes `https://api.openai.com/v1/chat/completions`.
The `ollama` provider is configurable but appends the native `/api/generate`
path, so it cannot talk to an OpenAI-shaped endpoint either. Result: OpenRouter,
Ollama Cloud, LiteLLM, LM Studio, vLLM, Groq, Together and every other
OpenAI-compatible host is unreachable, even though the wire format already matches.

**1.2 Every module is always visible.**
Six tabs and fourteen quick-action buttons render unconditionally. Post-birth,
Get Ready and Contractions are dead weight; a breastfeeding-only household has no
use for Bottle or Solids; not everyone tracks sleep. On a phone this is wasted
screen and mis-tap risk.

## 2. Non-goals

- No per-user or per-device visibility. One household, one configuration.
- No data deletion. Hiding is presentation only: rows already logged still appear
  in the log, MQTT topics and HA entities keep publishing.
- No new provider types. `openai` becomes "OpenAI-compatible", nothing is added.

## 3. Design

### 3.1 `summary_openai_url` (request 1.1)

New add-on option:

```yaml
options:
  summary_openai_url: ""
schema:
  # Base URL for the OpenAI-compatible provider. Blank = api.openai.com.
  # Point it at any OpenAI-shaped service: OpenRouter
  # (https://openrouter.ai/api/v1), Ollama (http://homeassistant.local:11434/v1),
  # Ollama Cloud (https://ollama.com/v1), LiteLLM, LM Studio, vLLM, Groq.
  summary_openai_url: str?
```

`config.py`: `summary_openai_url: str = ""`, loaded from
`opts["summary_openai_url"] or env["SUMMARY_OPENAI_URL"] or ""`.

`llm.py::_openai` resolves the endpoint:

```python
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"

def _openai_endpoint(cfg) -> str:
    base = (cfg.summary_openai_url or DEFAULT_OPENAI_BASE).rstrip("/")
    if base.endswith("/chat/completions"):
        return base                      # user pasted the full path
    if not base.endswith("/v1") and "/v1/" not in base + "/":
        base += "/v1"                    # user pasted the host only
    return base + "/chat/completions"
```

Tolerant on purpose. All three of `https://openrouter.ai/api/v1`,
`https://openrouter.ai/api`, and `.../v1/chat/completions` resolve to the same
call, because the exact shape a service documents varies and getting it wrong
surfaces as an opaque 404.

Behaviour is unchanged when the option is blank, so no migration and no bump to
existing installs beyond the new field.

Docs: DOCS.md gains a short "Using an OpenAI-compatible service" table with
base URL and a working model id per service.

### 3.2 `hidden_modules` (request 1.2)

New add-on option, a repeatable dropdown:

```yaml
options:
  hidden_modules: []
schema:
  hidden_modules:
    - list(tab.get_ready|tab.contractions|tab.health|tab.growth|tab.supplies|
           group.feed|group.pump|group.diaper|group.other|
           feed.breast|feed.bottle|feed.solid|pump.left|pump.right|
           diaper.pee|diaper.poop|diaper.both|diaper.change|
           sleep|bath|medicine|tummy_time|card.summary|card.manual)?
```

**Hide-list, not enable-list.** The default is empty, so an existing install
sees no change, and a module added in a future version shows up automatically
instead of silently missing because it was absent from someone's saved
`enabled_modules`.

`tab.baby` is deliberately not in the catalog. Something has to remain
navigable, and Baby is the home tab.

Server side:
- `config.py`: `hidden_modules: list[str] = field(default_factory=list)`,
  read from options, lowercased and stripped.
- `main.py::get_config`: return `hidden_modules`, and fall back
  `default_tab` to `baby` when the configured tab is hidden (the existing `valid`
  check gains `and tab not in hidden`).

Client side (`app.js`), one predicate `visible(id)` plus four call sites:
1. Tab bar: drop `<button data-tab>` and its panel when `tab.<id>` is hidden.
2. `GROUPS` render: filter each group's rows on the action id; when a group ends
   up empty, or `group.<name>` is hidden outright, hide the group title too.
   Sleep is one id (`sleep`) covering both start and end tiles, since a
   half-visible sleep pair is meaningless.
3. `manual-type` select: omit hidden event types from the backfill dropdown.
4. Summary card: `#sum-track` drops the Contractions and Ready figures when
   those tabs are hidden; `#sum-vitals` drops temperature or weight with
   `tab.health` / `tab.growth`; `card.summary` hides the AI block; `card.manual`
   hides the backfill card.

Nothing else changes. Hidden event types remain valid on `POST /api/log` and over
MQTT, so the ESP32 Baby Remote and existing HA automations keep working even if
the matching button is hidden in the web UI.

No new i18n strings: hiding removes markup, it does not add copy.

### 3.3 Rejected alternatives

- **Per-module booleans** (`tab_contractions: bool`, ...) render as tidy HA
  checkboxes but add 20+ options to the config form and need a default flip for
  every new module. Rejected on config-surface growth.
- **In-app settings modal.** Nicer (no add-on restart, and the language menu
  already sets a precedent) but it needs a writable settings store, an API, and
  its own UI. Worth doing later as a front end over the same `hidden_modules`
  list; not needed to answer the request.
- **Deleting hidden data.** Never. Hiding must be reversible.

## 4. Tests

`tests/test_sdd005.py`, 18 tests, suite total 116 passing:
- `openai_endpoint` resolution for blank, host only, `/api/v1`, `/v1`, trailing
  slash, a full `/chat/completions` path, and copy/paste whitespace.
- `_openai` posts to the resolved URL with the bearer header and the configured
  model (httpx stubbed).
- Default config still hits api.openai.com.
- `_as_list` lowercases, trims, dedupes, and drops unknown ids and `tab.baby`.
- `/api/config` echoes `hidden_modules`, defaults to empty, and falls back
  `default_tab` to `baby` only when the configured tab is hidden.
- `POST /api/event` with a hidden type still logs and still appears in `/api/log`.
- The `config.yaml` dropdown and `HIDEABLE_MODULES` cannot drift apart.

### 4.1 End-to-end (real server, real browser)

Run against the actual app with a Supervisor-shaped `options.json`, restarting
between each change the way the Supervisor does, driven with Playwright.

- Baseline: 6 tabs, 14 tiles, 15 manual-entry options, 8 summary figures.
- Hiding `tab.get_ready`, `tab.contractions`, `tab.growth`, `feed.bottle`,
  `feed.solid`, `group.pump`, `sleep`, `card.manual` leaves 3 tabs; Feed shows
  only Breast; the Pump group and its heading are gone; Other has no sleep
  tiles; the manual dropdown drops to 9 entries; the sleep row, backfill card
  and the pump/contraction/ready/weight figures disappear.
- `default_tab: contractions` with that tab hidden opens on Baby.
- The journal still lists the hidden types (Pump L, Contraction), and picking
  Bath in the filtered dropdown logs a bath, confirming the index pairing
  between the `<option>` list and the handler survives filtering.
- Clearing the option restores all 6 tabs, 14 tiles, 15 options and 8 figures.
- A stand-in OpenAI-compatible service on the OpenRouter path shape, configured
  host-only as `http://host/api`, receives `/api/v1/chat/completions` with the
  bearer and the custom model id, and the prompt is still counts-only.
- `card.summary` hides the AI block and its first-run notice while the API keeps
  reporting summaries as enabled.
- Zero console errors throughout.

Not run on a live Home Assistant: the Supervisor validates options against the
installed add-on's schema, so `hidden_modules` cannot be set there until an
image carrying this schema is published.

## 5. Rollout

1. Implement, full suite green, Playwright clean. **Done.**
2. CHANGELOG under Unreleased, DOCS.md for both options. **Done.**
3. Version, tag and ghcr publish: owner's call, not assigned here.
4. Reply on discussion #8 once released.
