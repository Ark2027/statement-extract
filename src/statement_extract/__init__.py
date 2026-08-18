"""Pull financial figures out of statement PDFs, and check them before use.

    from statement_extract import read_statement

    report = read_statement("q3.pdf", cross_check=True)
    print(report.fields.total_assets)
    for warning in report.validation.warnings:
        print(warning)

The pieces are usable on their own. `parse_dollar` and `find_dollar_on_line`
need no PDF at all and work on any text that contains accounting figures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fields import map_to_fields
from .models import ExtractedFields, StatedRatios, ValidationResult
from .money import extract_labeled_values, find_dollar_on_line, parse_dollar
from .pdf import DocumentReading, extract_text, ocr_available, read_pdf
from .stated import parse_stated_change_in_net_assets, parse_stated_ratios
from .validate import validate

__version__ = "1.0.0"

__all__ = [
    "read_statement",
    "read_text",
    "StatementReport",
    "ExtractedFields",
    "StatedRatios",
    "ValidationResult",
    "DocumentReading",
    "parse_dollar",
    "find_dollar_on_line",
    "extract_labeled_values",
    "map_to_fields",
    "parse_stated_ratios",
    "validate",
    "read_pdf",
    "extract_text",
    "ocr_available",
]


@dataclass
class StatementReport:
    """Everything one statement produced, including how much to trust it."""

    source: str
    fields: ExtractedFields
    stated: StatedRatios
    validation: ValidationResult
    reading: DocumentReading | None = None
    labels: dict[str, float] = field(default_factory=dict)

    @property
    def confidence(self) -> float:
        """0-100, combining how the text was read with how it validated.

        A crude ranking signal for deciding what a person should look at
        first. It is not a probability and should not be reported as one.
        """
        base = self.reading.confidence if self.reading is not None else 100.0
        found_ratio = self.fields.found() / 12
        base -= (1 - min(found_ratio, 1.0)) * 25
        return max(0.0, min(100.0, base + self.validation.confidence_adjustment))

    @property
    def needs_review(self) -> bool:
        """True when a person should read the source before trusting this."""
        return bool(self.validation.warnings) or self.confidence < 70

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "fields": self.fields.to_dict(),
            "stated_ratios": self.stated.to_dict(),
            "validation": self.validation.to_dict(),
            "confidence": round(self.confidence, 1),
            "needs_review": self.needs_review,
            "used_ocr": self.reading.used_ocr if self.reading else False,
            "cross_check_disagreements": self.reading.disagreements if self.reading else [],
        }


def read_text(text: str, prior: ExtractedFields | None = None,
              current_column: int = -1) -> StatementReport:
    """Run the pipeline over statement text that is already in hand."""
    labels = extract_labeled_values(text)
    fields = map_to_fields(labels)

    stated = parse_stated_ratios(text, current_column=current_column)
    if fields.change_in_net_assets is None:
        from_summary = parse_stated_change_in_net_assets(text, current_column)
        if from_summary is not None:
            fields.change_in_net_assets = from_summary

    result = validate(fields, stated=stated, prior=prior)
    return StatementReport(
        source="<text>", fields=fields, stated=stated,
        validation=result, labels=labels,
    )


def read_statement(
    path: str | Path,
    cross_check: bool = False,
    prior: ExtractedFields | None = None,
    current_column: int = -1,
) -> StatementReport:
    """Read one statement PDF end to end.

    Set *cross_check* to OCR each balance-sheet page as well and compare the
    two readings. Pass *prior* to enable period-over-period checks.
    """
    reading = read_pdf(path, cross_check=cross_check)
    report = read_text(reading.text, prior=prior, current_column=current_column)
    report.source = str(path)
    report.reading = reading
    return report
