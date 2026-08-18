"""The shapes that move between extraction and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedFields:
    """Canonical financial fields read off a statement.

    Every field is optional, because a real statement is allowed to omit any
    of them and the whole point is to be honest about what was found. None
    means "not located," which is a different claim from zero.
    """

    total_assets: float | None = None
    total_liabilities: float | None = None
    total_net_assets: float | None = None
    total_current_assets: float | None = None
    total_current_liabilities: float | None = None

    change_in_net_assets: float | None = None
    total_revenue: float | None = None
    total_expenses: float | None = None
    total_operating_expenses: float | None = None

    unrestricted_cash: float | None = None
    loan_loss_allowance: float | None = None
    gross_loans_receivable: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Only the fields that were actually found."""
        return {k: v for k, v in self.__dict__.items() if v is not None}

    def found(self) -> int:
        return sum(1 for v in self.__dict__.values() if v is not None)

    def merge(self, other: ExtractedFields) -> ExtractedFields:
        """Fold another set in. Values already present are not overwritten.

        Statements arrive as several PDFs — a balance sheet, an income
        statement, sometimes a separate schedule. Earlier documents win so
        that the order you pass them in is the order of precedence.
        """
        for key, value in other.__dict__.items():
            if value is not None and getattr(self, key) is None:
                setattr(self, key, value)
        return self


@dataclass
class StatedRatios:
    """Ratios the statement calculated for itself.

    Some preparers include a summary table with their own ratios worked out.
    Those are worth capturing separately from anything derived, because
    comparing the two is a genuine check on both.
    """

    net_asset_ratio: float | None = None
    current_ratio: float | None = None
    operating_liquidity: float | None = None
    loan_loss_reserve_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    def any_found(self) -> bool:
        return any(v is not None for v in self.__dict__.values())


@dataclass
class ValidationResult:
    """What validation concluded about one set of extracted fields.

    Corrections were applied. Warnings were not — they describe something
    that looks wrong but could not be fixed without guessing which of several
    values was the bad one.
    """

    corrections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence_adjustment: float = 0.0

    @property
    def ok(self) -> bool:
        """True when nothing needed changing and nothing looked wrong."""
        return not self.corrections and not self.warnings

    def to_dict(self) -> dict[str, Any]:
        return {
            "corrections": self.corrections,
            "warnings": self.warnings,
            "confidence_adjustment": round(self.confidence_adjustment, 1),
            "ok": self.ok,
        }
