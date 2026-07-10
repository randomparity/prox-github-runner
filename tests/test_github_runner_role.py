from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import threading
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


def build_runner_tarball(server_dir: Path) -> Path:
    src = server_dir / "src"
    src.mkdir()
    (src / "config.sh").write_text(CONFIG_SH)
    (src / "config.sh").chmod(0o755)
    (src / "svc.sh").write_text(SVC_SH)
    (src / "svc.sh").chmod(0o755)
    tarball = server_dir / "actions-runner.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(src / "config.sh", arcname="config.sh")
        tf.add(src / "svc.sh", arcname="svc.sh")
    return tarball


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
        return f"http://{host}:{port}/actions-runner.tar.gz"

    def __enter__(self) -> RunnerServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=5)


def runner_server_and_vars(tmp_path: Path) -> tuple[RunnerServer, dict[str, object]]:
    server_dir = tmp_path / "runner-server"
    server_dir.mkdir()
    tarball = build_runner_tarball(server_dir)
    checksum = hashlib.sha256(tarball.read_bytes()).hexdigest()
    server = RunnerServer(server_dir)
    return server, {
        "github_runner_download_url": server.url,
        "github_runner_sha256": checksum,
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


def test_target_repo_mismatch_fails(tmp_path: Path) -> None:
    root = tmp_path / "actions-runner" / "svc-1"
    root.mkdir(parents=True)
    (root / ".runner").write_text('{"gitHubUrl": "https://github.com/other/wrong-repo"}')
    proc = run_github_runner(tmp_path)
    assert proc.returncode != 0
    assert "unregister-runner.yml" in proc.stdout
    assert "different repository" in proc.stdout
