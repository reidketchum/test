"""Base classes and data models for verification checks."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Literal


@dataclass
class CheckResult:
    """Result of a single verification check item."""

    check_name: str
    status: Literal["PASS", "FAIL", "ERROR", "SKIPPED"]
    details: str
    metadata: dict = field(default_factory=dict)

    def __str__(self):
        return f"[{self.status}] {self.check_name}: {self.details}"


class BaseCheck(ABC):
    """Abstract base class for all verification checks."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this check."""
        ...

    @abstractmethod
    def run(self, target_date: date) -> list[CheckResult]:
        """Run this verification check for the given date.

        Args:
            target_date: The date to check compliance for.

        Returns:
            List of CheckResult objects (one per item checked).
        """
        ...
