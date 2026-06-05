#!/usr/bin/env bashio
# shellcheck shell=bash
#
# Baby Tracker add-on entrypoint.
#
# MQTT broker resolution — Supervisor auto-discovery is the PRIMARY path and the
# default for ~90% of installs: with the Mosquitto add-on (or any add-on that
# provides the `mqtt` service) the Supervisor hands us host/port/user/pass and
# NOTHING needs configuring. The `mqtt_host` option is a FALLBACK, used only when
# the Supervisor has no MQTT service to offer — e.g. an EXTERNAL broker like EMQX
# on another host. Final precedence lives in app/config.py: the env vars exported
# here (from the Supervisor service) win; the mqtt_host option fills in only when
# they're absent.

set -e

if bashio::services.available "mqtt" 2>/dev/null; then
    # PRIMARY: broker supplied by the Supervisor (Mosquitto add-on, etc.)
    export MQTT_HOST="$(bashio::services mqtt 'host' 2>/dev/null)"
    export MQTT_PORT="$(bashio::services mqtt 'port' 2>/dev/null)"
    export MQTT_USERNAME="$(bashio::services mqtt 'username' 2>/dev/null)"
    export MQTT_PASSWORD="$(bashio::services mqtt 'password' 2>/dev/null)"
    echo "[baby-tracker] MQTT: using the Supervisor-provided broker at ${MQTT_HOST}:${MQTT_PORT} (auto-discovered, no config needed)"
else
    # FALLBACK: external broker from the mqtt_host option (read by app/config.py).
    MQTT_HOST_OPT="$(python3 -c 'import json;print(json.load(open("/data/options.json")).get("mqtt_host") or "")' 2>/dev/null || true)"
    if [ -n "${MQTT_HOST_OPT}" ]; then
        echo "[baby-tracker] MQTT: no Supervisor broker; using external mqtt_host=${MQTT_HOST_OPT} (fallback)"
    else
        echo "[baby-tracker] MQTT: no Supervisor broker and no mqtt_host set — bridge disabled (install Mosquitto, or set mqtt_host)"
    fi
fi

cd /app
exec uvicorn app.main:app --host 0.0.0.0 --port 8099
