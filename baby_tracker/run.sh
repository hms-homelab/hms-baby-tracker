#!/usr/bin/env bashio
# shellcheck shell=bash
#
# Baby Tracker add-on entrypoint.
#
# All user options (timezone, pump_hours, notify_targets, database_url, and the
# MQTT broker settings) are read by app/config.py directly from
# /data/options.json — NO Supervisor API call, so no "forbidden"/403 noise.
#
# The only thing we do here is a best-effort auto-discovery of the Mosquitto
# add-on's MQTT service (for users who run it). It is fully guarded so that an
# absent or forbidden services API stays silent. If you use an EXTERNAL broker
# (e.g. EMQX on another host), just set mqtt_host in the add-on Configuration —
# that takes precedence over auto-discovery.

set -e

if command -v bashio >/dev/null 2>&1 && bashio::services.available "mqtt" 2>/dev/null; then
    export MQTT_HOST="$(bashio::services mqtt 'host' 2>/dev/null)"
    export MQTT_PORT="$(bashio::services mqtt 'port' 2>/dev/null)"
    export MQTT_USERNAME="$(bashio::services mqtt 'username' 2>/dev/null)"
    export MQTT_PASSWORD="$(bashio::services mqtt 'password' 2>/dev/null)"
    echo "[baby-tracker] auto-discovered Mosquitto MQTT at ${MQTT_HOST}:${MQTT_PORT}"
else
    echo "[baby-tracker] no Mosquitto service; using mqtt_host from add-on options (if set)"
fi

cd /app
exec uvicorn app.main:app --host 0.0.0.0 --port 8099
