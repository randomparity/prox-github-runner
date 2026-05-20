# Sprint 2 Proxmox Ubuntu Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe, idempotent Ansible playbook and role that creates the
Ubuntu 24.04 cloud-init VM template on Proxmox.

**Architecture:** Keep Proxmox template creation in a dedicated
`proxmox_template` role that runs `qm` on the Proxmox host over SSH. Local tests
exercise the role through `ansible-playbook` with a fake `qm` binary, so CI can
verify idempotency, command sequencing, and rescue cleanup without a live
Proxmox node.

**Tech Stack:** Ansible builtin modules, Proxmox `qm` CLI, Python pytest,
stdlib HTTP server for local cloud-image fixture tests, Ubuntu Noble cloud image
`noble-server-cloudimg-amd64.img`.

---

## Scope Check

This plan implements Sprint 2 only:

- `playbooks/provision-template.yml`
- `roles/proxmox_template`
- Ubuntu 24.04 template inventory defaults
- local behavior tests with a fake `qm`
- operator documentation for template provisioning

This plan does not clone the runner VM, install Docker, configure GitHub runner
software, register a runner, or add network isolation. Those remain Sprint 3 and
later work.

## File Structure

- `inventory/group_vars/proxmox/vars.yml`: add Ubuntu template image, checksum,
  VM hardware, bridge, and cache directory defaults.
- `playbooks/provision-template.yml`: Proxmox-hosted playbook that runs only
  `proxmox_template`.
- `roles/proxmox_template/defaults/main.yml`: internal role mappings and derived
  values such as the local image path and `net0` string.
- `roles/proxmox_template/meta/main.yml`: ansible-lint role metadata.
- `roles/proxmox_template/tasks/main.yml`: idempotent template existence checks,
  guarded creation block, rescue cleanup, image cleanup, and final verification.
- `tests/test_proxmox_template_role.py`: local role behavior tests using a fake
  `qm` executable and a temporary HTTP server for the image download path.
- `docs/proxmox-template.md`: operator instructions and failure behavior.
- `Makefile`: add an Ansible syntax-check target and include it in `check`.

## Task 1: Template Inventory, Playbook, And Syntax Target

**Files:**

- Modify: `inventory/group_vars/proxmox/vars.yml`
- Create: `playbooks/provision-template.yml`
- Modify: `Makefile`

- [ ] **Step 1: Add Ubuntu template defaults**

Replace `inventory/group_vars/proxmox/vars.yml` with:

```yaml
---
proxmox_api_host: "192.168.20.10"
proxmox_api_port: 8006
proxmox_node: "pve"
proxmox_storage: "local-lvm"

proxmox_template_name: "ubuntu-2404-cloud"
proxmox_template_vmid: 9000
proxmox_template_memory_mb: 2048
proxmox_template_cores: 2
proxmox_template_bridge: "vmbr0"
proxmox_template_vlan: null
proxmox_template_image_cache_dir: "/tmp/prox-github-runner"
proxmox_template_cloud_image_url: >-
  https://cloud-images.ubuntu.com/noble/20260323/noble-server-cloudimg-amd64.img
proxmox_template_cloud_image_filename: "noble-server-cloudimg-amd64-20260323.img"
proxmox_template_cloud_image_checksum: >-
  sha256:6e7016f2c9f4d3c00f48789eb6b9043ba2172ccc1b6b1eaf3ed1e29dd3e52bb3
```

- [ ] **Step 2: Add the template playbook**

Create `playbooks/provision-template.yml`:

```yaml
---
- name: Ensure Ubuntu cloud-init template exists on Proxmox
  hosts: proxmox
  gather_facts: false
  roles:
    - proxmox_template
```

- [ ] **Step 3: Add syntax verification to Makefile**

Update `Makefile` so the phony targets and verification section are:

```make
.PHONY: help setup lint syntax test inventory preflight check clean
```

Add this target after `lint`:

```make
syntax: setup ## Run Ansible playbook syntax checks
	$(ACTIVATE) && for playbook in playbooks/*.yml; do \
		ansible-playbook --syntax-check "$$playbook"; \
	done
```

Change `check` to:

```make
check: lint syntax test inventory ## Run local verification
```

- [ ] **Step 4: Verify syntax target fails before the role exists**

Run:

```bash
make syntax
```

Expected: FAIL because `proxmox_template` does not exist yet.

- [ ] **Step 5: Commit the playbook shell**

Run:

```bash
git add inventory/group_vars/proxmox/vars.yml playbooks/provision-template.yml Makefile
git commit -m "Add Proxmox template playbook shell"
```

## Task 2: Proxmox Template Role Skeleton

**Files:**

- Create: `roles/proxmox_template/defaults/main.yml`
- Create: `roles/proxmox_template/meta/main.yml`
- Create: `roles/proxmox_template/tasks/main.yml`

- [ ] **Step 1: Add role defaults**

Create `roles/proxmox_template/defaults/main.yml`:

```yaml
---
proxmox_template_storage: "{{ proxmox_storage }}"
proxmox_template_scsi_controller: "virtio-scsi-pci"
proxmox_template_os_type: "l26"
proxmox_template_image_path: >-
  {{ proxmox_template_image_cache_dir }}/{{ proxmox_template_cloud_image_filename }}
proxmox_template_net0: >-
  virtio,bridge={{ proxmox_template_bridge }}
  {%- if proxmox_template_vlan is defined and proxmox_template_vlan is not none -%}
  ,tag={{ proxmox_template_vlan }}
  {%- endif -%}
```

- [ ] **Step 2: Add role metadata**

Create `roles/proxmox_template/meta/main.yml`:

```yaml
---
galaxy_info:
  role_name: proxmox_template
  author: dave
  description: Create an Ubuntu cloud-init template VM with the Proxmox qm CLI.
  license: MIT
  min_ansible_version: "2.21"
  platforms:
    - name: GenericLinux
      versions:
        - all
dependencies: []
```

- [ ] **Step 3: Add a minimal task file**

Create `roles/proxmox_template/tasks/main.yml`:

```yaml
---
- name: Validate Proxmox template variables
  ansible.builtin.assert:
    that:
      - proxmox_template_vmid | int > 0
      - proxmox_template_name | length > 0
      - proxmox_template_storage | length > 0
      - proxmox_template_bridge | length > 0
      - proxmox_template_cloud_image_url is match('^https?://')
      - proxmox_template_cloud_image_filename | length > 0
      - proxmox_template_cloud_image_checksum is match('^sha256:[a-f0-9]{64}$')
      - proxmox_template_memory_mb | int >= 1024
      - proxmox_template_cores | int >= 1
    fail_msg: "Missing or invalid Proxmox template configuration."
```

- [ ] **Step 4: Verify syntax checks pass**

Run:

```bash
make syntax
```

Expected: PASS for `playbooks/preflight.yml` and
`playbooks/provision-template.yml`.

- [ ] **Step 5: Commit the role skeleton**

Run:

```bash
git add roles/proxmox_template
git commit -m "Add Proxmox template role skeleton"
```

## Task 3: Existing Template Verification

**Files:**

- Create: `tests/test_proxmox_template_role.py`
- Modify: `roles/proxmox_template/tasks/main.yml`

- [ ] **Step 1: Add failing tests for existing template behavior**

Create `tests/test_proxmox_template_role.py`:

```python
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path


def write_inventory(path: Path) -> None:
    path.write_text(
        """
---
all:
  children:
    proxmox:
      hosts:
        pve-test:
          ansible_connection: local
          ansible_python_interpreter: "{python}"
""".format(python=sys.executable)
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
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
pytest tests/test_proxmox_template_role.py -q
```

Expected: FAIL because the role does not call `qm status` or verify existing
template state.

- [ ] **Step 3: Implement existing-template checks**

Replace `roles/proxmox_template/tasks/main.yml` with:

```yaml
---
- name: Validate Proxmox template variables
  ansible.builtin.assert:
    that:
      - proxmox_template_vmid | int > 0
      - proxmox_template_name | length > 0
      - proxmox_template_storage | length > 0
      - proxmox_template_bridge | length > 0
      - proxmox_template_cloud_image_url is match('^https?://')
      - proxmox_template_cloud_image_filename | length > 0
      - proxmox_template_cloud_image_checksum is match('^sha256:[a-f0-9]{64}$')
      - proxmox_template_memory_mb | int >= 1024
      - proxmox_template_cores | int >= 1
    fail_msg: "Missing or invalid Proxmox template configuration."

- name: Check whether template VMID exists
  ansible.builtin.command:
    argv:
      - qm
      - status
      - "{{ proxmox_template_vmid | string }}"
  register: proxmox_template_status
  changed_when: false
  failed_when: false

- name: Record template existence
  ansible.builtin.set_fact:
    proxmox_template_exists: "{{ proxmox_template_status.rc == 0 }}"

- name: Read existing template config
  ansible.builtin.command:
    argv:
      - qm
      - config
      - "{{ proxmox_template_vmid | string }}"
  register: proxmox_template_existing_config
  changed_when: false
  when: proxmox_template_exists

- name: Fail when existing VMID has a different name
  ansible.builtin.fail:
    msg: >-
      VMID {{ proxmox_template_vmid }} already exists with a different name.
      Expected {{ proxmox_template_name }}.
  when:
    - proxmox_template_exists
    - >-
      proxmox_template_existing_config.stdout is not search(
      '(?m)^name:\s*' ~ (proxmox_template_name | regex_escape) ~ '$')

- name: Fail when existing VMID is not a template
  ansible.builtin.fail:
    msg: "VMID {{ proxmox_template_vmid }} exists but is not a template."
  when:
    - proxmox_template_exists
    - proxmox_template_existing_config.stdout is not search('(?m)^template:\s*1$')

- name: Verify final template config
  ansible.builtin.command:
    argv:
      - qm
      - config
      - "{{ proxmox_template_vmid | string }}"
  register: proxmox_template_final_config
  changed_when: false

- name: Assert final VM is the expected template
  ansible.builtin.assert:
    that:
      - proxmox_template_final_config.stdout is search('(?m)^template:\s*1$')
      - >-
        proxmox_template_final_config.stdout is search(
        '(?m)^name:\s*' ~ (proxmox_template_name | regex_escape) ~ '$')
    fail_msg: "Proxmox template verification failed for VMID {{ proxmox_template_vmid }}."
```

- [ ] **Step 4: Run the focused tests**

Run:

```bash
pytest tests/test_proxmox_template_role.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit existing-template behavior**

Run:

```bash
git add roles/proxmox_template/tasks/main.yml tests/test_proxmox_template_role.py
git commit -m "Verify existing Proxmox template state"
```

## Task 4: Template Creation And Image Cleanup

**Files:**

- Modify: `tests/test_proxmox_template_role.py`
- Modify: `roles/proxmox_template/tasks/main.yml`

- [ ] **Step 1: Add failing create-path behavior test**

Update the import block in `tests/test_proxmox_template_role.py` to include
these imports:

```python
import hashlib
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
```

Then append this code:

```python
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

    def __enter__(self) -> "ImageServer":
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
```

Extend the fake `qm` script in `write_fake_qm()` with:

```bash
elif [[ "$mode" == "create-success" ]]; then
  case "$1" in
    status) exit 2 ;;
    config) printf 'name: ubuntu-2404-cloud\ntemplate: 1\n' ;;
    create|set|template) exit 0 ;;
    *) echo "unexpected qm $*" >&2; exit 42 ;;
  esac
```

- [ ] **Step 2: Run the create-path test to verify it fails**

Run:

```bash
pytest tests/test_proxmox_template_role.py::test_missing_template_creates_template_and_removes_image -q
```

Expected: FAIL because the role does not create the template yet.

- [ ] **Step 3: Add guarded create block and cleanup**

Insert this block before `Verify final template config` in
`roles/proxmox_template/tasks/main.yml`:

```yaml
- name: Create Ubuntu cloud image template
  when: not proxmox_template_exists
  block:
    - name: Ensure template image cache directory exists
      ansible.builtin.file:
        path: "{{ proxmox_template_image_cache_dir }}"
        state: directory
        mode: "0755"

    - name: Download Ubuntu cloud image
      ansible.builtin.get_url:
        url: "{{ proxmox_template_cloud_image_url }}"
        dest: "{{ proxmox_template_image_path }}"
        checksum: "{{ proxmox_template_cloud_image_checksum }}"
        mode: "0644"

    - name: Create Proxmox template VM shell
      ansible.builtin.command:
        argv:
          - qm
          - create
          - "{{ proxmox_template_vmid | string }}"
          - --name
          - "{{ proxmox_template_name }}"
          - --memory
          - "{{ proxmox_template_memory_mb | string }}"
          - --cores
          - "{{ proxmox_template_cores | string }}"
          - --net0
          - "{{ proxmox_template_net0 }}"
          - --scsihw
          - "{{ proxmox_template_scsi_controller }}"
          - --ostype
          - "{{ proxmox_template_os_type }}"
      changed_when: true

    - name: Import Ubuntu cloud image disk
      ansible.builtin.command:
        argv:
          - qm
          - set
          - "{{ proxmox_template_vmid | string }}"
          - --scsi0
          - "{{ proxmox_template_storage }}:0,import-from={{ proxmox_template_image_path }}"
      changed_when: true

    - name: Configure cloud-init and guest agent
      ansible.builtin.command:
        argv:
          - qm
          - set
          - "{{ proxmox_template_vmid | string }}"
          - --ide2
          - "{{ proxmox_template_storage }}:cloudinit"
          - --boot
          - order=scsi0
          - --serial0
          - socket
          - --vga
          - serial0
          - --agent
          - enabled=1
      changed_when: true

    - name: Convert VM to template
      ansible.builtin.command:
        argv:
          - qm
          - template
          - "{{ proxmox_template_vmid | string }}"
      changed_when: true

  rescue:
    - name: Check for partial template VM after failure
      ansible.builtin.command:
        argv:
          - qm
          - status
          - "{{ proxmox_template_vmid | string }}"
      register: proxmox_template_partial_status
      changed_when: false
      failed_when: false

    - name: Destroy partial template VM after failure
      ansible.builtin.command:
        argv:
          - qm
          - destroy
          - "{{ proxmox_template_vmid | string }}"
          - --purge
      changed_when: proxmox_template_partial_status.rc == 0
      failed_when: false
      when: proxmox_template_partial_status.rc == 0

    - name: Report template creation failure
      ansible.builtin.fail:
        msg: >-
          Template creation failed for VMID {{ proxmox_template_vmid }}.
          Partial VM cleanup was attempted.

  always:
    - name: Remove downloaded Ubuntu cloud image
      ansible.builtin.file:
        path: "{{ proxmox_template_image_path }}"
        state: absent
```

- [ ] **Step 4: Run the focused create-path test**

Run:

```bash
pytest tests/test_proxmox_template_role.py::test_missing_template_creates_template_and_removes_image -q
```

Expected: PASS.

- [ ] **Step 5: Run all template role tests**

Run:

```bash
pytest tests/test_proxmox_template_role.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit template creation**

Run:

```bash
git add roles/proxmox_template/tasks/main.yml tests/test_proxmox_template_role.py
git commit -m "Create Ubuntu Proxmox template"
```

## Task 5: Rescue Cleanup Coverage

**Files:**

- Modify: `tests/test_proxmox_template_role.py`

- [ ] **Step 1: Add failing rescue cleanup test**

Append this test to `tests/test_proxmox_template_role.py`:

```python
def test_failed_template_creation_destroys_partial_vm(tmp_path: Path) -> None:
    server, extra_vars = image_server_and_vars(tmp_path)
    with server:
        proc = run_template_playbook(
            tmp_path=tmp_path,
            mode="fail-import",
            extra_vars=extra_vars,
        )
    assert proc.returncode != 0
    log = (tmp_path / "qm.log").read_text()
    assert "destroy 9000 --purge" in log
    assert "Partial VM cleanup was attempted" in proc.stdout
    assert not (tmp_path / "cache" / "image.img").exists()
```

Extend the fake `qm` script in `write_fake_qm()` with:

```bash
elif [[ "$mode" == "fail-import" ]]; then
  state="${FAKE_QM_STATE:?}"
  case "$1" in
    status)
      if [[ -f "$state" ]]; then exit 0; fi
      exit 2
      ;;
    create) touch "$state" ;;
    set)
      if [[ "$*" == *"--scsi0"* ]]; then exit 55; fi
      exit 0
      ;;
    destroy) rm -f "$state" ;;
    config) printf 'name: ubuntu-2404-cloud\ntemplate: 1\n' ;;
    *) echo "unexpected qm $*" >&2; exit 42 ;;
  esac
```

Update `run_template_playbook()` environment to include:

```python
"FAKE_QM_STATE": str(tmp_path / "qm.state"),
```

- [ ] **Step 2: Run the rescue test to verify it fails**

Run:

```bash
pytest tests/test_proxmox_template_role.py::test_failed_template_creation_destroys_partial_vm -q
```

Expected: FAIL until the fake `qm` mode and environment support are added.

- [ ] **Step 3: Complete the fake `qm` rescue support**

Apply the fake `qm` and environment changes from Step 1.

- [ ] **Step 4: Run all template role tests**

Run:

```bash
pytest tests/test_proxmox_template_role.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit rescue coverage**

Run:

```bash
git add tests/test_proxmox_template_role.py
git commit -m "Test Proxmox template rescue cleanup"
```

## Task 6: Operator Documentation And Final Verification

**Files:**

- Create: `docs/proxmox-template.md`

- [ ] **Step 1: Add operator documentation**

Create `docs/proxmox-template.md`:

```markdown
# Proxmox Ubuntu Template

Sprint 2 creates the Ubuntu 24.04 cloud-init template VM used by later runner VM
provisioning.

Run:

```bash
ansible-playbook playbooks/provision-template.yml
```

The playbook is intentionally narrow:

- It runs against the `proxmox` inventory group.
- It uses the Proxmox `qm` CLI over SSH.
- It creates only `proxmox_template_vmid`.
- It fails if that VMID already exists but is not a template.
- It fails if that VMID has a different name than `proxmox_template_name`.
- It removes the downloaded cloud image after each creation attempt.
- It destroys a partial VM if template creation fails after `qm create`.

The Ubuntu image is pinned in `inventory/group_vars/proxmox/vars.yml` by URL and
SHA256 checksum. Updating the base image is an explicit inventory change.

Re-running the playbook is safe when the expected template already exists. The
playbook verifies that Proxmox reports `template: 1` and the expected name.
```

- [ ] **Step 2: Run self-review scans**

Run:

```bash
PATTERN='T''BD|TO''DO|PLACE''HOLDER|FIX''ME|example_replace_me'
rg -n "$PATTERN" \
  --glob '!inventory/group_vars/all/vault.yml.example' \
  --glob '!docs/superpowers/plans/**'
```

Expected: no output.

Run:

```bash
rg -n "github_pat_[A-Za-z0-9_]{80,}|gh[pousr]_[A-Za-z0-9_]{36,}|BEGIN .*PRIVATE KEY" \
  --glob '!docs/superpowers/plans/**' .
```

Expected: no output.

- [ ] **Step 3: Run final verification**

Run:

```bash
make check
```

Expected: PASS for YAML lint, Ansible lint, syntax checks, Python lint,
format check, type check, pytest, and inventory parsing.

- [ ] **Step 4: Commit docs and polish**

Run:

```bash
git add docs/proxmox-template.md Makefile inventory playbooks roles tests
git commit -m "Document Proxmox template provisioning"
```

If Step 2 or Step 3 required fixes outside those paths, include only the files
changed by those fixes in the same commit.

## Implementation Notes

- Use `ansible.builtin.command` with `argv` for `qm`; do not build shell strings.
- Do not use `rm -rf`; cleanup uses `ansible.builtin.file` with `state: absent`.
- Keep live Proxmox checks operator-run. Local CI uses fake `qm` behavior tests.
- The pinned Ubuntu image URL and checksum were checked against
  `https://cloud-images.ubuntu.com/noble/20260323/` and the Noble
  `SHA256SUMS` file on 2026-05-20.
