from __future__ import annotations

import subprocess


def test_preflight_playbook_requires_vault_token() -> None:
    proc = subprocess.run(
        ["ansible-playbook", "playbooks/preflight.yml", "-e", "vault_github_pat="],
        check=False,
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    assert "Missing GitHub preflight config or vault_github_pat" in proc.stdout
