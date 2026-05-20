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
- Fail preflight when the target repository is not private, the PAT is rejected
  by GitHub, the PAT lacks required access, or the PAT expiration date is
  expired or inside the warning window, defaulting to 14 days.
- Rerun safely without destroying the VM or forcibly replacing the runner.
- Provide an unregister-only cleanup playbook.
- Provide a runner health-check playbook and documented log locations.
- Configure basic disk maintenance for Docker and runner work directories.
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
- The GitHub preflight can run without touching Proxmox.

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
CPU, RAM, disk, network, and cloud-init settings, starts the VM, waits for SSH,
then waits for cloud-init completion with `cloud-init status --wait`.

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
- Configure a maintenance timer that prunes stale Docker data and runner
  workspace files only when no runner job process is active.

Verification:

- Baseline commands are present.
- Docker Engine is running.
- The runner user can run `docker ps`.
- The maintenance timer is installed and reports its last run status.

### Sprint 5: GitHub Runner Registration

Add the GitHub runner role. It validates required configuration, checks the
target repository privacy, checks the fine-grained PAT expiration date, validates
the PAT with live GitHub API calls, requests a short-lived repository runner
registration token, downloads the GitHub Actions runner package, registers the
runner, and installs it as a systemd service.

The target repository for MVP is `drc-dot-nz/paper-archives`. The token is
stored in Ansible Vault. The non-secret expiration date is stored in inventory.
The fine-grained PAT must have the repository permission needed to create and
remove self-hosted runner registration tokens: Administration write access for
`drc-dot-nz/paper-archives`.

The runner package version is pinned by `github_runner_version`, and automatic
runner self-updates are disabled. Updating the runner is an explicit operator
workflow: update `github_runner_version`, run the setup playbook, and verify
the runner with the health-check playbook.

Registration is guarded by local runner state. If the runner is already
configured, the playbook updates host packages and service state without
registering a duplicate runner.

Verification:

- Expired and near-expiry PAT dates fail preflight.
- Public repositories fail preflight.
- Revoked or under-scoped PATs fail preflight before runner registration.
- The runner service is active.
- The GitHub API lists the runner for `drc-dot-nz/paper-archives`.

### Sprint 6: Cleanup, Health, And Operations

Add an unregister-only cleanup playbook. It stops the runner service, requests
the required GitHub token, unregisters the local runner when configured, removes
the systemd service, and leaves the VM intact.

Cleanup is idempotent. If the local runner config or GitHub runner entry is
already gone, the playbook reports no-op rather than failing.

Add `playbooks/check-runner-health.yml` for routine operator checks. It verifies
runner service state, GitHub API runner status, Docker health, disk usage, recent
maintenance-timer status, and the runner connectivity check from the installed
runner application.

Documentation covers:

- Full deploy.
- Safe rerun behavior.
- PAT expiration and rotation.
- Runner unregister cleanup.
- Runner health checks.
- Log locations and disk maintenance.

Verification:

- Cleanup can run twice.
- The VM remains intact.
- The local runner service is stopped or removed.
- The runner no longer appears in GitHub when it was present before cleanup.
- The health-check playbook reports service, GitHub, Docker, and disk status.

### Sprint 7: Smoke Test And MVP Polish

Add a copyable workflow template for `paper-archives`. The workflow runs on the
self-hosted runner label and proves:

- Repository checkout.
- Shell command execution.
- Docker execution.
- Workspace cleanup basics.

Documentation explicitly states the private-repository-only security boundary
and describes public or ephemeral runners as future work. The smoke workflow is
`workflow_dispatch` only by default, so copying it does not immediately run
unreviewed code on the self-hosted runner.

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
- `playbooks/preflight.yml`: validate inventory, GitHub repo privacy, PAT
  acceptance, PAT permissions, and PAT expiration before infrastructure changes.
- `playbooks/provision-template.yml`: create the Ubuntu 24.04 cloud-init
  template.
- `playbooks/provision-runner-vm.yml`: clone and configure the single runner VM.
- `playbooks/setup-runner.yml`: configure Ubuntu, Docker, GitHub runner files,
  and service.
- `playbooks/unregister-runner.yml`: unregister the runner and stop or remove
  the runner service, leaving the VM intact.
- `playbooks/check-runner-health.yml`: report runner, GitHub, Docker, disk,
  maintenance, and connectivity status.

Roles:

- `preflight`: local config validation and GitHub API checks that must run before
  Proxmox changes in `site.yml`.
- `proxmox_template`: template creation.
- `proxmox_vm`: VM clone, configuration, startup, and SSH wait.
- `runner_host`: Ubuntu packages, Docker, user, directories, and baseline
  tooling, including disk maintenance.
- `github_runner`: PAT preflight, registration token retrieval, runner install,
  service install, status checks, and unregister tasks.

This keeps Proxmox provisioning separate from GitHub runner behavior, so future
operating systems or runner modes can be added without rewriting the MVP.

## Data Flow And Secrets

Configuration starts in inventory. The user sets Proxmox host, node, storage,
template values, one runner VM definition, target repository
`drc-dot-nz/paper-archives`, runner labels, `github_runner_version`, and
`github_pat_expires_on`.

Default runner VM sizing:

- CPU: 4 vCPU.
- RAM: 8192 MB.
- Disk: 128 GB.

Operators can override these values in inventory. The defaults are intentionally
large enough for Docker-backed CI without assuming repository-specific build
caches.

Vault stores:

- Proxmox API token secret.
- VM bootstrap password, if password bootstrap is used.
- GitHub fine-grained PAT.

Deploy flow:

1. Local Ansible runs preflight before Proxmox changes. It validates inventory,
   calls `GET /repos/{owner}/{repo}`, fails unless `private` is true, validates
   the PAT with authenticated GitHub API calls, and verifies Administration write
   access by requesting and discarding a short-lived registration token.
2. Local Ansible connects to the Proxmox host over SSH for `qm` template
   creation.
3. Local Ansible uses `community.proxmox` API calls to clone, configure, and
   start the runner VM.
4. Ansible connects to the new Ubuntu VM after SSH is reachable, then waits for
   cloud-init to complete.
5. The `github_runner` role checks the PAT expiration date before calling
   GitHub.
6. The role requests a short-lived repository runner registration token from
   GitHub.
7. The role downloads the pinned GitHub runner package, registers the runner to
   `drc-dot-nz/paper-archives` with automatic runner updates disabled, and
   installs it as a systemd service.
8. Reruns detect existing runner configuration and converge host state without
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

PAT rotation is an operator workflow:

1. Create a replacement fine-grained PAT scoped to `drc-dot-nz/paper-archives`
   with Administration write access.
2. Update the vaulted PAT.
3. Update `github_pat_expires_on` in inventory.
4. Run `playbooks/preflight.yml`.
5. Run the needed converge or cleanup playbook.

If GitHub returns a `github-authentication-token-expiration` response header,
preflight compares it with `github_pat_expires_on`. A mismatch fails with a
rotation error so the operator fixes inventory and vault together. If the header
is absent, the inventory date remains the source of truth.

## Threat Model

The MVP is for trusted private-repository workflows only. The load-bearing
enforcement is the GitHub preflight check that calls `GET /repos/{owner}/{repo}`
and fails unless GitHub reports `private: true`.

The trusted actor model for `paper-archives` is:

- Only trusted maintainers can push workflow changes that target the
  self-hosted runner label.
- Branch protection or review policy prevents unreviewed workflow changes from
  landing on protected branches.
- Workflows using `pull_request_target` do not target this self-hosted runner.
- The runner label is specific enough that unrelated workflows do not
  accidentally select the VM.

Residual risks remain in the MVP:

- The runner is persistent, so filesystem state can survive between jobs.
- Docker Engine gives the runner user root-equivalent control of the VM through
  the Docker socket.
- Build caches, Docker images, `_work`, `/tmp`, and files under the runner
  user's home can carry state between workflow runs.
- A trusted maintainer or compromised account that can modify workflows can get
  root-equivalent persistent access to the runner VM.

The MVP mitigates these risks with the private-repo preflight, a dedicated VM,
specific runner labels, documented workflow-trigger guidance, and scheduled
maintenance. It does not provide strong isolation between jobs. Stronger
isolation belongs to the future public-repository design with ephemeral or JIT
runners.

## Operational Defaults

Default inventory values:

- Runner VM CPU: 4 vCPU.
- Runner VM memory: 8192 MB.
- Runner VM disk: 128 GB.
- PAT warning threshold: 14 days.
- Docker and stale workspace maintenance: weekly.
- Disk health warning threshold: 80 percent used.
- Disk health failure threshold: 90 percent used.

The maintenance timer runs a script that:

- Exits without changes if a runner job process is active.
- Runs Docker cleanup for images, containers, networks, and build cache older
  than seven days.
- Removes stale runner `_work` and `_temp` entries older than seven days.
- Logs actions to journald.

The maintenance timer is conservative: it protects active jobs and removes only
stale data. It does not clean language-specific caches under the runner user's
home in the MVP; those remain an operator or workflow responsibility.

## Failure Recovery

Preflight fails:
No Proxmox or runner changes are made. Reruns keep failing until the operator
fixes inventory, the target repo, or vault values.

Template creation fails:
A partial template VM may exist. The playbook attempts to purge it before
failing. If cleanup fails, the operator must inspect Proxmox.

VM clone succeeds, cloud-init fails:
The runner VM exists but is not ready. Reruns wait again and report cloud-init
failure. The operator should inspect `/var/log/cloud-init.log`.

Host baseline fails:
The VM exists with partial package state. Reruns resume package, Docker, user,
and timer setup after the operator fixes apt, network, or disk issues.

Runner download fails:
The host baseline remains and the runner may be absent. Reruns download the
pinned package again after the operator fixes network or version values.

Registration token request fails:
The host baseline remains and the runner is unregistered. Reruns repeat GitHub
validation and token request after the operator fixes PAT permissions or expiry.

Runner registers, service install fails:
Local runner config and a GitHub runner entry may exist. Reruns install or
repair the service without duplicate registration. If local config is corrupt,
the operator should run unregister cleanup.

Cleanup fails after local remove:
The GitHub entry may be gone while the service remains. Reruns treat missing
GitHub or local state as no-op and remove the service when possible. The
operator can manually remove the service if systemd state is corrupt.

## Observability And Health

The MVP includes a health-check playbook for routine operations:

```bash
ansible-playbook playbooks/check-runner-health.yml
```

The health check reports:

- Runner systemd service state.
- GitHub API runner status for `drc-dot-nz/paper-archives`.
- Docker daemon state.
- Runner user Docker access.
- Root filesystem and Docker storage usage.
- Last maintenance timer result.
- Runner connectivity check from the installed runner application.

Primary log locations:

- Runner service: `journalctl -u 'actions.runner.*'`.
- Runner diagnostics: the runner install directory's `_diag/` directory.
- Maintenance timer: `journalctl -u prox-github-runner-maintenance.service`.
- Cloud-init: `/var/log/cloud-init.log` and `/var/log/cloud-init-output.log`.
- Docker daemon: `journalctl -u docker.service`.

The runbook documents a weekly manual review cadence for the health-check
playbook until external alerting exists.

## Error Handling And Safety

The MVP fails early before touching infrastructure when:

- Required config is missing.
- The target repo is missing or malformed.
- GitHub reports the target repo is not private.
- GitHub rejects the PAT.
- The PAT cannot access the target repo.
- The PAT cannot request runner registration and removal tokens.
- The PAT expiration date is invalid.
- The PAT is expired.
- The PAT is inside the configured warning window, which defaults to 14 days.
- Required vault variables are unavailable.

GitHub API failures include the endpoint purpose, HTTP status, GitHub request
ID when present, and likely fix, but never echo tokens.

Proxmox template creation cleans up a partial template VM after failed creation.
Runner VM provisioning is non-destructive and must not delete, overwrite, or
rebuild the VM by default.

Runner registration is guarded by local runner state. Existing runner
configuration prevents duplicate registration. Cleanup is idempotent and treats
already-removed local or GitHub state as no-op. Intermediate states are covered
by the failure recovery matrix.

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
  examples contain no real secrets, and preflight can fail safely before Proxmox
  changes.
- Sprint 2: Ubuntu 24.04 template exists and reports as a template.
- Sprint 3: runner VM exists, is started, cloud-init completed, and SSH is
  reachable; SSH readiness and cloud-init completion are separate checks.
- Sprint 4: baseline tools exist, Docker Engine is running, and the runner user
  can run `docker ps`; maintenance is installed and inactive during runner jobs.
- Sprint 5: preflight catches public repos, revoked PATs, under-scoped PATs, and
  expired or near-expiry dates; the pinned runner service is active; GitHub lists
  the runner.
- Sprint 6: unregister cleanup can run twice, leaves the VM intact, removes or
  stops the local service, removes the GitHub runner entry when present, and
  health checks report service, GitHub, Docker, disk, and maintenance status.
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
