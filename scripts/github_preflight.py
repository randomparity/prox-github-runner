#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class CheckResult:
    errors: list[str]
    warnings: list[str]


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
