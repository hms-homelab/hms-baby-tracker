#!/usr/bin/env bashio
# shellcheck shell=bash
#
# Baby Tracker add-on entrypoint.
#
# All user options (timezone, pump_hours, notify_targets, mqtt_*, database_url)
# are read by app/config.py directly from /data/options.json — NO Supervisor
# API call, so no "forbidden"/403 noise.
#
# The ONLY time we touch the Supervisor services API is to auto-discover the
# Mosquitto add-on, and only when the user has NOT set mqtt_host. If an external
# broker is configured (e.g. EMQX), we skip the probe entirely — so setups
# where that API is restricted stay completely quiet.

set -e

# Read the configured broker host straight from options (python3 ships in the
# base-python image; no jq, no API).
MQTT_HOST_OPT="$(python3 -c 'import json;print(json.load(open("/data/options.json")).get("mqtt_host") or "")' 2>/dev/null || true)"

if [ -n "${MQTT_HOST_OPT}" ]; then
    echo "[baby-tracker] using configured mqtt_host=${MQTT_HOST_OPT}"
elif command -v bashio >/dev/null 2>&1 && bashio::services.available "mqtt" 2>/dev/null; then
    export MQTT_HOST="$(bashio::services mqtt 'host' 2>/dev/null)"
    export MQTT_PORT="$(bashio::services mqtt 'port' 2>/dev/null)"
    export MQTT_USERNAME="$(bashio::services mqtt 'username' 2>/dev/null)"
    export MQTT_PASSWORD="$(bashio::services mqtt 'password' 2>/dev/null)"
    echo "[baby-tracker] auto-discovered Mosquitto MQTT at ${MQTT_HOST}:${MQTT_PORT}"
else
    echo "[baby-tracker] no mqtt_host set and no Mosquitto service; MQTT bridge disabled"
fi

cd /app
exec uvicorn app.main:app --host 0.0.0.0 --port 8099
