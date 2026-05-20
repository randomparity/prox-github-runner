from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from tests.mock_github import MockGitHubServer


BASE_ARGS = [
    sys.executable,
    "scripts/github_preflight.py",
    "--api-version",
    "2026-03-10",
    "--target-repo",
    "drc-dot-nz/paper-archives",
    "--token",
    "github_pat_test",
    "--expires-on",
    "2026-06-10",
    "--warning-days",
    "14",
    "--failure-days",
    "7",
    "--max-days",
    "30",
    "--today",
    "2026-05-20",
    "--required-label",
    "paper-archives",
    "--runner-labels",
    "self-hosted,linux,x64,paper-archives",
]


def run_preflight(api_base_url: str) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        [*BASE_ARGS, "--api-base-url", api_base_url],
        check=False,
        text=True,
        capture_output=True,
    )
    assert proc.stderr == ""
    return proc.returncode, json.loads(proc.stdout)


def test_public_repo_fails() -> None:
    routes = {
        ("GET", "/repos/drc-dot-nz/paper-archives"): (
            200,
            {"private": False, "default_branch": "main"},
        ),
    }
    with MockGitHubServer(routes) as server:
        code, result = run_preflight(server.url)
    assert code == 1
    assert "Target repository drc-dot-nz/paper-archives is public." in result["errors"]


def test_registration_token_403_fails() -> None:
    routes = {
        ("GET", "/repos/drc-dot-nz/paper-archives"): (
            200,
            {"private": True, "default_branch": "main"},
        ),
        ("POST", "/repos/drc-dot-nz/paper-archives/actions/runners/registration-token"): (
            403,
            {"message": "Resource not accessible by personal access token"},
        ),
    }
    with MockGitHubServer(routes) as server:
        code, result = run_preflight(server.url)
    assert code == 1
    assert any("registration token" in error for error in result["errors"])
