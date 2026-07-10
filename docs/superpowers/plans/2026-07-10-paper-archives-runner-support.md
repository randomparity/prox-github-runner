# Paper-Archives Runner Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Sprints 3–7 of the Proxmox GitHub runner so `drc-dot-nz/paper-archives` can offload all its `ubuntu-latest` CI jobs to 3–4 concurrent self-hosted runner services on one Ubuntu 24.04 VM.

**Architecture:** Four new Ansible roles (`proxmox_vm`, `runner_host`, `github_runner`) plus operational playbooks, converged non-destructively. External commands (`qm`, `gh`, `apt-get`, `systemctl`, the runner `config.sh`/`svc.sh`) are exercised in tests through **fake binaries injected on `PATH`** and a local inventory with `ansible_connection: local` — the same idiom already used by `tests/test_proxmox_template_role.py`. A companion PR in `paper-archives` re-labels its workflows.

**Tech Stack:** Ansible (community.proxmox), Python 3.13 + pytest for tests, `ansible-lint`/`yamllint`/`ruff`/`ty` guardrails, systemd, GitHub Actions self-hosted runner.

## Global Constraints

- Target repo: `github_runner_target_repo` = `drc-dot-nz/paper-archives`; required label `paper-archives`; label set `self-hosted,linux,x64,paper-archives` (from `inventory/group_vars/all/vars.yml`, verbatim).
- Private-repo-only. Every GitHub-changing playbook (`site.yml`, `setup-runner.yml`, `unregister-runner.yml`) must invoke the existing `preflight` role before any registration/token request.
- Reruns are non-destructive: never delete/rebuild the VM by default; grow-only disk; identity changes (IP, bridge, VLAN, template) fail when the VM exists.
- Runner auto-update disabled at registration (`--disableupdate`).
- The GitHub PAT is never written to persistent runner config; only short-lived registration/removal tokens touch the VM, and only transiently.
- Guardrails (run from repo root): `make lint` (yamllint, ansible-lint, ruff, ry→`ty check`), `make syntax` (ansible syntax-check), `make test` (pytest). `make check` runs lint+syntax+test+inventory. Every commit must leave `make check` green.
- Conventional Commits; each commit ends with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- New inventory defaults introduced by this plan live in role `defaults/main.yml`; operator-facing values live in `inventory/group_vars/{runner,proxmox}/vars.yml`.

---

## File Structure

**New role — `proxmox_vm` (Sprint 3):**
- `roles/proxmox_vm/defaults/main.yml` — VM sizing (16 vCPU / 32768 MB / 256 GB), net, firewall/egress allowlist + deny CIDRs.
- `roles/proxmox_vm/meta/main.yml` — role metadata.
- `roles/proxmox_vm/tasks/main.yml` — validate, clone (non-destructive), converge safe settings, attach firewall, start, wait SSH, wait cloud-init.
- `playbooks/provision-runner-vm.yml` — thin wrapper targeting `proxmox`.
- `tests/test_proxmox_vm_role.py` — fake `qm` behavioral tests.

**New role — `runner_host` (Sprint 4):**
- `roles/runner_host/defaults/main.yml` — package list, Tauri libs, Docker, per-service env layout, tool versions.
- `roles/runner_host/tasks/main.yml` — packages, runner user, Docker, clang, Python 3.12, Tauri libs, passwordless sudo, per-service dirs (`_work`, `RUNNER_TOOL_CACHE`, `RUSTUP_HOME`), guard/cleanup script install.
- `roles/runner_host/files/prox-github-runner-guard.sh` — public-repo guard (systemctl-stop primitive).
- `roles/runner_host/files/prox-github-runner-cleanup.sh` — post-job cleanup (scans every per-service `_work`).
- `roles/runner_host/templates/*.j2` — systemd guard timer/service, sudoers drop-in.
- `playbooks/setup-runner.yml` — converge; **created in Sprint 4 as preflight → runner_host, extended in Sprint 5 (Task 5.6) to add github_runner** so `make syntax` stays green each sprint.
- `tests/test_runner_host_role.py`, `tests/test_runner_guard_script.py`, `tests/test_runner_cleanup_script.py`.

**New role — `github_runner` (Sprint 5):**
- `roles/github_runner/defaults/main.yml` — `github_runner_count`, `github_runner_version`, download URL/checksum, install root.
- `roles/github_runner/tasks/main.yml` — preflight guard, token request, download+verify, per-service register (unique names), systemd install, job-hook wiring.
- `roles/github_runner/tasks/unregister.yml` — idempotent removal reused by cleanup.
- `tests/test_github_runner_role.py` — behavioral tests: `gh` as a PATH fake; the runner's `config.sh`/`svc.sh` are directory-local (baked into the served tarball, invoked by path), not PATH fakes.

**Operational playbooks (Sprint 6):**
- `playbooks/unregister-runner.yml`, `playbooks/check-runner-health.yml`.
- `playbooks/site.yml` — full non-destructive converge chain.
- `tests/test_unregister_playbook.py`, `tests/test_health_playbook.py`.

**Smoke + companion (Sprint 7):**
- `docs/smoke-workflow.md` + `templates/paper-archives-smoke.yml` — copyable `workflow_dispatch` workflow.
- `playbooks/run-smoke-workflow.yml` — operator dispatch+poll.
- Companion PR (separate `paper-archives` branch): edit `.github/workflows/ci.yml`.

---

## PHASE / SPRINT 3 — `proxmox_vm` role

**Deliverable:** a non-destructive role + playbook that clones the template into one runner VM, applies sizing, attaches Proxmox firewall isolation, starts it, and waits for SSH + cloud-init. Verified with a fake `qm`.

### Task 3.1: Role skeleton + variable validation

**Files:**
- Create: `roles/proxmox_vm/meta/main.yml`, `roles/proxmox_vm/defaults/main.yml`, `roles/proxmox_vm/tasks/main.yml`
- Create: `playbooks/provision-runner-vm.yml`
- Test: `tests/test_proxmox_vm_role.py`

**Interfaces:**
- Consumes (inventory): `runner_vm_id`, `runner_vm_name`, `runner_vm_ip`, `runner_vm_gateway`, `runner_vm_cidr`, `runner_vm_nameserver`, `proxmox_template_vmid`, `proxmox_storage`, `proxmox_template_bridge`.
- Produces: `defaults` names `proxmox_vm_cpu` (16), `proxmox_vm_memory_mb` (32768), `proxmox_vm_disk_gb` (256), `proxmox_vm_vlan`, `proxmox_vm_lock_dir`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proxmox_vm_role.py
from __future__ import annotations
import json, os, subprocess, sys
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
    # Supplies every var the role requires. The tmp inventory/play bypass the
    # repo's group_vars, so (like tests/test_proxmox_template_role.py) each run
    # must pass the full set via -e; tests override only the var under test.
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
        "proxmox_vm_wait_for_ssh": False,  # gate the SSH/cloud-init waits in tests
    }


def run_role(tmp_path: Path, overrides: dict | None = None,
             env_extra: dict | None = None):
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
    # Mirror run_template_playbook: inherit os.environ (HOME, ansible.cfg
    # discovery) and PREPEND tmp_path so fake qm/ssh shadow the real ones and
    # ansible-playbook resolves from .venv/bin.
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    env.update(env_extra or {})
    cmd = ["ansible-playbook", "-i", str(inv), str(play), "-e", json.dumps(extra)]
    return subprocess.run(cmd, text=True, capture_output=True,
                          cwd=Path.cwd(), env=env)


def test_missing_runner_ip_fails(tmp_path: Path) -> None:
    proc = run_role(tmp_path, {"runner_vm_ip": ""})
    assert proc.returncode != 0
    assert "Missing or invalid runner VM configuration" in proc.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_proxmox_vm_role.py::test_missing_runner_ip_fails -v`
Expected: FAIL (role `proxmox_vm` not found).

- [ ] **Step 3: Write the role skeleton + validation**

`roles/proxmox_vm/meta/main.yml`:
```yaml
---
galaxy_info:
  role_name: proxmox_vm
  description: Clone and converge one runner VM on Proxmox, non-destructively.
dependencies: []
```

`roles/proxmox_vm/defaults/main.yml`:
```yaml
---
proxmox_vm_cpu: 16
proxmox_vm_memory_mb: 32768
proxmox_vm_disk_gb: 256
proxmox_vm_vlan: "{{ proxmox_template_vlan | default(none) }}"
proxmox_vm_lock_dir: "/var/lock/prox-github-runner-vm-{{ runner_vm_id }}.lock"
proxmox_vm_net0: >-
  virtio,bridge={{ proxmox_template_bridge }}{%- if proxmox_vm_vlan is not none -%},tag={{ proxmox_vm_vlan }}{%- endif -%}
proxmox_vm_wait_for_ssh: true   # tests set false to skip the live SSH/cloud-init waits
```

`roles/proxmox_vm/tasks/main.yml` (validation block):
```yaml
---
- name: Validate runner VM variables
  ansible.builtin.assert:
    that:
      - (runner_vm_id | string) is match('\\A[0-9]+\\Z')
      - runner_vm_id | int >= 100
      - runner_vm_name | length > 0
      - runner_vm_ip | length > 0
      - runner_vm_ip is match('\\A[0-9]{1,3}(\\.[0-9]{1,3}){3}\\Z')
      - runner_vm_gateway | length > 0
      - (runner_vm_cidr | int) >= 1 and (runner_vm_cidr | int) <= 32
      - proxmox_vm_cpu | int >= 1
      - proxmox_vm_memory_mb | int >= 2048
      - proxmox_vm_disk_gb | int >= 32
    fail_msg: "Missing or invalid runner VM configuration."
```

`playbooks/provision-runner-vm.yml`:
```yaml
---
- name: Provision the runner VM
  hosts: proxmox
  gather_facts: false
  roles:
    - proxmox_vm
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_proxmox_vm_role.py::test_missing_runner_ip_fails -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add roles/proxmox_vm playbooks/provision-runner-vm.yml tests/test_proxmox_vm_role.py
git commit -m "feat(proxmox_vm): add role skeleton and variable validation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 3.2: Non-destructive clone + safe convergence

**Files:**
- Modify: `roles/proxmox_vm/tasks/main.yml`
- Test: `tests/test_proxmox_vm_role.py`

**Interfaces:**
- Consumes: fake `qm` returning VM status/config (mirror `write_fake_qm` in `tests/test_proxmox_template_role.py`).
- Produces: task names `Clone runner VM from template`, `Converge CPU and memory`, `Fail on identity change`.

- [ ] **Step 1: Write failing tests** — add to `tests/test_proxmox_vm_role.py`:

```python
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
  existing:config) printf 'name: paper-archives-runner\nnet0: virtio,bridge=vmbr0\nscsi0: local-lvm:vm-2100-disk-0,size=256G\n' ;;
  existing:set) exit 0 ;;
  existing:start) exit 0 ;;
  *) echo "unexpected qm $*" >&2; exit 42 ;;
esac
"""
    )
    qm.chmod(0o755)
```

**Rule:** when a later task introduces a new `qm` subcommand (e.g. `start` in Task 3.4), add its `mode:subcommand) exit 0 ;;` case to `write_fake_qm` in the same task, or the catch-all `exit 42` fails that task's run. (`start` cases are already included above.)

```python
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
```

(`run_role` already accepts `env_extra` and merges it into the inherited env — see Task 3.1.)

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest tests/test_proxmox_vm_role.py -v`
Expected: the two new tests FAIL (clone/convergence tasks absent).

- [ ] **Step 3: Implement clone + convergence** — append to `roles/proxmox_vm/tasks/main.yml`:

```yaml
- name: Read runner VM status
  ansible.builtin.command:
    argv: ["qm", "status", "{{ runner_vm_id }}"]
  register: proxmox_vm_status
  changed_when: false
  failed_when: false

- name: Read runner VM config when present
  ansible.builtin.command:
    argv: ["qm", "config", "{{ runner_vm_id }}"]
  register: proxmox_vm_config
  changed_when: false
  when: proxmox_vm_status.rc == 0

- name: Fail on identity change (bridge) when VM exists
  ansible.builtin.fail:
    msg: >-
      Refusing an identity change on existing VM {{ runner_vm_id }}.
      Bridge change requires explicit rebuild (see unregister-runner.yml).
  when:
    - proxmox_vm_status.rc == 0
    - proxmox_vm_config.stdout is defined
    - "'bridge=' ~ proxmox_template_bridge not in proxmox_vm_config.stdout"

- name: Clone runner VM from template
  ansible.builtin.command:
    argv: ["qm", "clone", "{{ proxmox_template_vmid }}", "{{ runner_vm_id }}",
           "--name", "{{ runner_vm_name }}", "--full", "1"]
  when: proxmox_vm_status.rc != 0
  changed_when: true

- name: Converge CPU and memory
  ansible.builtin.command:
    argv: ["qm", "set", "{{ runner_vm_id }}",
           "--cores", "{{ proxmox_vm_cpu }}",
           "--memory", "{{ proxmox_vm_memory_mb }}"]
  changed_when: true

- name: Grow boot disk (grow-only)
  ansible.builtin.command:
    argv: ["qm", "resize", "{{ runner_vm_id }}", "scsi0", "{{ proxmox_vm_disk_gb }}G"]
  when: proxmox_vm_status.rc != 0
  changed_when: true
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_proxmox_vm_role.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add roles/proxmox_vm/tasks/main.yml tests/test_proxmox_vm_role.py
git commit -m "feat(proxmox_vm): clone template and converge safe settings

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 3.3: Proxmox firewall isolation (egress allowlist + deny CIDRs)

**Files:**
- Modify: `roles/proxmox_vm/defaults/main.yml`, `roles/proxmox_vm/tasks/main.yml`
- Test: `tests/test_proxmox_vm_role.py`

**Interfaces:**
- Produces defaults: `proxmox_vm_denied_cidrs` (list: Proxmox mgmt host/24, control host), `proxmox_vm_allowed_egress_hosts` (documented list from spec Amendment 4). Firewall applied with `qm set --firewall 1` + a rules file written via the fake `qm`/`pvesh` shim. (Because live firewall wiring is Proxmox-version-specific, the test asserts the role *emits* a deny rule for each denied CIDR and enables the firewall; the concrete `pvesh`/rules path is resolved at implementation.)

- [ ] **Step 1: Failing test** — assert the fake `qm` log enables the firewall (`--firewall 1`) and that the rendered rules file (`proxmox_vm_fw_rules_path`, set to a tmp path in `base_extra_vars`) contains **both** each denied CIDR with `REJECT`/`DROP` **and** the load-bearing Amendment-4 allow hosts (a dropped/empty allowlist must fail this test).

```python
def test_firewall_denies_cidrs_and_allows_egress_hosts(tmp_path: Path) -> None:
    write_fake_qm(tmp_path, "existing")
    log = tmp_path / "qm.log"
    proc = run_role(
        tmp_path, {},
        env_extra={"FAKE_QM_LOG": str(log), "FAKE_QM_MODE": "existing"},
    )
    assert proc.returncode == 0, proc.stdout
    assert "--firewall 1" in log.read_text()
    body = (tmp_path / "fw.rules").read_text()
    assert "192.168.20.10" in body               # proxmox mgmt host denied
    assert "REJECT" in body or "DROP" in body
    for host in ("static.rust-lang.org", "index.crates.io", "pypi.org",
                 "objects.githubusercontent.com"):
        assert host in body                       # Amendment-4 allow rules present
```

- [ ] **Step 2: Run to verify fail** — `.venv/bin/pytest tests/test_proxmox_vm_role.py::test_firewall_denies_management_cidrs -v` → FAIL.

- [ ] **Step 3: Implement** — add defaults and a `template` task that renders the rules file and a `qm set --firewall 1` command. Defaults:

```yaml
proxmox_vm_fw_rules_path: "/etc/pve/firewall/{{ runner_vm_id }}.fw"
proxmox_vm_denied_cidrs:
  - "{{ proxmox_api_host }}/32"
  - "192.168.20.0/24"   # Proxmox management network (operator adjusts)
proxmox_vm_allowed_egress_hosts:   # Amendment 4 (spec) — rendered as annotated allow entries
  - static.rust-lang.org
  - index.crates.io
  - static.crates.io
  - pypi.org
  - files.pythonhosted.org
  - objects.githubusercontent.com
  # DNS, NTP, GitHub API, Ubuntu mirrors, Docker registry, and the Actions
  # cache backend are resolved from GitHub's published meta ranges at
  # implementation and added here.
```
The template renders each `proxmox_vm_allowed_egress_hosts` entry as an annotated OUT ACCEPT rule, then each `proxmox_vm_denied_cidrs` entry as an OUT REJECT rule, then a default-deny. Emit the hostname as a rule comment so the positive test can assert its presence even though the live rule resolves to an IP set.

- [ ] **Step 4: Run to verify pass** — expected PASS.

- [ ] **Step 5: Commit**

```bash
git add roles/proxmox_vm tests/test_proxmox_vm_role.py
git commit -m "feat(proxmox_vm): apply Proxmox firewall isolation for the runner VM

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 3.4: Start VM, wait for SSH, wait for cloud-init

**Files:** Modify `roles/proxmox_vm/tasks/main.yml`; Test `tests/test_proxmox_vm_role.py`.

The SSH and cloud-init waits touch a live host, so they are gated behind `proxmox_vm_wait_for_ssh` (default `true`; `base_extra_vars` sets it `false`). `wait_for` is a module and cannot be shadowed by a PATH fake, so the test must not execute it against the unroutable runner IP.

- [ ] **Step 1: Failing test** — with fake `qm` mode `absent` and `proxmox_vm_wait_for_ssh=False`, assert the fake-`qm` log contains `start`, and assert (by reading `roles/proxmox_vm/tasks/main.yml`) that a `cloud-init status --wait` invocation and a `wait_for` task exist and are both guarded by `when: proxmox_vm_wait_for_ssh`.

```python
def test_start_logged_and_waits_are_gated(tmp_path: Path) -> None:
    write_fake_qm(tmp_path, "absent")
    log = tmp_path / "qm.log"
    proc = run_role(tmp_path, {}, env_extra={"FAKE_QM_LOG": str(log), "FAKE_QM_MODE": "absent"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "start" in log.read_text()
    tasks = Path("roles/proxmox_vm/tasks/main.yml").read_text()
    assert "cloud-init" in tasks and "wait_for" in tasks
    assert "proxmox_vm_wait_for_ssh" in tasks
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** — append:

```yaml
- name: Start runner VM
  ansible.builtin.command:
    argv: ["qm", "start", "{{ runner_vm_id }}"]
  when: proxmox_vm_status.rc != 0
  changed_when: true

- name: Wait for SSH on the runner VM
  ansible.builtin.wait_for:
    host: "{{ runner_vm_ip }}"
    port: 22
    timeout: 600
  delegate_to: localhost
  when: proxmox_vm_wait_for_ssh | bool

- name: Wait for cloud-init to finish
  ansible.builtin.command:
    # Bound with the coreutils `timeout` binary — `timeout:` is NOT a valid
    # command-module or task keyword. 900s wall-clock cap on the remote wait.
    argv: ["timeout", "900", "ssh", "-o", "StrictHostKeyChecking=accept-new",
           "{{ runner_bootstrap_user }}@{{ runner_vm_ip }}",
           "cloud-init", "status", "--wait"]
  register: proxmox_vm_cloudinit
  changed_when: false
  failed_when: proxmox_vm_cloudinit.rc != 0
  when: proxmox_vm_wait_for_ssh | bool
```

The Task 3.4 test additionally asserts the tasks file contains no unsupported `timeout:` key on a `command` task (`assert "timeout: 900" not in tasks`) — a cheap guard against the invalid-keyword regression.

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** `feat(proxmox_vm): start VM and wait for SSH and cloud-init`.

### Task 3.5: Lint, syntax, playbook wiring

- [ ] **Step 1:** Run `make lint && make syntax` — fix any `ansible-lint`/`yamllint` findings (FQCN, `changed_when`, name casing) until clean.
- [ ] **Step 2:** Run `make test` — full pytest green.
- [ ] **Step 3: Commit** any lint fixes: `style(proxmox_vm): satisfy ansible-lint and yamllint`.

---

## PHASE / SPRINT 4 — `runner_host` role

**Deliverable:** the Ubuntu baseline: packages, runner user, Docker, clang, Python 3.12, Tauri libs, passwordless sudo, per-service directories/env, and the guard + cleanup scripts (installed but not yet wired to a runner).

**Verification strategy (differs from Sprint 3).** The convergence tasks here use OS-mutating Ansible **modules** (`ansible.builtin.apt`/`user`/`systemd`/`file`), not module-free CLIs. Those modules do **not** resolve through `PATH` (a fake binary cannot intercept them) and `apt` will not even import on the darwin dev host. So every system-mutating task is gated behind `runner_host_apply_system` (default `true`; `base_extra_vars` sets it **false**), exactly as Sprint 3 gates its live waits behind `proxmox_vm_wait_for_ssh`. With the gate false the role applies cleanly on any host without touching real state, and Sprint 4 tests assert behavior by **parsing** `roles/runner_host/{tasks,defaults}/main.yml` and the templates. The module-free **shell scripts** (guard, cleanup — Tasks 4.4/4.5) are separate files and keep real behavioral tests with fake `curl`/`systemctl`/`docker`. Using proper modules (not `command: apt-get`) keeps `make lint` clean — this repo has no `command-instead-of-module` exception.

### Task 4.1: Baseline packages + runner user + passwordless sudo

**Files:**
- Create: `roles/runner_host/{meta,defaults,tasks}/main.yml`, `roles/runner_host/templates/runner-sudoers.j2`
- Create: `playbooks/setup-runner.yml`
- Test: `tests/test_runner_host_role.py`

**Interfaces:**
- Produces defaults: `runner_host_packages` (git, curl, jq, build-essential, clang, python3.12, python3.12-venv, python3-pip, ca-certificates), `runner_host_tauri_libs` (the five `-dev` packages), `runner_host_user` (= `runner_bootstrap_user`), `runner_host_install_root` (`/opt/actions-runner`), `runner_host_apply_system` (default `true`).

**Test harness:** `tests/test_runner_host_role.py` reuses the `run_role` pattern from Task 3.1 (inherit `os.environ`, prepend `tmp_path` to `PATH`, pass `base_extra_vars` via `-e json`), renamed `run_runner_host`, with a play applying the `runner_host` role. Its `base_extra_vars` supplies `runner_bootstrap_user`, `github_runner_count`, `runner_host_install_root` (a `tmp_path` dir), and **`runner_host_apply_system: False`** so no real apt/user/systemd work runs. Tests then assert on the parsed role files.

- [ ] **Step 1: Failing test** — run the role with the gate false (returns 0 on any host), then parse the role files: assert `defaults/main.yml` lists clang, python3.12(+venv), python3-pip and the five Tauri libs; assert `tasks/main.yml` uses `ansible.builtin.apt` gated by `runner_host_apply_system`; assert the sudoers template grants `NOPASSWD:ALL`.

```python
from pathlib import Path

def test_baseline_declares_clang_python312_tauri_and_sudo(tmp_path):
    proc = run_runner_host(tmp_path)          # runner_host_apply_system=False
    assert proc.returncode == 0, proc.stdout + proc.stderr
    defaults = Path("roles/runner_host/defaults/main.yml").read_text()
    for pkg in ("clang", "python3.12", "python3.12-venv", "python3-pip",
                "libwebkit2gtk-4.1-dev", "libxdo-dev", "librsvg2-dev"):
        assert pkg in defaults
    tasks = Path("roles/runner_host/tasks/main.yml").read_text()
    assert "ansible.builtin.apt" in tasks
    assert "runner_host_apply_system" in tasks     # system tasks are gated
    sudoers = Path("roles/runner_host/templates/runner-sudoers.j2").read_text()
    assert "NOPASSWD:ALL" in sudoers
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — `ansible.builtin.apt` over `runner_host_packages + runner_host_tauri_libs`; `ansible.builtin.user` for the runner user; render sudoers drop-in `runner-sudoers.j2` (`{{ runner_host_user }} ALL=(ALL) NOPASSWD:ALL`) into `/etc/sudoers.d/` with `validate: "visudo -cf %s"`. **Every OS-mutating task carries `when: runner_host_apply_system | bool`.** Also create `playbooks/setup-runner.yml` with **only** `preflight` → `runner_host` (github_runner is added in Task 5.6) so `make syntax` passes this sprint.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(runner_host): install baseline packages, Tauri libs, runner user, sudo`.

### Task 4.2: Docker Engine + runner user in docker group

- [ ] **Step 1: Failing test** — with the gate false, parse `tasks/main.yml`: assert it adds the Docker apt repo and installs `docker-ce`, enables the service via `ansible.builtin.systemd`, and adds `runner_host_user` to the `docker` group via `ansible.builtin.user` with `groups: docker` / `append: true`; assert those tasks are gated by `runner_host_apply_system`.

```python
def test_docker_install_and_group(tmp_path):
    proc = run_runner_host(tmp_path)
    assert proc.returncode == 0, proc.stdout
    tasks = Path("roles/runner_host/tasks/main.yml").read_text()
    assert "docker-ce" in tasks
    assert "groups: docker" in tasks and "append: true" in tasks
    assert "ansible.builtin.systemd" in tasks
```

- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** Docker install tasks (upstream Docker apt repo + `docker-ce`) + `ansible.builtin.user` with `groups: docker, append: true` + `ansible.builtin.systemd` enable/start, all gated by `runner_host_apply_system`.
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(runner_host): install Docker Engine and grant runner docker access`.

### Task 4.3: Per-service directories and environment (RUNNER_TOOL_CACHE, RUSTUP_HOME)

**Interfaces:**
- Consumes: `github_runner_count` (default 3; declared in `runner_host/defaults` too so the role is testable standalone), `runner_host_apply_system`.
- Produces: for each index `1..N`, dirs `{{ runner_host_install_root }}/svc-<index>/{_work,_tool,rustup}`; `~/.cargo` stays shared. Each service's env file sets `RUNNER_TOOL_CACHE=<svc>/_tool`, `RUSTUP_HOME=<svc>/rustup`, `CARGO_HOME=~/.cargo`.

- [ ] **Step 1: Failing test** — with the gate false and `github_runner_count=3`, parse `tasks/main.yml`: assert a `loop` over `range(1, (github_runner_count | int) + 1)` creates the per-service dirs and renders an env file per service; assert the env template sets `RUSTUP_HOME` to the per-service `svc-<index>/rustup` and `CARGO_HOME` to the shared `~/.cargo`.

```python
def test_per_service_env_isolation(tmp_path):
    proc = run_runner_host(tmp_path, overrides={"github_runner_count": 3})
    assert proc.returncode == 0, proc.stdout
    tasks = Path("roles/runner_host/tasks/main.yml").read_text()
    assert "range(1, (github_runner_count | int) + 1)" in tasks
    env_tmpl = Path("roles/runner_host/templates/runner-env.j2").read_text()
    assert "RUSTUP_HOME=" in env_tmpl and "rustup" in env_tmpl
    assert "CARGO_HOME=" in env_tmpl
```

- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** a `loop: "{{ range(1, (github_runner_count | int) + 1) | list }}"` over `ansible.builtin.file` (dirs) and `ansible.builtin.template` (env file `runner-env.j2`), gated by `runner_host_apply_system`.
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(runner_host): create per-service work, tool-cache, and rustup dirs`.

### Task 4.4: Public-repo guard script (systemctl-stop primitive)

**Files:**
- Create: `roles/runner_host/files/prox-github-runner-guard.sh`, `roles/runner_host/templates/prox-github-runner-guard.{service,timer}.j2`
- Test: `tests/test_runner_guard_script.py`

**Interfaces:**
- Produces: guard script that (1) unauthenticated `GET /repos/<repo>` via `curl`; (2) on `200 private:false` (hard) or soft-failure threshold → `systemctl stop 'actions.runner.*'` for all services; (3) `404` resets soft counter. No PAT on the VM. Uses `shellcheck`-clean bash.

- [ ] **Step 1: Failing test** — a pure shell test: run the guard with `PATH` containing a fake `curl` (returns canned HTTP) and a fake `systemctl` logging args; assert on `private:false` the log shows `stop` for the runner units, and on `404` it does not.

```python
def test_guard_stops_all_services_on_public(tmp_path):
    # fake curl -> 200 {"private": false}; fake systemctl logs to file
    ...
    assert "stop" in systemctl_log.read_text()

def test_guard_noop_on_404(tmp_path):
    assert systemctl_log.read_text().strip() == ""
```

- [ ] **Step 2: Fail** (script absent).
- [ ] **Step 3: Implement** the guard script per spec "Concurrency-safe job tracking and guard": hard signal → `systemctl stop` all `actions.runner.*`; soft-failure counter in `/run/prox-github-runner/guard.soft`; log each decision to journald via `logger`. Then the systemd timer/service templates (every 15 min).
- [ ] **Step 4: Pass**; also run `shellcheck roles/runner_host/files/prox-github-runner-guard.sh` (pre-commit parity).
- [ ] **Step 5: Commit** `feat(runner_host): add public-repo guard with systemctl-stop primitive`.

### Task 4.5: Cleanup script (scans every per-service _work)

**Files:** Create `roles/runner_host/files/prox-github-runner-cleanup.sh`; Test `tests/test_runner_cleanup_script.py`.

- [ ] **Step 1: Failing test** — create fake `svc-1/_work` and `svc-2/_work` with an old dir (mtime > 7d, via `touch -d`) and a fresh dir; run cleanup with a fake `docker`; assert old dirs in **both** services are removed, fresh dirs kept, and `flock` on `maintenance.lock` is taken.
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** cleanup: `flock` on `/run/prox-github-runner/maintenance.lock`; loop over every `svc-*/_work` and `_temp`; remove entries older than 7 days; `docker system prune` age-gated (7d); log to journald with tag `prox-github-runner-cleanup`.
- [ ] **Step 4: Pass**; `shellcheck` clean.
- [ ] **Step 5: Commit** `feat(runner_host): add per-service workspace and docker cleanup`.

### Task 4.6: Lint/syntax/test sweep

- [ ] Run `make check`; fix findings; commit `style(runner_host): satisfy guardrails`.

---

## PHASE / SPRINT 5 — `github_runner` role

**Deliverable:** N uniquely-named runner services registered to `paper-archives`, self-update disabled, job hooks wired, preflight enforced first. Verified with fake `gh`, `config.sh`, `svc.sh`.

### Task 5.1: Preflight guard + target-repo mismatch

**Files:** Create `roles/github_runner/{meta,defaults,tasks}/main.yml`; Test `tests/test_github_runner_role.py`.

**Interfaces:**
- Consumes: `github_runner_target_repo`, `github_runner_labels`. **preflight is invoked at the playbook level** (`site.yml`/`setup-runner.yml`, per Global Constraints), NOT as a `github_runner` role dependency — so `meta/main.yml` `dependencies: []`, the Sprint 5 unit tests need no `vault_github_pat`, and preflight does not run twice. The role keeps its own lightweight target-repo mismatch guard (below).
- Produces defaults: `github_runner_count: 3`, `github_runner_version` (resolve latest stable at implementation; pin exact), `github_runner_sha256` (matching checksum), `github_runner_install_root: /opt/actions-runner`, `github_runner_name_prefix: "{{ runner_vm_name }}"`.

**Test harness:** `tests/test_github_runner_role.py` reuses the Task 3.1 `run_role` pattern (renamed `run_github_runner`), with `base_extra_vars` supplying `github_runner_target_repo`, `github_runner_labels`, `github_runner_count`, `github_runner_version`, `github_runner_sha256`, and `github_runner_install_root` (overridden to a `tmp_path` dir). No `vault_github_pat` — preflight is playbook-level, not a dependency.

**Fake-binary rules (important):** only `gh` (and, in Task 6.2, `systemctl`) are genuine PATH-resolved CLIs and are faked by prepending `tmp_path` to `PATH`. The runner's `config.sh`/`svc.sh` are **directory-local** scripts shipped inside the runner tarball and invoked as `./config.sh` / `sudo ./svc.sh` from each `svc-<index>` dir — they are never on `PATH`. So the Task 5.3 local HTTP server serves a tarball that **contains executable fake `config.sh`/`svc.sh`** which log their args; after unpack each `svc-<index>` has them, and the role invokes them by path (`chdir` into the svc dir, run `./config.sh`), matching real runner usage. Each Sprint 5 task's test must supply **all prior fakes** (gh + the local server/tarball with in-dir scripts), mirroring the Sprint 3 "extend the fake per new subcommand" rule.

- [ ] **Step 1: Failing test** — assert the role fails when a local `svc-<index>/.runner` state file (fake) names a different repo than `github_runner_target_repo`, with a message pointing to `unregister-runner.yml`. No `vault_github_pat` is supplied (preflight is not a dependency).
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** the mismatch check reading each `svc-<index>/.runner` `gitHubUrl`.
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(github_runner): enforce preflight and target-repo match`.

### Task 5.2: Registration-token request via gh (no PAT persisted)

- [ ] **Step 1: Failing test** — fake `gh` returns a canned registration token for `POST /repos/<repo>/actions/runners/registration-token`; assert the role calls it and that the token is never written to any file under the install root.
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** token request with `no_log: true`; store in a fact only.
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(github_runner): request short-lived registration token`.

### Task 5.3: Download + checksum-verify the runner package

- [ ] **Step 1: Failing test** — a local HTTP server serves a runner tarball that **contains executable fake `config.sh` and `svc.sh`** (each logs its args to `$FAKE_RUNNER_LOG`); the role downloads it and fails on checksum mismatch (mirror the `test_proxmox_template_role.py` HTTP-server pattern). Assert the fakes land in each `svc-<index>` after unpack.
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** `get_url` with `checksum: "sha256:{{ github_runner_sha256 }}"`, unpack into each `svc-<index>` (so `svc-<index>/config.sh` and `svc.sh` exist for the register step).
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(github_runner): download and verify pinned runner package`.

### Task 5.4: Register N uniquely-named services + wire job hooks

**Interfaces:**
- Produces: for index `1..N`, `chdir`s into `svc-<index>` and runs `./config.sh --url ... --token ... --name {{ github_runner_name_prefix }}-<index> --labels {{ github_runner_labels | join(',') }} --unattended --disableupdate`, then `sudo ./svc.sh install`/`start`. Sets `ACTIONS_RUNNER_HOOK_JOB_STARTED`/`_COMPLETED` env in each service to the per-service marker-writing hook + the cleanup script.

- [ ] **Step 1: Failing test** — using the in-dir fake `config.sh`/`svc.sh` unpacked by Task 5.3 (plus the `gh` PATH fake and local server); with `github_runner_count=3`, assert three distinct `--name <prefix>-1..3`, each with `--disableupdate` and the full label set; assert `svc.sh install` runs 3×; assert the job-hook env points `JOB_STARTED` at the per-service marker path `/run/prox-github-runner/jobs/<name>`.
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** the register loop invoking the scripts **by path** (`ansible.builtin.command` with `chdir: "{{ install_root }}/svc-<index>"`, `argv: ["./config.sh", ...]`) + hook wiring. Registration guarded by existing `svc-<index>/.runner` (skip re-register).
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(github_runner): register N labeled services with job hooks`.

### Task 5.5: Job-hook marker scripts + lint sweep

- [ ] **Step 1: Failing test** — the `JOB_STARTED` hook writes a timestamped marker at `/run/prox-github-runner/jobs/<runner-name>`; `JOB_COMPLETED` removes it and invokes cleanup when due.
- [ ] **Step 2: Fail.** **Step 3: Implement** the two hook scripts (shellcheck-clean). **Step 4: Pass.**
- [ ] **Step 5:** `make check`; **Commit** `feat(github_runner): add per-service job-started/completed hooks`.

### Task 5.6: Wire github_runner into setup-runner.yml

**Files:** Modify `playbooks/setup-runner.yml`; Test `tests/test_setup_runner_playbook.py`.

- [ ] **Step 1: Failing test** — assert (parse `playbooks/setup-runner.yml`) that the role order is `preflight` → `runner_host` → `github_runner`, and that `ansible-playbook --syntax-check playbooks/setup-runner.yml` succeeds now that `github_runner` exists.
- [ ] **Step 2: Run to verify fail** (role not yet appended).
- [ ] **Step 3: Implement** — append the `github_runner` role to `setup-runner.yml` after `runner_host`.
- [ ] **Step 4: Run to verify pass** (`make syntax` + the new test).
- [ ] **Step 5: Commit** `feat(ops): wire github_runner into setup-runner converge`.

---

## PHASE / SPRINT 6 — cleanup, health, and site converge

**Deliverable:** `unregister-runner.yml`, `check-runner-health.yml`, `site.yml`; scale-down reconciliation over discovered units; both stop actors use `systemctl stop`.

### Task 6.1: unregister-runner playbook (idempotent, discovered units)

**Files:** Create `roles/github_runner/tasks/unregister.yml`, `playbooks/unregister-runner.yml`; Test `tests/test_unregister_playbook.py`.

- [ ] **Step 1: Failing test** — pre-place in-dir fake `svc.sh`/`config.sh` in two `svc-<index>` dirs (as Task 5.3 unpacks them) plus a `gh` PATH fake; with two discovered `svc-*` dirs but `github_runner_count=1`, assert the surplus service is stopped, `./config.sh remove` is invoked (by path) with a removal token, and its unit removed; running twice is a no-op (second run finds nothing → `changed=false`).
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** `unregister.yml`: enumerate discovered `svc-*` dirs, request removal token via `gh`, `chdir` into each and run `./config.sh remove` / `sudo ./svc.sh uninstall` by path, remove dirs; treat missing state as no-op.
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(github_runner): idempotent unregister with scale-down reconciliation`.

### Task 6.2: check-runner-health playbook

**Files:** Create `playbooks/check-runner-health.yml`; Test `tests/test_health_playbook.py`.

- [ ] **Step 1: Failing test** — fake `gh`/`systemctl`/`docker`/`df`; assert the health check (1) reports each service state, (2) on definitive `private:false` runs `systemctl stop` on all services, (3) warns on a marker older than the scheduling-latency threshold or a service offline, (4) exits nonzero on definitive unsafe state.
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** the playbook: authenticated privacy check (via preflight tasks), per-service systemd status, Docker health, disk thresholds (80/90), guard/cleanup last-result, scheduling-latency warning (>10 min queued while zero idle).
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(ops): add check-runner-health playbook`.

### Task 6.3: site.yml converge chain

- [ ] **Step 1: Failing test** — `ansible-playbook --syntax-check playbooks/site.yml` succeeds and the play order is preflight → proxmox_template → proxmox_vm → runner_host → github_runner (assert by parsing the playbook).
- [ ] **Step 2: Fail.** **Step 3: Implement** `site.yml`. **Step 4: Pass** (`make syntax`).
- [ ] **Step 5: Commit** `feat(ops): add site.yml non-destructive converge chain`.

---

## PHASE / SPRINT 7 — smoke workflow + companion PR

**Deliverable:** a copyable `workflow_dispatch` smoke workflow + docs, an operator dispatch/poll playbook, and the `paper-archives` re-label PR.

### Task 7.1: Smoke workflow template + docs

**Files:** Create `templates/paper-archives-smoke.yml`, `docs/smoke-workflow.md`; Test `tests/test_smoke_workflow_template.py`.

- [ ] **Step 1: Failing test** — parse the template YAML and assert: `on: workflow_dispatch` only; `runs-on: [self-hosted, linux, x64, paper-archives]`; steps prove checkout, shell, Docker run, and workspace cleanup.
- [ ] **Step 2: Fail.** **Step 3: Implement** the template + docs (private-repo-only boundary called out). **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(smoke): add paper-archives self-hosted smoke workflow template`.

### Task 7.2: run-smoke-workflow playbook

- [ ] **Step 1: Failing test** — fake `gh`; assert the playbook triggers `workflow_dispatch` and polls the run conclusion, failing on non-success. Skips gracefully if the workflow is not present in the repo.
- [ ] **Step 2: Fail.** **Step 3: Implement.** **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(ops): add run-smoke-workflow dispatch-and-poll playbook`.

### Task 7.3: Companion PR in `paper-archives` (separate repo/branch)

**Files (in `~/src/paper-archives`):** Modify `.github/workflows/ci.yml`.

- [ ] **Step 1:** In `~/src/paper-archives`, branch `feat/self-hosted-linux-runner` off `main`.
- [ ] **Step 2:** Convert `clippy` and `test` `matrix.os` → `matrix.include` with **literal** `runs-on` arrays: Linux arm `[self-hosted, linux, x64, paper-archives]`, `macos-latest`/`windows-latest` unchanged.
- [ ] **Step 3:** Change every single-OS ubuntu job (`fmt`, `deny`, `python-lint`, `pre-commit-checks`, `docs-check`, `spec-sync`, `vector-immutability`, `integration`, `fuzz-smoke`, `demo`) `runs-on: ubuntu-latest` → `[self-hosted, linux, x64, paper-archives]`.
- [ ] **Step 4:** Remove the inline `sudo apt-get update && install` Tauri steps from `clippy`/`test`/`demo` (libs pre-installed on the runner; concurrent runs would collide on the dpkg lock).
- [ ] **Step 5:** Add a top-level `concurrency:` group: `group: ci-${{ github.ref }}`, `cancel-in-progress: true`.
- [ ] **Step 6:** Leave `release.yml`, `mutants.yml`, and macOS/Windows arms unchanged.
- [ ] **Step 7:** `actionlint .github/workflows/ci.yml`; commit `ci: route Linux jobs to self-hosted paper-archives runner`; open PR. Do not merge until the runner is live and its preflight passes on this branch.

---

## Self-Review

**Spec coverage:** Sprint 3 (proxmox_vm: clone/converge/firewall/SSH/cloud-init) → Tasks 3.1–3.5. Sprint 4 (baseline/Docker/per-service env/guard/cleanup) → 4.1–4.6. Sprint 5 (preflight/token/download/register N/hooks) → 5.1–5.5. Sprint 6 (unregister/health/site, scale-down, systemctl-stop both actors) → 6.1–6.3. Sprint 7 (smoke/companion, literal runs-on, apt removal, concurrency group) → 7.1–7.3. Egress allowlist (PyPI, crates, rust-lang, Actions-cache) → Task 3.3 defaults. Per-service RUSTUP_HOME/tool-cache → Task 4.3. Unique names → Task 5.4. MemoryMax/CPUWeight knob → fold into Task 4.3 defaults (optional, off by default). No spec requirement is left without a task.

**Placeholder scan:** `github_runner_version`/`github_runner_sha256` are the only deferred values, explicitly flagged "resolve latest stable at implementation" — a real lookup, not a placeholder behavior. Firewall rules-path concreteness is deferred to implementation because it is Proxmox-version-specific; the test asserts observable behavior (deny rule emitted, firewall enabled).

**Type consistency:** service dir scheme `svc-<index>`, marker path `/run/prox-github-runner/jobs/<runner-name>`, name scheme `<runner_vm_name>-<index>`, and `github_runner_count` are used consistently across Tasks 4.3, 5.4, 5.5, 6.1, 6.2.
