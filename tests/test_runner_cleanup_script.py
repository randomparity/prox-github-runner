from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

CLEANUP = Path("roles/runner_host/files/prox-github-runner-cleanup.sh").resolve()


def write_fake(tmp_path: Path, name: str, body: str) -> None:
    fake = tmp_path / name
    fake.write_text(body)
    fake.chmod(0o755)


def make_aged_dir(parent: Path, name: str, age_days: float) -> Path:
    target = parent / name
    target.mkdir(parents=True)
    when = time.time() - age_days * 86400
    os.utime(target, (when, when))
    return target


def run_cleanup(
    tmp_path: Path, install_root: Path
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    write_fake(
        tmp_path,
        "flock",
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >>"${FAKE_FLOCK_LOG:?}"\nexit 0\n',
    )
    write_fake(
        tmp_path,
        "docker",
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >>"${FAKE_DOCKER_LOG:?}"\n',
    )
    state = tmp_path / "state"
    flock_log = tmp_path / "flock.log"
    docker_log = tmp_path / "docker.log"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CLEANUP_INSTALL_ROOT": str(install_root),
        "CLEANUP_STATE_DIR": str(state),
        "CLEANUP_MAX_AGE_DAYS": "7",
        "FAKE_FLOCK_LOG": str(flock_log),
        "FAKE_DOCKER_LOG": str(docker_log),
    }
    proc = subprocess.run(["bash", str(CLEANUP)], text=True, capture_output=True, env=env)
    return proc, state, flock_log


def test_cleanup_removes_old_dirs_in_every_service(tmp_path: Path) -> None:
    root = tmp_path / "actions-runner"
    old1 = make_aged_dir(root / "svc-1" / "_work", "old-job", age_days=30)
    fresh1 = make_aged_dir(root / "svc-1" / "_work", "fresh-job", age_days=0)
    old2 = make_aged_dir(root / "svc-2" / "_work", "old-job", age_days=30)
    fresh2 = make_aged_dir(root / "svc-2" / "_work", "fresh-job", age_days=0)

    proc, state, flock_log = run_cleanup(tmp_path, root)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not old1.exists(), "old dir in svc-1 should be removed"
    assert not old2.exists(), "old dir in svc-2 should be removed"
    assert fresh1.exists(), "fresh dir in svc-1 should be kept"
    assert fresh2.exists(), "fresh dir in svc-2 should be kept"
    # The maintenance lock is opened and flock is taken.
    assert (state / "maintenance.lock").exists()
    assert flock_log.read_text().strip() != ""


def test_cleanup_noop_without_services(tmp_path: Path) -> None:
    root = tmp_path / "actions-runner"
    root.mkdir()
    proc, _state, _flock_log = run_cleanup(tmp_path, root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
