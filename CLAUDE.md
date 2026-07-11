# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Ansible automation that provisions GitHub Actions **self-hosted runners as VMs on one or more local Proxmox hosts**. The design centers on one security invariant: the target repository (`github_runner_target_repo`, default `drc-dot-nz/paper-archives`) must stay **private**. Much of the code exists to enforce that invariant or to keep the GitHub PAT off the runner VM.

## Commands

All development flows through the `Makefile` (uv-managed `.venv`, Python 3.13):

```bash
make setup       # create .venv, install dev deps, install galaxy collections
make lint        # yamllint + ansible-lint + ruff check + ruff format --check + ty
make syntax      # ansible-playbook --syntax-check on every playbooks/*.yml
make test        # pytest
make check       # lint + syntax + test + inventory  (run this before committing)
make preflight   # ansible-playbook playbooks/preflight.yml (hits real GitHub)
```

Single test / focused runs (activate the venv first, or prefix with `.venv/bin/`):

```bash
.venv/bin/pytest tests/test_github_runner_role.py
.venv/bin/pytest tests/test_github_runner_role.py -k register -q
.venv/bin/ruff check scripts/github_preflight.py
.venv/bin/ty check
```

Guardrail versions are pinned in `pyproject.toml` / `requirements-dev.txt`. Ruff line-length is 100; lint selects `B,E,F,I,UP`. There is a zero-warnings expectation across yamllint, ansible-lint, ruff, and ty.

## Running against real infrastructure

Playbooks are the operator surface. `playbooks/site.yml` is the full non-destructive converge chain (preflight → template → VM → host baseline → runner services); it grows-only and never rebuilds the VM by default. Other playbooks are targeted slices of that chain: `preflight.yml`, `provision-template.yml`, `provision-runner-vm.yml`, `setup-runner.yml`, `check-runner-health.yml`, `unregister-runner.yml`, `run-smoke-workflow.yml`.

`ansible.cfg` points inventory at `inventory/hosts.yml` with two groups: `proxmox` (the PVE node, SSH as root) and `runner` (the guest VM).

## Roles and the converge order

Roles run in this sequence and each has a distinct trust boundary:

1. **`preflight`** (on `localhost`) — runs `scripts/github_preflight.py`, a standalone 650-line Python GitHub auditor. Hard-fails if the repo is public, the PAT is invalid/expiring/too-long-lived, or workflow triggers/labels could route jobs to the runner unsafely. This is the first of three stop actors.
2. **`proxmox_template`** (on `proxmox`) — ensures an Ubuntu 24.04 cloud-init template exists (downloads + checksum-verifies the cloud image, gates on `pveversion`).
3. **`proxmox_vm`** (on `proxmox`) — clones the template to the runner VM via `qm`. Cloud-init identity (static IP, SSH key, user) is written **clone-time only**; identity changes on an existing VM are refused and require an explicit rebuild. Renders a `firewall.fw` denying `proxmox_vm_denied_cidrs` (default: the hypervisor management IP).
4. **`runner_host`** (on `runner`, `become: true`) — all root-level OS config: apt packages (incl. Docker CE, Tauri build libs, qemu-guest-agent), the runner user, per-service directory tree, the tmpfiles.d rule for the tmpfs runtime dir, and the **guard** systemd service+timer.
5. **`github_runner`** (on `runner`, selective escalation) — downloads the pinned `actions/runner` release (sha256-verified), unpacks it into `svc-1..N`, registers each service, and wires job hooks. Runs `config.sh` **unprivileged**; only `svc.sh`/systemd steps escalate via `github_runner_become`.

## Cross-cutting invariants — read before editing

- **The PAT never touches the runner VM.** Every task that uses `vault_github_pat` (registration/removal tokens, health repo-privacy query, smoke dispatch) uses `delegate_to: localhost` + `run_once: true` + `no_log: true`. Preserve all four when adding GitHub API calls. The `# noqa: run-once[task]` comments are intentional.
- **Shared on-disk contract.** Install paths (`runner_install_root`, `runner_bin_dir`, `runner_state_dir`, `runner_jobs_dir`) are defined once in `inventory/group_vars/all/vars.yml` and imported by `runner_host`, `github_runner`, and `check-runner-health.yml`. The role that writes files and the roles that read them must agree — never hardcode a path in one role; change the canonical value.
- **Three stop actors enforce the private-repo invariant.** (a) `preflight` blocks converge; (b) the on-VM **guard timer** (`roles/runner_host/files/prox-github-runner-guard.sh`) polls repo privacy *unauthenticated* every 15 min and `systemctl stop`s all services if the repo reads public or after N soft failures (fail-closed); (c) `check-runner-health.yml` stops all services and fails on a definitively-public repo. Job hook markers under `runner_jobs_dir` are diagnostic only — **not** a stop mechanism.
- **`unregister-runner.yml` intentionally skips preflight** — a now-public/compromised repo's runner must still be tearable down. It scale-reconciles discovered `svc-*` down to `github_runner_count` (pass `github_runner_count=0` for full teardown).
- Runner count supported range is 3–4. Scaling down reconciles surplus services before scaling up.

## Site configuration (secrets & local overrides)

The inventory models each Proxmox host + its one runner VM as a **site group**
(`site_pdx` = `ultron` + `pa-runner`, `site_cae` = `gamera` + `pa-runner-cae`).
A host inherits its site group's `group_vars`, so every play (`hosts: proxmox`,
`hosts: runner`, `hosts: localhost`) runs unchanged against all sites. Adding a
site = add two host entries + a `site_*` group in `inventory/hosts.yml` and a
`group_vars/site_*/` directory.

Per-site identity (`proxmox_api_host`, `proxmox_node`, `runner_vm_id/name/ip/
gateway/nameserver/searchdomain`, and `github_runner_name_prefix` when a site
shares a repo with another) lives in `group_vars/site_*`. Repo-scoped config
(target repo, labels, `github_runner_count`, on-disk contract, PAT policy) stays
in `group_vars/all`; shared Proxmox settings (storage, bridge, template) stay in
`group_vars/proxmox`.

Committed vars use RFC 5737 placeholder IPs. Real values live in **gitignored**
files loaded after the committed `vars.yml`:

- `group_vars/site_*/vars_local.yml` — real per-site IPs/hostnames/node names
  (template: `vars_local.yml.example`).
- `inventory/group_vars/all/vault.yml` — `vault_github_pat`, Proxmox API token,
  bootstrap password; encrypt with `ansible-vault` (template: `vault.yml.example`).

GitHub runner names must be unique per repo: a second VM on the same repo sets a
distinct `github_runner_name_prefix` (e.g. `pa-runner-cae` → `pa-runner-cae-1..3`).

`github_pat_expires_on` defaults to `1970-01-01`, a placeholder that
intentionally fails preflight until set to the real fine-grained PAT expiry.

Converge everything with `ansible-playbook playbooks/site.yml` (idempotent;
already-built sites are grow-only no-ops), or target one site with
`--limit 'site_cae:localhost'` (keep `localhost` so the preflight play runs).

## Testing conventions

`tests/` are pytest, but they don't unit-test in isolation — most **invoke real `ansible-playbook`** against a throwaway inventory and assert on results. Two gate variables make roles converge on any dev machine:

- `*_apply_system: false` skips tasks that mutate the OS (apt, users, systemd, `/etc`).
- `*_become: false` runs privileged steps (`svc.sh`) unprivileged against fakes.

`tests/mock_github.py` provides an in-process `ThreadingHTTPServer` standing in for `api.github.com`; role/playbook tests point runner tarball downloads and API calls at served fakes (see the `CONFIG_SH`/`SVC_SH` fakes baked into a served tarball in `test_github_runner_role.py`). When adding a role behavior, add a test that drives the playbook with these gates set, following the existing `subprocess.run(["ansible-playbook", ...])` pattern.

## Docs

`docs/preflight.md`, `docs/proxmox-template.md`, `docs/smoke-workflow.md` document the operator-facing behavior of the corresponding playbooks — keep them in sync when changing hard-fail/warning conditions.
