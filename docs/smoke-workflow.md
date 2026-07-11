# Runner smoke workflow

`templates/paper-archives-smoke.yml` is a copyable GitHub Actions workflow that
verifies a freshly provisioned self-hosted runner can do the four things every
real CI job depends on:

1. **Checkout** the repository (`actions/checkout`).
2. **Run a shell step** and read/write `$GITHUB_WORKSPACE`.
3. **Run a Docker container** (`docker run hello-world`), proving the runner
   user has working Docker access.
4. **Clean its workspace** in an `always()` step, matching the post-job cleanup
   the runner performs between jobs.

It is triggered by `workflow_dispatch` only, so it never competes with real CI
for the runner pool and only runs when an operator asks for it.

## Private-repo-only boundary

Only run this workflow while `drc-dot-nz/paper-archives` is **private**.

The runner is designed to serve a private repository. Its public-repo guard and
the `check-runner-health` playbook both stop every runner service the moment the
target repository is confirmed **public** (a fail-closed safety layer that keeps
an untrusted fork's workflow from executing on the host). Dispatching this smoke
workflow against a public repository therefore cannot produce a green run — the
services will have been stopped. Keep the repository private for the smoke test,
and treat a public repository as an operational incident rather than a smoke
failure.

## Usage

1. Copy `templates/paper-archives-smoke.yml` into the repository as
   `.github/workflows/smoke.yml` and push it.
2. Dispatch it from the Actions tab ("Self-hosted runner smoke" -> Run
   workflow) or with `gh workflow run smoke.yml`.
3. Alternatively, run `playbooks/run-smoke-workflow.yml` from the control host
   to dispatch and poll the run to completion in one step.
