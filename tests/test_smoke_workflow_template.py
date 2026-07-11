from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

TEMPLATE = Path("templates/paper-archives-smoke.yml")
DOCS = Path("docs/smoke-workflow.md")

EXPECTED_LABELS = ["self-hosted", "linux", "x64", "paper-archives"]


def load_template() -> dict:
    return yaml.safe_load(TEMPLATE.read_text())


def workflow_triggers(doc: dict) -> Any:
    # PyYAML parses the bare `on:` key as the boolean True (YAML 1.1), so the
    # trigger block lives under either key depending on the loader.
    return doc[True] if True in doc else doc["on"]


def only_job(doc: dict) -> dict:
    jobs = doc["jobs"]
    assert len(jobs) == 1, jobs
    return next(iter(jobs.values()))


def test_workflow_dispatch_only() -> None:
    triggers = workflow_triggers(load_template())
    keys = [triggers] if isinstance(triggers, str) else list(triggers)
    assert keys == ["workflow_dispatch"]


def test_runs_on_carries_paper_archives_labels() -> None:
    job = only_job(load_template())
    assert job["runs-on"] == EXPECTED_LABELS


def test_steps_prove_checkout_shell_docker_and_cleanup() -> None:
    job = only_job(load_template())
    steps = job["steps"]
    uses = [s.get("uses", "") for s in steps]
    runs = [s.get("run", "") for s in steps]
    assert any("actions/checkout" in u for u in uses), "checkout step missing"
    assert any("docker run" in r for r in runs), "docker run step missing"
    assert any("$GITHUB_WORKSPACE" in r or "workspace" in r.lower() for r in runs), (
        "shell step referencing the workspace missing"
    )
    cleanup = [s for s in steps if s.get("if") == "always()"]
    assert cleanup, "no always()-guarded cleanup step"
    assert any("rm -rf" in s.get("run", "") for s in cleanup), (
        "cleanup step does not remove workspace contents"
    )


def test_docs_call_out_private_repo_only_boundary() -> None:
    body = DOCS.read_text().lower()
    assert "private" in body
    assert "public" in body
