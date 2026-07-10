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
| Tauri libs: `libwebkit2gtk-4.1-dev`, `libxdo-dev`, `libssl-dev`, `libayatana-appindicator3-dev`, `librsvg2-dev` | `clippy`, `test`, `demo` | Installed by workflow via `sudo apt-get`; requires passwordless sudo |
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

- is its own `actions.runner.*` systemd unit with its own `_work` directory;
- shares `~/.cargo/registry`, the pre-installed Tauri libs, and the single
  Docker daemon;
- registers with labels `self-hosted,linux,x64,paper-archives`.

Multiple runner VMs and ephemeral/JIT runners remain out of scope.

### 2. Toolchain baseline additions (Sprint 4 `runner_host`)

Add to the host baseline:

- `clang` (for `cargo-fuzz`/libFuzzer and native crate builds).
- `python3.12`, `python3.12-venv`, `python3-pip` (system fallback; Ubuntu 24.04
  ships Python 3.12 as default).
- Pre-install the five Tauri `-dev` libraries so the workflows' inline
  `sudo apt-get install` step becomes a fast no-op.
- Passwordless sudo for the runner user. **Security note:** this is consistent
  with the already-accepted Docker-group root-equivalence in the MVP threat
  model — a compromised workflow already has root-equivalent control of the VM.
- Ensure the runner tool cache (`RUNNER_TOOL_CACHE`) is writable so
  `actions/setup-python` can provision its isolated Python.
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
- The inline `sudo apt-get install` Tauri steps stay (idempotent; fast once the
  libs are pre-installed on the runner).

## Work Breakdown

Implement in the existing MVP sprint order, with the deltas above folded in.

- **Sprint 3 — `proxmox_vm` role:** clone the template, apply the amended
  sizing, attach VLAN/bridge and Proxmox firewall rules with the expanded
  egress allowlist, start the VM, wait for SSH, then wait for cloud-init.
- **Sprint 4 — `runner_host` role:** OS packages, runner user, Docker, plus the
  toolchain deltas (`clang`, Python 3.12, Tauri libs, passwordless sudo,
  writable tool cache, optional pinned `cargo-fuzz`).
- **Sprint 5 — `github_runner` role:** preflight, registration-token retrieval,
  install of `github_runner_count` labeled services, self-updates disabled.
- **Sprint 6 — cleanup/health:** unregister and health-check playbooks extended
  to enumerate all N services.
- **Sprint 7 — smoke test + paper-archives PR:** the re-label PR in
  `paper-archives`, then drive one real CI run to green.

## Verification

- **Sprint 3:** VM exists, started, SSH reachable, cloud-init completed; probes
  to Proxmox management SSH/API and denied CIDRs fail; `static.rust-lang.org`
  and `index.crates.io` are reachable from the runner.
- **Sprint 4:** `python3.12`, `clang`, and Docker are present; the runner user
  can run `docker ps` and passwordless `sudo`; `actions/setup-python` provisions
  Python 3.12 cleanly into the tool cache.
- **Sprint 5:** preflight runs before registration; GitHub lists N runners for
  `drc-dot-nz/paper-archives`, each carrying the `paper-archives` label; the N
  services are active.
- **Sprint 6:** cleanup runs twice, removes/stops all N services, leaves the VM
  intact, and removes the GitHub runner entries; health check reports per-service
  status.
- **Overall success:** a PR in `paper-archives` shows every former
  `ubuntu-latest` job executing on the self-hosted runner (3–4 concurrently),
  macOS/Windows arms still GitHub-hosted, and CI green.

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

**Rejected — single runner, serial jobs:** simplest, but the ~13 parallel
Linux jobs in `paper-archives` `ci.yml` would queue behind one another, making
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
- Repository-specific build caches beyond the shared `~/.cargo/registry` and
  the GitHub Actions remote cache used by `Swatinem/rust-cache`.
