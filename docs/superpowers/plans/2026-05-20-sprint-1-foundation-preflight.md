# Sprint 1 Foundation And Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the repo foundation and a tested GitHub preflight gate for the
private `drc-dot-nz/paper-archives` runner before any Proxmox work starts.

**Architecture:** Keep Ansible as the operator interface, but put GitHub
preflight logic in a small Python CLI that is easier to unit test against mock
GitHub responses. The `preflight` Ansible role runs locally, invokes the CLI,
prints posture warnings, and fails on hard safety errors.

**Tech Stack:** Python 3.13 via `uv`, `ansible-core==2.21.0`,
`ansible-lint==26.4.0`, `yamllint==1.38.0`, `pytest==9.0.3`,
`PyYAML==6.0.3`, Ansible builtin modules, Python stdlib HTTP server for tests.

---

## Scope Check

The approved design covers the whole MVP. This plan covers Sprint 1 only:
project foundation, inventory skeleton, vault examples, `playbooks/preflight.yml`,
the `preflight` role, the GitHub preflight CLI, and fixture-backed tests.

No Proxmox template, VM provisioning, runner installation, Docker setup, or
runner cleanup is implemented in this plan.

## File Structure

- `pyproject.toml`: Python project metadata, pytest config, and exact dev
  dependencies used by `uv`.
- `requirements-dev.txt`: exact dependency pins for users who install with
  `uv pip install -r`.
- `requirements.yml`: Ansible Galaxy collection requirements. Sprint 1 has no
  collection dependency, so this is an empty collection list.
- `ansible.cfg`: local Ansible defaults for inventory, roles, YAML output, and
  warning behavior.
- `.yamllint.yml`: YAML lint policy for playbooks, roles, inventory, and docs.
- `.gitignore`: local virtualenv and transient test files.
- `Makefile`: setup, lint, test, inventory, preflight, and check targets.
- `inventory/hosts.yml`: static `proxmox` and `runner` inventory groups.
- `inventory/group_vars/all/vars.yml`: shared defaults, target repo, labels,
  PAT thresholds, and mockable GitHub API base URL.
- `inventory/group_vars/all/vault.yml.example`: non-secret example of required
  vault variable names.
- `inventory/group_vars/proxmox/vars.yml`: Proxmox placeholders used by later
  sprints and inventory parsing now.
- `inventory/group_vars/runner/vars.yml`: static runner host/IP settings used
  by later sprints and inventory parsing now.
- `playbooks/preflight.yml`: local playbook that runs only the `preflight` role.
- `roles/preflight/defaults/main.yml`: default thresholds and script path.
- `roles/preflight/tasks/main.yml`: config assertions and CLI invocation.
- `scripts/github_preflight.py`: GitHub API and workflow safety preflight CLI.
- `tests/mock_github.py`: fixture-backed local HTTP server.
- `tests/test_github_preflight_core.py`: pytest coverage for local PAT lifetime
  and argument validation helpers.
- `tests/test_github_preflight_api.py`: pytest coverage for mock GitHub API
  hard failures, warnings, and API-backed workflow fetches.
- `tests/test_github_preflight_workflows.py`: pytest coverage for workflow YAML
  trigger and `runs-on` safety logic.
- `tests/test_preflight_playbook.py`: pytest coverage for the Ansible playbook
  wrapper.
- `docs/preflight.md`: operator-facing preflight behavior and PAT notes.

## Task 1: Tooling And Repository Foundation

**Files:**
- Create: `pyproject.toml`
- Create: `requirements-dev.txt`
- Create: `requirements.yml`
- Create: `ansible.cfg`
- Create: `.yamllint.yml`
- Create: `.gitignore`
- Create: `Makefile`

- [ ] **Step 1: Create the Python and test dependency files**

Create `pyproject.toml`:

```toml
[project]
name = "prox-github-runner"
version = "0.1.0"
description = "Ansible automation for Proxmox-hosted GitHub Actions runners"
requires-python = ">=3.13"
dependencies = [
  "ansible-core==2.21.0",
  "PyYAML==6.0.3",
]

[dependency-groups]
dev = [
  "ansible-lint==26.4.0",
  "pytest==9.0.3",
  "yamllint==1.38.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"
```

Create `requirements-dev.txt`:

```text
ansible-core==2.21.0
ansible-lint==26.4.0
PyYAML==6.0.3
pytest==9.0.3
yamllint==1.38.0
```

- [ ] **Step 2: Create Ansible config and empty collection requirements**

Create `requirements.yml`:

```yaml
---
collections: []
```

Create `ansible.cfg`:

```ini
[defaults]
inventory = inventory/hosts.yml
roles_path = roles
host_key_checking = false
retry_files_enabled = false
stdout_callback = default
result_format = yaml
interpreter_python = auto_silent

[privilege_escalation]
become_method = sudo

[ssh_connection]
pipelining = true
ssh_args = -o ControlMaster=auto -o ControlPersist=60s
```

- [ ] **Step 3: Create lint config and ignored local files**

Create `.yamllint.yml`:

```yaml
---
extends: default

rules:
  comments:
    min-spaces-from-content: 1
  document-start:
    present: true
  line-length:
    max: 100
    level: error
  truthy:
    allowed-values: ["true", "false"]
```

Create `.gitignore`:

```gitignore
.venv/
.pytest_cache/
__pycache__/
*.pyc
.vault_pass*
inventory/group_vars/**/vault.yml
```

- [ ] **Step 4: Create the project Makefile**

Create `Makefile`:

```make
SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV := .venv
PYTHON := $(VENV)/bin/python
UV := uv
ACTIVATE := source $(VENV)/bin/activate

.PHONY: help setup lint test inventory preflight check clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

setup: $(PYTHON) ## Create venv and install development dependencies

$(PYTHON):
	$(UV) venv $(VENV) --python 3.13
	$(UV) pip install --python $(PYTHON) -r requirements-dev.txt
	$(ACTIVATE) && ansible-galaxy collection install -r requirements.yml

lint: setup ## Run YAML and Ansible lint
	$(ACTIVATE) && yamllint -c .yamllint.yml .
	$(ACTIVATE) && ansible-lint playbooks roles

test: setup ## Run unit tests
	$(ACTIVATE) && pytest

inventory: setup ## Parse inventory
	$(ACTIVATE) && ansible-inventory --list >/dev/null

preflight: setup ## Run GitHub preflight
	$(ACTIVATE) && ansible-playbook playbooks/preflight.yml

check: lint test inventory ## Run local verification

clean: ## Remove local generated files
	$(RM) -r $(VENV) .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
```

- [ ] **Step 5: Verify foundation files parse**

Run:

```bash
make setup
make lint
```

Expected:

```text
yamllint -c .yamllint.yml .
ansible-lint playbooks roles
```

`make lint` may fail at this point because `playbooks/` and `roles/` do not
exist yet. If it does, continue to Task 2 before re-running.

- [ ] **Step 6: Commit tooling foundation**

Run:

```bash
git add pyproject.toml requirements-dev.txt requirements.yml ansible.cfg \
  .yamllint.yml .gitignore Makefile
git commit -m "Add Ansible project tooling"
```

## Task 2: Inventory, Vault Examples, And Preflight Playbook

**Files:**
- Create: `inventory/hosts.yml`
- Create: `inventory/group_vars/all/vars.yml`
- Create: `inventory/group_vars/all/vault.yml.example`
- Create: `inventory/group_vars/proxmox/vars.yml`
- Create: `inventory/group_vars/runner/vars.yml`
- Create: `playbooks/preflight.yml`
- Create: `roles/preflight/defaults/main.yml`
- Create: `roles/preflight/meta/main.yml`
- Create: `roles/preflight/tasks/main.yml`

- [ ] **Step 1: Create static inventory**

Create `inventory/hosts.yml`:

```yaml
---
all:
  children:
    proxmox:
      hosts:
        pve:
          ansible_host: "{{ proxmox_api_host }}"
          ansible_user: root
    runner:
      hosts:
        paper-archives-runner:
          ansible_host: "{{ runner_vm_ip }}"
          ansible_user: "{{ runner_bootstrap_user }}"
```

- [ ] **Step 2: Create shared defaults**

Create `inventory/group_vars/all/vars.yml`:

```yaml
---
github_api_base_url: "https://api.github.com"
github_api_version: "2026-03-10"
github_runner_target_repo: "drc-dot-nz/paper-archives"
github_runner_required_label: "paper-archives"
github_runner_labels:
  - self-hosted
  - linux
  - x64
  - paper-archives

# Placeholder date that intentionally fails until the operator sets the real
# fine-grained PAT expiration date.
github_pat_expires_on: "1970-01-01"
github_pat_warning_days: 14
github_pat_failure_days: 7
github_pat_max_remaining_days: 30

github_preflight_alert_hook: ""
github_preflight_allow_dynamic_workflows: []
github_preflight_local_action_paths:
  - ".github/actions/**"

runner_vm_id: 2100
runner_vm_name: "paper-archives-runner"
runner_vm_ip: "192.168.20.50"
runner_vm_gateway: "192.168.20.1"
runner_vm_cidr: 24
runner_vm_nameserver: "192.168.20.1"
runner_bootstrap_user: "runner"
```

- [ ] **Step 3: Create vault example**

Create `inventory/group_vars/all/vault.yml.example`:

```yaml
---
# Copy to vault.yml, fill values, then encrypt with ansible-vault.
# ansible-vault encrypt inventory/group_vars/all/vault.yml

vault_github_pat: "github_pat_example_replace_me"
vault_proxmox_api_user: "ansible@pam!runner"
vault_proxmox_api_token_secret: "00000000-0000-0000-0000-000000000000"
vault_runner_bootstrap_password: "replace-me"
```

- [ ] **Step 4: Create Proxmox and runner variable files**

Create `inventory/group_vars/proxmox/vars.yml`:

```yaml
---
proxmox_api_host: "192.168.20.10"
proxmox_api_port: 8006
proxmox_node: "pve"
proxmox_storage: "local-lvm"
proxmox_template_name: "ubuntu-2404-cloud"
proxmox_template_vmid: 9000
```

Create `inventory/group_vars/runner/vars.yml`:

```yaml
---
ansible_python_interpreter: /usr/bin/python3
```

- [ ] **Step 5: Create a preflight playbook and stub role**

Create `playbooks/preflight.yml`:

```yaml
---
- name: Run GitHub runner preflight checks
  hosts: localhost
  gather_facts: false
  roles:
    - preflight
```

Create `roles/preflight/defaults/main.yml`:

```yaml
---
preflight_script_path: "{{ playbook_dir }}/../scripts/github_preflight.py"
preflight_timeout_seconds: 60
preflight_hide_command: true
```

Create `roles/preflight/meta/main.yml`:

```yaml
---
galaxy_info:
  role_name: preflight
  author: dave
  description: Validate GitHub repository and runner safety before provisioning.
  license: MIT
  min_ansible_version: "2.21"
  platforms:
    - name: GenericLinux
      versions:
        - all
dependencies: []
```

Create `roles/preflight/tasks/main.yml`:

```yaml
---
- name: Validate required preflight variables
  ansible.builtin.assert:
    that:
      - github_runner_target_repo is match('^[^/]+/[^/]+$')
      - github_runner_required_label | length > 0
      - github_runner_labels | length > 0
      - github_pat_expires_on is match('^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
      - vault_github_pat is defined
      - vault_github_pat | length > 0
    fail_msg: "Missing GitHub preflight config or vault_github_pat."

- name: Run GitHub preflight script
  ansible.builtin.command:
    argv:
      - "{{ ansible_playbook_python }}"
      - "{{ preflight_script_path }}"
      - "--api-base-url"
      - "{{ github_api_base_url }}"
      - "--api-version"
      - "{{ github_api_version }}"
      - "--target-repo"
      - "{{ github_runner_target_repo }}"
      - "--token"
      - "{{ vault_github_pat }}"
      - "--expires-on"
      - "{{ github_pat_expires_on }}"
      - "--warning-days"
      - "{{ github_pat_warning_days | string }}"
      - "--failure-days"
      - "{{ github_pat_failure_days | string }}"
      - "--max-days"
      - "{{ github_pat_max_remaining_days | string }}"
      - "--required-label"
      - "{{ github_runner_required_label }}"
      - "--runner-labels"
      - "{{ github_runner_labels | join(',') }}"
  register: _preflight
  changed_when: false
  failed_when: false
  no_log: "{{ preflight_hide_command | default(true) }}"

- name: Decode preflight JSON
  ansible.builtin.set_fact:
    preflight_result: "{{ _preflight.stdout | from_json }}"

- name: Show raw preflight stderr
  ansible.builtin.debug:
    msg: "{{ _preflight.stderr }}"
  when: _preflight.stderr | length > 0

- name: Show preflight warnings
  ansible.builtin.debug:
    msg: "{{ preflight_result.warnings }}"
  when: preflight_result.warnings | length > 0

- name: Fail on preflight errors
  ansible.builtin.fail:
    msg: "{{ preflight_result.errors }}"
  when: preflight_result.errors | length > 0

- name: Show preflight success summary
  ansible.builtin.debug:
    msg: "{{ preflight_result.summary }}"
```

- [ ] **Step 6: Verify inventory parses**

Run:

```bash
make inventory
```

Expected: command exits `0`.

- [ ] **Step 7: Commit inventory and preflight shell**

Run:

```bash
git add inventory playbooks roles
git commit -m "Add inventory and preflight playbook"
```

## Task 3: GitHub Preflight CLI Core

**Files:**
- Create: `scripts/github_preflight.py`
- Create: `tests/test_github_preflight_core.py`

- [ ] **Step 1: Write failing tests for PAT lifetime validation**

Create `tests/test_github_preflight_core.py`:

```python
from __future__ import annotations

from datetime import date, timedelta

from scripts.github_preflight import evaluate_pat_lifetime


def test_pat_lifetime_warns_at_warning_threshold() -> None:
    today = date(2026, 5, 20)
    result = evaluate_pat_lifetime(
        expires_on=today + timedelta(days=14),
        today=today,
        warning_days=14,
        failure_days=7,
        max_days=30,
    )
    assert result.errors == []
    assert result.warnings == ["GitHub PAT expires in 14 days."]


def test_pat_lifetime_fails_at_failure_threshold() -> None:
    today = date(2026, 5, 20)
    result = evaluate_pat_lifetime(
        expires_on=today + timedelta(days=7),
        today=today,
        warning_days=14,
        failure_days=7,
        max_days=30,
    )
    assert result.errors == ["GitHub PAT expires in 7 days; rotate before running."]
    assert result.warnings == []


def test_pat_lifetime_fails_when_too_far_out() -> None:
    today = date(2026, 5, 20)
    result = evaluate_pat_lifetime(
        expires_on=today + timedelta(days=31),
        today=today,
        warning_days=14,
        failure_days=7,
        max_days=30,
    )
    assert result.errors == [
        "GitHub PAT expires in 31 days; maximum allowed remaining lifetime is 30 days."
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
make test
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.github_preflight'`.

- [ ] **Step 3: Implement CLI core and PAT lifetime validation**

Create `scripts/github_preflight.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import PurePosixPath
from typing import Any

import yaml


@dataclass(frozen=True)
class CheckResult:
    errors: list[str]
    warnings: list[str]


class GitHubError(RuntimeError):
    def __init__(self, purpose: str, status: int | None, message: str) -> None:
        self.purpose = purpose
        self.status = status
        super().__init__(f"{purpose} failed: {message}")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}; expected YYYY-MM-DD.") from exc


def evaluate_pat_lifetime(
    *,
    expires_on: date,
    today: date,
    warning_days: int,
    failure_days: int,
    max_days: int,
) -> CheckResult:
    remaining_days = (expires_on - today).days
    errors: list[str] = []
    warnings: list[str] = []

    if remaining_days < 0:
        errors.append("GitHub PAT is expired; rotate before running.")
    elif remaining_days <= failure_days:
        errors.append(
            f"GitHub PAT expires in {remaining_days} days; rotate before running."
        )
    elif remaining_days > max_days:
        errors.append(
            "GitHub PAT expires in "
            f"{remaining_days} days; maximum allowed remaining lifetime is {max_days} days."
        )
    elif remaining_days <= warning_days:
        warnings.append(f"GitHub PAT expires in {remaining_days} days.")

    return CheckResult(errors=errors, warnings=warnings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GitHub runner preflight checks")
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--api-version", required=True)
    parser.add_argument("--target-repo", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--expires-on", required=True)
    parser.add_argument("--warning-days", type=int, required=True)
    parser.add_argument("--failure-days", type=int, required=True)
    parser.add_argument("--max-days", type=int, required=True)
    parser.add_argument("--today")
    parser.add_argument("--required-label", required=True)
    parser.add_argument("--runner-labels", required=True)
    args = parser.parse_args(argv)

    errors: list[str] = []
    warnings: list[str] = []

    try:
        expires_on = parse_date(args.expires_on)
    except ValueError as exc:
        errors.append(str(exc))
        expires_on = date.today()

    if "/" not in args.target_repo:
        errors.append("Target repository must be in owner/repo form.")

    lifetime = evaluate_pat_lifetime(
        expires_on=expires_on,
        today=parse_date(args.today) if args.today else datetime.now(timezone.utc).date(),
        warning_days=args.warning_days,
        failure_days=args.failure_days,
        max_days=args.max_days,
    )
    errors.extend(lifetime.errors)
    warnings.extend(lifetime.warnings)

    result = {
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "target_repo": args.target_repo,
            "required_label": args.required_label,
        },
    }
    print(json.dumps(result, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify core passes**

Run:

```bash
make test
```

Expected: PASS.

- [ ] **Step 5: Commit CLI core**

Run:

```bash
git add scripts/github_preflight.py tests/test_github_preflight_core.py
git commit -m "Add GitHub preflight CLI core"
```

## Task 4: Mock GitHub API And Repository Checks

**Files:**
- Create: `tests/mock_github.py`
- Create: `tests/test_github_preflight_api.py`
- Modify: `scripts/github_preflight.py`

- [ ] **Step 1: Add the mock server helper**

Create `tests/mock_github.py`:

```python
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockGitHubServer:
    def __init__(self, routes: dict[tuple[str, str], tuple[int, dict[str, Any]]]) -> None:
        self.routes = routes
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "MockGitHubServer":
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=5)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        routes = self.routes

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._respond("GET")

            def do_POST(self) -> None:
                self._respond("POST")

            def log_message(self, format: str, *args: object) -> None:
                return

            def _respond(self, method: str) -> None:
                status, body = routes.get((method, self.path), (404, {"message": "not found"}))
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("x-github-request-id", "TEST123")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        return Handler
```

- [ ] **Step 2: Add failing API tests**

Create `tests/test_github_preflight_api.py`:

```python
from __future__ import annotations

import json
import subprocess
import sys

from tests.mock_github import MockGitHubServer


BASE_ARGS = [
    sys.executable,
    "scripts/github_preflight.py",
    "--api-version",
    "2026-03-10",
    "--target-repo",
    "drc-dot-nz/paper-archives",
    "--token",
    "github_pat_test",
    "--expires-on",
    "2026-06-10",
    "--warning-days",
    "14",
    "--failure-days",
    "7",
    "--max-days",
    "30",
    "--today",
    "2026-05-20",
    "--required-label",
    "paper-archives",
    "--runner-labels",
    "self-hosted,linux,x64,paper-archives",
]


def run_preflight(api_base_url: str) -> tuple[int, dict[str, object]]:
    proc = subprocess.run(
        [*BASE_ARGS, "--api-base-url", api_base_url],
        check=False,
        text=True,
        capture_output=True,
    )
    assert proc.stderr == ""
    return proc.returncode, json.loads(proc.stdout)


def test_public_repo_fails() -> None:
    routes = {
        ("GET", "/repos/drc-dot-nz/paper-archives"): (
            200,
            {"private": False, "default_branch": "main"},
        ),
    }
    with MockGitHubServer(routes) as server:
        code, result = run_preflight(server.url)
    assert code == 1
    assert "Target repository drc-dot-nz/paper-archives is public." in result["errors"]


def test_registration_token_403_fails() -> None:
    routes = {
        ("GET", "/repos/drc-dot-nz/paper-archives"): (
            200,
            {"private": True, "default_branch": "main"},
        ),
        ("POST", "/repos/drc-dot-nz/paper-archives/actions/runners/registration-token"): (
            403,
            {"message": "Resource not accessible by personal access token"},
        ),
    }
    with MockGitHubServer(routes) as server:
        code, result = run_preflight(server.url)
    assert code == 1
    assert any("registration token" in error for error in result["errors"])
```

- [ ] **Step 3: Run API tests to verify they fail**

Run:

```bash
pytest tests/test_github_preflight_api.py -q
```

Expected: FAIL because the CLI does not call the mock API yet.

- [ ] **Step 4: Implement GitHub API calls in the CLI**

Modify `scripts/github_preflight.py` by adding these functions above `main`:

```python
def request_json(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    method: str,
    path: str,
    purpose: str,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    url = f"{api_base_url.rstrip('/')}{path}"
    last_error: str | None = None

    for attempt in range(1, 4):
        request = urllib.request.Request(url, method=method)
        request.add_header("accept", "application/vnd.github+json")
        request.add_header("authorization", f"Bearer {token}")
        request.add_header("x-github-api-version", api_version)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode() or "{}")
                headers = {key.lower(): value for key, value in response.headers.items()}
                return response.status, data, headers
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            try:
                data = json.loads(body or "{}")
            except json.JSONDecodeError:
                data = {"message": body}
            headers = {key.lower(): value for key, value in exc.headers.items()}
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                return exc.code, data, headers
            last_error = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last_error = str(exc.reason)
            if attempt == 3:
                raise GitHubError(purpose, None, last_error) from exc

    raise GitHubError(purpose, None, last_error or "unknown network error")


def check_repository(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    owner: str,
    repo: str,
) -> tuple[dict[str, Any] | None, CheckResult]:
    status, data, _headers = request_json(
        api_base_url=api_base_url,
        api_version=api_version,
        token=token,
        method="GET",
        path=f"/repos/{owner}/{repo}",
        purpose="repository metadata lookup",
    )
    if status != 200:
        return None, CheckResult(
            errors=[f"GitHub rejected repository metadata lookup with HTTP {status}."],
            warnings=[],
        )
    if data.get("private") is not True:
        return data, CheckResult(
            errors=[f"Target repository {owner}/{repo} is public."],
            warnings=[],
        )
    return data, CheckResult(errors=[], warnings=[])


def check_registration_token_permission(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    owner: str,
    repo: str,
) -> CheckResult:
    status, data, _headers = request_json(
        api_base_url=api_base_url,
        api_version=api_version,
        token=token,
        method="POST",
        path=f"/repos/{owner}/{repo}/actions/runners/registration-token",
        purpose="runner registration token probe",
    )
    if status != 201:
        message = data.get("message", "unknown GitHub error")
        return CheckResult(
            errors=[
                "GitHub rejected runner registration token probe "
                f"with HTTP {status}: {message}"
            ],
            warnings=[],
        )
    return CheckResult(errors=[], warnings=[])
```

Then update `main` after PAT lifetime validation:

```python
    if "/" in args.target_repo:
        owner, repo = args.target_repo.split("/", 1)
        try:
            repo_data, repo_check = check_repository(
                api_base_url=args.api_base_url,
                api_version=args.api_version,
                token=args.token,
                owner=owner,
                repo=repo,
            )
            errors.extend(repo_check.errors)
            warnings.extend(repo_check.warnings)
            if repo_data is not None and not repo_check.errors:
                token_check = check_registration_token_permission(
                    api_base_url=args.api_base_url,
                    api_version=args.api_version,
                    token=args.token,
                    owner=owner,
                    repo=repo,
                )
                errors.extend(token_check.errors)
                warnings.extend(token_check.warnings)
        except GitHubError as exc:
            errors.append(str(exc))
```

- [ ] **Step 5: Run API tests to verify they pass**

Run:

```bash
pytest tests/test_github_preflight_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit API checks**

Run:

```bash
git add scripts/github_preflight.py tests/mock_github.py tests/test_github_preflight_api.py
git commit -m "Add GitHub API preflight checks"
```

## Task 5: Workflow And CODEOWNERS Audit

**Files:**
- Create: `tests/test_github_preflight_workflows.py`
- Modify: `scripts/github_preflight.py`

- [ ] **Step 1: Add failing workflow audit tests**

Create `tests/test_github_preflight_workflows.py`:

```python
from __future__ import annotations

from scripts.github_preflight import audit_workflow_text


def test_broad_self_hosted_label_fails() -> None:
    workflow = """
on: push
jobs:
  ci:
    runs-on: [self-hosted, linux, x64]
    steps:
      - run: echo unsafe
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == [
        ".github/workflows/ci.yml job ci targets self-hosted runners without "
        "required label paper-archives."
    ]


def test_pull_request_target_to_runner_fails() -> None:
    workflow = """
on: pull_request_target
jobs:
  ci:
    runs-on: [self-hosted, paper-archives]
    steps:
      - uses: actions/checkout@v4
      - run: make test
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == [
        ".github/workflows/ci.yml uses unsafe trigger pull_request_target on "
        "runner label paper-archives."
    ]


def test_broad_label_with_unsafe_trigger_reports_both_errors() -> None:
    workflow = """
on: pull_request_target
jobs:
  ci:
    runs-on: [self-hosted, linux, x64]
    steps:
      - run: make test
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == [
        ".github/workflows/ci.yml job ci targets self-hosted runners without "
        "required label paper-archives.",
        ".github/workflows/ci.yml uses unsafe trigger pull_request_target on "
        "runner label paper-archives.",
    ]


def test_repo_specific_label_on_push_passes() -> None:
    workflow = """
on: push
jobs:
  ci:
    runs-on: [self-hosted, linux, x64, paper-archives]
    steps:
      - run: echo ok
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == []
```

- [ ] **Step 2: Run workflow tests to verify they fail**

Run:

```bash
pytest tests/test_github_preflight_workflows.py -q
```

Expected: FAIL because `audit_workflow_text` is missing.

- [ ] **Step 3: Implement workflow audit helpers**

Modify `scripts/github_preflight.py` by adding these helpers before `main`:

```python
BROAD_LABELS = {"self-hosted", "linux", "x64"}
UNSAFE_TRIGGERS = {"pull_request_target", "workflow_run", "issue_comment"}


def load_workflow(text: str) -> dict[str, Any]:
    loaded = yaml.load(text, Loader=yaml.BaseLoader)
    return loaded if isinstance(loaded, dict) else {}


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def workflow_triggers(workflow: dict[str, Any]) -> set[str]:
    raw = workflow.get("on", {})
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(item) for item in raw}
    if isinstance(raw, dict):
        return {str(key) for key in raw}
    return set()


def job_labels(job: dict[str, Any]) -> list[str]:
    return normalize_list(job.get("runs-on"))


def job_targets_self_hosted(labels: list[str], required_label: str) -> bool:
    label_set = set(labels)
    return required_label in label_set or "self-hosted" in label_set


def audit_workflow_text(*, path: str, text: str, required_label: str) -> CheckResult:
    workflow = load_workflow(text)
    triggers = workflow_triggers(workflow)
    jobs = workflow.get("jobs", {})
    errors: list[str] = []

    if not isinstance(jobs, dict):
        return CheckResult(errors=[], warnings=[])

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        labels = job_labels(job)
        if not labels:
            continue
        if "${{" in ",".join(labels):
            errors.append(f"{path} job {job_name} uses dynamic runs-on.")
            continue
        if job_targets_self_hosted(labels, required_label) and required_label not in labels:
            errors.append(
                f"{path} job {job_name} targets self-hosted runners without "
                f"required label {required_label}."
            )
        if job_targets_self_hosted(labels, required_label):
            unsafe = sorted(triggers & UNSAFE_TRIGGERS)
            for trigger in unsafe:
                errors.append(
                    f"{path} uses unsafe trigger {trigger} on runner label "
                    f"{required_label}."
                )

    return CheckResult(errors=errors, warnings=[])
```

- [ ] **Step 4: Run workflow tests to verify they pass**

Run:

```bash
pytest tests/test_github_preflight_workflows.py -q
```

Expected: PASS.

- [ ] **Step 5: Wire workflow and CODEOWNERS warnings into API preflight**

Add tests for this wiring in `tests/test_github_preflight_api.py`:

```python
def test_missing_codeowners_warns_but_does_not_fail() -> None:
    routes = {
        ("GET", "/repos/drc-dot-nz/paper-archives"): (
            200,
            {"private": True, "default_branch": "main"},
        ),
        ("POST", "/repos/drc-dot-nz/paper-archives/actions/runners/registration-token"): (
            201,
            {"token": "short-lived"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/rules/branches/main"): (200, []),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/.github/workflows?ref=main"): (
            200,
            [],
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/.github/CODEOWNERS?ref=main"): (
            404,
            {"message": "not found"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/CODEOWNERS?ref=main"): (
            404,
            {"message": "not found"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/docs/CODEOWNERS?ref=main"): (
            404,
            {"message": "not found"},
        ),
    }
    with MockGitHubServer(routes) as server:
        code, result = run_preflight(server.url)
    assert code == 0
    assert "No CODEOWNERS coverage found for workflow or local action paths." in result[
        "warnings"
    ]


def test_codeowners_without_required_paths_warns() -> None:
    codeowners = "/src/** @drc-dot-nz"
    routes = {
        ("GET", "/repos/drc-dot-nz/paper-archives"): (
            200,
            {"private": True, "default_branch": "main"},
        ),
        ("POST", "/repos/drc-dot-nz/paper-archives/actions/runners/registration-token"): (
            201,
            {"token": "short-lived"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/rules/branches/main"): (200, []),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/.github/workflows?ref=main"): (
            200,
            [],
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/.github/CODEOWNERS?ref=main"): (
            200,
            {"content": encoded(codeowners)},
        ),
    }
    with MockGitHubServer(routes) as server:
        code, result = run_preflight(server.url)
    assert code == 0
    assert "No CODEOWNERS coverage found for workflow or local action paths." in result[
        "warnings"
    ]
```

Then implement the minimal API wiring:

```python
def decode_content(data: dict[str, Any]) -> str:
    content = str(data.get("content", "")).replace("\n", "")
    return base64.b64decode(content).decode()


def codeowners_covers_path(pattern: str, path: str) -> bool:
    pattern = pattern.strip()
    if not pattern or pattern.startswith("#"):
        return False
    pattern = pattern.split()[0]
    if pattern.startswith("/"):
        pattern = pattern[1:]
    if pattern.endswith("/"):
        pattern = f"{pattern}**"
    if pattern == "*":
        return True
    return PurePosixPath(path).match(pattern)


def codeowners_has_required_coverage(text: str, required_paths: list[str]) -> bool:
    patterns = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    patterns = [pattern for pattern in patterns if pattern]
    for required_path in required_paths:
        if not any(codeowners_covers_path(pattern, required_path) for pattern in patterns):
            return False
    return True


def check_codeowners(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    owner: str,
    repo: str,
    branch: str,
    required_paths: list[str],
) -> CheckResult:
    for path in [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"]:
        status, data, _headers = request_json(
            api_base_url=api_base_url,
            api_version=api_version,
            token=token,
            method="GET",
            path=f"/repos/{owner}/{repo}/contents/{path}?ref={branch}",
            purpose=f"CODEOWNERS lookup {path}",
        )
        if status == 200:
            text = decode_content(data)
            if codeowners_has_required_coverage(text, required_paths):
                return CheckResult(errors=[], warnings=[])
    return CheckResult(
        errors=[],
        warnings=["No CODEOWNERS coverage found for workflow or local action paths."],
    )
```

Call `check_codeowners` after the registration token probe when repo metadata
has a `default_branch`, passing:

```python
required_codeowner_paths = [
    ".github/workflows/example.yml",
    ".github/actions/example/action.yml",
]
```

This minimal list verifies workflow and local-action coverage in Sprint 1.
Later sprints can derive exact paths from the fetched workflow/action tree.

- [ ] **Step 6: Run all tests**

Run:

```bash
make test
```

Expected: PASS.

- [ ] **Step 7: Commit workflow audit**

Run:

```bash
git add scripts/github_preflight.py tests
git commit -m "Add workflow safety preflight"
```

## Task 6: Complete API-Backed Preflight Coverage

**Files:**
- Modify: `scripts/github_preflight.py`
- Modify: `tests/test_github_preflight_api.py`

- [ ] **Step 1: Add failing tests for labels, branch posture, and workflow fetch**

Append these tests to `tests/test_github_preflight_api.py`:

```python
import base64


def encoded(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def test_missing_required_runner_label_fails() -> None:
    proc = subprocess.run(
        [
            *BASE_ARGS,
            "--api-base-url",
            "http://127.0.0.1:9",
            "--runner-labels",
            "self-hosted,linux,x64",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    result = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert "Runner labels must include repository-specific label paper-archives." in result[
        "errors"
    ]


def test_empty_branch_rules_warns() -> None:
    routes = {
        ("GET", "/repos/drc-dot-nz/paper-archives"): (
            200,
            {"private": True, "default_branch": "main"},
        ),
        ("POST", "/repos/drc-dot-nz/paper-archives/actions/runners/registration-token"): (
            201,
            {"token": "short-lived"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/rules/branches/main"): (200, []),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/.github/workflows?ref=main"): (
            404,
            {"message": "not found"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/.github/CODEOWNERS?ref=main"): (
            404,
            {"message": "not found"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/CODEOWNERS?ref=main"): (
            404,
            {"message": "not found"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/docs/CODEOWNERS?ref=main"): (
            404,
            {"message": "not found"},
        ),
    }
    with MockGitHubServer(routes) as server:
        code, result = run_preflight(server.url)
    assert code == 0
    assert "No active branch rules returned for default branch main." in result["warnings"]


def test_workflow_file_from_api_with_broad_label_fails() -> None:
    workflow = """
on: push
jobs:
  ci:
    runs-on: [self-hosted, linux, x64]
    steps:
      - run: echo unsafe
"""
    routes = {
        ("GET", "/repos/drc-dot-nz/paper-archives"): (
            200,
            {"private": True, "default_branch": "main"},
        ),
        ("POST", "/repos/drc-dot-nz/paper-archives/actions/runners/registration-token"): (
            201,
            {"token": "short-lived"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/rules/branches/main"): (
            200,
            [{"type": "pull_request"}],
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/.github/workflows?ref=main"): (
            200,
            [{"name": "ci.yml", "path": ".github/workflows/ci.yml", "type": "file"}],
        ),
        (
            "GET",
            "/repos/drc-dot-nz/paper-archives/contents/.github/workflows/ci.yml?ref=main",
        ): (
            200,
            {"content": encoded(workflow)},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/.github/CODEOWNERS?ref=main"): (
            404,
            {"message": "not found"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/CODEOWNERS?ref=main"): (
            404,
            {"message": "not found"},
        ),
        ("GET", "/repos/drc-dot-nz/paper-archives/contents/docs/CODEOWNERS?ref=main"): (
            404,
            {"message": "not found"},
        ),
    }
    with MockGitHubServer(routes) as server:
        code, result = run_preflight(server.url)
    assert code == 1
    assert any("without required label paper-archives" in error for error in result["errors"])
```

- [ ] **Step 2: Run API tests to verify they fail**

Run:

```bash
pytest tests/test_github_preflight_api.py -q
```

Expected: FAIL because label validation, branch posture, and workflow API fetch
are not fully wired yet.

- [ ] **Step 3: Implement runner label validation**

Add this function to `scripts/github_preflight.py`:

```python
def check_runner_labels(*, required_label: str, runner_labels: list[str]) -> CheckResult:
    errors: list[str] = []
    if required_label not in runner_labels:
        errors.append(
            f"Runner labels must include repository-specific label {required_label}."
        )
    return CheckResult(errors=errors, warnings=[])
```

In `main`, after PAT lifetime validation, add:

```python
    runner_labels = [label.strip() for label in args.runner_labels.split(",") if label.strip()]
    label_check = check_runner_labels(
        required_label=args.required_label,
        runner_labels=runner_labels,
    )
    errors.extend(label_check.errors)
    warnings.extend(label_check.warnings)
```

If `label_check.errors` is non-empty, skip GitHub API calls and print the JSON
result so local label failures do not depend on network behavior.

- [ ] **Step 4: Implement branch posture and workflow API helpers**

Add these functions to `scripts/github_preflight.py`:

```python
def check_branch_rules(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    owner: str,
    repo: str,
    branch: str,
) -> CheckResult:
    status, data, _headers = request_json(
        api_base_url=api_base_url,
        api_version=api_version,
        token=token,
        method="GET",
        path=f"/repos/{owner}/{repo}/rules/branches/{branch}",
        purpose=f"branch rules lookup for {branch}",
    )
    if status != 200:
        return CheckResult(
            errors=[],
            warnings=[f"Could not read active branch rules for {branch}: HTTP {status}."],
        )
    if not data:
        return CheckResult(
            errors=[],
            warnings=[f"No active branch rules returned for default branch {branch}."],
        )
    rule_types = {str(rule.get("type", "")) for rule in data if isinstance(rule, dict)}
    if "pull_request" not in rule_types:
        return CheckResult(
            errors=[],
            warnings=[f"No pull request review rule returned for default branch {branch}."],
        )
    return CheckResult(errors=[], warnings=[])


def fetch_workflow_audit(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    owner: str,
    repo: str,
    branch: str,
    required_label: str,
) -> CheckResult:
    status, data, _headers = request_json(
        api_base_url=api_base_url,
        api_version=api_version,
        token=token,
        method="GET",
        path=f"/repos/{owner}/{repo}/contents/.github/workflows?ref={branch}",
        purpose="workflow directory listing",
    )
    if status == 404:
        return CheckResult(errors=[], warnings=["No workflow directory found."])
    if status != 200:
        return CheckResult(
            errors=[f"Could not list workflow files: HTTP {status}."],
            warnings=[],
        )

    errors: list[str] = []
    warnings: list[str] = []
    for entry in data:
        path = str(entry.get("path", ""))
        name = str(entry.get("name", ""))
        if not name.endswith((".yml", ".yaml")):
            continue
        status, content, _headers = request_json(
            api_base_url=api_base_url,
            api_version=api_version,
            token=token,
            method="GET",
            path=f"/repos/{owner}/{repo}/contents/{path}?ref={branch}",
            purpose=f"workflow file fetch {path}",
        )
        if status != 200:
            errors.append(f"Could not fetch workflow file {path}: HTTP {status}.")
            continue
        audit = audit_workflow_text(
            path=path,
            text=decode_content(content),
            required_label=required_label,
        )
        errors.extend(audit.errors)
        warnings.extend(audit.warnings)

    return CheckResult(errors=errors, warnings=warnings)
```

Call `check_branch_rules`, `check_codeowners`, and `fetch_workflow_audit` after
the registration token probe when `repo_data["default_branch"]` is present.

- [ ] **Step 5: Run API tests to verify they pass**

Run:

```bash
pytest tests/test_github_preflight_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Run all tests**

Run:

```bash
make test
```

Expected: PASS.

- [ ] **Step 7: Commit completed API-backed preflight coverage**

Run:

```bash
git add scripts/github_preflight.py tests/test_github_preflight_api.py
git commit -m "Complete API-backed preflight checks"
```

## Task 7: Ansible Role Integration Tests And Operator Docs

**Files:**
- Create: `docs/preflight.md`
- Create: `tests/test_preflight_playbook.py`

- [ ] **Step 1: Add playbook integration test**

Create `tests/test_preflight_playbook.py`:

```python
from __future__ import annotations

import subprocess


def test_preflight_playbook_requires_vault_token() -> None:
    proc = subprocess.run(
        ["ansible-playbook", "playbooks/preflight.yml", "-e", "vault_github_pat="],
        check=False,
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    assert "Missing GitHub preflight config or vault_github_pat" in proc.stdout
```

- [ ] **Step 2: Run integration test**

Run:

```bash
pytest tests/test_preflight_playbook.py -q
```

Expected: PASS.

- [ ] **Step 3: Add operator documentation**

Create `docs/preflight.md`:

````markdown
# GitHub Preflight

Run preflight before any Proxmox work:

```bash
ansible-playbook playbooks/preflight.yml
```

Hard failures:

- Target repository is public.
- GitHub rejects the PAT.
- PAT cannot request a repository runner registration token.
- PAT has 7 days or fewer remaining.
- PAT has more than 30 days remaining.
- Runner labels do not include `paper-archives`.
- Workflow jobs can route to the runner without `paper-archives`.
- Unsafe triggers can route to the runner.

Warnings:

- PAT has 14 days or fewer remaining.
- Branch protection or active branch rulesets are missing.
- Required review posture is weak.
- CODEOWNERS coverage is missing.

Warnings do not block solo-developer mode because the repository owner can
bypass review and CODEOWNERS controls.

The inventory default `github_pat_expires_on: "1970-01-01"` is a placeholder
that intentionally fails. Set it to the real fine-grained PAT expiration date
before running preflight against GitHub.
````

- [ ] **Step 4: Run full local verification**

Run:

```bash
make check
```

Expected: PASS for YAML lint, Ansible lint, tests, and inventory parsing.

- [ ] **Step 5: Commit integration and docs**

Run:

```bash
git add docs/preflight.md tests/test_preflight_playbook.py roles/preflight/tasks/main.yml
git commit -m "Document and test GitHub preflight"
```

## Task 8: Sprint 1 Self-Review

**Files:**
- Modify only files changed by prior tasks if review finds issues.

- [ ] **Step 1: Scan for unresolved placeholders and unsafe terms**

Run:

```bash
PATTERN='T''BD|TO''DO|PLACE''HOLDER|FIX''ME|example_replace_me'
rg -n "$PATTERN" \
  --glob '!inventory/group_vars/all/vault.yml.example' \
  --glob '!docs/superpowers/plans/**'
```

Expected: no output.

- [ ] **Step 2: Confirm no real secrets are present**

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

Expected: PASS.

- [ ] **Step 4: Commit any review fixes**

If Step 1, Step 2, or Step 3 required changes, commit them:

```bash
git add .gitignore .yamllint.yml Makefile ansible.cfg docs inventory \
  playbooks requirements-dev.txt requirements.yml roles scripts tests pyproject.toml
git commit -m "Polish Sprint 1 preflight foundation"
```

If no files changed, do not create an empty commit.

## Sources Checked For Version And API Details

- PyPI package metadata on 2026-05-20:
  `ansible-core==2.21.0`, `ansible-lint==26.4.0`, `yamllint==1.38.0`,
  `pytest==9.0.3`, `PyYAML==6.0.3`.
- GitHub REST rules endpoints:
  `GET /repos/{owner}/{repo}/rules/branches/{branch}`.
- GitHub self-hosted runner pre-job and post-job hook documentation.
- GitHub CODEOWNERS documentation for supported file locations.
