#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any


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
                "GitHub rejected runner registration token probe "
                f"with HTTP {status}: {message}"
            ],
            warnings=[],
        )
    return CheckResult(errors=[], warnings=[])


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
        today = parse_date(args.today) if args.today else datetime.now(timezone.utc).date()
    except ValueError as exc:
        errors.append(str(exc))
        today = datetime.now(timezone.utc).date()

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
