#!/usr/bin/env bash
# on_movie_start hook: cam_notifier.sh <filepath> <motion_timestamp> <event_number>
set -euo pipefail

FILEPATH="$1"
SEQUENCE="$3"
CONFIG="/etc/cam_motion/config.toml"
LOG="/var/log/cam_motion/notifier.log"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

parse_toml_string() {
    # parse_toml_string <section> <key> <file>
    # Extracts the value of key = "value" under [section], stops at next section header.
    sed -n "/^\[$1\]/,/^\[/{ s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*\"\(.*\)\"/\1/p }" "$3" | head -1
}

parse_toml_int() {
    sed -n "/^\[$1\]/,/^\[/{ s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*\([0-9]*\)[[:space:]]*/\1/p }" "$3" | head -1
}

CAMERA_NAME=$(parse_toml_string camera name "$CONFIG")
WEBHOOK_URL=$(parse_toml_string webhook url "$CONFIG")
TIMEOUT=$(parse_toml_int webhook timeout_seconds "$CONFIG")
TIMEOUT="${TIMEOUT:-5}"

CLIP=$(basename "$FILEPATH")
TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

PAYLOAD=$(printf '{"camera":"%s","timestamp":"%s","sequence":%s,"clip":"%s"}' \
    "$CAMERA_NAME" "$TIMESTAMP" "$SEQUENCE" "$CLIP")

HTTP_STATUS=$(curl -s -o /dev/null -w '%{http_code}' \
    --max-time "$TIMEOUT" \
    -X POST \
    -H 'Content-Type: application/json' \
    -d "$PAYLOAD" \
    "$WEBHOOK_URL") || HTTP_STATUS="000"

if [ "${HTTP_STATUS}" -ge 200 ] 2>/dev/null && [ "${HTTP_STATUS}" -lt 300 ] 2>/dev/null; then
    log "INFO Webhook OK status=${HTTP_STATUS} clip=${CLIP} seq=${SEQUENCE}"
else
    log "ERROR Webhook failed status=${HTTP_STATUS} clip=${CLIP} seq=${SEQUENCE}"
fi
