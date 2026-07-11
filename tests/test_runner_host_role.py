from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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


def base_extra_vars(tmp_path: Path) -> dict[str, object]:
    return {
        "runner_bootstrap_user": "runner",
        "github_runner_count": 3,
        "runner_host_install_root": str(tmp_path / "actions-runner"),
        "runner_host_apply_system": False,
    }


def run_runner_host(
    tmp_path: Path,
    overrides: dict | None = None,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    inv = tmp_path / "inv.yml"
    write_inventory(inv)
    play = tmp_path / "play.yml"
    play.write_text(
        """---
- hosts: runner
  gather_facts: false
  roles:
    - runner_host
"""
    )
    extra = base_extra_vars(tmp_path)
    extra.update(overrides or {})
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    env.update(env_extra or {})
    cmd = ["ansible-playbook", "-i", str(inv), str(play), "-e", json.dumps(extra)]
    return subprocess.run(cmd, text=True, capture_output=True, cwd=Path.cwd(), env=env)


def test_baseline_declares_clang_python312_tauri_and_sudo(tmp_path: Path) -> None:
    proc = run_runner_host(tmp_path)  # runner_host_apply_system=False
    assert proc.returncode == 0, proc.stdout + proc.stderr
    defaults = Path("roles/runner_host/defaults/main.yml").read_text()
    for pkg in (
        "clang",
        "python3.12",
        "python3.12-venv",
        "python3-pip",
        "libwebkit2gtk-4.1-dev",
        "libxdo-dev",
        "librsvg2-dev",
    ):
        assert pkg in defaults
    tasks = Path("roles/runner_host/tasks/main.yml").read_text()
    assert "ansible.builtin.apt" in tasks
    assert "runner_host_apply_system" in tasks  # system tasks are gated
    sudoers = Path("roles/runner_host/templates/runner-sudoers.j2").read_text()
    assert "NOPASSWD:ALL" in sudoers


def test_qemu_guest_agent_installed_and_enabled(tmp_path: Path) -> None:
    # The QEMU guest agent gives Proxmox control/visibility into the running VM
    # (IP reporting, graceful shutdown, fs-freeze). Install it in the guest and
    # enable the service; the template already sets agent enabled=1 host-side.
    proc = run_runner_host(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    defaults = Path("roles/runner_host/defaults/main.yml").read_text()
    assert "qemu-guest-agent" in defaults
    tasks = Path("roles/runner_host/tasks/main.yml").read_text()
    assert "qemu-guest-agent" in tasks


def test_docker_install_and_group(tmp_path: Path) -> None:
    proc = run_runner_host(tmp_path)
    assert proc.returncode == 0, proc.stdout
    tasks = Path("roles/runner_host/tasks/main.yml").read_text()
    assert "docker-ce" in tasks
    assert "groups: docker" in tasks and "append: true" in tasks
    assert "ansible.builtin.systemd" in tasks


def test_per_service_env_isolation(tmp_path: Path) -> None:
    proc = run_runner_host(tmp_path, overrides={"github_runner_count": 3})
    assert proc.returncode == 0, proc.stdout
    tasks = Path("roles/runner_host/tasks/main.yml").read_text()
    assert "range(1, (github_runner_count | int) + 1)" in tasks
    env_tmpl = Path("roles/runner_host/templates/runner-env.j2").read_text()
    assert "RUSTUP_HOME=" in env_tmpl and "rustup" in env_tmpl
    assert "CARGO_HOME=" in env_tmpl
