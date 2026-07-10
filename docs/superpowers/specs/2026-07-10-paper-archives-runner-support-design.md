# Paper-Archives Runner Support Design

Date: 2026-07-10

## Context

This document amends the [Proxmox GitHub Runner MVP
design](2026-05-20-proxmox-github-runner-mvp-design.md) with the concrete
requirements needed to offload the Linux x86-64 CI of
`drc-dot-nz/paper-archives` to a local self-hosted runner.

The MVP design already names `paper-archives` as its target and builds the
security model (private-repo gate, repo-specific label audit, network
isolation). Sprints 1–2 are implemented (`preflight` role, Proxmox Ubuntu
24.04 template). This amendment records the deltas required before Sprints 3–7
are implemented, plus the companion workflow changes needed in the
`paper-archives` repository itself.

`paper-archives` is a Rust workspace (edition 2021, pinned Rust `1.92.0`) with
a Tauri desktop app and Python tooling. Its `ci.yml` runs a three-OS matrix
plus a fan-out of Linux jobs. Only the Linux x86-64 work is offloaded;
macOS and Windows matrix arms remain GitHub-hosted because they cannot run on
this runner.

## Decisions

- **Offload scope:** all `ubuntu-latest` jobs in `.github/workflows/ci.yml`.
- **Concurrency model:** multiple runner services on a single VM.
- **Parallelism:** 3–4 concurrent jobs (default 3 services, range 3–4).
- **Out of scope for this offload:** `release.yml` (tag-triggered) and
  `mutants.yml` (manual dispatch) stay on GitHub-hosted runners, as do all
  macOS/Windows matrix arms.
- **Rust toolchain acquisition:** the runner keeps using `paper-archives`'
  existing `dtolnay/rust-toolchain` steps to install `1.92.0` and `nightly`
  per job. The baseline provides `rustup` and egress to `static.rust-lang.org`;
  it does not pre-bake or pin the toolchain version, so the pinned version stays
  owned by the workflow.

## Gap Analysis

Each `ubuntu-latest` job in `ci.yml` maps to a runner requirement:

| Requirement | Driven by | Status vs. MVP design |
|---|---|---|
| Rust toolchain via rustup (`1.92.0` **and nightly**) | all Rust jobs; `fuzz-smoke` needs nightly | Implied (dtolnay action installs it); needs egress to `static.rust-lang.org` |
| `build-essential` **+ `clang`** | native crates; `cargo-fuzz`/libFuzzer | Gap — baseline lists `build-essential`, not `clang` |
| **Python 3.12 + pip/venv** | `python-lint`, `pre-commit-checks`, `vector-immutability`, `demo.sh` | Gap — Sprint 4 baseline omits Python |
| Tauri libs: `libwebkit2gtk-4.1-dev`, `libxdo-dev`, `libssl-dev`, `libayatana-appindicator3-dev`, `librsvg2-dev` | `clippy`, `test`, `demo` | Pre-install in baseline; companion PR drops the inline `sudo apt-get` step (concurrent jobs collide on the dpkg lock) |
| Docker Engine + runner user in `docker` group | container-based actions / general | Present (Sprint 4) |
| `jq`, `git`, `curl` | checkout, version scripts | `git`/`curl` present; `jq` cheap to add |
| Repo-specific label `paper-archives` on every routed job | preflight label audit (fails closed otherwise) | Present in design; requires `paper-archives` workflow edits |

Notes confirmed by inspecting `paper-archives`:

- `EmbarkStudios/cargo-deny-action@v2` downloads a static `cargo-deny` binary;
  it does not require a local Docker image.
- `pre-commit-checks` self-provisions its hook environments (shellcheck-py,
  detect-secrets, ruff) via `pre-commit`; the runner needs only Python + git +
  network, not those tools pre-installed.
- `demo.sh` uses system `python3` and `cargo build`; it does not use Docker.

## Amendments To The MVP Design

### 1. Multi-runner concurrency (scope change)

The MVP listed "multiple runner services on one VM" and "multiple runner VMs"
as out of scope. This amendment moves **multiple runner services on one VM**
into scope. The `github_runner` role (Sprint 5) installs `github_runner_count`
services (default `3`, supported range 3–4) on the single runner VM. Each
service:

- is its own `actions.runner.*` systemd unit with a unique runner name
  `<vm-hostname>-<index>` (1..N), its own `_work` directory, its own
  `RUNNER_TOOL_CACHE` (the per-runner `_work/_tool` default), and its own
  `RUSTUP_HOME`, so concurrent `actions/setup-python` extractions and
  `rustup toolchain install` runs (up to five `fuzz-smoke` jobs installing
  `nightly` at once) never race a shared cache or toolchain store;
- shares `~/.cargo/registry` (cargo's own file locks make concurrent reads
  safe), the pre-installed Tauri libs, and the single Docker daemon. The
  registry is shared for cache reuse; the *toolchain* store (`RUSTUP_HOME`) is
  not, because `rustup` install/update writes are less tolerant of concurrent
  mutation than cargo's locked registry reads;
- registers with labels `self-hosted,linux,x64,paper-archives`.

The `<vm-hostname>-<index>` naming is required, not cosmetic: GitHub runner
names must be unique per repository, and registering a second runner under an
existing name replaces the first, silently leaving fewer than N live runners.

**Concurrency-safe job tracking and guard (re-specifies MVP single-job
machinery).** The MVP's active-job marker and public-repo-guard stop path
(MVP "Operational Defaults" / "Runtime Guard Behavior") assume exactly one job
at a time. Under N concurrent services that machinery is redefined:

- The `JOB_STARTED`/`JOB_COMPLETED` hooks write a **per-service** marker keyed
  by runner name under `/run/prox-github-runner/jobs/<runner-name>`, not one
  shared marker. A completing job removes only its own marker.
- Markers carry a timestamp. A marker older than the MVP staleness threshold
  (12h) is treated as **orphaned** — it does not count toward the idle test, and
  its service is stopped immediately rather than drained.
- The guard's "idle" test becomes "no **fresh** marker exists for **any**
  service" (orphaned markers do not count), preserving the MVP's `fresh`-marker
  semantics.
- On a hard or soft stop signal the guard **first freezes all N services** by
  writing a `stop-after-job` flag for every service, so none can accept a new
  job during teardown; only then does it evaluate markers. This closes the
  snapshot-then-stop race where an idle service picks up a queued job between
  the guard's check and `systemctl stop`.
- Services with no fresh marker are then stopped immediately; services with a
  fresh marker drain (the completed-job hook honors the flag and stops the
  service after the current job).
- Each `stop-after-job` flag has a hard timeout (the same 12h bound). If the
  completed-job hook has not fired by then, the service is force-stopped, so a
  job that dies without `JOB_COMPLETED` cannot keep serving a now-public repo
  indefinitely.
- Cleanup still takes `flock` on `maintenance.lock`, serializing across
  services.

Multiple runner VMs and ephemeral/JIT runners remain out of scope.

### 2. Toolchain baseline additions (Sprint 4 `runner_host`)

Add to the host baseline:

- `clang` (for `cargo-fuzz`/libFuzzer and native crate builds).
- `python3.12`, `python3.12-venv`, `python3-pip` (system fallback; Ubuntu 24.04
  ships Python 3.12 as default).
- Pre-install the five Tauri `-dev` libraries. **This does not make the
  workflows' inline `sudo apt-get` step a safe no-op** — `apt-get update` and
  `apt-get install` still take exclusive dpkg/apt locks, so two concurrent
  Tauri-dep jobs (`clippy` + `test`) would collide and error. Pre-installing
  instead lets the companion PR drop the inline apt step entirely (see
  Companion Changes); the libs are already present, so the step is unnecessary.
- Passwordless sudo for the runner user. **Security note:** this is consistent
  with the already-accepted Docker-group root-equivalence in the MVP threat
  model — a compromised workflow already has root-equivalent control of the VM.
- Ensure each service's `RUNNER_TOOL_CACHE` (the per-runner `_work/_tool`
  default from Amendment 1) is writable so `actions/setup-python` provisions
  its isolated Python 3.12 without racing sibling jobs. Ubuntu 24.04's system
  Python 3.12 does not satisfy `setup-python` (which manages its own tool-cache
  copy); the system packages are only the `demo.sh` `python3` fallback.
- Optional speed-up: pre-install a pinned `cargo-fuzz` so `fuzz-smoke` jobs skip
  `cargo install` on every run.

### 3. VM sizing for 3–4 concurrent jobs

Raise the runner VM defaults (all overridable in inventory):

| Setting | MVP default (1 runner) | Amended default (3–4 concurrent) |
|---|---|---|
| vCPU | 4 | 16 |
| RAM | 8192 MB | 32768 MB |
| Disk | 128 GB | 256 GB |

Concurrent Rust builds, Docker layers, and per-service `_work` directories are
the disk driver.

### 4. Egress allowlist (Sprint 3 network isolation)

Extend the MVP allowed-egress list (GitHub, Ubuntu mirrors, Docker registry,
DNS, NTP) with:

- `static.rust-lang.org` — rustup toolchains, including nightly.
- `index.crates.io` and `static.crates.io` — cargo registry index and crates.
- `objects.githubusercontent.com` — release-asset downloads for
  `actions/setup-python`, `cargo-deny`, `cargo-fuzz`, and the runner tarball.
- `pypi.org` and `files.pythonhosted.org` — pip index and wheel CDN. Required
  by `python-lint` (`pip install ruff`) and `pre-commit-checks` (`pip install
  pre-commit` plus each hook's PyPI-built environment). Omitting this
  contradicts the Gap Analysis note that pre-commit self-provisions from the
  network; with deny-by-default egress, `pip install` would otherwise have no
  route and both jobs fail closed.

Deny-to-Proxmox-management, deny-to-control-host, and deny-to-configured-private
CIDRs remain unchanged.

## Companion Changes In `paper-archives`

A separate branch/PR in the `paper-archives` repository re-labels its
workflows. This is what satisfies the runner preflight's repo-specific-label
audit; without it, preflight fails closed.

- **Matrix jobs (`clippy`, `test`):** convert `matrix.os` to `matrix.include`
  with a per-entry `runs-on`. The Linux arm becomes
  `runs-on: [self-hosted, linux, x64, paper-archives]`; `macos-latest` and
  `windows-latest` arms are unchanged.
- **Single-OS ubuntu jobs** (`fmt`, `deny`, `python-lint`, `pre-commit-checks`,
  `docs-check`, `spec-sync`, `vector-immutability`, `integration`,
  `fuzz-smoke`, `demo`): change `runs-on: ubuntu-latest` to
  `runs-on: [self-hosted, linux, x64, paper-archives]`.
- **Left on GitHub-hosted:** `release.yml`, `mutants.yml`, and all
  macOS/Windows matrix arms.
- **Remove the inline `sudo apt-get update && sudo apt-get install` Tauri
  steps** from `clippy`, `test`, and `demo`. They only ever ran on the Linux
  arm — now the self-hosted runner, where the libs are pre-installed — and
  leaving them causes dpkg/apt-lock failures when concurrent jobs run them at
  once. (If one must be retained, guard it with `-o DPkg::Lock::Timeout=600`
  and drop the `apt-get update`.)
- **Add a `concurrency:` group** to the workflow (e.g. `group:
  ci-${{ github.ref }}`, `cancel-in-progress: true`). Its real benefit is
  superseding **rapid re-pushes to the same ref** (repeated pushes to one PR
  branch) — the actual pool-starvation case for a solo developer, since each
  event enqueues 16 Linux job instances against 3–4 runners. Push-to-`main` and
  PR runs carry different refs (`refs/heads/main` vs `refs/pull/N/merge`) and so
  remain intentionally separate groups.

## Work Breakdown

Implement in the existing MVP sprint order, with the deltas above folded in.

- **Sprint 3 — `proxmox_vm` role:** clone the template, apply the amended
  sizing, attach VLAN/bridge and Proxmox firewall rules with the expanded
  egress allowlist, start the VM, wait for SSH, then wait for cloud-init.
- **Sprint 4 — `runner_host` role:** OS packages, runner user, Docker, plus the
  toolchain deltas (`clang`, Python 3.12, Tauri libs, passwordless sudo,
  writable tool cache, optional pinned `cargo-fuzz`).
- **Sprint 5 — `github_runner` role:** preflight, registration-token retrieval,
  install of `github_runner_count` uniquely-named (`<vm-hostname>-<index>`)
  labeled services, self-updates disabled, and per-service job hooks that write
  the per-service active-job markers (Amendment 1).
- **Sprint 6 — cleanup/health:** unregister and health-check playbooks extended
  to enumerate all N services; the public-repo guard and stop path implement
  the concurrency-safe "idle = no marker for any service" and
  "stop-all-with-drain" behavior from Amendment 1; the health check reports
  per-service status plus scheduling latency (a job queued beyond a threshold,
  or any service offline, is a warning).
- **Sprint 7 — smoke test + paper-archives PR:** the re-label PR in
  `paper-archives`, then drive one real CI run to green.

## Verification

- **Sprint 3:** VM exists, started, SSH reachable, cloud-init completed; probes
  to Proxmox management SSH/API and denied CIDRs fail; `static.rust-lang.org`
  and `index.crates.io` are reachable from the runner; and `pip install` of a
  trivial package from `pypi.org` succeeds.
- **Sprint 4:** `python3.12`, `clang`, and Docker are present; the runner user
  can run `docker ps` and passwordless `sudo`; `actions/setup-python` provisions
  Python 3.12 cleanly into the tool cache.
- **Sprint 5:** preflight runs before registration; GitHub lists N runners for
  `drc-dot-nz/paper-archives` with **N distinct names** (`<vm-hostname>-<index>`),
  each carrying the `paper-archives` label and each simultaneously Online and
  Idle — not merely "a runner with the label exists".
- **Sprint 6:** cleanup runs twice, removes/stops all N services, leaves the VM
  intact, and removes the GitHub runner entries; a hard unsafe signal stops all
  services while draining any in-flight job; health check reports per-service
  status and scheduling latency.
- **Overall success — runner correctness, tracked separately from repo CI
  health:**
  - every former `ubuntu-latest` job is assigned to a self-hosted runner
    carrying the `paper-archives` label (inspect the run's per-job runner
    assignments); macOS/Windows arms still run GitHub-hosted;
  - toolchain and dependency resolution succeed on the runner (Rust stable and
    nightly, Python 3.12, Tauri libs, Docker);
  - **concurrency is observed** — at least one moment in the run where multiple
    services are simultaneously Busy (from per-job start/finish timestamps or a
    `check-runner-health` snapshot), proving parallel rather than serial
    execution;
  - repo-CI outcome ("all jobs green") is tracked **separately**, because
    `fuzz-smoke` pins `nightly` and can fail for repo or toolchain reasons
    unrelated to the runner; a red nightly job does not by itself mean the
    runner is misprovisioned.

## Alternatives Considered

### Concurrency model: multiple services on one VM (chosen) vs. multiple VMs

**Decision:** run `github_runner_count` runner services on a single VM.

**Rejected — multiple runner VMs (one service each):** stronger job-to-job
isolation and a cleaner per-job "dedicated VM" story, but rejected because it
(a) multiplies RAM/disk overhead by N, (b) forces N VM converges and N
firewall/egress attachments per run, (c) loses the shared `~/.cargo/registry`
so every VM re-downloads crates, and (d) still does not isolate jobs from the
persistent-runner residual-state risks the MVP threat model already accepts.
The one benefit — stronger isolation — is not load-bearing for a trusted
private-repo runner whose threat model already grants a compromised workflow
root-equivalent control of its VM (Docker socket, passwordless sudo). Shared
toolchain, shared cache, and one VM to converge win for this use case.

**Accepted new risk from concurrency.** Unlike the MVP's sequential model, N
jobs now run at the same time on one VM with shared passwordless sudo, one
Docker daemon, shared `~/.cargo/registry`, and shared `/tmp`. A buggy or
compromised job can therefore fail or corrupt a **peer running concurrently**,
not merely leave residue for a later job. This blast radius is accepted for a
trusted private-repo runner; jobs needing hard isolation from peers are out of
scope and belong to the future multi-VM / ephemeral design.

**Rejected — single runner, serial jobs:** simplest, but the 16 Linux job
instances in `paper-archives` `ci.yml` would queue behind one another, making
CI wall-clock materially worse than GitHub-hosted. The operator explicitly
wants concurrency.

### Rust toolchain acquisition

Recorded under Decisions above: per-job `dtolnay/rust-toolchain` install was
chosen over baking pinned toolchains into the baseline, so the pinned version
stays owned by the `paper-archives` workflow and the runner repo does not have
to track it in lockstep.

## Out Of Scope

- Offloading `release.yml` or `mutants.yml`.
- macOS/Windows jobs (cannot run on this Linux runner).
- Multiple runner VMs, ephemeral/JIT runners, organization-level runners.
- Hard isolation between concurrently-running jobs (see the accepted-risk note
  in Alternatives Considered).
- Repository-specific build caches beyond the shared `~/.cargo/registry` and
  the GitHub Actions remote cache used by `Swatinem/rust-cache`.
