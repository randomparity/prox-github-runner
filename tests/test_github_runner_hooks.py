from __future__ import annotations

import os
import subprocess
from pathlib import Path

FILES = Path("roles/github_runner/files").resolve()
JOB_STARTED = FILES / "prox-github-runner-job-started.sh"
JOB_COMPLETED = FILES / "prox-github-runner-job-completed.sh"


def run_hook(script: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **env_extra}
    return subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)


def test_job_started_writes_timestamped_marker(tmp_path: Path) -> None:
    marker = tmp_path / "jobs" / "paper-archives-runner-1"
    proc = run_hook(JOB_STARTED, {"PROX_RUNNER_JOB_MARKER": str(marker)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert marker.exists()
    assert marker.read_text().strip().isdigit()  # unix timestamp


def test_job_completed_removes_marker_and_runs_cleanup(tmp_path: Path) -> None:
    marker = tmp_path / "jobs" / "paper-archives-runner-1"
    marker.parent.mkdir(parents=True)
    marker.write_text("1700000000\n")
    cleanup_log = tmp_path / "cleanup.log"
    cleanup = tmp_path / "cleanup.sh"
    cleanup.write_text(f'#!/usr/bin/env bash\nprintf "ran\\n" >>"{cleanup_log}"\n')
    cleanup.chmod(0o755)
    proc = run_hook(
        JOB_COMPLETED,
        {"PROX_RUNNER_JOB_MARKER": str(marker), "PROX_RUNNER_CLEANUP": str(cleanup)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not marker.exists()
    assert cleanup_log.read_text().strip() == "ran"


def test_job_completed_noop_when_marker_missing(tmp_path: Path) -> None:
    marker = tmp_path / "jobs" / "paper-archives-runner-2"
    proc = run_hook(
        JOB_COMPLETED,
        {"PROX_RUNNER_JOB_MARKER": str(marker), "PROX_RUNNER_CLEANUP": "/nonexistent"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not marker.exists()
