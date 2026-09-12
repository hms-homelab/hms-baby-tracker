# SDD-003 — AI Daily Summaries

Status: **APPROVED** — 2026-07-05 (decisions in §4). Pending build.
Date: 2026-07-05
Component: `baby_tracker/` (app + web) + a **separate hosted proxy server** (§5)
Ships as: **2026.4.3** (patch — staying on the 4.0 line; no minors)

## 1. Goal

A warm, plain-language **AI recap of the baby's day**, shown as a section inside
the summary card. It generalizes today's contraction-only `assessment.py` into a
whole-day digest that reads **all** event types (feeds, diapers, sleep, health,
growth, contractions) and gently flags anything that stands out.

- **Cadence:** one **automatic** digest each morning (default 06:00 local) **plus
  an on-demand "Summarize now" button**. Both count toward a **2/day cap** that
  resets at local midnight.
- **Providers (pluggable):** a **hosted proxy** (the shipped default), a
  **self-hosted Ollama**, or a **big API** — Claude (Anthropic), Gemini (Google)
  or ChatGPT (OpenAI).
- **Privacy:** the prompt only ever contains a **de-identified structured digest**
  (aggregate counts + trends). **No names, no note text, no special-note text** —
  so even the default hosted server never sees identifiable data.
- **Tone:** warm recap + gentle heads-up (e.g. "quiet day, 4 feeds, slept 3h;
  diaper count a bit low vs yesterday"), no diagnosis or medical advice.

## 2. Background — what exists

- `app/assessment.py` already does a **contraction-specific** Ollama assessment
  (`call_ollama`, `maybe_assess`) fired on each `contraction` event, published to
  `baby/assessment` + `sensor.baby_contraction_assessment`. This SDD **keeps that
  as-is** (real-time labor tool) and adds a **separate daily digest**, sharing a
  new provider layer.
- Prior art: **CpapDash `SummaryService`** — provider=ollama (Cloud Pro relay),
  per-user/day caps via an `ai_usage` table, ~277 prompt + 501 output tokens per
  summary. This is the template for caps + provider config + the relay.
- The add-on already has the enriched `state_stats()` roll-up (contractions
  today, checklist, low supplies) and per-metric series — good raw material for
  the digest.

## 3. Design

### 3.1 Provider abstraction — `app/llm.py` (new)

One `generate(prompt) -> str` interface with a driver per `summary_provider`:

| provider | endpoint | auth | notes |
|---|---|---|---|
| `hosted` (default) | `summary_hosted_url` | install token | Albin's throttled Ollama proxy (§5) |
| `ollama` | `summary_ollama_url` `/api/generate` | none | self-hosted; reuses assessment.call_ollama shape |
| `anthropic` | Messages API | `summary_api_key` | default model e.g. `claude-haiku-4-5` (cheap) |
| `openai` | Chat Completions | `summary_api_key` | default model e.g. `gpt-5-mini` |
| `gemini` | generateContent | `summary_api_key` | default model e.g. `gemini-2.5-flash` |

Only one provider is active at a time, so a single `summary_api_key` +
`summary_model` covers the three API options. `app/assessment.py` may later
migrate to `llm.py` (not required for v1).

### 3.2 The de-identified digest (privacy core)

`build_digest(db, cfg, day)` returns a **structured, anonymized** dict — the only
thing ever sent to a provider:

- counts today by type/subtype (feeds breast/bottle/solid, diapers pee/poop/…,
  pumps, baths, tummy, medicines, contractions)
- sleep: total minutes, longest stretch, currently sleeping?
- feeding: average interval **today vs yesterday** (drives "gap lengthening")
- diapers: count **today vs yesterday** (drives "a bit low")
- health: last temperature (value + unit + fever bool)
- growth: latest weight/length/head + delta since previous
- **excluded:** all `note` / special-note text, event ids, exact wall-clock
  timestamps beyond hour buckets, anything free-text or naming.

The digest is rendered into the prompt as labelled numbers, never raw rows.

### 3.3 Prompt (warm recap + gentle flags) — editable in config

The **instruction/persona** is a first-class config option, **`summary_prompt`,
shipped pre-filled** with the default below (not blank), so it's visible in the
add-on Configuration UI and users can retune the tone to their comfort:

> "You are a warm, encouraging newborn-care assistant. From today's anonymized
> activity, write a 2–3 sentence plain-language recap for a tired parent. Gently
> note anything that stands out (a longer gap between feeds, fewer diapers than
> yesterday, a fever) without alarming, diagnosing, or giving medical advice. No
> names. Respond with only the recap."

**The code always appends the de-identified digest itself** (§3.2) after whatever
instruction the user sets — i.e. `prompt = summary_prompt + "\n\nToday's data:\n"
+ render(digest)`. So editing `summary_prompt` changes the voice/instructions but
**never changes what data is sent** (privacy stays code-controlled; a user can't
inject notes/names via the prompt). A "Reset to default" is just clearing the
field back to the shipped text.

### 3.4 Cap + storage

New table **`baby_summaries`** (dual SQLite/Postgres): `id, text, provider,
source (auto|manual), generated_at, day (local date str)`.

- **Cap:** `summary_daily_cap` (default 2) counted as rows for today's local
  `day`. Enforced in the add-on before any provider call; the **hosted server
  also enforces it** server-side (defense in depth).
- Latest row feeds the UI + MQTT. History beyond "latest" is out of scope for v1
  (rows are kept, surfaced later).

### 3.5 API

- `GET /api/summary` → `{latest:{text,generated_at,source}, used_today, cap,
  can_generate, enabled}`
- `POST /api/summary` → generate now (source=manual); 200 with the new summary,
  or **429** `{error:"cap", used_today, cap}` when capped / provider error.
- `/api/config` → add `summary_enabled` so the UI shows/hides the section.

### 3.6 Scheduler

APScheduler daily cron at `summary_hour` local (default 6; `0` = on-demand only)
→ `generate(source="auto")` when enabled and under cap.

### 3.7 UI (summary-card section)

Under the roll-up lines in the summary card:
```
🤖  "Calm night — 3 feeds, slept 3h, no fever. Diaper
     count is a touch low vs yesterday; keep an eye out."
     generated 6:04 AM · 1/2 today      [ Summarize now ]
```
- Loading state while generating; button disabled at cap (tooltip: "2/2 today,
  resets at midnight"). Hidden entirely when `summary_enabled` is false.

### 3.8 MQTT / HA

Publish the latest summary to **`baby/summary`** (retained `{text, time,
source}`) + a discovery **`sensor.baby_summary`**, mirroring the contraction
assessment sensors so HA dashboards get it too.

### 3.10 First-run AI notice (privacy disclosure)

Because AI summaries ship **on-by-default** and send data to the hosted service,
the UI shows a **one-time, dismissible banner** on first load:

> 🤖 **AI summaries are on.** Each day an *anonymized* recap of your baby's stats
> (counts, sleep, trends — never names or notes) is sent to the hosted summary
> service. Prefer to keep it local or off? Switch the provider or disable it in
> the add-on **Configuration**.  **[ Got it ]**

- "Seen" is tracked in `localStorage` (`bt_ai_notice_seen`) — shown once per
  device (so a partner's phone also gets the heads-up), never again after
  dismissal. Also suppressed entirely when `summary_enabled` is false.
- We can't deep-link into HA add-on options reliably, so the banner just names
  where to go (Settings → the add-on → Configuration).

### 3.9 Config options

`summary_enabled` (bool, default **true**), `summary_provider`
(hosted|ollama|anthropic|openai|gemini, default `hosted`), `summary_hour` (int,
default 6), `summary_daily_cap` (int, default 2), `summary_hosted_url` (str,
default = the proxy URL), `summary_ollama_url` (str), `summary_model` (str),
`summary_api_key` (password), and **`summary_prompt` (str, pre-filled with the
default instruction from §3.3, editable)**. An `install_token` (random UUID) is
minted on first run and stored in `/data` (not PII) for hosted rate-limiting.

## 4. Decisions (resolved 2026-07-05)

1. **Cadence** → auto daily (06:00) **+** on-demand; both count to the 2/day cap. ✅
2. **UI home** → a section inside the summary card. ✅
3. **Focus** → warm recap + gentle flags (parent tone, no diagnosis). ✅
4. **Default** → **hosted server on by default** (see §5 dependency + §6 risk). ✅
5. **Privacy** → de-identified structured digest only; no notes/names ever sent. ✅
6. **Providers** → hosted / ollama / anthropic / openai / gemini. ✅
7. **Prompt** → `summary_prompt` is a config option **pre-filled with the default
   instruction** (editable to taste); the code always appends the de-identified
   digest, so edits change tone but never what data is sent. ✅
8. **On-by-default + first-run notice** → ship hosted-on out of the box, with a
   one-time dismissible in-app notice (§3.10) pointing to Configuration to switch
   provider or disable. ✅

## 5. Hosted proxy server (separate deliverable)

A small service **Albin runs** (own repo; can reuse the CpapDash Ollama Cloud
relay). Contract the add-on codes against:

- `POST {summary_hosted_url}/summary` body `{install_token, prompt}` →
  `200 {summary, remaining}` or `429 {error:"cap", reset_at}`. The add-on builds
  the full (de-identified) prompt client-side so `summary_prompt` applies to the
  hosted path too; the server is a **dumb rate-limited relay**.
- **Rate-limit 2/day per `install_token`** (server-side, authoritative).
- Forwards the prompt to a **dedicated Ollama account** (its own, not shared with
  CpapDash's relay), returns text only. Sees only the de-identified prompt + a
  random token. No accounts, no PII.

**Sequencing (CHOSEN):** **build the add-on side first**, ship with
`summary_enabled` default **false** (feature dormant, harmless). Stand up the
server + its dedicated account afterward, then flip `summary_enabled` default to
**true** and set the real `summary_hosted_url` in one commit — that's the "on out
of the box" moment (§4.8 first-run notice starts showing then).

## 6. Out of scope / risks
- Server implementation (separate repo — contract only here), summary history UI,
  weekly/monthly digests, streaming, multi-baby.
- **Privacy** (handled): a public MIT add-on defaulting ON to send data to an
  external server. Mitigations spec'd: payload is de-identified (§3.2), a
  **first-run in-app notice** (§3.10) discloses it and points to opt-out, DOCS
  call it out, and provider/disable are one option away. Default stays hosted-on
  per §4.4 + §4.8.

## 7. Acceptance / test plan
- Digest contains only aggregate numbers — assert **no note/name/free-text** ever
  serialized into the prompt (unit test over `build_digest`).
- Cap: 3rd generation in a local day returns 429; resets next local day.
- Auto cron fires at `summary_hour`; on-demand button generates + updates the
  card + `baby/summary`.
- Each provider driver builds the right request (unit-test with a stub client);
  hosted driver sends `install_token`.
- `summary_enabled=false` hides the UI section and skips the cron.
- Both backends: `baby_summaries` auto-creates; existing tables untouched.

## 8. Versioning
`VERSION` + CHANGELOG → **2026.4.3** (patch — staying on the 4.0 line; no minors).
Standard two-phase release. Tag on request; ship the feature **on-by-default only
after** the hosted proxy is live (§5).
