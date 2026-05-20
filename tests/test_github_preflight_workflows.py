from __future__ import annotations

from scripts.github_preflight import audit_workflow_text


def test_broad_self_hosted_label_fails() -> None:
    workflow = """
on: push
jobs:
  ci:
    runs-on: [self-hosted, linux, x64]
    steps:
      - run: echo unsafe
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == [
        ".github/workflows/ci.yml job ci targets self-hosted runners without "
        "required label paper-archives."
    ]


def test_pull_request_target_to_runner_fails() -> None:
    workflow = """
on: pull_request_target
jobs:
  ci:
    runs-on: [self-hosted, paper-archives]
    steps:
      - uses: actions/checkout@v4
      - run: make test
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == [
        ".github/workflows/ci.yml uses unsafe trigger pull_request_target on "
        "runner label paper-archives."
    ]


def test_broad_label_with_unsafe_trigger_reports_both_errors() -> None:
    workflow = """
on: pull_request_target
jobs:
  ci:
    runs-on: [self-hosted, linux, x64]
    steps:
      - run: make test
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == [
        ".github/workflows/ci.yml job ci targets self-hosted runners without "
        "required label paper-archives.",
        ".github/workflows/ci.yml uses unsafe trigger pull_request_target on "
        "runner label paper-archives.",
    ]


def test_repo_specific_label_on_push_passes() -> None:
    workflow = """
on: push
jobs:
  ci:
    runs-on: [self-hosted, linux, x64, paper-archives]
    steps:
      - run: echo ok
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == []
