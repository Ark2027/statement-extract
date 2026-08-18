"""Get text out of a statement PDF, by whichever route the file allows.

A digital PDF carries its characters, and reading them directly is exact. A
scanned one carries pixels, and the only way in is OCR, which will sometimes
read an 8 as a 3 and hand you a number that looks perfectly ordinary.

Both paths are here. The interesting part is the third option: on a digital
page, OCR it anyway and compare the two readings of the key totals. Native
extraction and OCR fail in unrelated ways, so where they agree the number is
about as trustworthy as it gets, and where they disagree something is wrong
with one of them and you want to know which page.

The cross-check costs a render and an OCR pass per page, so it is off by
default and turned on when accuracy matters more than speed.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from .money import find_dollar_on_line

log = logging.getLogger(__name__)

# Labels worth cross-checking. Every one feeds a validation identity, so an
# error here propagates rather than staying local.
CROSS_CHECK_LABELS = (
    "total assets",
    "total liabilities",
    "total net assets",
    "total current assets",
    "total current liabilities",
    "gross loans receivable",
    "loan loss allowance",
)

# Below this the two readings agree closely enough to be the same number.
CROSS_CHECK_TOLERANCE_PCT = 1.0


@dataclass
class PageReading:
    """What one page produced, and how it was read."""

    number: int
    text: str
    used_ocr: bool = False
    disagreements: list[str] = field(default_factory=list)


@dataclass
class DocumentReading:
    """The whole document, with the provenance kept alongside the text."""

    path: str
    pages: list[PageReading] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)

    @property
    def used_ocr(self) -> bool:
        return any(p.used_ocr for p in self.pages)

    @property
    def disagreements(self) -> list[str]:
        return [d for p in self.pages for d in p.disagreements]

    @property
    def confidence(self) -> float:
        """A rough 0-100 read on how much to trust the text.

        Deliberately crude. It exists to rank documents for review, not to be
        reported as a probability of anything.
        """
        score = 100.0
        if self.used_ocr:
            score -= 15.0
        score -= 12.0 * len(self.disagreements)
        return max(0.0, min(100.0, score))


def tesseract_path() -> str | None:
    """Locate the Tesseract binary, or return None if it isn't installed.

    Checks TESSERACT_CMD first so a caller can point at a specific install,
    then falls back to whatever is on PATH. Nothing is hardcoded, because a
    path that only exists on the author's machine is not a default.
    """
    configured = os.environ.get("TESSERACT_CMD")
    if configured and Path(configured).exists():
        return configured
    return shutil.which("tesseract")


def ocr_available() -> bool:
    if tesseract_path() is None:
        return False
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def _ocr_page(page) -> str:
    """Render a page and run OCR over it. Returns "" if that isn't possible."""
    binary = tesseract_path()
    if binary is None:
        return ""
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = binary
        image = page.to_image(resolution=300).original
        return pytesseract.image_to_string(image) or ""
    except Exception as exc:
        log.warning("OCR failed on page %s: %s", getattr(page, "page_number", "?"), exc)
        return ""


def key_values(text: str) -> dict[str, float]:
    """Amounts for the labels worth cross-checking, keyed by label."""
    found: dict[str, float] = {}
    for line in text.split("\n"):
        lowered = line.lower().strip()
        for label in CROSS_CHECK_LABELS:
            if label in lowered and label not in found:
                value = find_dollar_on_line(line)
                if value is not None:
                    found[label] = value
                break
    return found


def compare_readings(
    native: str, ocr: str, page_number: int, tolerance_pct: float = CROSS_CHECK_TOLERANCE_PCT
) -> list[str]:
    """Report key totals where the two readings of a page disagree."""
    if not ocr.strip():
        return []

    native_values = key_values(native)
    ocr_values = key_values(ocr)

    disagreements = []
    for label, native_value in native_values.items():
        if label not in ocr_values:
            continue
        ocr_value = ocr_values[label]
        if native_value == 0 or ocr_value == 0:
            continue
        off_by = abs(native_value - ocr_value) / max(abs(native_value), 1) * 100
        if off_by > tolerance_pct:
            disagreements.append(
                f"page {page_number}: '{label}' reads {native_value:,.0f} from the "
                f"PDF text but {ocr_value:,.0f} from OCR ({off_by:.1f}% apart)"
            )
    return disagreements


def read_pdf(path: str | Path, cross_check: bool = False) -> DocumentReading:
    """Read a statement PDF into text, page by page.

    With *cross_check* on, every page that looks like a balance sheet is also
    OCR'd and the two readings compared. Pages that had to be OCR'd in the
    first place are not cross-checked, since there is nothing to compare
    against.
    """
    path = Path(path)
    document = DocumentReading(path=str(path))

    with pdfplumber.open(str(path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            native = page.extract_text() or ""

            if native.strip():
                reading = PageReading(number=index, text=native)
                if cross_check and _looks_like_balance_sheet(native):
                    reading.disagreements = compare_readings(
                        native, _ocr_page(page), index
                    )
                    for note in reading.disagreements:
                        log.warning("Cross-check mismatch in %s: %s", path.name, note)
            else:
                scanned = _ocr_page(page)
                reading = PageReading(number=index, text=scanned, used_ocr=bool(scanned.strip()))

            document.pages.append(reading)

    if document.used_ocr:
        log.info("Used OCR for at least one page of %s", path.name)
    return document


def extract_text(path: str | Path, cross_check: bool = False) -> str:
    """Convenience wrapper for callers that only want the text."""
    return read_pdf(path, cross_check=cross_check).text


def _looks_like_balance_sheet(text: str) -> bool:
    lowered = text.lower()
    return "total assets" in lowered or "total liabilities" in lowered
