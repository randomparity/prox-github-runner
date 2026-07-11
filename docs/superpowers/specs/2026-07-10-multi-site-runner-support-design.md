# Multi-site GitHub runner support

**Date:** 2026-07-10
**Status:** Approved

## Goal

Support more than one Proxmox host, each hosting its own GitHub Actions runner
VM, adding capacity to the same target repository (`drc-dot-nz/paper-archives`).

Concretely: add a second Proxmox host `gamera.cae.drc.nz` and a second runner VM
`pa-runner` (VMID 599, `192.168.5.99`, FQDN `pa-runner.cae.drc.nz`) alongside the
existing `ultron` host and its `pa-runner` VM (VMID 299, `192.168.2.99`).

## Background

The current layout assumes exactly one Proxmox host and one runner VM. Per-VM
identity (`runner_vm_*`) lives in `group_vars/all`; the Proxmox target
(`proxmox_api_host`, `proxmox_node`) lives in `group_vars/proxmox`. Real values
are kept out of git in `vars_local.yml` overrides.

Two findings shape the design:

1. **Roles read flat, per-host variables and never loop over VMs.** `proxmox_vm`
   and `proxmox_template` consume scalar `runner_vm_*` / `proxmox_*` vars. Making
   a host carry its own values is therefore sufficient for multi-VM support — no
   role or playbook logic changes.
2. **GitHub runner names must be unique within a repo.** Registrations are
   `{{ github_runner_name_prefix | default(runner_vm_name) }}-{N}`. Two VMs both
   named `pa-runner` targeting the same repo would collide on `pa-runner-1`.

## Design

### Per-site inventory groups

Introduce a *site* group bundling one Proxmox host with its one runner VM. A host
inherits its site group's `group_vars`, so every existing play
(`hosts: localhost`, `hosts: proxmox`, `hosts: runner`) runs unchanged against
both sites.

```
all
├── proxmox   (hosts: ultron, gamera)
├── runner    (hosts: pa-runner, pa-runner-cae)
├── site_pdx  (hosts: ultron,  pa-runner)
└── site_cae  (hosts: gamera,  pa-runner-cae)
```

Inventory `ansible_host` stays `"{{ proxmox_api_host }}"` (proxmox hosts) and
`"{{ runner_vm_ip }}"` (runner hosts); each now resolves from the host's site
group.

### Variable ownership

| Variable(s) | Scope | Location |
|---|---|---|
| `proxmox_api_host`, `proxmox_node` | per-site | `group_vars/site_*` |
| `runner_vm_id`, `runner_vm_name`, `runner_vm_ip`, `runner_vm_gateway`, `runner_vm_nameserver`, `runner_vm_searchdomain` | per-site | `group_vars/site_*` |
| `github_runner_name_prefix` | per-site (cae only) | `group_vars/site_cae` |
| `github_runner_target_repo`, labels, `github_runner_count`, on-disk contract, PAT policy, preflight config, `proxmox_vm_denied_cidrs` | shared | `group_vars/all` (unchanged) |
| `proxmox_storage`, `proxmox_api_port`, `proxmox_template_*` (bridge, storage, image, memory, cores) | shared | `group_vars/proxmox` (unchanged) |

`proxmox_vm_denied_cidrs` stays in `group_vars/all` — its value
`["{{ proxmox_api_host }}/32"]` auto-resolves to each site's own hypervisor.

### Per-site file layout

Each site mirrors today's committed-placeholder / gitignored-real pattern:

- `group_vars/site_<name>/vars.yml` — committed; RFC 5737 placeholders plus the
  VM-ID-convention docs (last two octets of the runner IP → VMID).
- `group_vars/site_<name>/vars_local.yml` — gitignored; real values.
- `group_vars/site_<name>/vars_local.yml.example` — committed template.

The existing `.gitignore` glob `inventory/group_vars/**/vars_local.yml` already
covers the new files. The real values currently in
`group_vars/{all,proxmox}/vars_local.yml` migrate into `site_pdx/vars_local.yml`;
those two source files are removed.

### site_cae real values (gitignored)

```yaml
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

`github_runner_name_prefix` is an existing override honored by the
`github_runner` role and `check-runner-health.yml`, so cae registrations become
`pa-runner-cae-1/2/3` while the guest hostname stays `pa-runner.cae.drc.nz`.
`proxmox_storage`, bridge, template settings, and `github_runner_count: 3` are
inherited from the shared groups.

### Inventory host renames

For clarity the committed inventory host labels change: `pve` → `ultron`,
`paper-archives-runner` → `pa-runner`. These are inventory labels only.

## Non-goals / unchanged

No changes to role tasks, playbooks, templates, scripts, or test logic. This is
purely an inventory + `group_vars` restructuring plus a `CLAUDE.md` doc update.

## Operating the result

- `ansible-playbook playbooks/site.yml` converges both sites; the already-built
  ultron site is a grow-only no-op.
- `ansible-playbook playbooks/site.yml --limit 'site_cae:localhost'` targets only
  the new site (localhost kept so the preflight play still runs).

## Verification

- `make check` stays green: yamllint, ansible-lint, ruff, ty,
  `ansible-playbook --syntax-check` on every playbook, pytest, and
  `ansible-inventory --list`.
- Confirm no test references the renamed committed inventory host labels; adjust
  if any do.
- `ansible-inventory --host pa-runner-cae` shows the cae identity;
  `ansible-inventory --host gamera` shows gamera's Proxmox target.
- Update `CLAUDE.md` to describe the site-group model.
