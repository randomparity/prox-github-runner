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
- `playbooks/setup-runner.yml` — full converge (preflight → runner_host → github_runner).
- `tests/test_runner_host_role.py`, `tests/test_runner_guard_script.py`, `tests/test_runner_cleanup_script.py`.

**New role — `github_runner` (Sprint 5):**
- `roles/github_runner/defaults/main.yml` — `github_runner_count`, `github_runner_version`, download URL/checksum, install root.
- `roles/github_runner/tasks/main.yml` — preflight guard, token request, download+verify, per-service register (unique names), systemd install, job-hook wiring.
- `roles/github_runner/tasks/unregister.yml` — idempotent removal reused by cleanup.
- `tests/test_github_runner_role.py` — fake `gh`/`config.sh`/`svc.sh` behavioral tests.

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
import subprocess, sys
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


def run_role(tmp_path: Path, extra: dict, fake_qm_mode: str | None = None):
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
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin"}
    if fake_qm_mode:
        (tmp_path / "qm").write_text("#!/usr/bin/env bash\nexit 0\n")
        (tmp_path / "qm").chmod(0o755)
    cmd = ["ansible-playbook", "-i", str(inv), str(play)]
    for k, v in extra.items():
        cmd += ["-e", f"{k}={v}"]
    return subprocess.run(cmd, text=True, capture_output=True, cwd=Path.cwd(), env={**env})


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
  existing:status) exit 0 ;;
  existing:config) printf 'name: paper-archives-runner\nnet0: virtio,bridge=vmbr0\nscsi0: local-lvm:vm-2100-disk-0,size=256G\n' ;;
  existing:set) exit 0 ;;
  *) echo "unexpected qm $*" >&2; exit 42 ;;
esac
"""
    )
    qm.chmod(0o755)


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

Update `run_role` to accept `env_extra` and merge it into `env`.

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

- [ ] **Step 1: Failing test** — assert the fake `qm` log contains `--firewall 1` and that a rendered rules file (written to a `proxmox_vm_fw_rules_path` override under tmp) contains each denied CIDR with `-j DROP`/`REJECT`.

```python
def test_firewall_denies_management_cidrs(tmp_path: Path) -> None:
    write_fake_qm(tmp_path, "existing")
    rules = tmp_path / "fw.rules"
    proc = run_role(
        tmp_path,
        {"runner_vm_ip": "192.168.20.50",
         "proxmox_vm_fw_rules_path": str(rules)},
        env_extra={"FAKE_QM_LOG": str(tmp_path/'qm.log'), "FAKE_QM_MODE": "existing"},
    )
    assert proc.returncode == 0, proc.stdout
    body = rules.read_text()
    assert "192.168.20.10" in body   # proxmox mgmt host denied
    assert "REJECT" in body or "DROP" in body
```

- [ ] **Step 2: Run to verify fail** — `.venv/bin/pytest tests/test_proxmox_vm_role.py::test_firewall_denies_management_cidrs -v` → FAIL.

- [ ] **Step 3: Implement** — add defaults and a `template`/`copy` task that renders the rules file and a `qm set --firewall 1` command. Defaults:

```yaml
proxmox_vm_fw_rules_path: "/etc/pve/firewall/{{ runner_vm_id }}.fw"
proxmox_vm_denied_cidrs:
  - "{{ proxmox_api_host }}/32"
  - "192.168.20.0/24"   # Proxmox management network (operator adjusts)
```
Task renders each denied CIDR as an OUT REJECT rule above a default-allow for the documented egress hosts (DNS/NTP/GitHub/Ubuntu/Docker/crates/PyPI/rust-lang/Actions-cache). Reference the spec Amendment 4 host list in a comment.

- [ ] **Step 4: Run to verify pass** — expected PASS.

- [ ] **Step 5: Commit**

```bash
git add roles/proxmox_vm tests/test_proxmox_vm_role.py
git commit -m "feat(proxmox_vm): apply Proxmox firewall isolation for the runner VM

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 3.4: Start VM, wait for SSH, wait for cloud-init

**Files:** Modify `roles/proxmox_vm/tasks/main.yml`; Test `tests/test_proxmox_vm_role.py`.

- [ ] **Step 1: Failing test** — with fake `qm` mode `absent`, assert log contains `start` and that the role uses `ansible.builtin.wait_for` (SSH) and a `cloud-init status --wait` command guarded by an explicit `timeout`. Test asserts the rendered task list includes a `cloud-init status --wait` invocation (parse `roles/proxmox_vm/tasks/main.yml` for the string) and that `start` is logged.

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

- name: Wait for cloud-init to finish
  ansible.builtin.command:
    argv: ["ssh", "-o", "StrictHostKeyChecking=accept-new",
           "{{ runner_bootstrap_user }}@{{ runner_vm_ip }}",
           "cloud-init", "status", "--wait"]
  register: proxmox_vm_cloudinit
  changed_when: false
  failed_when: proxmox_vm_cloudinit.rc != 0
  timeout: 900
```

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** `feat(proxmox_vm): start VM and wait for SSH and cloud-init`.

### Task 3.5: Lint, syntax, playbook wiring

- [ ] **Step 1:** Run `make lint && make syntax` — fix any `ansible-lint`/`yamllint` findings (FQCN, `changed_when`, name casing) until clean.
- [ ] **Step 2:** Run `make test` — full pytest green.
- [ ] **Step 3: Commit** any lint fixes: `style(proxmox_vm): satisfy ansible-lint and yamllint`.

---

## PHASE / SPRINT 4 — `runner_host` role

**Deliverable:** the Ubuntu baseline: packages, runner user, Docker, clang, Python 3.12, Tauri libs, passwordless sudo, per-service directories/env, and the guard + cleanup scripts (installed but not yet wired to a runner). Verified with fake `apt-get`/`systemctl` and shell-level tests of the scripts.

### Task 4.1: Baseline packages + runner user + passwordless sudo

**Files:**
- Create: `roles/runner_host/{meta,defaults,tasks}/main.yml`, `roles/runner_host/templates/runner-sudoers.j2`
- Create: `playbooks/setup-runner.yml`
- Test: `tests/test_runner_host_role.py`

**Interfaces:**
- Produces defaults: `runner_host_packages` (git, curl, jq, build-essential, clang, python3.12, python3.12-venv, python3-pip, ca-certificates), `runner_host_tauri_libs` (the five `-dev` packages), `runner_host_user` (= `runner_bootstrap_user`), `runner_host_install_root` (`/opt/actions-runner`).

- [ ] **Step 1: Failing test** — run the role against a local inventory with a fake `apt-get` logging args; assert the log contains each package name and that a sudoers drop-in file is rendered granting `NOPASSWD: ALL` to the runner user, and that it passes `visudo -cf`.

```python
def test_baseline_installs_clang_and_python312(tmp_path):
    log = tmp_path / "apt.log"
    proc = run_runner_host(tmp_path, env_extra={"FAKE_APT_LOG": str(log)})
    assert proc.returncode == 0, proc.stdout
    body = log.read_text()
    assert "clang" in body and "python3.12" in body
    for lib in ("libwebkit2gtk-4.1-dev", "libxdo-dev", "librsvg2-dev"):
        assert lib in body
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — `apt` tasks over `runner_host_packages + runner_host_tauri_libs`; create user; render sudoers drop-in `runner-sudoers.j2` (`{{ runner_host_user }} ALL=(ALL) NOPASSWD:ALL`) into `/etc/sudoers.d/` with `validate: "visudo -cf %s"`.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(runner_host): install baseline packages, Tauri libs, runner user, sudo`.

### Task 4.2: Docker Engine + runner user in docker group

- [ ] **Step 1: Failing test** — assert the role adds the Docker apt repo, installs `docker-ce`, enables the service, and adds `runner_host_user` to the `docker` group (parse task file + fake `apt-get`/`usermod` log).
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** Docker install tasks (mirror upstream Docker apt steps) + `ansible.builtin.user` with `groups: docker, append: true`.
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(runner_host): install Docker Engine and grant runner docker access`.

### Task 4.3: Per-service directories and environment (RUNNER_TOOL_CACHE, RUSTUP_HOME)

**Interfaces:**
- Consumes: `github_runner_count` (default 3; declared here in `runner_host/defaults` too so the role is testable standalone).
- Produces: for each index `1..N`, dirs `{{ runner_host_install_root }}/svc-<index>/{_work,_tool}` and `{{ runner_host_install_root }}/svc-<index>/rustup`; a shared `{{ runner_host_user }}` `~/.cargo` remains shared. Each service's env file sets `RUNNER_TOOL_CACHE=<svc>/_tool`, `RUSTUP_HOME=<svc>/rustup`, `CARGO_HOME=~/.cargo`.

- [ ] **Step 1: Failing test** — with `github_runner_count=3`, assert three per-service dirs and three env files are created, each pointing `RUSTUP_HOME` at its own `svc-<index>/rustup` and `CARGO_HOME` at the shared `~/.cargo`.
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** with a `loop: "{{ range(1, (github_runner_count | int) + 1) | list }}"` over `file`/`template` tasks.
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
- Consumes: existing `preflight` role (as a role dependency in `meta/main.yml`), `github_runner_target_repo`, `github_runner_labels`.
- Produces defaults: `github_runner_count: 3`, `github_runner_version` (resolve latest stable at implementation; pin exact), `github_runner_install_root: /opt/actions-runner`, `github_runner_name_prefix: "{{ runner_vm_name }}"`.

- [ ] **Step 1: Failing test** — assert the role fails when a local `.runner` state file (fake) names a different repo than `github_runner_target_repo`, with a message pointing to `unregister-runner.yml`.
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

- [ ] **Step 1: Failing test** — fake HTTP server serves a runner tarball; role downloads to a cache and fails on checksum mismatch (mirror `test_proxmox_template_role.py` HTTP-server pattern).
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** `get_url` with `checksum: "sha256:{{ github_runner_sha256 }}"`, unpack into each `svc-<index>`.
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(github_runner): download and verify pinned runner package`.

### Task 5.4: Register N uniquely-named services + wire job hooks

**Interfaces:**
- Produces: for index `1..N`, runs `config.sh --url ... --token ... --name {{ github_runner_name_prefix }}-<index> --labels {{ github_runner_labels | join(',') }} --unattended --disableupdate`, then `svc.sh install`/`start`. Sets `ACTIONS_RUNNER_HOOK_JOB_STARTED`/`_COMPLETED` env in each service to the per-service marker-writing hook + the cleanup script.

- [ ] **Step 1: Failing test** — fake `config.sh`/`svc.sh` logging args; with `github_runner_count=3`, assert three distinct `--name <prefix>-1..3`, each `--disableupdate` and the full label set; assert `svc.sh install` runs 3×; assert the job-hook env points `JOB_STARTED` at the per-service marker path `/run/prox-github-runner/jobs/<name>`.
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** the register loop + hook wiring. Registration guarded by existing `.runner` (skip re-register).
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `feat(github_runner): register N labeled services with job hooks`.

### Task 5.5: Job-hook marker scripts + lint sweep

- [ ] **Step 1: Failing test** — the `JOB_STARTED` hook writes a timestamped marker at `/run/prox-github-runner/jobs/<runner-name>`; `JOB_COMPLETED` removes it and invokes cleanup when due.
- [ ] **Step 2: Fail.** **Step 3: Implement** the two hook scripts (shellcheck-clean). **Step 4: Pass.**
- [ ] **Step 5:** `make check`; **Commit** `feat(github_runner): add per-service job-started/completed hooks`.

---

## PHASE / SPRINT 6 — cleanup, health, and site converge

**Deliverable:** `unregister-runner.yml`, `check-runner-health.yml`, `site.yml`; scale-down reconciliation over discovered units; both stop actors use `systemctl stop`.

### Task 6.1: unregister-runner playbook (idempotent, discovered units)

**Files:** Create `roles/github_runner/tasks/unregister.yml`, `playbooks/unregister-runner.yml`; Test `tests/test_unregister_playbook.py`.

- [ ] **Step 1: Failing test** — fake `svc.sh`/`config.sh`/`gh`; with two discovered `actions.runner.*` units but `github_runner_count=1`, assert the surplus service is stopped, `config.sh remove` is called with a removal token, and its systemd unit removed; running twice is a no-op (second run finds nothing → returns changed=false).
- [ ] **Step 2: Fail.**
- [ ] **Step 3: Implement** `unregister.yml`: enumerate discovered `svc-*`/units, request removal token via `gh`, `config.sh remove`, `svc.sh uninstall`, remove dirs; treat missing state as no-op.
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
