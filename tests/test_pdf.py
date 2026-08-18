"""Tests for the PDF layer and the OCR cross-check.

The cross-check logic is tested against text rather than by running OCR, so it
runs anywhere. Tesseract is not a CI dependency and the tests that genuinely
need it skip themselves when it is absent, which is also the honest way to
report that the OCR path was not exercised on this machine.

    python tests/test_pdf.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from statement_extract.pdf import (  # noqa: E402
    compare_readings,
    key_values,
    ocr_available,
    read_pdf,
    tesseract_path,
)

FIXTURES = ROOT / "fixtures"


class KeyValueTests(unittest.TestCase):
    def test_pulls_the_labels_worth_checking(self):
        text = (
            "Total assets $ 41,829,331\n"
            "Total liabilities 18,823,199\n"
            "Rent expense 42,000\n"
        )
        values = key_values(text)
        self.assertEqual(values["total assets"], 41829331.0)
        self.assertEqual(values["total liabilities"], 18823199.0)
        self.assertNotIn("rent expense", values)

    def test_first_occurrence_wins(self):
        text = "Total assets 41,829,331\nTotal assets 39,041,100"
        self.assertEqual(key_values(text)["total assets"], 41829331.0)


class CrossCheckTests(unittest.TestCase):
    """The whole point: two readings that disagree mean one of them is wrong."""

    def test_agreement_reports_nothing(self):
        native = "Total assets 41,829,331"
        ocr = "Total assets 41,829,331"
        self.assertEqual(compare_readings(native, ocr, 1), [])

    def test_a_misread_digit_is_caught(self):
        """OCR turning an 8 into a 3 gives a plausible but wrong number."""
        native = "Total assets 41,829,331"
        ocr = "Total assets 41,329,331"
        found = compare_readings(native, ocr, 1)
        self.assertEqual(len(found), 1)
        self.assertIn("total assets", found[0])
        self.assertIn("41,829,331", found[0])
        self.assertIn("41,329,331", found[0])

    def test_rounding_below_tolerance_is_not_reported(self):
        native = "Total assets 41,829,331"
        ocr = "Total assets 41,829,000"
        self.assertEqual(compare_readings(native, ocr, 1), [])

    def test_labels_missing_from_one_reading_are_skipped(self):
        """A label OCR failed to read is not evidence the value is wrong."""
        native = "Total assets 41,829,331\nTotal liabilities 18,823,199"
        ocr = "Total assets 41,829,331"
        self.assertEqual(compare_readings(native, ocr, 1), [])

    def test_empty_ocr_reports_nothing(self):
        self.assertEqual(compare_readings("Total assets 41,829,331", "", 1), [])

    def test_page_number_is_carried_into_the_message(self):
        found = compare_readings("Total assets 100,000", "Total assets 200,000", 7)
        self.assertIn("page 7", found[0])

    def test_multiple_disagreements_are_all_reported(self):
        native = "Total assets 41,829,331\nTotal liabilities 18,823,199"
        ocr = "Total assets 41,329,331\nTotal liabilities 13,823,199"
        self.assertEqual(len(compare_readings(native, ocr, 1)), 2)


class TesseractDiscoveryTests(unittest.TestCase):
    def test_env_var_is_honored_when_it_points_at_a_real_file(self):
        # Any file that certainly exists stands in for the binary here; the
        # function checks the path resolves, not that it is really Tesseract.
        stand_in = str(Path(__file__).resolve())
        previous = os.environ.get("TESSERACT_CMD")
        os.environ["TESSERACT_CMD"] = stand_in
        try:
            self.assertEqual(tesseract_path(), stand_in)
        finally:
            if previous is None:
                os.environ.pop("TESSERACT_CMD", None)
            else:
                os.environ["TESSERACT_CMD"] = previous

    def test_a_bad_env_var_falls_through_rather_than_crashing(self):
        previous = os.environ.get("TESSERACT_CMD")
        os.environ["TESSERACT_CMD"] = "/nowhere/tesseract"
        try:
            self.assertNotEqual(tesseract_path(), "/nowhere/tesseract")
        finally:
            if previous is None:
                os.environ.pop("TESSERACT_CMD", None)
            else:
                os.environ["TESSERACT_CMD"] = previous


class DocumentReadingTests(unittest.TestCase):
    def test_digital_pdf_needs_no_ocr(self):
        doc = read_pdf(FIXTURES / "clean.pdf")
        self.assertFalse(doc.used_ocr)
        self.assertEqual(doc.confidence, 100.0)
        self.assertEqual(len(doc.pages), 1)

    def test_cross_check_on_a_consistent_page_finds_nothing(self):
        if not ocr_available():
            self.skipTest("Tesseract not installed; OCR path not exercised")
        doc = read_pdf(FIXTURES / "clean.pdf", cross_check=True)
        self.assertEqual(doc.disagreements, [])

    def test_text_is_page_separated(self):
        doc = read_pdf(FIXTURES / "clean.pdf")
        self.assertIn("Total assets", doc.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
