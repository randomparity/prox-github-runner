from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.test_github_runner_role import CONFIG_SH, SVC_SH, write_fake_gh

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


def place_service(install_root: Path, idx: int, repo: str = REPO) -> None:
    svc = install_root / f"svc-{idx}"
    svc.mkdir(parents=True)
    for name, body in (("config.sh", CONFIG_SH), ("svc.sh", SVC_SH)):
        script = svc / name
        script.write_text(body)
        script.chmod(0o755)
    (svc / ".runner").write_text(f'{{"gitHubUrl": "https://github.com/{repo}"}}')


def run_unregister(
    tmp_path: Path,
    overrides: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    inv = tmp_path / "inv.yml"
    write_inventory(inv)
    write_fake_gh(tmp_path)
    extra: dict[str, object] = {
        "runner_vm_name": "paper-archives-runner",
        "runner_bootstrap_user": "runner",
        "github_runner_target_repo": REPO,
        "github_runner_labels": ["self-hosted", "linux", "x64", "paper-archives"],
        "github_runner_count": 1,
        "github_runner_install_root": str(tmp_path / "actions-runner"),
        "github_runner_become": False,
    }
    extra.update(overrides or {})
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
        "FAKE_RUNNER_LOG": str(tmp_path / "runner.log"),
    }
    cmd = [
        "ansible-playbook",
        "-i",
        str(inv),
        "playbooks/unregister-runner.yml",
        "-e",
        json.dumps(extra),
    ]
    return subprocess.run(cmd, text=True, capture_output=True, cwd=Path.cwd(), env=env)


def test_scale_down_removes_surplus_service(tmp_path: Path) -> None:
    install_root = tmp_path / "actions-runner"
    place_service(install_root, 1)
    place_service(install_root, 2)
    proc = run_unregister(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    runner_log = (tmp_path / "runner.log").read_text()
    assert "svc uninstall" in runner_log
    assert "config remove" in runner_log
    assert "REMOVE-TOKEN-123" in runner_log
    gh_log = (tmp_path / "gh.log").read_text()
    assert "remove-token" in gh_log
    assert not (install_root / "svc-2").exists()
    assert (install_root / "svc-1").exists()


def test_second_run_is_noop(tmp_path: Path) -> None:
    install_root = tmp_path / "actions-runner"
    place_service(install_root, 1)
    place_service(install_root, 2)
    first = run_unregister(tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    second = run_unregister(tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "changed=0" in second.stdout


def test_full_teardown_removes_all_services(tmp_path: Path) -> None:
    install_root = tmp_path / "actions-runner"
    place_service(install_root, 1)
    place_service(install_root, 2)
    proc = run_unregister(tmp_path, overrides={"github_runner_count": 0})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (install_root / "svc-1").exists()
    assert not (install_root / "svc-2").exists()


def test_missing_install_root_is_noop(tmp_path: Path) -> None:
    proc = run_unregister(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "changed=0" in proc.stdout
