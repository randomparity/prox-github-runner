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

Re-running the playbook is safe when the expected template already exists. The
playbook verifies that Proxmox reports `template: 1` and the expected name.
