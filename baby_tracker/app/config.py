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

OPTIONS_PATH = Path(os.environ.get("OPTIONS_PATH", "/data/options.json"))


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
    notify_targets: list[str] = field(default_factory=list)
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
            notify_targets=opts.get("notify_targets") or _split(env.get("NOTIFY_TARGETS")),
            data_dir=Path(env.get("DATA_DIR", "/data")),
            database_url=opts.get("database_url") or env.get("DATABASE_URL") or None,
            # Supervisor service (env vars exported by run.sh) is PRIMARY; the
            # mqtt_host option is the FALLBACK for an external broker (e.g. EMQX).
            mqtt_host=env.get("MQTT_HOST") or opts.get("mqtt_host") or None,
            mqtt_port=int(env.get("MQTT_PORT") or opts.get("mqtt_port") or 1883),
            mqtt_username=env.get("MQTT_USERNAME") or opts.get("mqtt_username"),
            mqtt_password=env.get("MQTT_PASSWORD") or opts.get("mqtt_password"),
            mqtt_enabled=_as_bool(env.get("MQTT_ENABLED", "1")),
            supervisor_token=env.get("SUPERVISOR_TOKEN"),
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


def _split(val: str | None) -> list[str]:
    return [s.strip() for s in val.split(",") if s.strip()] if val else []


def _as_bool(val: str | None) -> bool:
    return str(val).lower() in ("1", "true", "yes", "on")
