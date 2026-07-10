from __future__ import annotations

import os
import subprocess
from pathlib import Path

GUARD = Path("roles/runner_host/files/prox-github-runner-guard.sh").resolve()


def log_text(log: Path) -> str:
    return log.read_text() if log.exists() else ""


def write_fake_curl(tmp_path: Path) -> None:
    curl = tmp_path / "curl"
    curl.write_text(
        r"""#!/usr/bin/env bash
set -euo pipefail
out=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[[ -n "$out" ]] && printf '%s' "${FAKE_CURL_BODY:-}" >"$out"
printf '%s' "${FAKE_CURL_CODE:-000}"
"""
    )
    curl.chmod(0o755)


def write_fake_systemctl(tmp_path: Path) -> None:
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"${FAKE_SYSTEMCTL_LOG:?}"
"""
    )
    systemctl.chmod(0o755)


def run_guard(
    tmp_path: Path,
    code: str,
    body: str,
    soft_threshold: int = 4,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    write_fake_curl(tmp_path)
    write_fake_systemctl(tmp_path)
    state = tmp_path / "state"
    log = tmp_path / "systemctl.log"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GUARD_REPO": "drc-dot-nz/paper-archives",
        "GUARD_API_BASE": "http://127.0.0.1:0",
        "GUARD_STATE_DIR": str(state),
        "GUARD_SOFT_THRESHOLD": str(soft_threshold),
        "FAKE_CURL_CODE": code,
        "FAKE_CURL_BODY": body,
        "FAKE_SYSTEMCTL_LOG": str(log),
    }
    proc = subprocess.run(["bash", str(GUARD)], text=True, capture_output=True, env=env)
    return proc, log


def test_guard_stops_all_services_on_public(tmp_path: Path) -> None:
    proc, log = run_guard(tmp_path, "200", '{"private": false}')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "stop" in log_text(log)
    assert "actions.runner" in log_text(log)


def test_guard_noop_on_404(tmp_path: Path) -> None:
    proc, log = run_guard(tmp_path, "404", '{"message": "Not Found"}')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert log_text(log).strip() == ""


def test_guard_noop_on_private_true(tmp_path: Path) -> None:
    proc, log = run_guard(tmp_path, "200", '{"private": true}')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert log_text(log).strip() == ""


def test_guard_soft_failure_threshold_stops(tmp_path: Path) -> None:
    # Below threshold: no stop, counter accrues in the shared state dir.
    for _ in range(3):
        proc, log = run_guard(tmp_path, "000", "", soft_threshold=4)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    assert log_text(log).strip() == ""
    # Fourth consecutive soft failure crosses the threshold and stops services.
    proc, log = run_guard(tmp_path, "000", "", soft_threshold=4)
    assert "stop" in log_text(log)


def test_guard_404_resets_soft_counter(tmp_path: Path) -> None:
    for _ in range(3):
        run_guard(tmp_path, "000", "", soft_threshold=4)
    # A definitive 404 resets the counter, so the next soft failure does not stop.
    run_guard(tmp_path, "404", '{"message": "Not Found"}', soft_threshold=4)
    proc, log = run_guard(tmp_path, "000", "", soft_threshold=4)
    assert log_text(log).strip() == ""
