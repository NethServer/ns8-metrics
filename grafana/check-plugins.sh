#!/bin/sh

#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

# Run inside the built Grafana image by build-images.sh: start Grafana
# and check that it loads every bundled plugin with a valid signature
# made by Grafana Labs.
# A plugin with a broken signature, or not compatible with this Grafana
# version, is not loaded and fails the check.

bundled_dir=/usr/share/grafana/data/plugins-bundled
port=3999
api="http://127.0.0.1:${port}/api"

GF_PATHS_DATA=$(mktemp -d)
GF_PATHS_LOGS=$(mktemp -d)
export GF_PATHS_DATA GF_PATHS_LOGS
export GF_SERVER_HTTP_ADDR=127.0.0.1
export GF_SERVER_HTTP_PORT=${port}
export GF_AUTH_ANONYMOUS_ENABLED=true
export GF_PLUGINS_PREINSTALL_DISABLED=true
export GF_ANALYTICS_REPORTING_ENABLED=false
export GF_ANALYTICS_CHECK_FOR_UPDATES=false
export GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false
# Log only warnings and errors, so the log tail shows why a check failed
export GF_LOG_LEVEL=warn

grafana server --homepath=/usr/share/grafana >"${GF_PATHS_LOGS}/check.log" 2>&1 &
grafana_pid=$!

fail() {
    echo "ERROR: $*" >&2
    echo "--- Grafana log:" >&2
    # Show plugin messages, or the last lines if there are none
    grep 'logger=plugins' "${GF_PATHS_LOGS}/check.log" >&2 \
        || tail -n 20 "${GF_PATHS_LOGS}/check.log" >&2
    kill "${grafana_pid}" 2>/dev/null
    exit 1
}

# Wait for the API to be ready
tries=0
until wget -q -O /dev/null "${api}/health" 2>/dev/null; do
    tries=$((tries + 1))
    [ "${tries}" -le 120 ] || fail "Grafana did not start"
    kill -0 "${grafana_pid}" 2>/dev/null || fail "Grafana exited during startup"
    sleep 1
done

errors=0
for dir in "${bundled_dir}"/*/; do
    id=$(basename "${dir}")
    settings=$(wget -q -O - "${api}/plugins/${id}/settings" 2>/dev/null)
    if echo "${settings}" | grep -q '"signature":"valid"' \
        && echo "${settings}" | grep -q '"signatureType":"grafana"'; then
        echo "Plugin ${id}: loaded, signed by Grafana Labs"
    else
        echo "ERROR: plugin ${id} is not loaded or not signed by Grafana Labs" >&2
        errors=$((errors + 1))
    fi
done

[ "${errors}" -eq 0 ] || fail "${errors} bundled plugin(s) failed the check"
kill "${grafana_pid}"
