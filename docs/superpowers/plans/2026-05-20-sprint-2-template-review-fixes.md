# Sprint 2 Template Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten the Proxmox template role before merge by fixing status
classification, image pinning policy, PVE compatibility, concurrency, recovery
docs, and input validation.

**Architecture:** Keep the existing `proxmox_template` role shape, but add
fail-fast checks before any large download or VM mutation. Local coverage stays
in the fake-`qm` pytest harness, with a small fake `pveversion` helper and
inventory-policy tests for the cloud-image URL.

**Tech Stack:** Ansible builtin modules, Proxmox `qm` and `pveversion` CLIs,
Python pytest, PyYAML, ruff, ty, yamllint, ansible-lint.

---

## Scope Check

This plan addresses only the Sprint 2 review findings:

- distinguish `qm status` absent from unexpected failures;
- switch Ubuntu image policy away from dated snapshot URLs;
- add PVE 8 compatibility detection;
- add a host-side lock for template operations;
- document stuck partial-VM recovery;
- tighten VMID and cloud-image filename validation.

It does not change Sprint 3 VM cloning, runner host setup, Docker, or GitHub
runner registration.

## File Structure

- `roles/proxmox_template/defaults/main.yml`: add a lock directory default.
- `roles/proxmox_template/tasks/main.yml`: add stricter validation, PVE version
  probe, lock acquisition, `qm status` rc classification, explicit download
  failure handling, and lock release.
- `inventory/group_vars/proxmox/vars.yml`: switch the default Ubuntu image URL
  to `noble/current/` and remove the dated filename.
- `docs/proxmox-template.md`: document the image bump procedure, PVE 8
  requirement, single-run lock behavior, and stuck partial-VM recovery commands.
- `tests/test_proxmox_template_role.py`: extend fake `qm`/`pveversion` support
  and add behavior tests for status errors, PVE 7 rejection, lock contention,
  validation, and clearer download failure.
- `tests/test_proxmox_template_inventory.py`: assert the checked-in image URL
  uses `noble/current/` and no path-like filename.

## Task 1: Harden Status Classification And Inventory Validation

**Files:**

- Modify: `roles/proxmox_template/tasks/main.yml`
- Modify: `tests/test_proxmox_template_role.py`

- [ ] **Step 1: Add failing tests for `qm status` rc handling and validation**

Append these tests to `tests/test_proxmox_template_role.py`:

```python
def test_status_error_fails_before_download_or_create(tmp_path: Path) -> None:
    proc = run_template_playbook(tmp_path=tmp_path, mode="status-error")
    assert proc.returncode != 0
    assert "Could not determine whether VMID 9000 exists" in proc.stdout
    assert "rc=13" in proc.stdout
    assert "permission denied" in proc.stdout
    log = (tmp_path / "qm.log").read_text()
    assert "status 9000" in log
    assert "create 9000" not in log


def test_vmid_below_100_is_rejected(tmp_path: Path) -> None:
    proc = run_template_playbook(
        tmp_path=tmp_path,
        mode="existing-template",
        extra_vars={"proxmox_template_vmid": 99},
    )
    assert proc.returncode != 0
    assert "Missing or invalid Proxmox template configuration" in proc.stdout


def test_cloud_image_filename_cannot_escape_cache_dir(tmp_path: Path) -> None:
    proc = run_template_playbook(
        tmp_path=tmp_path,
        mode="existing-template",
        extra_vars={"proxmox_template_cloud_image_filename": "../image.img"},
    )
    assert proc.returncode != 0
    assert "Missing or invalid Proxmox template configuration" in proc.stdout
```

Extend `write_fake_qm()` with this mode:

```bash
elif [[ "$mode" == "status-error" ]]; then
  case "$1" in
    status)
      echo "permission denied" >&2
      exit 13
      ;;
    *) echo "unexpected qm $*" >&2; exit 42 ;;
  esac
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
source .venv/bin/activate && pytest \
  tests/test_proxmox_template_role.py::test_status_error_fails_before_download_or_create \
  tests/test_proxmox_template_role.py::test_vmid_below_100_is_rejected \
  tests/test_proxmox_template_role.py::test_cloud_image_filename_cannot_escape_cache_dir \
  -q
```

Expected: FAIL. The current role treats `status-error` as absent, accepts VMID
`99`, and accepts a path-like image filename.

- [ ] **Step 3: Tighten validation and `qm status` classification**

In `roles/proxmox_template/tasks/main.yml`, change the validation assertions to:

```yaml
      - proxmox_template_vmid | int >= 100
      - proxmox_template_name | length > 0
      - proxmox_template_storage | length > 0
      - proxmox_template_bridge | length > 0
      - proxmox_template_cloud_image_url is match('^https?://')
      - proxmox_template_cloud_image_filename | length > 0
      - proxmox_template_cloud_image_filename is not search('/')
      - proxmox_template_cloud_image_filename is not search('\\.\\.')
      - proxmox_template_cloud_image_checksum is match('^sha256:[a-f0-9]{64}$')
      - proxmox_template_memory_mb | int >= 1024
      - proxmox_template_cores | int >= 1
```

After `Check whether template VMID exists`, insert:

```yaml
- name: Fail when template VMID status cannot be determined
  ansible.builtin.fail:
    msg: >-
      Could not determine whether VMID {{ proxmox_template_vmid }} exists.
      qm status rc={{ proxmox_template_status.rc }};
      stdout={{ proxmox_template_status.stdout | default('') }};
      stderr={{ proxmox_template_status.stderr | default('') }}.
  when: proxmox_template_status.rc not in [0, 2]
```

Keep `Record template existence` as:

```yaml
- name: Record template existence
  ansible.builtin.set_fact:
    proxmox_template_exists: "{{ proxmox_template_status.rc == 0 }}"
```

- [ ] **Step 4: Run the focused tests**

Run:

```bash
source .venv/bin/activate && pytest \
  tests/test_proxmox_template_role.py::test_status_error_fails_before_download_or_create \
  tests/test_proxmox_template_role.py::test_vmid_below_100_is_rejected \
  tests/test_proxmox_template_role.py::test_cloud_image_filename_cannot_escape_cache_dir \
  -q
```

Expected: PASS.

- [ ] **Step 5: Run the role test file**

Run:

```bash
source .venv/bin/activate && pytest tests/test_proxmox_template_role.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit status and validation hardening**

Run:

```bash
git add roles/proxmox_template/tasks/main.yml tests/test_proxmox_template_role.py
git commit -m "Harden Proxmox template status checks"
```

## Task 2: Adopt Current Ubuntu Image Policy

**Files:**

- Modify: `inventory/group_vars/proxmox/vars.yml`
- Modify: `docs/proxmox-template.md`
- Create: `tests/test_proxmox_template_inventory.py`

- [ ] **Step 1: Add failing inventory-policy test**

Create `tests/test_proxmox_template_inventory.py`:

```python
from __future__ import annotations

from pathlib import Path

import yaml


def load_proxmox_vars() -> dict[str, object]:
    return yaml.safe_load(Path("inventory/group_vars/proxmox/vars.yml").read_text())


def test_ubuntu_cloud_image_uses_current_url() -> None:
    data = load_proxmox_vars()
    assert data["proxmox_template_cloud_image_url"] == (
        "https://cloud-images.ubuntu.com/noble/current/"
        "noble-server-cloudimg-amd64.img"
    )


def test_cloud_image_filename_is_not_path_like() -> None:
    data = load_proxmox_vars()
    filename = str(data["proxmox_template_cloud_image_filename"])
    assert "/" not in filename
    assert ".." not in filename
```

- [ ] **Step 2: Run the inventory-policy test to verify it fails**

Run:

```bash
source .venv/bin/activate && pytest tests/test_proxmox_template_inventory.py -q
```

Expected: FAIL because the inventory still uses a dated `20260323` URL and
filename.

- [ ] **Step 3: Switch inventory to `noble/current/`**

Change `inventory/group_vars/proxmox/vars.yml` to:

```yaml
proxmox_template_cloud_image_url: >-
  https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
proxmox_template_cloud_image_filename: "noble-server-cloudimg-amd64.img"
proxmox_template_cloud_image_checksum: >-
  sha256:6e7016f2c9f4d3c00f48789eb6b9043ba2172ccc1b6b1eaf3ed1e29dd3e52bb3
```

The checksum is intentionally still pinned. When Canonical updates
`noble/current/`, operators update the checksum as a reviewed inventory change.

- [ ] **Step 4: Document image update policy**

In `docs/proxmox-template.md`, replace the current image note with:

````markdown
The Ubuntu image URL tracks `noble/current/`, while the SHA256 checksum remains
pinned in `inventory/group_vars/proxmox/vars.yml`. This avoids dated snapshot
URL rot without silently accepting a changed base image.

When Canonical publishes a new current image, the playbook fails checksum
verification until the operator deliberately updates the checksum. Bump it with:

```bash
curl -fsS https://cloud-images.ubuntu.com/noble/current/SHA256SUMS |
  rg 'noble-server-cloudimg-amd64\.img$'
```

Copy the reported SHA256 into `proxmox_template_cloud_image_checksum`, run
`make check`, and commit the inventory change.
````

- [ ] **Step 5: Run the inventory-policy test**

Run:

```bash
source .venv/bin/activate && pytest tests/test_proxmox_template_inventory.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit image policy changes**

Run:

```bash
git add inventory/group_vars/proxmox/vars.yml docs/proxmox-template.md \
  tests/test_proxmox_template_inventory.py
git commit -m "Document Ubuntu cloud image update policy"
```

## Task 3: Add PVE Version Guard And Template Lock

**Files:**

- Modify: `roles/proxmox_template/defaults/main.yml`
- Modify: `roles/proxmox_template/tasks/main.yml`
- Modify: `tests/test_proxmox_template_role.py`
- Modify: `docs/proxmox-template.md`

- [ ] **Step 1: Add fake `pveversion` helper and default lock test variables**

In `tests/test_proxmox_template_role.py`, add this helper after
`write_fake_qm()`:

```python
def write_fake_pveversion(tmp_path: Path) -> Path:
    pveversion_path = tmp_path / "pveversion"
    pveversion_path.write_text(
        r"""#!/usr/bin/env bash
set -euo pipefail
mode="${FAKE_PVEVERSION_MODE:-pve8}"

if [[ "$mode" == "pve8" ]]; then
  printf 'proxmox-ve: 8.2.0\n'
elif [[ "$mode" == "pve7" ]]; then
  printf 'proxmox-ve: 7.4.0\n'
else
  echo "pveversion failed" >&2
  exit 12
fi
"""
    )
    pveversion_path.chmod(pveversion_path.stat().st_mode | stat.S_IXUSR)
    return pveversion_path
```

In `base_extra_vars()`, add:

```python
        "proxmox_template_lock_dir": str(tmp_path / "template.lock"),
```

In `run_template_playbook()`, call `write_fake_pveversion(tmp_path)` after
`write_fake_qm(tmp_path, mode)`.

- [ ] **Step 2: Add failing tests for PVE 7 and lock contention**

Append these tests:

```python
def test_pve_7_is_rejected_before_status_check(tmp_path: Path) -> None:
    inventory = tmp_path / "hosts.yml"
    log = tmp_path / "qm.log"
    write_inventory(inventory)
    write_fake_qm(tmp_path, "existing-template")
    write_fake_pveversion(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_QM_MODE": "existing-template",
        "FAKE_QM_LOG": str(log),
        "FAKE_QM_STATE": str(tmp_path / "qm.state"),
        "FAKE_PVEVERSION_MODE": "pve7",
    }
    proc = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            str(inventory),
            "playbooks/provision-template.yml",
            "-e",
            json.dumps(base_extra_vars(tmp_path)),
        ],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    assert proc.returncode != 0
    assert "Proxmox VE 8 is required" in proc.stdout
    assert not log.exists()


def test_template_lock_contention_fails_before_status_check(tmp_path: Path) -> None:
    lock_dir = tmp_path / "template.lock"
    lock_dir.mkdir()
    proc = run_template_playbook(tmp_path=tmp_path, mode="existing-template")
    assert proc.returncode != 0
    assert "Could not acquire Proxmox template lock" in proc.stdout
    assert not (tmp_path / "qm.log").exists()
```

- [ ] **Step 3: Run the focused tests to verify they fail**

Run:

```bash
source .venv/bin/activate && pytest \
  tests/test_proxmox_template_role.py::test_pve_7_is_rejected_before_status_check \
  tests/test_proxmox_template_role.py::test_template_lock_contention_fails_before_status_check \
  -q
```

Expected: FAIL because the role does not probe PVE version or acquire a lock.

- [ ] **Step 4: Add role defaults**

Append to `roles/proxmox_template/defaults/main.yml`:

```yaml
proxmox_template_lock_dir: "/var/lock/prox-github-runner-template-{{ proxmox_template_vmid }}.lock"
```

- [ ] **Step 5: Add PVE 8 guard**

Insert after validation in `roles/proxmox_template/tasks/main.yml`:

```yaml
- name: Read Proxmox VE version
  ansible.builtin.command:
    argv:
      - pveversion
      - -v
  register: proxmox_template_pveversion
  changed_when: false
  failed_when: false

- name: Fail when Proxmox VE version cannot be read
  ansible.builtin.fail:
    msg: >-
      Could not read Proxmox VE version with pveversion -v.
      rc={{ proxmox_template_pveversion.rc }};
      stdout={{ proxmox_template_pveversion.stdout | default('') }};
      stderr={{ proxmox_template_pveversion.stderr | default('') }}.
  when: proxmox_template_pveversion.rc != 0

- name: Require Proxmox VE 8 for import-from disk import
  ansible.builtin.assert:
    that:
      - proxmox_template_pveversion.stdout is search('(?m)^proxmox-ve:\s*8\.')
    fail_msg: >-
      Proxmox VE 8 is required because this role uses qm set
      --scsi0 storage:0,import-from=...
      pveversion output: {{ proxmox_template_pveversion.stdout | default('') }}
```

- [ ] **Step 6: Add lock acquisition and release**

Insert after the PVE 8 guard:

```yaml
- name: Acquire Proxmox template lock
  ansible.builtin.command:
    argv:
      - mkdir
      - "{{ proxmox_template_lock_dir }}"
  register: proxmox_template_lock
  changed_when: proxmox_template_lock.rc == 0
  failed_when: false

- name: Fail when Proxmox template lock is held
  ansible.builtin.fail:
    msg: >-
      Could not acquire Proxmox template lock {{ proxmox_template_lock_dir }}.
      Another template provisioning run may be active. If no run is active,
      remove the stale lock directory manually.
      mkdir rc={{ proxmox_template_lock.rc }};
      stderr={{ proxmox_template_lock.stderr | default('') }}.
  when: proxmox_template_lock.rc != 0
```

Then move every existing task from `Check whether template VMID exists` through
`Assert final VM is the expected template` under this block without changing
their bodies in this step. The moved task sequence should remain:

1. status check;
2. status rc classification;
3. existence fact;
4. existing config read;
5. existing-name/template assertions;
6. create block with its rescue/always handling;
7. final config read;
8. final template assertion.

The wrapper and release task should look like this:

```yaml
- name: Converge Proxmox template while holding lock
  block:
    - name: Check whether template VMID exists
      ansible.builtin.command:
        argv:
          - qm
          - status
          - "{{ proxmox_template_vmid | string }}"
      register: proxmox_template_status
      changed_when: false
      failed_when: false
  always:
    - name: Release Proxmox template lock
      ansible.builtin.file:
        path: "{{ proxmox_template_lock_dir }}"
        state: absent
```

- [ ] **Step 7: Run focused tests**

Run:

```bash
source .venv/bin/activate && pytest \
  tests/test_proxmox_template_role.py::test_pve_7_is_rejected_before_status_check \
  tests/test_proxmox_template_role.py::test_template_lock_contention_fails_before_status_check \
  -q
```

Expected: PASS.

- [ ] **Step 8: Run all template role tests**

Run:

```bash
source .venv/bin/activate && pytest tests/test_proxmox_template_role.py -q
```

Expected: PASS.

- [ ] **Step 9: Document PVE and lock behavior**

Add this section to `docs/proxmox-template.md`:

````markdown
## Proxmox Requirements

The role requires Proxmox VE 8.x because it imports the cloud image with
`qm set --scsi0 <storage>:0,import-from=<path>`. The playbook checks
`pveversion -v` before downloading the image or mutating a VM.

The role also acquires a host-side lock directory before checking or creating
the template:

```text
/var/lock/prox-github-runner-template-<vmid>.lock
```

A second concurrent run fails before touching Proxmox. If a prior run was
interrupted and no Ansible process is active, remove the stale lock directory
manually and rerun the playbook.
````

- [ ] **Step 10: Commit PVE and locking changes**

Run:

```bash
git add roles/proxmox_template/defaults/main.yml \
  roles/proxmox_template/tasks/main.yml tests/test_proxmox_template_role.py \
  docs/proxmox-template.md
git commit -m "Add Proxmox template version and lock guards"
```

## Task 4: Improve Download Failure Message And Recovery Docs

**Files:**

- Modify: `roles/proxmox_template/tasks/main.yml`
- Modify: `tests/test_proxmox_template_role.py`
- Modify: `docs/proxmox-template.md`

- [ ] **Step 1: Add failing test for download failure messaging**

Append this test to `tests/test_proxmox_template_role.py`:

```python
def test_cloud_image_download_failure_names_url_and_checksum(tmp_path: Path) -> None:
    proc = run_template_playbook(tmp_path=tmp_path, mode="create-success")
    assert proc.returncode != 0
    assert "Ubuntu cloud image download failed" in proc.stdout
    assert "proxmox_template_cloud_image_url" in proc.stdout
    assert "proxmox_template_cloud_image_checksum" in proc.stdout
    assert "create 9000" not in (tmp_path / "qm.log").read_text()
```

This uses the default `https://example.invalid/image.img` from
`base_extra_vars()` and should fail before any VM creation.

- [ ] **Step 2: Run the download failure test to verify it fails**

Run:

```bash
source .venv/bin/activate && pytest \
  tests/test_proxmox_template_role.py::test_cloud_image_download_failure_names_url_and_checksum \
  -q
```

Expected: FAIL because the current failure message is generic.

- [ ] **Step 3: Preserve explicit download failures**

Inside the `Create Ubuntu cloud image template` block, insert this task before
`Download Ubuntu cloud image`:

```yaml
- name: Record template creation phase before image download
  ansible.builtin.set_fact:
    proxmox_template_creation_phase: download
```

Keep `Download Ubuntu cloud image` as a normal `get_url` task. After it, insert:

```yaml
- name: Record template creation phase before VM mutation
  ansible.builtin.set_fact:
    proxmox_template_creation_phase: qm
```

At the start of the existing `rescue:` block for `Create Ubuntu cloud image
template`, insert:

```yaml
    - name: Report Ubuntu cloud image download failure
      ansible.builtin.fail:
        msg: >-
          Ubuntu cloud image download failed before VM creation.
          Check proxmox_template_cloud_image_url={{
          proxmox_template_cloud_image_url }} and
          proxmox_template_cloud_image_checksum={{
          proxmox_template_cloud_image_checksum }}.
      when: proxmox_template_creation_phase | default('') == 'download'
```

Then update the existing partial-VM rescue tasks with these `when` clauses:

```yaml
- name: Check for partial template VM after failure
  ansible.builtin.command:
    argv:
      - qm
      - status
      - "{{ proxmox_template_vmid | string }}"
  register: proxmox_template_partial_status
  changed_when: false
  failed_when: false
  when: proxmox_template_creation_phase | default('') != 'download'

- name: Destroy partial template VM after failure
  ansible.builtin.command:
    argv:
      - qm
      - destroy
      - "{{ proxmox_template_vmid | string }}"
      - --purge
  register: proxmox_template_destroy_result
  changed_when: proxmox_template_partial_status.rc == 0
  failed_when: false
  when:
    - proxmox_template_creation_phase | default('') != 'download'
    - proxmox_template_partial_status.rc == 0

- name: Fail when partial template cleanup fails
  ansible.builtin.fail:
    msg: >-
      Partial VM cleanup failed for VMID {{ proxmox_template_vmid }}.
      destroy rc={{ proxmox_template_destroy_result.rc }};
      stdout={{ proxmox_template_destroy_result.stdout | default('') }};
      stderr={{ proxmox_template_destroy_result.stderr | default('') }}.
  when:
    - proxmox_template_creation_phase | default('') != 'download'
    - proxmox_template_partial_status.rc == 0
    - proxmox_template_destroy_result.rc != 0

- name: Report template creation failure after cleanup
  ansible.builtin.fail:
    msg: >-
      Template creation failed for VMID {{ proxmox_template_vmid }}.
      Partial VM cleanup was completed.
  when:
    - proxmox_template_creation_phase | default('') != 'download'
    - proxmox_template_partial_status.rc == 0

- name: Report template creation failure without cleanup
  ansible.builtin.fail:
    msg: >-
      Template creation failed for VMID {{ proxmox_template_vmid }}.
      No partial VM was present for cleanup.
  when:
    - proxmox_template_creation_phase | default('') != 'download'
    - proxmox_template_partial_status.rc != 0
```

The final create block should retain the existing `always:` image cleanup. Keep
the download task as a normal task in the existing create block; do not wrap it
in its own nested block/rescue because the outer rescue would still handle the
failure and can mask the download cause.

- [ ] **Step 4: Run focused and full template tests**

Run:

```bash
source .venv/bin/activate && pytest \
  tests/test_proxmox_template_role.py::test_cloud_image_download_failure_names_url_and_checksum \
  tests/test_proxmox_template_role.py -q
```

Expected: PASS.

- [ ] **Step 5: Document stuck partial-VM recovery**

Add this section to `docs/proxmox-template.md`:

````markdown
## Recovering From A Stuck Partial VM

If cleanup fails, the next run will stop because the VMID exists but is not a
template. Recover manually on the Proxmox host:

```bash
qm unlock <vmid>
qm destroy <vmid> --purge --skiplock
```

Use the configured `proxmox_template_vmid`. Only run these commands after
confirming the VMID is the failed template VM and not a real workload.
````

- [ ] **Step 6: Commit download and recovery docs**

Run:

```bash
git add roles/proxmox_template/tasks/main.yml tests/test_proxmox_template_role.py \
  docs/proxmox-template.md
git commit -m "Clarify Proxmox template failure recovery"
```

## Task 5: Final Verification

**Files:**

- Modify only files touched by prior tasks if verification finds issues.

- [ ] **Step 1: Run self-review scans**

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

- [ ] **Step 2: Run full verification**

Run:

```bash
make check
```

Expected: PASS for YAML lint, Ansible lint, syntax checks, ruff, ty, pytest,
and inventory parsing.

- [ ] **Step 3: Commit verification fixes if needed**

If Step 1 or Step 2 required changes, commit only those fixes:

```bash
git add docs inventory roles tests
git commit -m "Polish Proxmox template review fixes"
```

If no files changed, do not create an empty commit.

## Review Feedback Mapping

- Finding 1 is covered by Task 1.
- Finding 2 is covered by Task 2 and Task 4.
- Finding 3 is covered by Task 3.
- Finding 4 is covered by Task 4.
- Finding 5 is covered by Task 1 and Task 2.
