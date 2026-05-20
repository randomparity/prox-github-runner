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
  expired, has 7 days or fewer remaining, or is more than 30 days out.
- Warn without failing when the PAT has 14 days or fewer remaining.
- Report branch protection, ruleset, and CODEOWNERS posture as warnings because
  `paper-archives` is a solo-developer repository.
- Fail preflight when unsafe workflows can reach the runner or runner labels are
  too broad.
- Stop the runner service if recurring repository safety checks detect that the
  target repository has become public.
- Verify runner network isolation from the Proxmox management network.
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

### Sprint 1: Project Foundation And Preflight

Set up the Ansible repository structure:

- `ansible.cfg`
- `requirements.yml`
- Inventory layout.
- Vault example files.
- Makefile targets.
- YAML and Ansible lint configuration.
- Documentation skeleton.
- `playbooks/preflight.yml`.
- A `preflight` role that validates inventory, target repository privacy, PAT
  acceptance, PAT expiration window, PAT maximum remaining lifetime, runner
  labels, branch protection or active branch ruleset posture, CODEOWNERS posture,
  and workflow trigger safety without touching Proxmox.
- A fixture-backed mock GitHub API test harness for preflight negative cases.

Verification:

- Dependency installation works.
- Ansible inventory parses.
- YAML lint passes.
- Vault examples do not contain real secrets.
- The GitHub preflight can run without touching Proxmox.
- Preflight fails closed for a public repository, broad runner labels, unsafe
  workflow triggers, and invalid or under-scoped PATs.
- Preflight warns, without failing, for missing branch protection, missing active
  branch rulesets, and missing CODEOWNERS coverage.
- CI exercises those preflight cases against mock GitHub responses, not live
  GitHub.

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

Apply Proxmox-side network isolation for the runner VM. The VM should run on a
dedicated VLAN or bridge and have Proxmox firewall rules that deny access to the
Proxmox management network, the Ansible control host, and configured private
CIDRs that the runner should not reach. Guest firewall rules are not sufficient,
because a compromised workflow has root-equivalent access inside the VM.

Reruns are non-destructive. If the VM already exists, the playbook converges
safe settings, starts it if needed, and waits for SSH. It must not destroy,
overwrite, or rebuild the VM by default.

Verification:

- The runner VM exists.
- The VM is started.
- SSH is reachable.
- Cloud-init completed successfully.
- From the runner VM, probes to Proxmox management SSH/API and configured denied
  CIDRs fail.

### Sprint 4: Runner Host Baseline

Configure Ubuntu 24.04 inside the VM:

- Apply OS package updates.
- Create and configure the runner user.
- Install baseline CI tools such as `git`, `curl`, `jq`, and
  `build-essential`.
- Install Docker Engine for GitHub Actions workflow compatibility.
- Create runner work directories.
- Configure service prerequisites.
- Install cleanup scripts that will be wired into runner job hooks after the
  runner package is installed.
- Configure a runner public-repo guard timer that stops the runner service if an
  unauthenticated GitHub API check shows the target repository has become public.

Verification:

- Baseline commands are present.
- Docker Engine is running.
- The runner user can run `docker ps`.
- Cleanup scripts are installed and pass shell lint.
- The public-repo guard stops the runner service in a simulated public-repo
  response.

### Sprint 5: GitHub Runner Registration

Add the GitHub runner role. It depends on the preflight role, requests a
short-lived repository runner registration token, downloads the GitHub Actions
runner package, wires the cleanup scripts into runner job hooks, registers the
runner, and installs it as a systemd service.

The target repository for MVP is `drc-dot-nz/paper-archives`. The token is
stored in Ansible Vault. The non-secret expiration date is stored in inventory.
The fine-grained PAT must have the repository permission needed to create and
remove self-hosted runner registration tokens: Administration write access for
`drc-dot-nz/paper-archives`. It also needs Contents read access so preflight can
audit workflow files for unsafe `pull_request_target` usage with the runner
label.

The runner package version is pinned by `github_runner_version`, and automatic
runner self-updates are disabled. Updating the runner is an explicit operator
workflow: update `github_runner_version`, run the setup playbook, and verify
the runner with the health-check playbook.

Registration is guarded by local runner state. If the runner is already
configured, the playbook updates host packages and service state without
registering a duplicate runner. If local runner state points at a different
repository than inventory, the playbook fails and tells the operator to run
`playbooks/unregister-runner.yml` before retargeting.

Verification:

- Registration invokes preflight before requesting a registration token.
- Target-repo mismatch fails clearly.
- The runner service is active.
- The GitHub API lists the runner for `drc-dot-nz/paper-archives`.

### Sprint 6: Cleanup, Health, And Operations

Add an unregister-only cleanup playbook. It stops the runner service, requests
the required GitHub token, unregisters the local runner when configured, removes
the systemd service, and leaves the VM intact.

Cleanup is idempotent. If the local runner config or GitHub runner entry is
already gone, the playbook reports no-op rather than failing.

Add `playbooks/check-runner-health.yml` for routine operator checks. It verifies
target repository privacy, default-branch protection or active rulesets,
CODEOWNERS coverage, unsafe workflow trigger usage, runner service state,
GitHub API runner status, Docker health, disk usage, recent guard and cleanup
status, and the runner connectivity check from the installed runner
application. If the health check sees definitive unsafe repository state, it
stops the runner service when idle or writes a stop-after-current-job flag when
a job is active.

In solo-developer mode, missing review protection, missing active branch rules,
and missing CODEOWNERS coverage are not definitive unsafe states. They are
reported as warnings because the repository owner can bypass them.

Documentation covers:

- Full deploy.
- Safe rerun behavior.
- PAT expiration and rotation.
- Runner unregister cleanup.
- Runner health checks.
- Log locations and disk maintenance.
- Retargeting procedure: unregister first, then change inventory, then converge.

Verification:

- Cleanup can run twice.
- The VM remains intact.
- The local runner service is stopped or removed.
- The runner no longer appears in GitHub when it was present before cleanup.
- The health-check playbook reports service, GitHub, Docker, and disk status.
- The health-check playbook stops or schedules a stop when repository safety
  checks return definitive unsafe state.

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
- If the workflow has been copied into `paper-archives`, an operator-run smoke
  playbook can trigger it with `workflow_dispatch` and poll the result. This is
  not a local CI-gated test because it needs the private target repository.

## Architecture

The project uses separate inventory groups for Proxmox and the runner VM:

- `proxmox`: Proxmox API and SSH operations.
- `runner`: the Ubuntu VM after provisioning.

Shared VM settings live in `inventory/group_vars/all/vars.yml`. Proxmox
connection and template settings live in `inventory/group_vars/proxmox/vars.yml`.
GitHub and runner settings live in `inventory/group_vars/runner/vars.yml`.
Secrets live in vault files with checked-in example files.

The runner inventory group is static. The operator reserves a runner IP address
in inventory, the Proxmox VM role applies that IP through cloud-init, and
playbooks that target `runner` execute only after VM provisioning waits for SSH
and cloud-init. The MVP does not use dynamic inventory or IP discovery.

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
  guard, cleanup, and connectivity status.
- `playbooks/run-smoke-workflow.yml`: operator-run workflow dispatch and polling
  for the smoke workflow after the operator has copied it to `paper-archives`.

Roles:

- `preflight`: local config validation and GitHub API checks that must run before
  Proxmox changes in `site.yml`.
- `proxmox_template`: template creation.
- `proxmox_vm`: VM clone, safe configuration convergence, Proxmox firewall
  attachment, startup, and SSH wait.
- `runner_host`: Ubuntu packages, Docker, user, directories, and baseline
  tooling, including job hooks, disk cleanup, and the public-repo guard timer.
- `github_runner`: registration token retrieval, runner install,
  service install, status checks, and unregister tasks.

This keeps Proxmox provisioning separate from GitHub runner behavior, so future
operating systems or runner modes can be added without rewriting the MVP.

VM setting convergence is intentionally narrow:

- CPU, memory, description, and tags are updated in place.
- Disk size may only grow. Shrink requests fail.
- Runner IP, gateway, VLAN, bridge, template, boot disk, and cloud-init identity
  changes fail when the VM already exists. The documented procedure is
  unregister, rebuild explicitly, then converge.
- SSH public key additions are allowed. Removing keys after first boot is
  documented as an operator task inside the VM.

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

Default timeout values:

- SSH wait: 10 minutes.
- Cloud-init completion: 15 minutes.
- GitHub API request: 30 seconds per request with three attempts.
- Runner package download: 5 minutes.

Default PAT lifetime thresholds:

- Warning only: 14 days or fewer remaining.
- Preflight failure: 7 days or fewer remaining.
- Preflight failure: more than 30 days remaining.

Vault stores:

- Proxmox API token secret.
- VM bootstrap password, if password bootstrap is used.
- GitHub fine-grained PAT.

Deploy flow:

1. Local Ansible runs preflight before Proxmox changes. It validates inventory,
   calls `GET /repos/{owner}/{repo}`, fails unless `private` is true, validates
   the PAT with authenticated GitHub API calls, and verifies Administration write
   access by requesting and discarding a short-lived registration token.
   Preflight checks both legacy branch protection and effective active rules
   from `GET /repos/{owner}/{repo}/rules/branches/{default_branch}`. It also
   queries repository rulesets with parent rules included for diagnostics when
   effective rules are missing. Preflight reports review-policy and CODEOWNERS
   posture, verifies runner-label specificity, audits workflow safety and broad
   `runs-on`, and checks PAT lifetime.
2. Local Ansible connects to the Proxmox host over SSH for `qm` template
   creation.
3. Local Ansible uses `community.proxmox` API calls to clone, configure, and
   start the runner VM.
4. Ansible connects to the new Ubuntu VM after SSH is reachable, then waits for
   cloud-init to complete with an explicit timeout.
5. The `runner_host` role installs the host baseline, public-repo guard, and
   cleanup scripts.
6. The `github_runner` role invokes preflight again before registration and
   fails if local runner state points at a different repository than inventory.
7. The role requests a short-lived repository runner registration token from
   GitHub.
8. The role downloads the pinned GitHub runner package, registers the runner to
   `drc-dot-nz/paper-archives` with automatic runner updates disabled, and
   installs it as a systemd service.
9. Reruns detect existing runner configuration and converge host state without
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
   with Administration write access and Contents read access.
2. Update the vaulted PAT.
3. Update `github_pat_expires_on` in inventory.
4. Run `playbooks/preflight.yml`.
5. Run the needed converge or cleanup playbook.

If GitHub returns a `github-authentication-token-expiration` response header,
preflight compares its UTC calendar date with `github_pat_expires_on`, allowing
one day of tolerance for timezone and formatting differences. Preflight checks
the header on the authenticated repository metadata call and the registration
token probe. A parsable disagreement outside tolerance fails. A malformed or
absent header produces a warning and leaves the inventory date as the source of
truth.

The MVP enforces `github_pat_max_remaining_days`, defaulting to 30 days.
Preflight fails if `github_pat_expires_on` is more than that many days in the
future, so the high-power PAT must be short lived. It warns, without failing, at
14 days or fewer remaining and fails at 7 days or fewer remaining. The
health-check playbook logs the same warning but only stops the runner for actual
GitHub rejection or definitive unsafe repository state.

## Threat Model

The MVP is for trusted private-repository workflows only. The load-bearing
enforcement has three layers:

- Deploy preflight calls `GET /repos/{owner}/{repo}` and fails unless GitHub
  reports `private: true`.
- The health-check playbook repeats the authenticated repository privacy and
  branch-protection checks. If they return definitive unsafe state, it stops the
  runner service or schedules a stop after the current job.
- A runner-side public-repo guard timer performs an unauthenticated GitHub API
  check. A private repository should be invisible without auth; if the target
  repo becomes publicly visible, the guard stops the runner service. This guard
  does not store the GitHub PAT on the runner VM.

The trusted actor model for `paper-archives` is:

- `paper-archives` is a solo-developer repository. Branch protection,
  required-review counts, and CODEOWNERS cannot be treated as a hard safety
  boundary because the owner can bypass them.
- Preflight reports whether legacy branch protection, active branch rulesets,
  required reviews, and CODEOWNERS coverage exist.
- CODEOWNERS posture covers `.github/workflows/**`, `.github/actions/**`, and
  any extra composite-action paths configured in inventory when those paths
  exist.
- Missing review protection or CODEOWNERS coverage emits a warning and alert
  hook event, not a preflight failure.
- Preflight verifies the runner is configured with a repository-specific label
  such as `paper-archives`.
- Preflight audits workflow files and fails if any job that can route to this
  runner uses only broad labels such as `self-hosted`, `linux`, or `x64`.
- Preflight audits workflow files and local reusable workflows for unsafe
  triggers that can reach this runner.
- The operator still controls who is trusted to approve and merge workflow
  changes.

Residual risks remain in the MVP:

- The runner is persistent, so filesystem state can survive between jobs.
- Docker Engine gives the runner user root-equivalent control of the VM through
  the Docker socket.
- Build caches, Docker images, `_work`, `/tmp`, and files under the runner
  user's home can carry state between workflow runs.
- A trusted maintainer or compromised account that can modify workflows can get
  root-equivalent persistent access to the runner VM.
- If runner network isolation is missing, a compromised workflow can attempt
  lateral movement toward the Proxmox management network, the Ansible control
  host, or other VMs.
- The fine-grained PAT needs Administration write access for runner
  registration. If vault contents are exposed, that PAT can change repository
  settings, branch protection, visibility, collaborators, and other
  administration-class settings for `paper-archives` until it expires or is
  revoked.

The MVP mitigates these risks with the private-repo preflight, a dedicated VM,
recurring repository safety checks, review-policy posture reporting, specific
runner labels, workflow audits, Proxmox-side network isolation, documented
workflow-trigger guidance, short-lived PATs, and scheduled maintenance. It does
not provide strong isolation between jobs, and it does not prevent the solo
owner or a compromised owner account from changing workflow code. Stronger
isolation belongs to the future public-repository design with ephemeral or JIT
runners.

The minimum network policy denies runner VM egress to:

- Proxmox API and SSH management addresses.
- The Ansible control host.
- Other configured private VM networks.

Allowed egress is limited to DNS, NTP, GitHub endpoints required by Actions,
Ubuntu package mirrors, Docker package and registry endpoints, and any
operator-configured package mirrors needed by `paper-archives`.

PAT compromise response:

- Revoke the PAT in GitHub.
- Rotate the vault value and `github_pat_expires_on`.
- Run `playbooks/preflight.yml`.
- Review repository settings, branch protection, visibility, collaborators, and
  the GitHub runner list for unexpected changes.
- Run `playbooks/unregister-runner.yml` for any unexpected runner registration.

## Operational Defaults

Default inventory values:

- Runner VM CPU: 4 vCPU.
- Runner VM memory: 8192 MB.
- Runner VM disk: 128 GB.
- PAT warning threshold: 14 days. This logs a warning and emits the alert hook.
- PAT failure threshold: 7 days.
- PAT maximum remaining lifetime: 30 days.
- Public-repo guard timer: every 15 minutes.
- Docker and stale workspace cleanup cadence: weekly, run from the runner
  post-job hook when due.
- Disk health warning threshold: 80 percent used.
- Disk health failure threshold: 90 percent used.
- Guard soft-failure threshold: four consecutive soft failures over at least
  45 minutes.
- Alert hook: optional local command path. When set, PAT warnings, guard soft
  failure threshold, guard hard stop, disk warnings, and health-check failures
  invoke it with a small JSON payload.

Runner job activity is tracked with GitHub's self-hosted runner job hooks:

- `ACTIONS_RUNNER_HOOK_JOB_STARTED` writes an active-job marker under
  `/run/prox-github-runner/`.
- `ACTIONS_RUNNER_HOOK_JOB_COMPLETED` removes the marker after the job and runs
  cleanup only when the weekly cleanup interval has elapsed.
- The active-job marker includes a timestamp. A marker older than 12 hours is
  treated as stale; cleanup still skips, and the health check fails so the
  operator can inspect the runner.
- Cleanup uses `flock` on `/run/prox-github-runner/maintenance.lock` so only one
  cleanup path runs at a time.

The cleanup script:

- Runs from the post-job hook, after job steps finish.
- Runs Docker cleanup for images, containers, networks, and build cache older
  than seven days.
- Removes stale runner `_work` and `_temp` entries older than seven days.
- Logs actions to journald.

The public-repo guard timer does not run Docker or workspace cleanup. It only
checks whether the target repository has become visible without authentication;
if unauthenticated `GET /repos/{owner}/{repo}` returns a visible repository with
`private: false`, it stops the runner service and logs the reason. A private
repository normally returns not found to this unauthenticated check, so the guard
does not need the GitHub PAT on the runner VM.

The cleanup policy is conservative: cleanup runs between jobs through the
runner's own hook path and removes only stale data. It does not clean
language-specific caches under the runner user's home in the MVP; those remain
an operator or workflow responsibility.

## Runtime Guard Behavior

The public-repo guard runs every 15 minutes from the runner VM without using the
GitHub PAT. It logs every decision to journald with timestamp, URL, HTTP status,
GitHub request ID when present, and action taken.

Response handling:

- `200` with `private: false`: hard unsafe signal.
- `200` with `private: true`: hard unexpected signal, because an unauthenticated
  request should not see a private repository.
- `404`: expected private-repo result; reset the soft-failure counter and leave
  the runner service alone.
- `301`, `302`, non-JSON `200`, `401`, `403`, `429`, `5xx`, DNS failure, TLS
  failure, timeout, and network failure: soft failure. Increment the
  soft-failure counter and leave the runner service alone until the threshold is
  reached.

Stop behavior:

- If a hard signal arrives and no fresh active-job marker exists, stop the
  runner service immediately.
- If a hard signal arrives while a fresh active-job marker exists, write
  `/run/prox-github-runner/stop-after-job`; the completed-job hook stops the
  runner service before cleanup.
- If the soft-failure threshold is reached and no fresh active-job marker
  exists, stop the runner service and emit the alert hook.
- If the soft-failure threshold is reached during a job, write
  `/run/prox-github-runner/stop-after-job` and emit the alert hook.

The guard uses the runner VM's normal egress path. Operators should keep that
egress path stable and avoid sharing it with high-volume unauthenticated GitHub
API clients; otherwise rate limits can produce soft failures.

## Workflow Safety Audit

Preflight parses workflow YAML files under `.github/workflows/` and local
reusable workflows called with `uses: ./.github/workflows/...`. Dynamic
`runs-on` values that cannot be evaluated from static YAML fail closed unless
the workflow path is listed in an explicit inventory allowlist.

A workflow targets this runner when any job's `runs-on` can match the runner's
label set. Every such job must include the repository-specific label, such as
`paper-archives`; jobs using only broad labels such as `self-hosted`, `linux`,
or `x64` fail preflight.

Unsafe trigger patterns fail preflight if they can reach this runner:

- `pull_request_target`.
- `workflow_run`.
- `issue_comment`.
- A high-risk workflow that calls a local reusable workflow which targets this
  runner.
- A high-risk workflow that uses `actions/checkout` with a pull request head ref
  or another user-controlled ref before running commands or local actions on
  this runner.

Composite actions do not choose runners themselves, but they execute on the
caller runner. Preflight reports whether CODEOWNERS covers `.github/actions/**`
and any additional local composite-action paths configured in inventory.

CODEOWNERS is read from GitHub-supported locations in priority order:
`.github/CODEOWNERS`, `CODEOWNERS`, then `docs/CODEOWNERS`. If no CODEOWNERS
file exists, or if none of its rules cover workflow and local-action paths,
preflight warns and emits the alert hook, but it does not fail in
solo-developer mode.

## Failure Recovery

Preflight fails:
No Proxmox or runner changes are made. Reruns keep failing until the operator
fixes inventory, the target repo, or vault values.

Recurring safety check fails:
The runner service is stopped when idle or marked to stop after the current job,
and the failure is logged. Reruns of the health check keep failing until
repository privacy, branch protection, workflow trigger safety, or PAT access is
fixed.

Template creation fails:
A partial template VM may exist. The playbook attempts to purge it before
failing. If cleanup fails, the operator must inspect Proxmox.

VM clone succeeds, cloud-init fails:
The runner VM exists but is not ready. Reruns wait again and report cloud-init
failure. The cloud-init wait times out after 15 minutes and includes excerpts
from `/var/log/cloud-init.log` and `/var/log/cloud-init-output.log` when
available.

Host baseline fails:
The VM exists with partial package state. Reruns resume package, Docker, user,
hook, and guard setup after the operator fixes apt, network, or disk issues.

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

Inventory target repo changes:
Local runner state points at the previous repository. Reruns fail before
registration and tell the operator to run `playbooks/unregister-runner.yml`,
change inventory, and converge again.

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

- Authenticated repository privacy.
- Default-branch protection or active ruleset posture.
- Required pull request review count and CODEOWNER posture.
- Unsafe workflow trigger audit status.
- Broad `runs-on` audit status.
- Runner systemd service state.
- GitHub API runner status for `drc-dot-nz/paper-archives`.
- Docker daemon state.
- Runner user Docker access.
- Root filesystem and Docker storage usage.
- Last public-repo guard result.
- Last cleanup hook result.
- Runner connectivity check from the installed runner application.

Primary log locations:

- Runner service: `journalctl -u 'actions.runner.*'`.
- Runner diagnostics: the runner install directory's `_diag/` directory.
- Public-repo guard: `journalctl -u prox-github-runner-guard.service`.
- Cleanup hook: `journalctl -t prox-github-runner-cleanup`.
- Cloud-init: `/var/log/cloud-init.log` and `/var/log/cloud-init-output.log`.
- Docker daemon: `journalctl -u docker.service`.

The runbook documents a weekly manual review cadence for the health-check
playbook until external alerting exists. Alerting in the MVP is local and
fail-closed: unsafe repository state stops the runner service, writes a journald
entry, and makes the health-check playbook exit nonzero.

The health-check playbook distinguishes definitive unsafe results from transient
check failures. Definitive unsafe results use the same stop-after-current-job
behavior as the public-repo guard. Transient GitHub API errors fail the playbook
and emit the alert hook but do not stop an active runner by themselves.

## Error Handling And Safety

The MVP fails early before touching infrastructure when:

- Required config is missing.
- The target repo is missing or malformed.
- GitHub reports the target repo is not private.
- Workflow audit finds unsafe triggers or broad `runs-on` usage that can reach
  the runner.
- Runner labels do not include a repository-specific label.
- GitHub rejects the PAT.
- The PAT cannot access the target repo.
- The PAT cannot request runner registration and removal tokens.
- The PAT expiration date is invalid.
- The PAT is expired.
- The PAT has 7 days or fewer remaining.
- The PAT expiration date is more than 30 days in the future.
- Required vault variables are unavailable.
- Local runner state points to a different repository than inventory.

The MVP warns, without failing, when branch protection, active branch rulesets,
required reviews, or CODEOWNERS coverage are missing. These checks are not hard
gates in solo-developer mode.

GitHub API failures include the endpoint purpose, HTTP status, GitHub request
ID when present, and likely fix, but never echo tokens.

Long-running operations use explicit timeouts. SSH wait, cloud-init wait,
GitHub API requests, and runner package downloads all fail with operation
context instead of hanging indefinitely.

Proxmox template creation cleans up a partial template VM after failed creation.
Runner VM provisioning is non-destructive and must not delete, overwrite, or
rebuild the VM by default.

Runner registration is guarded by local runner state. Existing runner
configuration prevents duplicate registration. Cleanup is idempotent and treats
already-removed local or GitHub state as no-op. Intermediate states are covered
by the failure recovery section.

The MVP is explicitly private-repository-only. Public repository support is not
implemented.

## Testing And Verification

Local CI for this repository runs:

- YAML lint.
- Ansible lint.
- Inventory parsing.
- Ansible syntax checks.
- Preflight unit tests against a fixture-backed mock GitHub API.
- Shell lint for runner hook and guard scripts.

Real Proxmox, live GitHub, and workflow-dispatch integration checks are
operator-run commands because they require private infrastructure and secrets.

Sprint-level verification:

- Sprint 1: dependencies install, inventory parses, YAML lint passes, vault
  examples contain no real secrets, and preflight can fail safely before Proxmox
  changes for public repos, broad labels, unsafe workflow triggers, invalid
  PATs, and PATs outside the allowed lifetime window. CI verifies these with
  mock GitHub responses and workflow fixtures. CI also verifies warning output
  for missing branch protection, missing active rulesets, and missing CODEOWNERS
  coverage.
- Sprint 2: Ubuntu 24.04 template exists and reports as a template.
- Sprint 3: runner VM exists, is started, cloud-init completed, and SSH is
  reachable; SSH readiness and cloud-init completion are separate checks; denied
  network probes to Proxmox management and configured private CIDRs fail.
- Sprint 4: baseline tools exist, Docker Engine is running, cleanup scripts are
  installed, the runner user can run `docker ps`, and the public-repo guard can
  stop the runner service.
- Sprint 5: registration invokes preflight, job hooks mark active jobs, cleanup
  runs only from the post-job path, target-repo mismatch fails clearly, the
  pinned runner service is active, and GitHub lists the runner.
- Sprint 6: unregister cleanup can run twice, leaves the VM intact, removes or
  stops the local service, removes the GitHub runner entry when present, and
  health checks report service, GitHub, Docker, disk, guard, and cleanup status.
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
