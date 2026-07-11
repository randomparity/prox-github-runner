# Multi-site GitHub Runner Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second Proxmox host (`gamera`) and its runner VM (`pa-runner`, VMID 599, 192.168.5.99) alongside the existing `ultron`/`pa-runner` site, both feeding the same GitHub repository.

**Architecture:** Introduce per-site inventory groups (`site_pdx`, `site_cae`), each bundling one Proxmox host with its one runner VM. Per-site identity moves into `group_vars/site_*`; the existing roles and playbooks — which read flat, per-host variables — run unchanged against both sites.

**Tech Stack:** Ansible (ansible-core 2.21), YAML inventory + `group_vars`. No Python, role, or playbook logic changes.

## Global Constraints

- Real site values (IPs, hostnames, node names) stay OUT of git: committed files use RFC 5737 placeholders; real values live in gitignored `vars_local.yml` (glob `inventory/group_vars/**/vars_local.yml`).
- GitHub runner names must be unique per repo: cae site sets `github_runner_name_prefix: "pa-runner-cae"`; pdx keeps the default (`runner_vm_name` → `pa-runner`).
- No changes to any role task, playbook, template, script, or test logic.
- yamllint: 100-char lines, `document-start` required (`---`), truthy limited to `true`/`false`.
- Guardrail suite is `make check` (yamllint, ansible-lint, ruff, ty, `ansible-playbook --syntax-check`, pytest, `ansible-inventory --list`). It must stay green.
- Work is on branch `feat/multi-site-runner-support`.

---

### Task 1: Restructure committed inventory into per-site groups

Land the committed structure: rename inventory host labels, add the second host + VM, add site groups, and move per-site vars out of the shared groups into committed placeholder `site_*/vars.yml`. Real values come in Task 2.

**Files:**
- Modify: `inventory/hosts.yml`
- Modify: `inventory/group_vars/all/vars.yml`
- Modify: `inventory/group_vars/proxmox/vars.yml`
- Create: `inventory/group_vars/site_pdx/vars.yml`
- Create: `inventory/group_vars/site_cae/vars.yml`
- Create: `inventory/group_vars/site_pdx/vars_local.yml.example`
- Create: `inventory/group_vars/site_cae/vars_local.yml.example`
- Delete (git rm): `inventory/group_vars/all/vars_local.yml.example`
- Delete (git rm): `inventory/group_vars/proxmox/vars_local.yml.example`

**Interfaces:**
- Produces: inventory groups `proxmox` (hosts `ultron`, `gamera`), `runner` (hosts `pa-runner`, `pa-runner-cae`), `site_pdx` (`ultron`, `pa-runner`), `site_cae` (`gamera`, `pa-runner-cae`). Each site group_vars defines `proxmox_api_host`, `proxmox_node`, `runner_vm_id`, `runner_vm_name`, `runner_vm_ip`, `runner_vm_gateway`, `runner_vm_nameserver`, `runner_vm_searchdomain`; `site_cae` additionally defines `github_runner_name_prefix`.

- [ ] **Step 1: Rewrite `inventory/hosts.yml`**

```yaml
---
all:
  children:
    proxmox:
      hosts:
        ultron:
          ansible_host: "{{ proxmox_api_host }}"
          ansible_user: root
        gamera:
          ansible_host: "{{ proxmox_api_host }}"
          ansible_user: root
    runner:
      hosts:
        pa-runner:
          ansible_host: "{{ runner_vm_ip }}"
          ansible_user: "{{ runner_bootstrap_user }}"
        pa-runner-cae:
          ansible_host: "{{ runner_vm_ip }}"
          ansible_user: "{{ runner_bootstrap_user }}"
    site_pdx:
      hosts:
        ultron:
        pa-runner:
    site_cae:
      hosts:
        gamera:
        pa-runner-cae:
```

- [ ] **Step 2: Trim per-site identity from `inventory/group_vars/all/vars.yml`**

Replace this block:

```yaml
# Site-specific runner VM identity. The values below are PLACEHOLDERS (RFC 5737
# documentation range). Override the real ones in a gitignored local file,
# inventory/group_vars/all/vars_local.yml, which Ansible loads after this file.
# VM ID convention: last two octets of the runner IP (e.g. x.x.2.99 -> 299).
runner_vm_id: 2100
runner_vm_name: "paper-archives-runner"
runner_vm_ip: "192.0.2.50"
runner_vm_gateway: "192.0.2.1"
runner_vm_cidr: 24
runner_vm_nameserver: "192.0.2.1"
runner_vm_searchdomain: "example.test"
runner_bootstrap_user: "runner"
```

with:

```yaml
# Shared runner-VM settings (identical across sites). Per-site identity
# (id/name/ip/gateway/nameserver/searchdomain) lives in group_vars/site_*.
runner_vm_cidr: 24
runner_bootstrap_user: "runner"
```

Leave the `proxmox_vm_denied_cidrs` block below it unchanged — it references `proxmox_api_host` and resolves per-site.

- [ ] **Step 3: Trim per-site Proxmox target from `inventory/group_vars/proxmox/vars.yml`**

Replace this block:

```yaml
# Site-specific Proxmox host. proxmox_api_host and proxmox_node are PLACEHOLDERS
# (RFC 5737 documentation range / default node name); override the real values
# in a gitignored local file, inventory/group_vars/proxmox/vars_local.yml, which
# Ansible loads after this file. proxmox_api_host is the SSH target for the node.
proxmox_api_host: "192.0.2.10"
proxmox_api_port: 8006
proxmox_node: "pve"
proxmox_storage: "local-lvm"
```

with:

```yaml
# Proxmox settings shared across all hypervisor hosts. The per-host SSH target
# (proxmox_api_host) and node name (proxmox_node) live in group_vars/site_*.
proxmox_api_port: 8006
proxmox_storage: "local-lvm"
```

Leave all `proxmox_template_*` lines below unchanged.

- [ ] **Step 4: Create `inventory/group_vars/site_pdx/vars.yml`**

```yaml
---
# Site "pdx": the ultron Proxmox host and its pa-runner VM. Values here are
# PLACEHOLDERS (RFC 5737 range / default node). Override the real ones in the
# gitignored inventory/group_vars/site_pdx/vars_local.yml, loaded after this.
# VM ID convention: last two octets of the runner IP (e.g. x.x.2.99 -> 299).
proxmox_api_host: "192.0.2.10"
proxmox_node: "pve"
runner_vm_id: 2100
runner_vm_name: "paper-archives-runner"
runner_vm_ip: "192.0.2.50"
runner_vm_gateway: "192.0.2.1"
runner_vm_nameserver: "192.0.2.1"
runner_vm_searchdomain: "example.test"
```

- [ ] **Step 5: Create `inventory/group_vars/site_cae/vars.yml`**

```yaml
---
# Site "cae": the gamera Proxmox host and its pa-runner VM. Values here are
# PLACEHOLDERS. Override the real ones in the gitignored
# inventory/group_vars/site_cae/vars_local.yml, loaded after this file.
# github_runner_name_prefix keeps this VM's runner registrations distinct from
# the pdx site's when both target the same repository.
proxmox_api_host: "192.0.2.20"
proxmox_node: "pve"
runner_vm_id: 2200
runner_vm_name: "paper-archives-runner"
runner_vm_ip: "192.0.2.60"
runner_vm_gateway: "192.0.2.1"
runner_vm_nameserver: "192.0.2.1"
runner_vm_searchdomain: "example.test"
github_runner_name_prefix: "paper-archives-runner-cae"
```

- [ ] **Step 6: Create `inventory/group_vars/site_pdx/vars_local.yml.example`**

```yaml
---
# Copy to vars_local.yml (gitignored) and set this site's real values. Loaded
# after vars.yml, so these keys override the placeholders there.
proxmox_api_host: "10.0.0.10"
proxmox_node: "pve"
runner_vm_id: 99
runner_vm_name: "pa-runner"
runner_vm_ip: "10.0.0.99"
runner_vm_gateway: "10.0.0.1"
runner_vm_nameserver: "10.0.0.1"
runner_vm_searchdomain: "example.lan"
```

- [ ] **Step 7: Create `inventory/group_vars/site_cae/vars_local.yml.example`**

```yaml
---
# Copy to vars_local.yml (gitignored) and set this site's real values. Loaded
# after vars.yml, so these keys override the placeholders there. Set a distinct
# github_runner_name_prefix so this site's runner registrations do not collide
# with another site's on the same repository.
proxmox_api_host: "10.0.1.10"
proxmox_node: "pve"
runner_vm_id: 199
runner_vm_name: "pa-runner"
runner_vm_ip: "10.0.1.99"
runner_vm_gateway: "10.0.1.1"
runner_vm_nameserver: "10.0.1.1"
runner_vm_searchdomain: "example.lan"
github_runner_name_prefix: "pa-runner-cae"
```

- [ ] **Step 8: Remove the now-obsolete shared-group example files**

```bash
git rm inventory/group_vars/all/vars_local.yml.example \
       inventory/group_vars/proxmox/vars_local.yml.example
```

- [ ] **Step 9: Verify the inventory parses and resolves placeholder site vars**

Run: `make setup >/dev/null && .venv/bin/ansible-inventory -i inventory/hosts.yml --host gamera`
Expected: JSON including `"proxmox_api_host": "192.0.2.20"`, `"proxmox_node": "pve"`, `"runner_vm_id": 2200`.

Run: `.venv/bin/ansible-inventory -i inventory/hosts.yml --host pa-runner-cae`
Expected: JSON including `"ansible_host": "192.0.2.60"`, `"github_runner_name_prefix": "paper-archives-runner-cae"`, `"runner_vm_searchdomain": "example.test"`.

Run: `.venv/bin/ansible-inventory -i inventory/hosts.yml --host ultron`
Expected: JSON including `"proxmox_api_host": "192.0.2.10"`, `"runner_vm_id": 2100`.

- [ ] **Step 10: Run the guardrail suite**

Run: `make check`
Expected: PASS — yamllint, ansible-lint, ruff, ruff format, ty, every `ansible-playbook --syntax-check`, pytest (all existing tests unaffected — they use throwaway inventories), and `ansible-inventory --list` all green.

- [ ] **Step 11: Commit**

```bash
git add inventory/hosts.yml \
        inventory/group_vars/all/vars.yml \
        inventory/group_vars/proxmox/vars.yml \
        inventory/group_vars/site_pdx/vars.yml \
        inventory/group_vars/site_cae/vars.yml \
        inventory/group_vars/site_pdx/vars_local.yml.example \
        inventory/group_vars/site_cae/vars_local.yml.example
git rm --cached inventory/group_vars/all/vars_local.yml.example \
                inventory/group_vars/proxmox/vars_local.yml.example 2>/dev/null || true
git commit -m "refactor: model Proxmox+runner sites as per-site inventory groups

Move per-site identity (proxmox_api_host/node, runner_vm_*) out of the
shared all/proxmox groups into group_vars/site_pdx and site_cae, and add
the gamera host + pa-runner-cae VM. Roles and playbooks are unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Migrate real site values into gitignored local overrides

Move the live `ultron` values into `site_pdx/vars_local.yml`, create the real `gamera`/cae values in `site_cae/vars_local.yml`, and delete the old shared-group local files. All files here are gitignored — no commit.

**Files (all gitignored, on-disk only):**
- Create: `inventory/group_vars/site_pdx/vars_local.yml`
- Create: `inventory/group_vars/site_cae/vars_local.yml`
- Delete: `inventory/group_vars/all/vars_local.yml`
- Delete: `inventory/group_vars/proxmox/vars_local.yml`

**Interfaces:**
- Consumes: the site groups and var names produced by Task 1.
- Produces: real values so `site.yml` targets the live hosts.

- [ ] **Step 1: Create `inventory/group_vars/site_pdx/vars_local.yml`**

```yaml
---
# Real values for the ultron (pdx) site. Gitignored -- never committed.
proxmox_api_host: "192.168.2.16"
proxmox_node: "ultron"
runner_vm_id: 299
runner_vm_name: "pa-runner"
runner_vm_ip: "192.168.2.99"
runner_vm_gateway: "192.168.2.1"
runner_vm_nameserver: "192.168.2.1"
runner_vm_searchdomain: "pdx.drc.nz"
```

- [ ] **Step 2: Create `inventory/group_vars/site_cae/vars_local.yml`**

```yaml
---
# Real values for the gamera (cae) site. Gitignored -- never committed.
proxmox_api_host: "gamera.cae.drc.nz"
proxmox_node: "gamera"
runner_vm_id: 599
runner_vm_name: "pa-runner"
runner_vm_ip: "192.168.5.99"
runner_vm_gateway: "192.168.5.1"
runner_vm_nameserver: "192.168.5.1"
runner_vm_searchdomain: "cae.drc.nz"
github_runner_name_prefix: "pa-runner-cae"
```

- [ ] **Step 3: Remove the migrated shared-group local files**

```bash
trash inventory/group_vars/all/vars_local.yml \
      inventory/group_vars/proxmox/vars_local.yml
```

- [ ] **Step 4: Verify real values now resolve**

Run: `.venv/bin/ansible-inventory -i inventory/hosts.yml --host gamera`
Expected: `"proxmox_api_host": "gamera.cae.drc.nz"`, `"proxmox_node": "gamera"`, `"runner_vm_id": 599`.

Run: `.venv/bin/ansible-inventory -i inventory/hosts.yml --host pa-runner-cae`
Expected: `"ansible_host": "192.168.5.99"`, `"github_runner_name_prefix": "pa-runner-cae"`, `"runner_vm_searchdomain": "cae.drc.nz"`.

Run: `.venv/bin/ansible-inventory -i inventory/hosts.yml --host pa-runner`
Expected: `"ansible_host": "192.168.2.99"`, `"runner_vm_id": 299`, and NO `github_runner_name_prefix` key (pdx uses the `runner_vm_name` default → `pa-runner`).

- [ ] **Step 5: Confirm the gitignore keeps the new local files untracked**

Run: `git status --porcelain inventory/group_vars/`
Expected: no `site_pdx/vars_local.yml` or `site_cae/vars_local.yml` lines (they are ignored). Only the committed `vars.yml` / `.example` changes from Task 1 already committed. No commit in this task.

---

### Task 3: Document the site-group model in CLAUDE.md

`CLAUDE.md` (created during `/init`, currently untracked) describes a single-host/single-VM layout. Update the site-configuration section to describe per-site groups, and commit it as part of this branch.

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the inventory structure from Tasks 1-2.

- [ ] **Step 1: Replace the "Site configuration" section body in `CLAUDE.md`**

Find the section beginning `## Site configuration (secrets & local overrides)` and replace its body with:

```markdown
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
```

- [ ] **Step 2: Verify no stale single-VM wording remains**

Run: `rg -n "single Proxmox host and a single runner|one runner VM" CLAUDE.md`
Expected: matches only in the intro "What this is" line (acceptable) — no leftover claim that the inventory holds exactly one runner. If the intro over-claims singularity, soften it to "one or more runner VMs".

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document per-site inventory groups in CLAUDE.md

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Final full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the complete guardrail suite from a clean state**

Run: `make check`
Expected: PASS across yamllint, ansible-lint, ruff, ruff format, ty, all playbook syntax checks, pytest, and `ansible-inventory --list`.

- [ ] **Step 2: Sanity-check the full inventory graph**

Run: `.venv/bin/ansible-inventory -i inventory/hosts.yml --graph`
Expected: `proxmox` contains `ultron` + `gamera`; `runner` contains `pa-runner` + `pa-runner-cae`; `site_pdx` and `site_cae` each contain their host pair.

- [ ] **Step 3: Report readiness**

The branch is ready to push and open a PR. Real provisioning of the gamera site is an operator step (`ansible-playbook playbooks/site.yml`, or `--limit 'site_cae:localhost'`), out of scope for this code change.

---

## Self-Review

**Spec coverage:**
- Per-site groups (`site_pdx`/`site_cae`) → Task 1 Step 1.
- Variable ownership moves → Task 1 Steps 2-5.
- Per-site file layout (vars.yml / vars_local.yml / .example) → Task 1 Steps 4-8, Task 2 Steps 1-2.
- site_cae real values incl. `github_runner_name_prefix` → Task 2 Step 2.
- Inventory host renames (`pve`→`ultron`, `paper-archives-runner`→`pa-runner`) → Task 1 Step 1.
- `.gitignore` already covers site `vars_local.yml` → verified Task 2 Step 5.
- Operating notes (`site.yml`, `--limit`) → Task 3 Step 1, Task 4 Step 3.
- Verification (`make check`, `ansible-inventory --host`) → Task 1 Step 9-10, Task 2 Step 4, Task 4.
- CLAUDE.md update → Task 3.

**Placeholder scan:** No TBD/TODO; every step has concrete file content or an exact command with expected output.

**Type/name consistency:** Group names (`proxmox`, `runner`, `site_pdx`, `site_cae`), host labels (`ultron`, `gamera`, `pa-runner`, `pa-runner-cae`), and variable names are identical across all tasks and match the spec.
