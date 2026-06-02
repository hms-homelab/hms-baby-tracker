#!/usr/bin/env bashio
# shellcheck shell=bash
#
# Baby Tracker add-on entrypoint.
#
# Reads user options from /data/options.json and the MQTT service credentials
# discovered by the Supervisor, exports them as env vars (app/config.py reads
# both /data/options.json AND the environment), then launches uvicorn.
#
# Falls back to a POSIX sh + jq path when bashio is unavailable (non-HA / dev).

set -e

# ---------------------------------------------------------------------------
# bashio path (normal Home Assistant runtime)
# ---------------------------------------------------------------------------
if command -v bashio >/dev/null 2>&1 && bashio::supervisor.ping >/dev/null 2>&1; then

    export TZ="$(bashio::config 'timezone')"
    export PUMP_HOURS="$(bashio::config 'pump_hours')"
    export DATABASE_URL="$(bashio::config 'database_url')"

    # notify_targets is a list -> comma-join into NOTIFY_TARGETS
    NOTIFY_TARGETS=""
    for target in $(bashio::config 'notify_targets'); do
        if [ -z "${NOTIFY_TARGETS}" ]; then
            NOTIFY_TARGETS="${target}"
        else
            NOTIFY_TARGETS="${NOTIFY_TARGETS},${target}"
        fi
    done
    export NOTIFY_TARGETS

    # MQTT from the `mqtt:need` service discovery.
    if bashio::services.available "mqtt"; then
        export MQTT_HOST="$(bashio::services mqtt 'host')"
        export MQTT_PORT="$(bashio::services mqtt 'port')"
        export MQTT_USERNAME="$(bashio::services mqtt 'username')"
        export MQTT_PASSWORD="$(bashio::services mqtt 'password')"
        export MQTT_ENABLED=1
        bashio::log.info "MQTT service available at ${MQTT_HOST}:${MQTT_PORT}"
    else
        export MQTT_ENABLED=0
        bashio::log.warning "No MQTT service available; remote/notify bridge disabled"
    fi

    bashio::log.info "Starting Baby Tracker (TZ=${TZ}, pump_hours=${PUMP_HOURS})"

else
    # -----------------------------------------------------------------------
    # POSIX fallback (no bashio): read /data/options.json with jq
    # -----------------------------------------------------------------------
    OPTIONS=/data/options.json
    if [ -f "${OPTIONS}" ] && command -v jq >/dev/null 2>&1; then
        export TZ="$(jq -r '.timezone // "America/New_York"' "${OPTIONS}")"
        export PUMP_HOURS="$(jq -r '.pump_hours // 2' "${OPTIONS}")"
        export DATABASE_URL="$(jq -r '.database_url // ""' "${OPTIONS}")"
        export NOTIFY_TARGETS="$(jq -r '(.notify_targets // []) | join(",")' "${OPTIONS}")"
    fi
    # MQTT env may be injected by the Supervisor regardless.
    if [ -n "${MQTT_HOST:-}" ]; then
        export MQTT_ENABLED=1
    else
        export MQTT_ENABLED=0
    fi
    echo "Starting Baby Tracker (fallback mode, TZ=${TZ:-America/New_York})"
fi

cd /app
exec uvicorn app.main:app --host 0.0.0.0 --port 8099
