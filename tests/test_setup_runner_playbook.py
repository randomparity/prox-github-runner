from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

PLAYBOOK = Path("playbooks/setup-runner.yml")


def roles_in_order() -> list[str]:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    roles: list[str] = []
    for play in plays:
        for role in play.get("roles", []):
            roles.append(role if isinstance(role, str) else role["role"])
    return roles


def test_role_order_is_preflight_runner_host_github_runner() -> None:
    assert roles_in_order() == ["preflight", "runner_host", "github_runner"]


def test_setup_runner_playbook_passes_syntax_check() -> None:
    proc = subprocess.run(
        ["ansible-playbook", "--syntax-check", str(PLAYBOOK)],
        text=True,
        capture_output=True,
        cwd=Path.cwd(),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
