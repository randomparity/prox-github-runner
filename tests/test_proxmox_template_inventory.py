from __future__ import annotations

from pathlib import Path

import yaml


def load_proxmox_vars() -> dict[str, object]:
    return yaml.safe_load(Path("inventory/group_vars/proxmox/vars.yml").read_text())


def test_ubuntu_cloud_image_uses_current_url_with_pinned_checksum() -> None:
    data = load_proxmox_vars()
    assert (
        data["proxmox_template_cloud_image_url"]
        == "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
    )
    assert (
        data["proxmox_template_cloud_image_checksum"]
        == "sha256:5fa5b05e5ec239858c4531485d6023b0896448c2df7c63b34f8dae6ea6051a44"
    )


def test_cloud_image_filename_is_not_path_like() -> None:
    data = load_proxmox_vars()
    filename = str(data["proxmox_template_cloud_image_filename"])
    assert "/" not in filename
    assert ".." not in filename
