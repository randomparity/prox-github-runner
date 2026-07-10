from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

CONFIG_SH = r"""#!/usr/bin/env bash
# Fake runner config.sh baked into the served tarball; logs its args by path.
set -euo pipefail
printf 'config %s\n' "$*" >>"${FAKE_RUNNER_LOG:?}"
if [[ "${1:-}" == "remove" ]]; then
  rm -f .runner
  exit 0
fi
url=""
name=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) url="$2"; shift 2 ;;
    --name) name="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf '{"gitHubUrl": "%s", "agentName": "%s"}\n' "$url" "$name" >.runner
"""

SVC_SH = r"""#!/usr/bin/env bash
# Fake runner svc.sh baked into the served tarball; logs its args by path.
set -euo pipefail
printf 'svc %s\n' "$*" >>"${FAKE_RUNNER_LOG:?}"
"""


def write_inventory(path: Path) -> None:
    path.write_text(
        f"""---
all:
  children:
    runner:
      hosts:
        runner-test:
          ansible_connection: local
          ansible_python_interpreter: "{sys.executable}"
"""
    )


def base_extra_vars(tmp_path: Path) -> dict[str, object]:
    return {
        "runner_vm_name": "paper-archives-runner",
        "runner_bootstrap_user": "runner",
        "github_runner_target_repo": "drc-dot-nz/paper-archives",
        "github_runner_labels": ["self-hosted", "linux", "x64", "paper-archives"],
        "github_runner_count": 3,
        "github_runner_version": "2.335.1",
        "github_runner_sha256": "a" * 64,
        "github_runner_install_root": str(tmp_path / "actions-runner"),
        "github_runner_bin_dir": str(tmp_path / "bin"),
        "github_runner_apply_system": False,
        "github_runner_become": False,
    }


def write_fake_gh(tmp_path: Path) -> None:
    gh = tmp_path / "gh"
    gh.write_text(
        r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${FAKE_GH_LOG:?}"
for arg in "$@"; do
  case "$arg" in
    */registration-token)
      printf '{"token": "REG-TOKEN-123", "expires_at": "2099-01-01T00:00:00Z"}'
      exit 0
      ;;
    */remove-token)
      printf '{"token": "REMOVE-TOKEN-123", "expires_at": "2099-01-01T00:00:00Z"}'
      exit 0
      ;;
  esac
done
echo "unexpected gh $*" >&2
exit 1
"""
    )
    gh.chmod(0o755)


def _add_executable(zf: zipfile.ZipFile, name: str, body: str) -> None:
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFREG | 0o755) << 16
    zf.writestr(info, body)


def build_runner_archive(server_dir: Path) -> Path:
    # The real runner package is a .tar.gz, but ansible's unarchive needs GNU
    # tar to extract it and the dev host (macOS) ships only bsdtar. The role's
    # get_url + unarchive path is archive-format agnostic, so the fixture serves
    # a .zip (handled by unzip everywhere); production keeps the .tar.gz default.
    archive = server_dir / "actions-runner.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        _add_executable(zf, "config.sh", CONFIG_SH)
        _add_executable(zf, "svc.sh", SVC_SH)
    return archive


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


class RunnerServer:
    def __init__(self, directory: Path) -> None:
        handler = partial(QuietHandler, directory=str(directory))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = cast(tuple[str, int], self.httpd.server_address)
        return f"http://{host}:{port}/actions-runner.zip"

    def __enter__(self) -> RunnerServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=5)


def runner_server_and_vars(tmp_path: Path) -> tuple[RunnerServer, dict[str, object]]:
    server_dir = tmp_path / "runner-server"
    server_dir.mkdir()
    archive = build_runner_archive(server_dir)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    server = RunnerServer(server_dir)
    return server, {
        "github_runner_download_url": server.url,
        "github_runner_sha256": checksum,
        "github_runner_tarball": "actions-runner.zip",
    }


def run_github_runner(
    tmp_path: Path,
    overrides: dict | None = None,
    env_extra: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    inv = tmp_path / "inv.yml"
    write_inventory(inv)
    write_fake_gh(tmp_path)
    play = tmp_path / "play.yml"
    play.write_text(
        """---
- hosts: runner
  gather_facts: false
  roles:
    - github_runner
"""
    )
    extra = base_extra_vars(tmp_path)
    extra.update(overrides or {})
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
        "FAKE_RUNNER_LOG": str(tmp_path / "runner.log"),
    }
    env.update(env_extra or {})
    cmd = ["ansible-playbook", "-i", str(inv), str(play), "-e", json.dumps(extra)]
    return subprocess.run(cmd, text=True, capture_output=True, cwd=Path.cwd(), env=env)


def token_absent_from_tree(root: Path, token: str) -> bool:
    for path in root.rglob("*"):
        if path.is_file() and token in path.read_text(errors="ignore"):
            return False
    return True


def test_target_repo_mismatch_fails(tmp_path: Path) -> None:
    root = tmp_path / "actions-runner" / "svc-1"
    root.mkdir(parents=True)
    (root / ".runner").write_text('{"gitHubUrl": "https://github.com/other/wrong-repo"}')
    proc = run_github_runner(tmp_path)
    assert proc.returncode != 0
    assert "unregister-runner.yml" in proc.stdout
    assert "different repository" in proc.stdout


def test_registration_token_requested_and_not_persisted(tmp_path: Path) -> None:
    server, server_vars = runner_server_and_vars(tmp_path)
    server_vars["vault_github_pat"] = "PAT-SENTINEL-XYZ"
    with server:
        proc = run_github_runner(tmp_path, overrides=server_vars)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    gh_log = (tmp_path / "gh.log").read_text()
    assert "registration-token" in gh_log
    assert "/repos/drc-dot-nz/paper-archives/actions/runners/registration-token" in gh_log
    install_root = tmp_path / "actions-runner"
    # Only the short-lived registration token reaches the VM (via config.sh);
    # neither it nor the repo-admin PAT is ever written under the install root.
    assert token_absent_from_tree(install_root, "REG-TOKEN-123")
    assert token_absent_from_tree(install_root, "PAT-SENTINEL-XYZ")


def test_runner_package_unpacked_into_each_service(tmp_path: Path) -> None:
    server, server_vars = runner_server_and_vars(tmp_path)
    with server:
        proc = run_github_runner(tmp_path, overrides=server_vars)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    install_root = tmp_path / "actions-runner"
    for idx in (1, 2, 3):
        assert (install_root / f"svc-{idx}" / "config.sh").exists()
        assert (install_root / f"svc-{idx}" / "svc.sh").exists()


def test_checksum_mismatch_fails_download(tmp_path: Path) -> None:
    server, server_vars = runner_server_and_vars(tmp_path)
    server_vars["github_runner_sha256"] = "b" * 64
    with server:
        proc = run_github_runner(tmp_path, overrides=server_vars)
    assert proc.returncode != 0
    assert "checksum" in proc.stdout.lower()
    install_root = tmp_path / "actions-runner"
    assert not (install_root / "svc-1" / "config.sh").exists()


def test_registers_three_unique_labeled_services_with_hooks(tmp_path: Path) -> None:
    server, server_vars = runner_server_and_vars(tmp_path)
    with server:
        proc = run_github_runner(tmp_path, overrides=server_vars)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    runner_log = (tmp_path / "runner.log").read_text()
    for idx in (1, 2, 3):
        assert f"--name paper-archives-runner-{idx}" in runner_log
    assert runner_log.count("--disableupdate") == 3
    assert runner_log.count("--unattended") == 3
    assert runner_log.count("self-hosted,linux,x64,paper-archives") == 3
    assert runner_log.count("svc install") == 3
    assert "svc start" in runner_log
    env_body = (tmp_path / "actions-runner" / "svc-1" / ".env").read_text()
    assert "ACTIONS_RUNNER_HOOK_JOB_STARTED=" in env_body
    assert "ACTIONS_RUNNER_HOOK_JOB_COMPLETED=" in env_body
    assert "/run/prox-github-runner/jobs/paper-archives-runner-1" in env_body


def test_registration_skipped_when_already_registered(tmp_path: Path) -> None:
    install_root = tmp_path / "actions-runner"
    for idx in (1, 2, 3):
        svc = install_root / f"svc-{idx}"
        svc.mkdir(parents=True)
        (svc / ".runner").write_text(
            '{"gitHubUrl": "https://github.com/drc-dot-nz/paper-archives"}'
        )
    server, server_vars = runner_server_and_vars(tmp_path)
    with server:
        proc = run_github_runner(tmp_path, overrides=server_vars)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    runner_log = tmp_path / "runner.log"
    body = runner_log.read_text() if runner_log.exists() else ""
    assert "config " not in body  # config.sh never re-invoked
