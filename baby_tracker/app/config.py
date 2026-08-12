"""Runtime configuration for the Baby Tracker app.

In an HA add-on the Supervisor writes user options to /data/options.json and
injects SUPERVISOR_TOKEN + MQTT service credentials into the environment. For
local dev everything falls back to environment variables / sane defaults so the
app runs with plain `uvicorn app.main:app`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Modules the UI can be told to hide (SDD-005). `tab.baby` is deliberately
# absent: something has to stay navigable, and Baby is the home tab.
HIDEABLE_MODULES = (
    "tab.get_ready", "tab.contractions", "tab.health", "tab.growth", "tab.supplies",
    "group.feed", "group.pump", "group.diaper", "group.other",
    "feed.breast", "feed.bottle", "feed.solid",
    "pump.left", "pump.right",
    "diaper.pee", "diaper.poop", "diaper.both", "diaper.change",
    "sleep", "bath", "medicine", "tummy_time",
    "card.summary", "card.manual",
)

OPTIONS_PATH = Path(os.environ.get("OPTIONS_PATH", "/data/options.json"))

# Default AI daily-summary instruction (SDD-003). Pre-filled + editable; the code
# always appends the de-identified digest, so edits change tone, not the data.
DEFAULT_SUMMARY_PROMPT = (
    "You are a warm, encouraging newborn-care assistant. From today's anonymized "
    "activity, write a 2-3 sentence plain-language recap for a tired parent. "
    "Gently note anything that stands out (a longer gap between feeds, fewer "
    "diapers than yesterday, a fever) without alarming, diagnosing, or giving "
    "medical advice. No names, and do not use em-dashes. Respond with only the recap."
)


def _load_options() -> dict:
    try:
        return json.loads(OPTIONS_PATH.read_text())
    except (OSError, ValueError):
        return {}


@dataclass
class Config:
    timezone: str = "America/New_York"
    pump_hours: float = 2.0
    feed_hours: float = 3.0
    # UI: which tab the Ingress SPA opens on (get_ready|baby|contractions|
    # health|growth|supplies). Changeable so an install can lead with
    # contractions/get_ready pre-birth and switch to baby after.
    default_tab: str = "baby"
    # Supplies: local hour (0-23) for the daily low/refill-due reminder sweep.
    supply_reminder_hour: int = 9
    # Get Ready checklist: local hour to auto-uncheck the list. 0 = off (the
    # seeded items are mostly one-time prep, so daily reset is opt-in).
    checklist_reset_hour: int = 0
    # Health tab: a logged temperature at/above this (in °C) is flagged as a
    # fever in the UI (°F entries are converted before comparing).
    fever_threshold_c: float = 38.0
    # UI unit defaults: "imperial" (°F, lb/oz, in) or "metric" (°C, kg, cm).
    # Per-entry unit is still stored, so this only sets the default pickers.
    measurement_system: str = "imperial"
    # UI + Baby Remote language (SDD-004). "auto" lets each browser follow its
    # own Accept-Language; any catalog code (en, nl, es, fr) forces it. NOTE
    # this is also the ONLY control for the device's OLED text, which is
    # rendered server-side: a browser-side picker cannot move it, because one
    # remote serves the whole household. Under "auto" the device uses English.
    language: str = "auto"
    # UI modules to hide (SDD-005), e.g. ["tab.contractions", "feed.bottle"].
    # A HIDE-list, not an enable-list: the default is empty so an existing
    # install is untouched, and a module added in a later version shows up on
    # its own instead of going missing from someone's saved selection. Purely
    # presentational. Hidden event types are still accepted over REST and MQTT,
    # so the Baby Remote and existing automations keep working.
    hidden_modules: list[str] = field(default_factory=list)
    # AI daily summaries (SDD-003). Opt-in: OFF by default so nothing leaves the
    # add-on until the user explicitly enables it (a 3rd-party call, even
    # de-identified, should never be on without consent). Hosted proxy is live at
    # babytracker.shmaestro.com; a first-run in-app notice explains the trade-off.
    summary_enabled: bool = False
    summary_provider: str = "hosted"   # hosted|ollama|anthropic|openai|gemini
    summary_hour: int = 6              # local hour for the auto digest; 0 = on-demand only
    summary_daily_cap: int = 2
    summary_hosted_url: str = "https://babytracker.shmaestro.com"
    summary_ollama_url: str = "http://192.168.2.5:11434"
    # Base URL for the `openai` provider. Blank = api.openai.com. Set it to
    # reach any OpenAI-compatible service (OpenRouter, Ollama, LiteLLM, Groq).
    summary_openai_url: str = ""
    summary_model: str = "gpt-oss:120b-cloud"
    summary_api_key: str = ""          # for anthropic/openai/gemini
    summary_prompt: str = DEFAULT_SUMMARY_PROMPT
    # storage
    data_dir: Path = Path(os.environ.get("DATA_DIR", "/data"))
    database_url: str | None = None  # optional external Postgres (unused in v1 SQLite path)
    # mqtt (Supervisor injects these for `services: mqtt:need`)
    mqtt_host: str | None = None
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_enabled: bool = True
    # supervisor
    supervisor_token: str | None = None
    # contraction AI assessment (Ollama) — opt-in; off for parents w/o an LLM.
    ollama_enabled: bool = False
    ollama_url: str = "http://192.168.2.5:11434"
    ollama_model: str = "gpt-oss:120b-cloud"
    ollama_timeout: float = 30.0
    ollama_prompt: str | None = None  # optional override (see assessment.build_prompt)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "baby.db"

    @classmethod
    def load(cls) -> "Config":
        opts = _load_options()
        env = os.environ
        return cls(
            timezone=opts.get("timezone") or env.get("TZ", "America/New_York"),
            pump_hours=float(opts.get("pump_hours", env.get("PUMP_HOURS", 2.0))),
            feed_hours=float(opts.get("feed_hours", env.get("FEED_HOURS", 3.0))),
            default_tab=(opts.get("default_tab") or env.get("DEFAULT_TAB") or "baby"),
            supply_reminder_hour=int(opts.get("supply_reminder_hour",
                                              env.get("SUPPLY_REMINDER_HOUR", 9))),
            checklist_reset_hour=int(opts.get("checklist_reset_hour",
                                              env.get("CHECKLIST_RESET_HOUR", 0))),
            fever_threshold_c=float(opts.get("fever_threshold_c",
                                             env.get("FEVER_THRESHOLD_C", 38.0))),
            measurement_system=(opts.get("measurement_system")
                                or env.get("MEASUREMENT_SYSTEM") or "imperial"),
            language=(opts.get("language") or env.get("LANGUAGE") or "auto"),
            hidden_modules=_as_list(opts.get("hidden_modules")
                                    or env.get("HIDDEN_MODULES")),
            summary_enabled=_as_bool(env.get("SUMMARY_ENABLED")
                                     or ("1" if opts.get("summary_enabled") else "0")),
            summary_provider=(opts.get("summary_provider")
                              or env.get("SUMMARY_PROVIDER") or "hosted"),
            summary_hour=int(opts.get("summary_hour", env.get("SUMMARY_HOUR", 6))),
            summary_daily_cap=int(opts.get("summary_daily_cap",
                                           env.get("SUMMARY_DAILY_CAP", 2))),
            summary_hosted_url=(opts.get("summary_hosted_url")
                                or env.get("SUMMARY_HOSTED_URL") or ""),
            summary_ollama_url=(opts.get("summary_ollama_url")
                                or env.get("SUMMARY_OLLAMA_URL")
                                or "http://192.168.2.5:11434"),
            summary_openai_url=(opts.get("summary_openai_url")
                                or env.get("SUMMARY_OPENAI_URL") or ""),
            summary_model=(opts.get("summary_model") or env.get("SUMMARY_MODEL")
                           or "gpt-oss:120b-cloud"),
            summary_api_key=(opts.get("summary_api_key")
                             or env.get("SUMMARY_API_KEY") or ""),
            summary_prompt=(opts.get("summary_prompt") or env.get("SUMMARY_PROMPT")
                            or DEFAULT_SUMMARY_PROMPT),
            data_dir=Path(env.get("DATA_DIR", "/data")),
            database_url=opts.get("database_url") or env.get("DATABASE_URL") or None,
            # Supervisor service (env vars exported by run.sh) is PRIMARY; the
            # mqtt_host option is the FALLBACK for an external broker (e.g. EMQX).
            mqtt_host=env.get("MQTT_HOST") or opts.get("mqtt_host") or None,
            mqtt_port=int(env.get("MQTT_PORT") or opts.get("mqtt_port") or 1883),
            mqtt_username=env.get("MQTT_USERNAME") or opts.get("mqtt_username"),
            mqtt_password=env.get("MQTT_PASSWORD") or opts.get("mqtt_password"),
            mqtt_enabled=_as_bool(env.get("MQTT_ENABLED", "1")),
            # Accept the legacy HASSIO_TOKEN name too (issue #3): some Supervisor
            # versions inject the token under that older variable.
            supervisor_token=env.get("SUPERVISOR_TOKEN") or env.get("HASSIO_TOKEN"),
            ollama_enabled=_as_bool(env.get("OLLAMA_ENABLED")
                                    or ("1" if opts.get("ollama_enabled") else "0")),
            ollama_url=(env.get("OLLAMA_URL") or opts.get("ollama_url")
                        or "http://192.168.2.5:11434"),
            ollama_model=(env.get("OLLAMA_MODEL") or opts.get("ollama_model")
                          or "gpt-oss:120b-cloud"),
            ollama_timeout=float(env.get("OLLAMA_TIMEOUT")
                                 or opts.get("ollama_timeout") or 30.0),
            ollama_prompt=(env.get("OLLAMA_PROMPT") or opts.get("ollama_prompt") or None),
        )


def _as_bool(val: str | None) -> bool:
    return str(val).lower() in ("1", "true", "yes", "on")


def _as_list(val) -> list[str]:
    """Normalize the hidden_modules option to a clean list of module ids.

    Supervisor hands us a real list; the env fallback is comma separated. Unknown
    ids are dropped rather than passed through, so a typo cannot silently travel
    to the browser and hide nothing while looking configured.
    """
    if isinstance(val, str):
        items = val.split(",")
    elif isinstance(val, (list, tuple)):
        items = list(val)
    else:
        return []
    seen, out = set(), []
    for item in items:
        mod = str(item).strip().lower()
        if mod in HIDEABLE_MODULES and mod not in seen:
            seen.add(mod)
            out.append(mod)
    return out
