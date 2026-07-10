from __future__ import annotations

import json
import os
import subprocess
import sys
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
conclusion="${FAKE_GH_CONCLUSION:-success}"

if [[ "${1:-}" == "api" ]]; then
  if [[ "${FAKE_GH_WORKFLOW_PRESENT:-1}" == "1" ]]; then
    printf '{"id": 42, "state": "active"}'
    exit 0
  fi
  echo '{"message": "Not Found"}' >&2
  exit 1
fi

case "${1:-} ${2:-}" in
  "workflow run") exit 0 ;;
  "run list")
    printf '[{"databaseId": 12345, "status": "completed", "conclusion": "%s"}]' \
      "${conclusion}"
    exit 0
    ;;
  "run view")
    printf '{"status": "completed", "conclusion": "%s"}' "${conclusion}"
    exit 0
    ;;
esac
echo "unexpected gh $*" >&2
exit 1
"""
    )
    gh.chmod(0o755)


def run_smoke(
    tmp_path: Path,
    overrides: dict | None = None,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    inv = tmp_path / "inv.yml"
    write_inventory(inv)
    write_fake_gh(tmp_path)
    extra: dict[str, object] = {
        "runner_vm_name": "paper-archives-runner",
        "github_runner_target_repo": REPO,
        "smoke_poll_retries": 1,
        "smoke_poll_delay": 0,
    }
    extra.update(overrides or {})
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
    }
    env.update(env_extra or {})
    cmd = [
        "ansible-playbook",
        "-i",
        str(inv),
        "playbooks/run-smoke-workflow.yml",
        "-e",
        json.dumps(extra),
    ]
    return subprocess.run(cmd, text=True, capture_output=True, cwd=Path.cwd(), env=env)


def gh_log(tmp_path: Path) -> str:
    log = tmp_path / "gh.log"
    return log.read_text() if log.exists() else ""


def test_dispatches_and_polls_a_successful_run(tmp_path: Path) -> None:
    proc = run_smoke(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = gh_log(tmp_path)
    assert "workflow run" in log
    assert "run view 12345" in log


def test_fails_when_run_does_not_succeed(tmp_path: Path) -> None:
    proc = run_smoke(tmp_path, env_extra={"FAKE_GH_CONCLUSION": "failure"})
    assert proc.returncode != 0
    assert "failure" in proc.stdout
    assert "workflow run" in gh_log(tmp_path)


def test_skips_gracefully_when_workflow_absent(tmp_path: Path) -> None:
    proc = run_smoke(tmp_path, env_extra={"FAKE_GH_WORKFLOW_PRESENT": "0"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "not present" in proc.stdout.lower()
    assert "workflow run" not in gh_log(tmp_path)
