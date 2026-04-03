"""Data models for verification reports."""

from dataclasses import dataclass, field
from datetime import date, datetime

from src.checks.base import CheckResult


@dataclass
class VerificationReport:
    """Aggregates all check results into a single report."""

    target_date: date
    generated_at: datetime = field(default_factory=datetime.utcnow)
    results: list[CheckResult] = field(default_factory=list)

    @property
    def total_checks(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "PASS")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "FAIL")

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == "ERROR")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "SKIPPED")

    @property
    def has_failures(self) -> bool:
        return self.failed > 0

    @property
    def has_errors(self) -> bool:
        return self.errors > 0

    def results_by_check(self) -> dict[str, list[CheckResult]]:
        """Group results by check name."""
        grouped: dict[str, list[CheckResult]] = {}
        for r in self.results:
            grouped.setdefault(r.check_name, []).append(r)
        return grouped

    def summary_by_check(self) -> list[dict]:
        """Get summary stats per check name."""
        summaries = []
        for check_name, check_results in self.results_by_check().items():
            summaries.append({
                "check_name": check_name,
                "total": len(check_results),
                "passed": sum(1 for r in check_results if r.status == "PASS"),
                "failed": sum(1 for r in check_results if r.status == "FAIL"),
                "errors": sum(1 for r in check_results if r.status == "ERROR"),
                "skipped": sum(1 for r in check_results if r.status == "SKIPPED"),
            })
        return summaries

    def failures_only(self) -> list[CheckResult]:
        """Return only FAIL results."""
        return [r for r in self.results if r.status == "FAIL"]
