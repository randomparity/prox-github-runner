from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast


def write_inventory(path: Path) -> None:
    path.write_text(
        f"""
---
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
        "proxmox_template_vmid": 9000,
        "proxmox_template_name": "ubuntu-2404-cloud",
        "proxmox_storage": "local-lvm",
        "proxmox_template_bridge": "vmbr0",
        "proxmox_template_vlan": None,
        "proxmox_template_image_cache_dir": str(tmp_path / "cache"),
        "proxmox_template_cloud_image_url": "https://example.invalid/image.img",
        "proxmox_template_cloud_image_filename": "image.img",
        "proxmox_template_cloud_image_checksum": "sha256:" + "a" * 64,
        "proxmox_template_memory_mb": 2048,
        "proxmox_template_cores": 2,
    }


def write_fake_qm(tmp_path: Path, mode: str) -> Path:
    qm_path = tmp_path / "qm"
    qm_path.write_text(
        r"""#!/usr/bin/env bash
set -euo pipefail
log="${FAKE_QM_LOG:?}"
printf '%s\n' "$*" >> "$log"
mode="${FAKE_QM_MODE:?}"

if [[ "$mode" == "existing-template" ]]; then
  case "$1" in
    status) exit 0 ;;
    config) printf 'name: ubuntu-2404-cloud\ntemplate: 1\n' ;;
    *) echo "unexpected qm $*" >&2; exit 42 ;;
  esac
elif [[ "$mode" == "existing-vm" ]]; then
  case "$1" in
    status) exit 0 ;;
    config) printf 'name: ubuntu-2404-cloud\nmemory: 2048\n' ;;
    *) echo "unexpected qm $*" >&2; exit 42 ;;
  esac
elif [[ "$mode" == "create-success" ]]; then
  case "$1" in
    status) exit 2 ;;
    config) printf 'name: ubuntu-2404-cloud\ntemplate: 1\n' ;;
    create|set|template) exit 0 ;;
    *) echo "unexpected qm $*" >&2; exit 42 ;;
  esac
else
  echo "unsupported fake qm mode $mode" >&2
  exit 43
fi
"""
    )
    qm_path.chmod(qm_path.stat().st_mode | stat.S_IXUSR)
    return qm_path


def run_template_playbook(
    *,
    tmp_path: Path,
    mode: str,
    extra_vars: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[str]:
    inventory = tmp_path / "hosts.yml"
    log = tmp_path / "qm.log"
    write_inventory(inventory)
    write_fake_qm(tmp_path, mode)
    merged_vars = base_extra_vars(tmp_path)
    if extra_vars:
        merged_vars.update(extra_vars)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_QM_MODE": mode,
        "FAKE_QM_LOG": str(log),
    }
    return subprocess.run(
        [
            "ansible-playbook",
            "-i",
            str(inventory),
            "playbooks/provision-template.yml",
            "-e",
            json.dumps(merged_vars),
        ],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )


def test_existing_template_passes_without_create_commands(tmp_path: Path) -> None:
    proc = run_template_playbook(tmp_path=tmp_path, mode="existing-template")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "create" not in (tmp_path / "qm.log").read_text()


def test_existing_non_template_vm_fails(tmp_path: Path) -> None:
    proc = run_template_playbook(tmp_path=tmp_path, mode="existing-vm")
    assert proc.returncode != 0
    assert "exists but is not a template" in proc.stdout


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


class ImageServer:
    def __init__(self, directory: Path) -> None:
        handler = partial(QuietHandler, directory=str(directory))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = cast(tuple[str, int], self.httpd.server_address)
        return f"http://{host}:{port}/image.img"

    def __enter__(self) -> ImageServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=5)


def image_server_and_vars(tmp_path: Path) -> tuple[ImageServer, dict[str, object]]:
    image_dir = tmp_path / "image-server"
    image_dir.mkdir()
    image = image_dir / "image.img"
    image.write_bytes(b"ubuntu cloud image fixture")
    checksum = hashlib.sha256(image.read_bytes()).hexdigest()
    server = ImageServer(image_dir)
    return (
        server,
        {
            "proxmox_template_cloud_image_url": server.url,
            "proxmox_template_cloud_image_checksum": f"sha256:{checksum}",
            "proxmox_template_image_cache_dir": str(tmp_path / "cache"),
        },
    )


def test_missing_template_creates_template_and_removes_image(tmp_path: Path) -> None:
    server, extra_vars = image_server_and_vars(tmp_path)
    with server:
        proc = run_template_playbook(
            tmp_path=tmp_path,
            mode="create-success",
            extra_vars=extra_vars,
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = (tmp_path / "qm.log").read_text()
    assert "create 9000" in log
    assert "set 9000 --scsi0" in log
    assert "set 9000 --ide2" in log
    assert "template 9000" in log
    assert not (tmp_path / "cache" / "image.img").exists()
