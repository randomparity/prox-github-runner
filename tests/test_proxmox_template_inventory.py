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
        == "sha256:6e7016f2c9f4d3c00f48789eb6b9043ba2172ccc1b6b1eaf3ed1e29dd3e52bb3"
    )


def test_cloud_image_filename_is_not_path_like() -> None:
    data = load_proxmox_vars()
    filename = str(data["proxmox_template_cloud_image_filename"])
    assert "/" not in filename
    assert ".." not in filename
