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
  existing-vmbr01:status) exit 0 ;;
  existing-vmbr01:config)
    printf 'name: paper-archives-runner\n'
    printf 'net0: virtio,bridge=vmbr01,firewall=1\n'
    printf 'scsi0: local-lvm:vm-2100-disk-0,size=256G\n'
    ;;
  existing-vmbr01:set) exit 0 ;;
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


def test_identity_guard_fires_on_prefix_collision_bridge(tmp_path: Path) -> None:
    # Desired bridge vmbr0 must NOT be treated as present when the existing VM is
    # on vmbr01 (substring collision). The delimited guard must fire.
    write_fake_qm(tmp_path, "existing-vmbr01")
    log = tmp_path / "qm.log"
    proc = run_role(
        tmp_path,
        {"runner_vm_ip": "192.168.20.50", "proxmox_template_bridge": "vmbr0"},
        env_extra={"FAKE_QM_LOG": str(log), "FAKE_QM_MODE": "existing-vmbr01"},
    )
    assert proc.returncode != 0
    assert "identity change" in proc.stdout.lower()


def test_firewall_denies_cidrs_default_allow_hosts_are_comments(tmp_path: Path) -> None:
    write_fake_qm(tmp_path, "existing")
    log = tmp_path / "qm.log"
    proc = run_role(
        tmp_path,
        {},
        env_extra={"FAKE_QM_LOG": str(log), "FAKE_QM_MODE": "existing"},
    )
    assert proc.returncode == 0, proc.stdout

    # VM firewall is activated by the net0 `firewall=1` NIC flag plus the .fw
    # `[OPTIONS] enable: 1`; `qm set --firewall 1` is not a valid qm option and
    # must never be emitted.
    qm_log = log.read_text()
    net0_sets = [line for line in qm_log.splitlines() if "--net0" in line]
    assert net0_sets, "no `qm set --net0` converge was logged"
    assert all("firewall=1" in line for line in net0_sets)
    assert "--firewall 1" not in qm_log

    body = (tmp_path / "fw.rules").read_text()
    lines = body.splitlines()

    # Deny-specific-CIDRs / default-allow model (Amendment 4): every denied CIDR
    # is a REJECT rule and the policy is ACCEPT so those denies are not shadowed.
    assert "policy_out: ACCEPT" in body
    assert "policy_out: DROP" not in body
    for cidr in ("192.168.20.10/32", "192.168.20.0/24"):
        assert any("REJECT" in line and cidr in line for line in lines), cidr

    # No accept-all: an OUT ACCEPT rule with no -dest matches every destination
    # and would defeat the denies. The old template emitted exactly that.
    for line in lines:
        rule = line.split("#", 1)[0].strip()
        if rule.startswith("OUT ACCEPT"):
            assert "-dest" in rule, f"unscoped accept-all rule present: {line!r}"

    # Hostname egress hosts are documentation-only comments, never live rules.
    for host in (
        "static.rust-lang.org",
        "index.crates.io",
        "pypi.org",
        "objects.githubusercontent.com",
    ):
        assert host in body  # present as annotation
        for line in lines:
            if host in line:
                assert line.lstrip().startswith("#"), f"{host} is not a comment: {line!r}"


def test_start_logged_and_waits_are_gated(tmp_path: Path) -> None:
    write_fake_qm(tmp_path, "absent")
    log = tmp_path / "qm.log"
    proc = run_role(tmp_path, {}, env_extra={"FAKE_QM_LOG": str(log), "FAKE_QM_MODE": "absent"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "start" in log.read_text()
    tasks = Path("roles/proxmox_vm/tasks/main.yml").read_text()
    assert "cloud-init" in tasks and "wait_for" in tasks
    assert "proxmox_vm_wait_for_ssh" in tasks
    assert "timeout: 900" not in tasks
