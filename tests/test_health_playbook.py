from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = "drc-dot-nz/paper-archives"


def write_inventory(path: Path) -> None:
    path.write_text(
        f"""---
all:
  children:
    runner:
      hosts:
        runner-test:
          ansible_connection: local
          ansible_python_interpreter: "{sys.executable}"
"""
    )


def write_fake_gh(tmp_path: Path) -> None:
    gh = tmp_path / "gh"
    gh.write_text(
        r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${FAKE_GH_LOG:?}"
json="${FAKE_GH_REPO_JSON:-}"
[[ -n "${json}" ]] || json='{"private": true}'
printf '%s' "${json}"
"""
    )
    gh.chmod(0o755)


def write_fake_systemctl(tmp_path: Path) -> None:
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"${FAKE_SYSTEMCTL_LOG:?}"
if [[ "${1:-}" == "is-active" ]]; then
  state="${FAKE_SYSTEMCTL_ACTIVE:-active}"
  printf '%s\n' "${state}"
  [[ "${state}" == "active" ]] && exit 0 || exit 3
fi
exit 0
"""
    )
    systemctl.chmod(0o755)


def write_fake_docker(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    docker.write_text(
        r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"${FAKE_DOCKER_LOG:-/dev/null}"
exit "${FAKE_DOCKER_RC:-0}"
"""
    )
    docker.chmod(0o755)


def write_fake_df(tmp_path: Path) -> None:
    df = tmp_path / "df"
    df.write_text(
        r"""#!/usr/bin/env bash
pct="${FAKE_DISK_PCT:-42}"
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
printf '/dev/fake 100 %s 100 %s%% /\n' "${pct}" "${pct}"
"""
    )
    df.chmod(0o755)


def run_health(
    tmp_path: Path,
    overrides: dict | None = None,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    inv = tmp_path / "inv.yml"
    write_inventory(inv)
    write_fake_gh(tmp_path)
    write_fake_systemctl(tmp_path)
    write_fake_docker(tmp_path)
    write_fake_df(tmp_path)
    install_root = tmp_path / "actions-runner"
    for idx in (1, 2, 3):
        (install_root / f"svc-{idx}").mkdir(parents=True, exist_ok=True)
    extra: dict[str, object] = {
        "runner_vm_name": "paper-archives-runner",
        "runner_bootstrap_user": "runner",
        "github_runner_target_repo": REPO,
        "github_runner_install_root": str(install_root),
        "github_runner_state_dir": str(tmp_path / "state"),
        "github_runner_become": False,
    }
    extra.update(overrides or {})
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
        "FAKE_SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
        "FAKE_DOCKER_LOG": str(tmp_path / "docker.log"),
    }
    env.update(env_extra or {})
    cmd = [
        "ansible-playbook",
        "-i",
        str(inv),
        "playbooks/check-runner-health.yml",
        "-e",
        json.dumps(extra),
    ]
    return subprocess.run(cmd, text=True, capture_output=True, cwd=Path.cwd(), env=env)


def systemctl_log(tmp_path: Path) -> str:
    log = tmp_path / "systemctl.log"
    return log.read_text() if log.exists() else ""


def test_reports_service_state_when_healthy(tmp_path: Path) -> None:
    proc = run_health(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "svc-1 state" in proc.stdout
    assert "svc-3 state" in proc.stdout
    assert "stop" not in systemctl_log(tmp_path)


def test_public_repo_stops_all_services_and_fails(tmp_path: Path) -> None:
    proc = run_health(tmp_path, env_extra={"FAKE_GH_REPO_JSON": '{"private": false}'})
    assert proc.returncode != 0
    log = systemctl_log(tmp_path)
    assert "stop" in log
    assert "actions.runner." in log
    assert "PUBLIC" in proc.stdout


def test_offline_service_warns(tmp_path: Path) -> None:
    proc = run_health(tmp_path, env_extra={"FAKE_SYSTEMCTL_ACTIVE": "inactive"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "HEALTH WARNING" in proc.stdout
    assert "offline" in proc.stdout


def test_stale_marker_warns_on_scheduling_latency(tmp_path: Path) -> None:
    jobs = tmp_path / "state" / "jobs"
    jobs.mkdir(parents=True)
    marker = jobs / "paper-archives-runner-1"
    marker.write_text("100\n")
    old = time.time() - 3600
    os.utime(marker, (old, old))
    proc = run_health(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "scheduling latency" in proc.stdout


def test_high_disk_usage_warns(tmp_path: Path) -> None:
    proc = run_health(tmp_path, env_extra={"FAKE_DISK_PCT": "95"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "disk usage critical" in proc.stdout
