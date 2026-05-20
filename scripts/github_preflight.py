#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import yaml

UNSAFE_TRIGGERS = {"issue_comment", "pull_request_target", "workflow_run"}


@dataclass(frozen=True)
class CheckResult:
    errors: list[str]
    warnings: list[str]


class GitHubError(RuntimeError):
    def __init__(self, purpose: str, status: int | None, message: str) -> None:
        self.purpose = purpose
        self.status = status
        super().__init__(f"{purpose} failed: {message}")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date {value!r}; expected YYYY-MM-DD.") from exc


def evaluate_pat_lifetime(
    *,
    expires_on: date,
    today: date,
    warning_days: int,
    failure_days: int,
    max_days: int,
) -> CheckResult:
    remaining_days = (expires_on - today).days
    errors: list[str] = []
    warnings: list[str] = []

    if remaining_days < 0:
        errors.append("GitHub PAT is expired; rotate before running.")
    elif remaining_days <= failure_days:
        errors.append(f"GitHub PAT expires in {remaining_days} days; rotate before running.")
    elif remaining_days > max_days:
        errors.append(
            f"GitHub PAT expires in {remaining_days} days; "
            f"maximum allowed remaining lifetime is {max_days} days."
        )
    elif remaining_days <= warning_days:
        warnings.append(f"GitHub PAT expires in {remaining_days} days.")

    return CheckResult(errors=errors, warnings=warnings)


def resolve_token(token: str | None, token_env: str | None) -> CheckResult:
    if token:
        return CheckResult(errors=[], warnings=[])
    if token_env and os.environ.get(token_env):
        return CheckResult(errors=[], warnings=[])
    return CheckResult(errors=["GitHub token was not provided."], warnings=[])


def read_token(token: str | None, token_env: str | None) -> str:
    if token:
        return token
    if token_env:
        return os.environ.get(token_env, "")
    return ""


def request_json(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    method: str,
    path: str,
    purpose: str,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    url = f"{api_base_url.rstrip('/')}{path}"
    last_error: str | None = None

    for attempt in range(1, 4):
        request = urllib.request.Request(url, method=method)
        request.add_header("accept", "application/vnd.github+json")
        request.add_header("authorization", f"Bearer {token}")
        request.add_header("x-github-api-version", api_version)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode() or "{}")
                headers = {key.lower(): value for key, value in response.headers.items()}
                return response.status, data, headers
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            try:
                data = json.loads(body or "{}")
            except json.JSONDecodeError:
                data = {"message": body}
            headers = {key.lower(): value for key, value in exc.headers.items()}
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                return exc.code, data, headers
            last_error = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last_error = str(exc.reason)
            if attempt == 3:
                raise GitHubError(purpose, None, last_error) from exc

    raise GitHubError(purpose, None, last_error or "unknown network error")


def check_repository(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    owner: str,
    repo: str,
) -> tuple[dict[str, Any] | None, CheckResult]:
    status, data, _headers = request_json(
        api_base_url=api_base_url,
        api_version=api_version,
        token=token,
        method="GET",
        path=f"/repos/{owner}/{repo}",
        purpose="repository metadata lookup",
    )
    if status != 200:
        return None, CheckResult(
            errors=[f"GitHub rejected repository metadata lookup with HTTP {status}."],
            warnings=[],
        )
    if data.get("private") is not True:
        return data, CheckResult(
            errors=[f"Target repository {owner}/{repo} is public."],
            warnings=[],
        )
    return data, CheckResult(errors=[], warnings=[])


def check_registration_token_permission(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    owner: str,
    repo: str,
) -> CheckResult:
    status, data, _headers = request_json(
        api_base_url=api_base_url,
        api_version=api_version,
        token=token,
        method="POST",
        path=f"/repos/{owner}/{repo}/actions/runners/registration-token",
        purpose="runner registration token probe",
    )
    if status != 201:
        message = data.get("message", "unknown GitHub error")
        return CheckResult(
            errors=[
                f"GitHub rejected runner registration token probe with HTTP {status}: {message}"
            ],
            warnings=[],
        )
    return CheckResult(errors=[], warnings=[])


def check_runner_labels(*, required_label: str, runner_labels: list[str]) -> CheckResult:
    if required_label not in runner_labels:
        return CheckResult(
            errors=[f"Runner labels must include repository-specific label {required_label}."],
            warnings=[],
        )
    return CheckResult(errors=[], warnings=[])


def load_workflow(text: str) -> dict[str, Any]:
    loaded = yaml.load(text, Loader=yaml.BaseLoader)
    return loaded if isinstance(loaded, dict) else {}


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def workflow_triggers(workflow: dict[str, Any]) -> set[str]:
    raw = workflow.get("on", {})
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(item) for item in raw}
    if isinstance(raw, dict):
        return {str(key) for key in raw}
    return set()


def job_labels(job: dict[str, Any]) -> list[str]:
    return normalize_list(job.get("runs-on"))


def job_targets_self_hosted(labels: list[str], required_label: str) -> bool:
    label_set = set(labels)
    return required_label in label_set or "self-hosted" in label_set


def audit_workflow_text(*, path: str, text: str, required_label: str) -> CheckResult:
    workflow = load_workflow(text)
    triggers = workflow_triggers(workflow)
    jobs = workflow.get("jobs", {})
    errors: list[str] = []

    if not isinstance(jobs, dict):
        return CheckResult(errors=[], warnings=[])

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        labels = job_labels(job)
        if not labels:
            continue
        if "${{" in ",".join(labels):
            errors.append(f"{path} job {job_name} uses dynamic runs-on.")
            continue
        if job_targets_self_hosted(labels, required_label) and required_label not in labels:
            errors.append(
                f"{path} job {job_name} targets self-hosted runners without "
                f"required label {required_label}."
            )
        if job_targets_self_hosted(labels, required_label):
            for trigger in sorted(triggers & UNSAFE_TRIGGERS):
                errors.append(
                    f"{path} uses unsafe trigger {trigger} on runner label {required_label}."
                )

    return CheckResult(errors=errors, warnings=[])


def decode_content(data: dict[str, Any]) -> str:
    content = str(data.get("content", "")).replace("\n", "")
    return base64.b64decode(content).decode()


def normalize_codeowners_pattern(line: str) -> str:
    pattern = line.split("#", 1)[0].strip()
    if not pattern:
        return ""
    pattern = pattern.split()[0]
    if pattern.startswith("/"):
        pattern = pattern[1:]
    if pattern.endswith("/"):
        pattern = f"{pattern}**"
    return pattern


def codeowners_covers_path(pattern: str, path: str) -> bool:
    pattern = normalize_codeowners_pattern(pattern)
    if not pattern or pattern.startswith("!"):
        return False
    if pattern == "*":
        return True
    if pattern.endswith("/**"):
        base = pattern[:-3].rstrip("/")
        return path == base or path.startswith(f"{base}/")
    if "/" not in pattern:
        return fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)
    return fnmatch.fnmatch(path, pattern)


def codeowners_has_required_coverage(text: str, required_paths: list[str]) -> bool:
    patterns = [
        normalize_codeowners_pattern(line)
        for line in text.splitlines()
        if normalize_codeowners_pattern(line)
    ]
    for required_path in required_paths:
        if not any(codeowners_covers_path(pattern, required_path) for pattern in patterns):
            return False
    return True


def check_codeowners(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    owner: str,
    repo: str,
    branch: str,
    required_paths: list[str],
) -> CheckResult:
    for path in [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"]:
        status, data, _headers = request_json(
            api_base_url=api_base_url,
            api_version=api_version,
            token=token,
            method="GET",
            path=f"/repos/{owner}/{repo}/contents/{path}?ref={branch}",
            purpose=f"CODEOWNERS lookup {path}",
        )
        if status == 200 and codeowners_has_required_coverage(decode_content(data), required_paths):
            return CheckResult(errors=[], warnings=[])
    return CheckResult(
        errors=[],
        warnings=["No CODEOWNERS coverage found for workflow or local action paths."],
    )


def check_branch_rules(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    owner: str,
    repo: str,
    branch: str,
) -> CheckResult:
    status, data, _headers = request_json(
        api_base_url=api_base_url,
        api_version=api_version,
        token=token,
        method="GET",
        path=f"/repos/{owner}/{repo}/rules/branches/{branch}",
        purpose=f"branch rules lookup for {branch}",
    )
    if status != 200:
        return CheckResult(
            errors=[],
            warnings=[f"Could not read active branch rules for {branch}: HTTP {status}."],
        )
    if not data:
        return CheckResult(
            errors=[],
            warnings=[f"No active branch rules returned for default branch {branch}."],
        )
    rule_types = {str(rule.get("type", "")) for rule in data if isinstance(rule, dict)}
    if "pull_request" not in rule_types:
        return CheckResult(
            errors=[],
            warnings=[f"No pull request review rule returned for default branch {branch}."],
        )
    return CheckResult(errors=[], warnings=[])


def fetch_workflow_audit(
    *,
    api_base_url: str,
    api_version: str,
    token: str,
    owner: str,
    repo: str,
    branch: str,
    required_label: str,
) -> CheckResult:
    status, data, _headers = request_json(
        api_base_url=api_base_url,
        api_version=api_version,
        token=token,
        method="GET",
        path=f"/repos/{owner}/{repo}/contents/.github/workflows?ref={branch}",
        purpose="workflow directory listing",
    )
    if status == 404:
        return CheckResult(errors=[], warnings=["No workflow directory found."])
    if status != 200:
        return CheckResult(errors=[f"Could not list workflow files: HTTP {status}."], warnings=[])
    if not isinstance(data, list):
        return CheckResult(errors=["Workflow directory listing was not a list."], warnings=[])

    errors: list[str] = []
    warnings: list[str] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", ""))
        name = str(entry.get("name", ""))
        if not name.endswith((".yml", ".yaml")):
            continue
        status, content, _headers = request_json(
            api_base_url=api_base_url,
            api_version=api_version,
            token=token,
            method="GET",
            path=f"/repos/{owner}/{repo}/contents/{path}?ref={branch}",
            purpose=f"workflow file fetch {path}",
        )
        if status != 200:
            errors.append(f"Could not fetch workflow file {path}: HTTP {status}.")
            continue
        audit = audit_workflow_text(
            path=path,
            text=decode_content(content),
            required_label=required_label,
        )
        errors.extend(audit.errors)
        warnings.extend(audit.warnings)

    return CheckResult(errors=errors, warnings=warnings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GitHub runner preflight checks")
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--api-version", required=True)
    parser.add_argument("--target-repo", required=True)
    parser.add_argument("--token")
    parser.add_argument("--token-env")
    parser.add_argument("--expires-on", required=True)
    parser.add_argument("--warning-days", type=int, required=True)
    parser.add_argument("--failure-days", type=int, required=True)
    parser.add_argument("--max-days", type=int, required=True)
    parser.add_argument("--today")
    parser.add_argument("--required-label", required=True)
    parser.add_argument("--runner-labels", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors: list[str] = []
    warnings: list[str] = []

    try:
        expires_on = parse_date(args.expires_on)
    except ValueError as exc:
        errors.append(str(exc))
        expires_on = date.today()

    if "/" not in args.target_repo:
        errors.append("Target repository must be in owner/repo form.")

    try:
        today = parse_date(args.today) if args.today else datetime.now(UTC).date()
    except ValueError as exc:
        errors.append(str(exc))
        today = datetime.now(UTC).date()

    lifetime = evaluate_pat_lifetime(
        expires_on=expires_on,
        today=today,
        warning_days=args.warning_days,
        failure_days=args.failure_days,
        max_days=args.max_days,
    )
    errors.extend(lifetime.errors)
    warnings.extend(lifetime.warnings)

    token_check = resolve_token(args.token, args.token_env)
    errors.extend(token_check.errors)
    warnings.extend(token_check.warnings)
    token = read_token(args.token, args.token_env)

    runner_labels = [label.strip() for label in args.runner_labels.split(",") if label.strip()]
    label_check = check_runner_labels(
        required_label=args.required_label,
        runner_labels=runner_labels,
    )
    errors.extend(label_check.errors)
    warnings.extend(label_check.warnings)

    if "/" in args.target_repo and not errors:
        owner, repo = args.target_repo.split("/", 1)
        try:
            repo_data, repo_check = check_repository(
                api_base_url=args.api_base_url,
                api_version=args.api_version,
                token=token,
                owner=owner,
                repo=repo,
            )
            errors.extend(repo_check.errors)
            warnings.extend(repo_check.warnings)
            if repo_data is not None and not repo_check.errors:
                registration_check = check_registration_token_permission(
                    api_base_url=args.api_base_url,
                    api_version=args.api_version,
                    token=token,
                    owner=owner,
                    repo=repo,
                )
                errors.extend(registration_check.errors)
                warnings.extend(registration_check.warnings)
                branch = str(repo_data.get("default_branch", ""))
                if branch and not registration_check.errors:
                    branch_check = check_branch_rules(
                        api_base_url=args.api_base_url,
                        api_version=args.api_version,
                        token=token,
                        owner=owner,
                        repo=repo,
                        branch=branch,
                    )
                    errors.extend(branch_check.errors)
                    warnings.extend(branch_check.warnings)
                    workflow_check = fetch_workflow_audit(
                        api_base_url=args.api_base_url,
                        api_version=args.api_version,
                        token=token,
                        owner=owner,
                        repo=repo,
                        branch=branch,
                        required_label=args.required_label,
                    )
                    errors.extend(workflow_check.errors)
                    warnings.extend(workflow_check.warnings)
                    codeowners_check = check_codeowners(
                        api_base_url=args.api_base_url,
                        api_version=args.api_version,
                        token=token,
                        owner=owner,
                        repo=repo,
                        branch=branch,
                        required_paths=[
                            ".github/workflows/example.yml",
                            ".github/actions/example/action.yml",
                        ],
                    )
                    errors.extend(codeowners_check.errors)
                    warnings.extend(codeowners_check.warnings)
        except GitHubError as exc:
            errors.append(str(exc))

    result = {
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "target_repo": args.target_repo,
            "required_label": args.required_label,
        },
    }
    print(json.dumps(result, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
