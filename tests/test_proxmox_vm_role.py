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
    proxmox:
      hosts:
        pve-test:
          ansible_connection: local
          ansible_python_interpreter: "{sys.executable}"
"""
    )


def base_extra_vars(tmp_path: Path) -> dict[str, object]:
    return {
        "runner_vm_id": 2100,
        "runner_vm_name": "paper-archives-runner",
        "runner_vm_ip": "192.168.20.50",
        "runner_vm_gateway": "192.168.20.1",
        "runner_vm_cidr": 24,
        "runner_vm_nameserver": "192.168.20.1",
        "runner_bootstrap_user": "runner",
        "proxmox_api_host": "192.168.20.10",
        "proxmox_template_vmid": 9000,
        "proxmox_storage": "local-lvm",
        "proxmox_template_bridge": "vmbr0",
        "proxmox_template_vlan": None,
        "proxmox_vm_lock_dir": str(tmp_path / "vm.lock"),
        "proxmox_vm_fw_rules_path": str(tmp_path / "fw.rules"),
        "proxmox_vm_wait_for_ssh": False,
    }


def run_role(
    tmp_path: Path,
    overrides: dict | None = None,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    inv = tmp_path / "inv.yml"
    write_inventory(inv)
    play = tmp_path / "play.yml"
    play.write_text(
        """---
- hosts: proxmox
  gather_facts: false
  roles:
    - proxmox_vm
"""
    )
    extra = base_extra_vars(tmp_path)
    extra.update(overrides or {})
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    env.update(env_extra or {})
    cmd = ["ansible-playbook", "-i", str(inv), str(play), "-e", json.dumps(extra)]
    return subprocess.run(cmd, text=True, capture_output=True, cwd=Path.cwd(), env=env)


def write_fake_qm(tmp_path: Path, mode: str) -> None:
    qm = tmp_path / "qm"
    qm.write_text(
        r"""#!/usr/bin/env bash
set -euo pipefail
log="${FAKE_QM_LOG:?}"; printf '%s\n' "$*" >> "$log"
mode="${FAKE_QM_MODE:?}"
case "$mode:$1" in
  absent:status) exit 2 ;;
  absent:clone) exit 0 ;;
  absent:set) exit 0 ;;
  absent:resize) exit 0 ;;
  absent:start) exit 0 ;;
  existing:status) exit 0 ;;
  existing:config)
    printf 'name: paper-archives-runner\n'
    printf 'net0: virtio,bridge=vmbr0\n'
    printf 'scsi0: local-lvm:vm-2100-disk-0,size=256G\n'
    ;;
  existing:set) exit 0 ;;
  existing:start) exit 0 ;;
  *) echo "unexpected qm $*" >&2; exit 42 ;;
esac
"""
    )
    qm.chmod(0o755)


def test_missing_runner_ip_fails(tmp_path: Path) -> None:
    proc = run_role(tmp_path, {"runner_vm_ip": ""})
    assert proc.returncode != 0
    assert "Missing or invalid runner VM configuration" in proc.stdout


def test_clone_when_absent(tmp_path: Path) -> None:
    write_fake_qm(tmp_path, "absent")
    log = tmp_path / "qm.log"
    proc = run_role(
        tmp_path,
        {"runner_vm_ip": "192.168.20.50"},
        env_extra={"FAKE_QM_LOG": str(log), "FAKE_QM_MODE": "absent"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "clone" in log.read_text()


def test_identity_change_fails_when_existing(tmp_path: Path) -> None:
    write_fake_qm(tmp_path, "existing")
    log = tmp_path / "qm.log"
    proc = run_role(
        tmp_path,
        {"runner_vm_ip": "192.168.20.50", "proxmox_template_bridge": "vmbr9"},
        env_extra={"FAKE_QM_LOG": str(log), "FAKE_QM_MODE": "existing"},
    )
    assert proc.returncode != 0
    assert "identity change" in proc.stdout.lower()
