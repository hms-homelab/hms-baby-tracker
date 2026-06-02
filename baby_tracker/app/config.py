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
            notify_targets=opts.get("notify_targets") or _split(env.get("NOTIFY_TARGETS")),
            data_dir=Path(env.get("DATA_DIR", "/data")),
            database_url=opts.get("database_url") or env.get("DATABASE_URL") or None,
            mqtt_host=opts.get("mqtt_host") or env.get("MQTT_HOST"),
            mqtt_port=int(opts.get("mqtt_port", env.get("MQTT_PORT", 1883))),
            mqtt_username=opts.get("mqtt_username") or env.get("MQTT_USERNAME"),
            mqtt_password=opts.get("mqtt_password") or env.get("MQTT_PASSWORD"),
            mqtt_enabled=_as_bool(env.get("MQTT_ENABLED", "1")),
            supervisor_token=env.get("SUPERVISOR_TOKEN"),
        )


def _split(val: str | None) -> list[str]:
    return [s.strip() for s in val.split(",") if s.strip()] if val else []


def _as_bool(val: str | None) -> bool:
    return str(val).lower() in ("1", "true", "yes", "on")
