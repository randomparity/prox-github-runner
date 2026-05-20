# Proxmox GitHub Runner MVP Design

Date: 2026-05-20

## Context

This repository will provide Ansible automation for creating local GitHub
Actions runners on Proxmox. The first MVP targets one persistent Ubuntu 24.04
runner VM for the private repository `drc-dot-nz/paper-archives`.

The design borrows the broad Ansible structure from
`randomparity/max-media-stack`: Proxmox inventory groups, vault-backed secrets,
`qm` cloud-image template creation, and `community.proxmox` VM clone/configure
tasks.

## MVP Scope

The MVP creates an Ansible project that can:

- Create an Ubuntu 24.04 cloud-image template on Proxmox.
- Clone one VM from that template.
- Configure the VM as a persistent GitHub Actions repository runner.
- Install a baseline CI toolchain plus Docker Engine.
- Register the runner to `drc-dot-nz/paper-archives`.
- Use a fine-grained GitHub PAT stored in Ansible Vault.
- Track the PAT expiration date in non-secret inventory.
- Fail preflight when the PAT is expired or inside the configured warning
  window, defaulting to 14 days.
- Rerun safely without destroying the VM or forcibly replacing the runner.
- Provide an unregister-only cleanup playbook.
- Include a smoke workflow template for `paper-archives`, without writing to
  that repository.

Out of scope for the MVP:

- Public repository runners.
- Organization-level runners.
- Ephemeral or JIT runners.
- Multiple runner VMs.
- Multiple runner services on one VM.
- VM destroy automation.
- GitHub App authentication.
- Repository-specific build stacks.

## Sprint Plan

### Sprint 1: Project Foundation

Set up the Ansible repository structure:

- `ansible.cfg`
- `requirements.yml`
- Inventory layout.
- Vault example files.
- Makefile targets.
- YAML and Ansible lint configuration.
- Documentation skeleton.

Verification:

- Dependency installation works.
- Ansible inventory parses.
- YAML lint passes.
- Vault examples do not contain real secrets.

### Sprint 2: Proxmox Ubuntu Template

Create a focused playbook and role for the Ubuntu 24.04 cloud-init template.
The role downloads the Ubuntu 24.04 cloud image, creates a Proxmox VM with `qm`,
imports the disk, configures cloud-init, enables the guest agent, converts the
VM to a template, and removes the downloaded image.

Template creation should follow the `max-media-stack` rescue pattern: if
template creation fails after a partial VM is created, clean up that partial
template VM before failing.

Verification:

- The template VM exists.
- Proxmox reports it as a template.
- Re-running the template playbook is safe.

### Sprint 3: Runner VM Provisioning

Create a `proxmox_vm` role for one runner VM. It clones the template, configures
CPU, RAM, disk, network, and cloud-init settings, starts the VM, and waits for
SSH.

Reruns are non-destructive. If the VM already exists, the playbook converges
safe settings, starts it if needed, and waits for SSH. It must not destroy,
overwrite, or rebuild the VM by default.

Verification:

- The runner VM exists.
- The VM is started.
- SSH is reachable.
- Cloud-init completed successfully.

### Sprint 4: Runner Host Baseline

Configure Ubuntu 24.04 inside the VM:

- Apply OS package updates.
- Create and configure the runner user.
- Install baseline CI tools such as `git`, `curl`, `jq`, and
  `build-essential`.
- Install Docker Engine for GitHub Actions workflow compatibility.
- Create runner work directories.
- Configure service prerequisites.

Verification:

- Baseline commands are present.
- Docker Engine is running.
- The runner user can run `docker ps`.

### Sprint 5: GitHub Runner Registration

Add the GitHub runner role. It validates required configuration, checks the
fine-grained PAT expiration date, requests a short-lived repository runner
registration token, downloads the GitHub Actions runner package, registers the
runner, and installs it as a systemd service.

The target repository for MVP is `drc-dot-nz/paper-archives`. The token is
stored in Ansible Vault. The non-secret expiration date is stored in inventory.
The fine-grained PAT must have the repository permission needed to create and
remove self-hosted runner registration tokens: Administration write access for
`drc-dot-nz/paper-archives`.

Registration is guarded by local runner state. If the runner is already
configured, the playbook updates host packages and service state without
registering a duplicate runner.

Verification:

- Expired and near-expiry PAT dates fail preflight.
- The runner service is active.
- The GitHub API lists the runner for `drc-dot-nz/paper-archives`.

### Sprint 6: Cleanup And Operations

Add an unregister-only cleanup playbook. It stops the runner service, requests
the required GitHub token, unregisters the local runner when configured, removes
the systemd service, and leaves the VM intact.

Cleanup is idempotent. If the local runner config or GitHub runner entry is
already gone, the playbook reports no-op rather than failing.

Documentation covers:

- Full deploy.
- Safe rerun behavior.
- PAT expiration and rotation.
- Runner unregister cleanup.

Verification:

- Cleanup can run twice.
- The VM remains intact.
- The local runner service is stopped or removed.
- The runner no longer appears in GitHub when it was present before cleanup.

### Sprint 7: Smoke Test And MVP Polish

Add a copyable workflow template for `paper-archives`. The workflow runs on the
self-hosted runner label and proves:

- Repository checkout.
- Shell command execution.
- Docker execution.
- Workspace cleanup basics.

Documentation explicitly states the private-repository-only security boundary
and describes public or ephemeral runners as future work.

Verification:

- The workflow template is documented.
- When copied into `paper-archives`, it can verify checkout, shell, Docker, and
  cleanup behavior on the runner.

## Architecture

The project uses separate inventory groups for Proxmox and the runner VM:

- `proxmox`: Proxmox API and SSH operations.
- `runner`: the Ubuntu VM after provisioning.

Shared VM settings live in `inventory/group_vars/all/vars.yml`. Proxmox
connection and template settings live in `inventory/group_vars/proxmox/vars.yml`.
GitHub and runner settings live in `inventory/group_vars/runner/vars.yml`.
Secrets live in vault files with checked-in example files.

Playbooks:

- `playbooks/site.yml`: full non-destructive converge path.
- `playbooks/provision-template.yml`: create the Ubuntu 24.04 cloud-init
  template.
- `playbooks/provision-runner-vm.yml`: clone and configure the single runner VM.
- `playbooks/setup-runner.yml`: configure Ubuntu, Docker, GitHub runner files,
  and service.
- `playbooks/unregister-runner.yml`: unregister the runner and stop or remove
  the runner service, leaving the VM intact.

Roles:

- `proxmox_template`: template creation.
- `proxmox_vm`: VM clone, configuration, startup, and SSH wait.
- `runner_host`: Ubuntu packages, Docker, user, directories, and baseline
  tooling.
- `github_runner`: PAT preflight, registration token retrieval, runner install,
  service install, status checks, and unregister tasks.

This keeps Proxmox provisioning separate from GitHub runner behavior, so future
operating systems or runner modes can be added without rewriting the MVP.

## Data Flow And Secrets

Configuration starts in inventory. The user sets Proxmox host, node, storage,
template values, one runner VM definition, target repository
`drc-dot-nz/paper-archives`, runner labels, and `github_pat_expires_on`.

Vault stores:

- Proxmox API token secret.
- VM bootstrap password, if password bootstrap is used.
- GitHub fine-grained PAT.

Deploy flow:

1. Local Ansible connects to the Proxmox host over SSH for `qm` template
   creation.
2. Local Ansible uses `community.proxmox` API calls to clone, configure, and
   start the runner VM.
3. Ansible connects to the new Ubuntu VM after cloud-init completes.
4. The `github_runner` role checks the PAT expiration date before calling
   GitHub.
5. The role requests a short-lived repository runner registration token from
   GitHub.
6. The role downloads the GitHub runner package, registers the runner to
   `drc-dot-nz/paper-archives`, and installs it as a systemd service.
7. Reruns detect existing runner configuration and converge host state without
   re-registering unless cleanup has been run.

Cleanup flow:

1. Ansible connects to the runner VM.
2. The `github_runner` role requests the needed GitHub token with the vaulted
   PAT.
3. The role runs the runner removal command if local runner config exists.
4. The role stops or removes the systemd service and leaves the VM running.

The GitHub PAT is never written into persistent runner configuration. The
short-lived runner registration or removal token is only used during
registration or cleanup.

## Error Handling And Safety

The MVP fails early before touching infrastructure when:

- Required config is missing.
- The target repo is missing or malformed.
- The PAT expiration date is invalid.
- The PAT is expired.
- The PAT is inside the configured warning window, which defaults to 14 days.
- Required vault variables are unavailable.

GitHub API failures include the endpoint purpose and likely fix, but never echo
tokens.

Proxmox template creation cleans up a partial template VM after failed creation.
Runner VM provisioning is non-destructive and must not delete, overwrite, or
rebuild the VM by default.

Runner registration is guarded by local runner state. Existing runner
configuration prevents duplicate registration. Cleanup is idempotent and treats
already-removed local or GitHub state as no-op.

The MVP is explicitly private-repository-only. Public repository support is not
implemented.

## Testing And Verification

Local CI for this repository runs:

- YAML lint.
- Ansible lint.
- Inventory parsing.
- Ansible syntax checks.

Real Proxmox and GitHub integration checks are operator-run commands because
they require private infrastructure and secrets.

Sprint-level verification:

- Sprint 1: dependencies install, inventory parses, YAML lint passes, vault
  examples contain no real secrets.
- Sprint 2: Ubuntu 24.04 template exists and reports as a template.
- Sprint 3: runner VM exists, is started, cloud-init completed, and SSH is
  reachable.
- Sprint 4: baseline tools exist, Docker Engine is running, and the runner user
  can run `docker ps`.
- Sprint 5: PAT expiry preflight catches expired or near-expiry dates, runner
  service is active, and GitHub lists the runner.
- Sprint 6: unregister cleanup can run twice, leaves the VM intact, removes or
  stops the local service, and removes the GitHub runner entry when present.
- Sprint 7: smoke workflow template is documented and can prove checkout, shell,
  Docker, and workspace cleanup when copied into `paper-archives`.

## Future Work

Future designs can cover:

- Public repository support with ephemeral or JIT runners.
- Organization-level runners.
- Multiple runner VMs.
- VM destroy and rebuild workflows.
- GitHub App authentication.
- Additional operating systems beyond Ubuntu 24.04.
