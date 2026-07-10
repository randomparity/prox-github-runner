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


def test_matrix_include_static_runs_on_passes() -> None:
    # The idiomatic multi-OS matrix: runs-on references a matrix key whose values
    # are statically enumerated in include entries. The self-hosted entry carries
    # the required label; the audit must resolve the matrix and pass it.
    workflow = """
on: push
jobs:
  test:
    runs-on: ${{ matrix.runs-on }}
    strategy:
      matrix:
        include:
          - os: linux
            runs-on: [self-hosted, linux, x64, paper-archives]
          - os: macos-latest
            runs-on: macos-latest
          - os: windows-latest
            runs-on: windows-latest
    steps:
      - run: cargo test
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == []


def test_matrix_include_self_hosted_without_label_fails() -> None:
    # Resolution must still catch a self-hosted matrix entry missing the label.
    workflow = """
on: push
jobs:
  test:
    runs-on: ${{ matrix.runs-on }}
    strategy:
      matrix:
        include:
          - os: linux
            runs-on: [self-hosted, linux, x64]
          - os: macos-latest
            runs-on: macos-latest
    steps:
      - run: cargo test
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == [
        ".github/workflows/ci.yml job test targets self-hosted runners without "
        "required label paper-archives."
    ]


def test_matrix_top_level_self_hosted_with_unsafe_trigger_fails() -> None:
    # Top-level matrix key values (not just include) must resolve too, and an
    # unsafe trigger on a resolved self-hosted value is still reported.
    workflow = """
on: pull_request_target
jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os:
          - [self-hosted, linux, x64, paper-archives]
          - ubuntu-latest
    steps:
      - run: echo hi
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


def test_input_driven_runs_on_still_fails() -> None:
    # A runs-on referencing non-matrix context is genuinely unauditable: reject.
    workflow = """
on: workflow_dispatch
jobs:
  test:
    runs-on: ${{ github.event.inputs.runner }}
    steps:
      - run: echo hi
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == [".github/workflows/ci.yml job test uses dynamic runs-on."]


def test_matrix_ref_without_matrix_values_is_dynamic() -> None:
    # A matrix reference with no matching matrix values cannot be resolved.
    workflow = """
on: push
jobs:
  test:
    runs-on: ${{ matrix.runs-on }}
    steps:
      - run: echo hi
"""
    result = audit_workflow_text(
        path=".github/workflows/ci.yml",
        text=workflow,
        required_label="paper-archives",
    )
    assert result.errors == [".github/workflows/ci.yml job test uses dynamic runs-on."]
