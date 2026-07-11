#!/usr/bin/env bash
# Public-repo guard for the prox-github-runner services.
#
# Polls the target repository's privacy with an UNAUTHENTICATED request (no PAT
# ever lives on the VM). GitHub hides private repositories from anonymous
# callers, so:
#   * HTTP 200 with "private": false  -> repo is PUBLIC (hard unsafe): stop all
#     runner services immediately via the systemctl-stop primitive.
#   * HTTP 200 with "private": true   -> private and confirmed: safe, no-op.
#   * HTTP 404                        -> hidden from anonymous view (private or
#     removed): safe, no-op, and reset the soft-failure counter.
#   * anything else (network, rate limit, 5xx) -> a soft failure; after
#     GUARD_SOFT_THRESHOLD consecutive soft failures, fail closed and stop all
#     services.
set -euo pipefail

repo="${GUARD_REPO:?GUARD_REPO must be set to owner/name}"
api_base="${GUARD_API_BASE:-https://api.github.com}"
state_dir="${GUARD_STATE_DIR:-/run/prox-github-runner}"
soft_threshold="${GUARD_SOFT_THRESHOLD:-4}"
unit_glob="${GUARD_UNIT_GLOB:-actions.runner.*}"
soft_file="${state_dir}/guard.soft"

log() {
  logger -t prox-github-runner-guard -- "$1" 2>/dev/null || true
}

read_soft() {
  if [[ -f "${soft_file}" ]]; then cat "${soft_file}"; else echo 0; fi
}

stop_all_services() {
  log "stopping all runner services: $1"
  systemctl stop "${unit_glob}"
}

mkdir -p "${state_dir}"

body_file="$(mktemp)"
trap 'rm -f "${body_file}"' EXIT

http_code="$(curl -sS -o "${body_file}" -w '%{http_code}' \
  -H 'Accept: application/vnd.github+json' \
  "${api_base}/repos/${repo}")" || http_code=000

if [[ "${http_code}" == "200" ]] &&
  grep -Eq '"private"[[:space:]]*:[[:space:]]*false' "${body_file}"; then
  echo 0 >"${soft_file}"
  stop_all_services "target repo ${repo} is public"
  exit 0
fi

if [[ "${http_code}" == "200" || "${http_code}" == "404" ]]; then
  echo 0 >"${soft_file}"
  log "target repo ${repo} confirmed private (http ${http_code}); no action"
  exit 0
fi

soft="$(read_soft)"
soft=$((soft + 1))
echo "${soft}" >"${soft_file}"
log "soft failure ${soft}/${soft_threshold} (http ${http_code})"
if ((soft >= soft_threshold)); then
  stop_all_services "soft-failure threshold ${soft_threshold} reached"
fi
exit 0
