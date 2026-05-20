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
- It reports a separate hard failure if partial VM cleanup itself fails.

The Ubuntu image is pinned in `inventory/group_vars/proxmox/vars.yml` by URL and
SHA256 checksum. Updating the base image is an explicit inventory change.

Re-running the playbook is safe when the expected template already exists. The
playbook verifies that Proxmox reports `template: 1` and the expected name.
