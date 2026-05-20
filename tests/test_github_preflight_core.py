from __future__ import annotations

from datetime import date, timedelta

from scripts.github_preflight import evaluate_pat_lifetime


def test_pat_lifetime_warns_at_warning_threshold() -> None:
    today = date(2026, 5, 20)
    result = evaluate_pat_lifetime(
        expires_on=today + timedelta(days=14),
        today=today,
        warning_days=14,
        failure_days=7,
        max_days=30,
    )
    assert result.errors == []
    assert result.warnings == ["GitHub PAT expires in 14 days."]


def test_pat_lifetime_fails_at_failure_threshold() -> None:
    today = date(2026, 5, 20)
    result = evaluate_pat_lifetime(
        expires_on=today + timedelta(days=7),
        today=today,
        warning_days=14,
        failure_days=7,
        max_days=30,
    )
    assert result.errors == ["GitHub PAT expires in 7 days; rotate before running."]
    assert result.warnings == []


def test_pat_lifetime_fails_when_too_far_out() -> None:
    today = date(2026, 5, 20)
    result = evaluate_pat_lifetime(
        expires_on=today + timedelta(days=31),
        today=today,
        warning_days=14,
        failure_days=7,
        max_days=30,
    )
    assert result.errors == [
        "GitHub PAT expires in 31 days; maximum allowed remaining lifetime is 30 days."
    ]
