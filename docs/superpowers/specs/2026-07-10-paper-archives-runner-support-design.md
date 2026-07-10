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
  `rustup toolchain install` runs (up to `github_runner_count` (3–4) concurrent
  `nightly` installs when `fuzz-smoke` instances co-schedule) never race a
  shared cache or toolchain store;
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
  shared marker. A completing job removes only its own marker. Markers drive
  cleanup scheduling and health reporting — they are **not** the stop mechanism.
- The **stop primitive is `systemctl stop` on the runner service(s)**, not an
  advisory flag. On SIGTERM the Actions listener (1) stops dequeuing new jobs
  immediately — the atomic "accept no new work" guarantee — and (2) **cancels**
  any in-flight job, which reports result `Canceled` (its `if: always()` / post
  steps still run). `TimeoutStopSec` (default 15 minutes) bounds time-to-SIGKILL
  if cancellation stalls; it is **not** a drain-to-completion window — the
  runner does not wait for a running job to finish. An advisory `/run` flag
  cannot provide guarantee (1), because the listener never consults it; only the
  post-dispatch `JOB_COMPLETED` hook would, which is too late to prevent a fresh
  checkout.
- On a **hard** unsafe signal (confirmed public repo) the guard runs
  `systemctl stop` on **all N** services. Because stopping the listener is what
  prevents dequeue, there is no snapshot-then-stop race and no dependence on
  marker freshness — an orphaned marker cannot keep a service alive.
- On a **soft**-failure threshold (the MVP's four consecutive soft failures
  over ≥45 minutes) the guard likewise `systemctl stop`s all N services and
  emits the alert hook. This **cancels** any in-flight jobs (result `Canceled`)
  — an accepted fail-closed cost, since the threshold signals a sustained, not
  transient, problem.
- The "idle" reporting still uses "no **fresh** marker for any service"
  (markers older than the 12h staleness bound do not count), but this is
  **diagnostic only** — it no longer gates the stop.
- The MVP's *second* stop actor — the `check-runner-health` playbook, which on
  definitive unsafe repository state stopped the single runner — is likewise
  redefined to `systemctl stop` all N services (same cancel-in-flight
  semantics). It does **not** inherit the retired advisory flag. This preserves
  the MVP's three-layer enforcement (deploy preflight, health-check playbook,
  guard timer).
- Cleanup still takes `flock` on `maintenance.lock`, serializing cleanup passes
  against each other. Workspace cleanup must scan **every** per-service
  `_work`/`_temp` directory, not just one. The `flock` does not serialize a
  cleanup pass against a *running* peer job; the inherited Docker prune is safe
  here only because it is age-gated **and** no routed `paper-archives` job uses
  Docker (per Gap Analysis). If a Docker-using workflow is later routed to this
  pool, gate the prune on "no other service marker fresh".

Scaling `github_runner_count` **down** (e.g. 4 → 3) is an explicit converge
operation. The role reconciles the running set to exactly `github_runner_count`:
it stops, unregisters from GitHub, and removes the systemd unit of every
locally-discovered `actions.runner.*` service whose index exceeds the target,
before ensuring the desired N are present. Cleanup and health-check enumerate
**discovered** `actions.runner.*` units — not just the current inventory count —
so a scale-down orphan is surfaced and removed rather than left registered and
job-eligible.

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

**Enforcement reality.** The Proxmox firewall is L3/L4 and cannot allowlist by
hostname. The `proxmox_vm` role therefore enforces a **deny-specific-CIDRs /
default-allow** model: `policy_out: ACCEPT` with `OUT REJECT -dest <cidr>` rules
(emitted first, first-match-wins) for the deny-to-Proxmox-management,
deny-to-control-host, and deny-to-configured-private CIDRs. What is *enforced*
at this layer is those denies, not the positive host allowlist. An earlier
draft rendered an unscoped `OUT ACCEPT` per host, which matched all traffic and
silently shadowed both the denies and a `policy_out: DROP` — that is removed.

The positive host list below is retained as **documentation only** (rendered as
`#` comments in the generated `.fw` file): it records the destinations the
runner needs for CI so an operator who fronts the VM with an upstream egress
proxy can allowlist by hostname *there*. Hostname allowlisting is not enforced
at the Proxmox layer. The documented host set (all documentation, not enforced)
is:

- `api.github.com`, `github.com`, `codeload.github.com` — API, git, and archive
  downloads for checkout and the runner tarball.
- `*.actions.githubusercontent.com` and `*.blob.core.windows.net` — the GitHub
  Actions cache service and its blob-storage backend, used by
  `Swatinem/rust-cache` on every Rust job.
- An Ubuntu mirror host (`archive.ubuntu.com`, `security.ubuntu.com`) — apt.
- The Docker registry host (`registry-1.docker.io`, `auth.docker.io`).
- DNS (`:53`) and NTP (`:123`) — name resolution and time sync (UDP/TCP by
  port, not a hostname).

plus the Rust/crates/PyPI/objects entries below:

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
- The GitHub Actions cache service and its blob-storage backend
  (`*.actions.githubusercontent.com`, `*.blob.core.windows.net`), used by
  `Swatinem/rust-cache` on every Rust job. Resolve concrete ranges from GitHub's
  published `meta` / `actions` endpoint list at implementation. If omitted,
  rust-cache fails soft to cold rebuilds (slower CI) rather than erroring — but
  the concurrency design exists for wall-clock, so allow it.

Deny-to-Proxmox-management, deny-to-control-host, and deny-to-configured-private
CIDRs remain unchanged.

### 5. Operational defaults (concurrency additions)

Concrete defaults introduced by this amendment; all MVP Operational Defaults
(PAT windows, guard cadence, disk 80/90% thresholds, 12h marker staleness) are
unchanged.

- `github_runner_count`: default `3`, supported range 3–4.
- Runner service `TimeoutStopSec`: 15 minutes — bounds time-to-SIGKILL if a
  cancelled job stalls when the guard stops a service (not a drain window).
- Scheduling-latency warning: a job queued longer than **10 minutes while zero
  services are idle**. Below that, queueing is expected for a 3–4 runner pool
  and is not a warning.
- Optional per-service resource limits: `MemoryMax` / `CPUWeight` on each runner
  systemd unit, off by default. The 32 GB / N budget (~8 GB per concurrent job)
  is generous, but without limits a runaway `cargo build` can OOM-kill a peer
  job. Expose these as operator tuning knobs so a heavy build fails only its own
  job.

## Companion Changes In `paper-archives`

A separate branch/PR in the `paper-archives` repository re-labels its
workflows. This is what satisfies the runner preflight's repo-specific-label
audit; without it, preflight fails closed.

- **Matrix jobs (`clippy`, `test`):** convert `matrix.os` to `matrix.include`
  with a per-entry `runs-on`. The Linux arm becomes
  `runs-on: [self-hosted, linux, x64, paper-archives]`; `macos-latest` and
  `windows-latest` arms are unchanged. Each `include` entry must carry a
  **literal** `runs-on` label array (not an unresolvable expression) so the
  runner's own preflight workflow-safety audit — which fails closed on
  `runs-on` values it cannot evaluate from static YAML — can confirm the
  `paper-archives` label is present.
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
- **Sprint 6 — cleanup/health:** unregister and health-check playbooks
  enumerate **discovered** `actions.runner.*` units (surfacing scale-down
  orphans, Amendment 1); on a confirmed unsafe signal both the public-repo guard
  and the health-check playbook stop all services via `systemctl stop` (SIGTERM
  cancels the in-flight job; `TimeoutStopSec` bounds SIGKILL); the health check
  also reports per-service status plus scheduling latency (a job queued >10
  minutes while no service is idle, or any service offline, is a warning).
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
- **Sprint 6:** cleanup runs twice, removes/stops all discovered services,
  leaves the VM intact, and removes the GitHub runner entries; decreasing
  `github_runner_count` (4 → 3) unregisters and removes the surplus service; a
  hard unsafe signal makes every service stop accepting new jobs immediately and
  terminates any in-flight job as `Canceled` within `TimeoutStopSec`; health
  check reports per-service status and scheduling latency.
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
