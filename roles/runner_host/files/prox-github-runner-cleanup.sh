#!/usr/bin/env bash
# Post-job workspace and Docker cleanup for the prox-github-runner services.
#
# Serializes concurrent cleanup passes with a flock on the shared maintenance
# lock, then prunes stale entries from EVERY per-service _work/_temp directory
# (not just one) and runs an age-gated Docker prune. Running peer jobs are not
# serialized against; the Docker prune is safe only because it is age-gated and
# no routed job uses Docker (see design Amendment 1).
set -euo pipefail

install_root="${CLEANUP_INSTALL_ROOT:-/opt/actions-runner}"
state_dir="${CLEANUP_STATE_DIR:-/run/prox-github-runner}"
max_age_days="${CLEANUP_MAX_AGE_DAYS:-7}"
lock_file="${state_dir}/maintenance.lock"

log() {
  logger -t prox-github-runner-cleanup -- "$1" 2>/dev/null || true
}

mkdir -p "${state_dir}"

exec 9>"${lock_file}"
if ! flock -n 9; then
  log "another cleanup run holds ${lock_file}; exiting"
  exit 0
fi

shopt -s nullglob
for work in "${install_root}"/svc-*/_work "${install_root}"/svc-*/_temp; do
  [[ -d "${work}" ]] || continue
  log "pruning entries older than ${max_age_days}d in ${work}"
  find "${work}" -mindepth 1 -maxdepth 1 -mtime "+${max_age_days}" \
    -exec rm -rf {} +
done

if command -v docker >/dev/null 2>&1; then
  docker system prune --force --filter "until=$((max_age_days * 24))h" \
    >/dev/null 2>&1 || log "docker prune failed (non-fatal)"
fi

log "cleanup complete"
