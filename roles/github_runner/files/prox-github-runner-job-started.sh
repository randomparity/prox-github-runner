#!/usr/bin/env bash
# ACTIONS_RUNNER_HOOK_JOB_STARTED hook for the prox-github-runner services.
#
# Writes a per-service active-job marker keyed by runner name under
# /run/prox-github-runner/jobs/<runner-name>. The marker is diagnostic only
# (it drives cleanup scheduling and health reporting); it is NOT the stop
# mechanism. A completing job removes only its own marker.
set -euo pipefail

marker="${PROX_RUNNER_JOB_MARKER:-/run/prox-github-runner/jobs/${RUNNER_NAME:-unknown}}"

mkdir -p "$(dirname "${marker}")"
date -u +%s >"${marker}"
logger -t prox-github-runner-job -- "job started; marker ${marker}" 2>/dev/null || true
