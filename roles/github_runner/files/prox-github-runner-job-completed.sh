#!/usr/bin/env bash
# ACTIONS_RUNNER_HOOK_JOB_COMPLETED hook for the prox-github-runner services.
#
# Removes this service's own active-job marker, then triggers the shared
# workspace/Docker cleanup (which is itself flock-serialized and age-gated, so
# calling it after every job is safe and cheap).
set -euo pipefail

marker="${PROX_RUNNER_JOB_MARKER:-/run/prox-github-runner/jobs/${RUNNER_NAME:-unknown}}"
cleanup="${PROX_RUNNER_CLEANUP:-/usr/local/bin/prox-github-runner-cleanup.sh}"

rm -f "${marker}"
logger -t prox-github-runner-job -- "job completed; removed marker ${marker}" 2>/dev/null || true

if [[ -x "${cleanup}" ]]; then
  "${cleanup}" ||
    logger -t prox-github-runner-job -- "cleanup failed (non-fatal)" 2>/dev/null || true
fi
