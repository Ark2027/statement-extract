"""End-to-end tests over the generated fixture PDFs.

Each fixture exists to prove one behavior, so these read as a description of
what the library claims to do. The fixtures are built by tools/make_fixtures.py
and committed, and a test here checks they still match what that script
produces.

    python tests/test_pipeline.py
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from statement_extract import read_statement, read_text  # noqa: E402
from statement_extract.fields import map_to_fields  # noqa: E402
from statement_extract.models import ExtractedFields  # noqa: E402
from statement_extract.stated import parse_stated_ratios  # noqa: E402

FIXTURES = ROOT / "fixtures"

# What the clean statement says, and what everything else is measured against.
ASSETS = 41_829_331.0
LIABILITIES = 18_823_199.0
NET_ASSETS = 23_006_132.0
CURRENT_ASSETS = 4_182_933.0
CURRENT_LIABILITIES = 3_346_346.0
PORTFOLIO = 28_283_707.0      # 6,873,260 current + 21,410,447 non-current
RESERVE = 1_614_185.0


class CleanStatementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = read_statement(FIXTURES / "clean.pdf")

    def test_balance_sheet_totals(self):
        fields = self.report.fields
        self.assertEqual(fields.total_assets, ASSETS)
        self.assertEqual(fields.total_liabilities, LIABILITIES)
        self.assertEqual(fields.total_net_assets, NET_ASSETS)

    def test_subtotals_are_not_mistaken_for_totals(self):
        """'Total assets' is a substring of several other lines on this page."""
        fields = self.report.fields
        self.assertEqual(fields.total_current_assets, CURRENT_ASSETS)
        self.assertEqual(fields.total_current_liabilities, CURRENT_LIABILITIES)
        self.assertNotEqual(fields.total_assets, fields.total_current_assets)

    def test_split_loan_portfolio_is_recombined(self):
        """A classified balance sheet never states the gross total."""
        self.assertEqual(self.report.fields.gross_loans_receivable, PORTFOLIO)

    def test_reserve_is_positive_despite_being_shown_negative(self):
        self.assertEqual(self.report.fields.loan_loss_allowance, RESERVE)

    def test_a_clean_statement_produces_no_findings(self):
        self.assertEqual(self.report.validation.corrections, [])
        self.assertEqual(self.report.validation.warnings, [])
        self.assertFalse(self.report.needs_review)


class BalanceSheetCorrectionTests(unittest.TestCase):
    def test_safe_correction_is_applied(self):
        report = read_statement(FIXTURES / "unbalanced.pdf")
        self.assertEqual(report.fields.total_net_assets, NET_ASSETS)
        self.assertEqual(len(report.validation.corrections), 1)
        self.assertIn("corrected", report.validation.corrections[0])
        self.assertEqual(report.validation.warnings, [])

    def test_unsafe_correction_is_refused(self):
        """Liabilities exceed assets, so which figure is wrong is a guess."""
        report = read_statement(FIXTURES / "ambiguous.pdf")
        self.assertEqual(report.validation.corrections, [])
        self.assertTrue(report.validation.warnings)
        self.assertIn("cannot be determined", report.validation.warnings[0])
        self.assertTrue(report.needs_review)

    def test_refusing_leaves_the_numbers_untouched(self):
        report = read_statement(FIXTURES / "ambiguous.pdf")
        self.assertEqual(report.fields.total_assets, 18_204_000.0)
        self.assertEqual(report.fields.total_net_assets, NET_ASSETS)


class StatedRatioTests(unittest.TestCase):
    def test_stated_ratios_are_read_from_the_summary_table(self):
        report = read_statement(FIXTURES / "summary_table.pdf")
        stated = report.stated
        self.assertAlmostEqual(stated.net_asset_ratio, 0.550, places=3)
        self.assertAlmostEqual(stated.current_ratio, 1.25, places=2)
        self.assertAlmostEqual(stated.loan_loss_reserve_ratio, 0.057, places=3)

    def test_threshold_text_is_not_read_as_a_value(self):
        """'should be > 25%' contains a number that must be ignored."""
        report = read_statement(FIXTURES / "summary_table.pdf")
        self.assertNotAlmostEqual(report.stated.net_asset_ratio, 0.25, places=2)

    def test_agreeing_ratios_raise_nothing(self):
        report = read_statement(FIXTURES / "summary_table.pdf")
        self.assertEqual(report.validation.warnings, [])
        self.assertFalse(report.needs_review)

    def test_disagreement_between_stated_and_derived_is_flagged(self):
        report = read_statement(FIXTURES / "contradictory.pdf")
        warnings = " ".join(report.validation.warnings)
        self.assertIn("net asset ratio", warnings)
        self.assertIn("0.318", warnings)
        self.assertIn("0.550", warnings)
        self.assertTrue(report.needs_review)

    def test_no_summary_table_means_no_stated_ratios(self):
        report = read_statement(FIXTURES / "clean.pdf")
        self.assertFalse(report.stated.any_found())


class ExtractionArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = read_statement(FIXTURES / "artifacts.pdf")

    def test_survives_a_space_inside_a_number(self):
        self.assertEqual(self.report.fields.total_current_assets, CURRENT_ASSETS)

    def test_survives_a_period_prefixed_label(self):
        self.assertEqual(self.report.fields.total_assets, ASSETS)

    def test_survives_an_ocr_comma_decimal(self):
        self.assertAlmostEqual(self.report.fields.total_net_assets, 23_006_131.55, places=2)

    def test_survives_a_currency_symbol_outside_the_parentheses(self):
        self.assertEqual(self.report.fields.loan_loss_allowance, RESERVE)

    def test_missing_fields_stay_missing(self):
        """None means not found. It must never quietly become zero."""
        self.assertIsNone(self.report.fields.unrestricted_cash)


class ValidationLogicTests(unittest.TestCase):
    def test_derives_net_assets_when_absent(self):
        fields = map_to_fields({
            "total assets": 100.0,
            "total liabilities": 40.0,
        })
        self.assertEqual(fields.total_net_assets, 60.0)

    def test_negative_total_assets_is_flagged(self):
        report = read_text("Total assets (41,829,331)\nTotal liabilities 18,823,199")
        warnings = " ".join(report.validation.warnings)
        self.assertIn("negative", warnings)

    def test_reserve_exceeding_the_portfolio_is_flagged(self):
        report = read_text(
            "Gross loans receivable 1,000,000\nLoan loss allowance 4,000,000"
        )
        warnings = " ".join(report.validation.warnings)
        self.assertIn("exceeds", warnings)

    def test_period_over_period_jump_is_flagged(self):
        prior = ExtractedFields(total_assets=10_000_000.0)
        report = read_text("Total assets 41,829,331", prior=prior)
        warnings = " ".join(report.validation.warnings)
        self.assertIn("prior period", warnings)

    def test_partial_extraction_gets_partial_validation(self):
        """Checks missing their inputs are skipped, not failed."""
        report = read_text("Total assets 41,829,331")
        self.assertEqual(report.validation.warnings, [])
        self.assertEqual(report.validation.corrections, [])


class FixtureIntegrityTests(unittest.TestCase):
    def test_all_fixtures_are_present(self):
        expected = {
            "clean.pdf", "unbalanced.pdf", "ambiguous.pdf",
            "summary_table.pdf", "contradictory.pdf", "artifacts.pdf",
        }
        self.assertTrue(expected.issubset({p.name for p in FIXTURES.glob("*.pdf")}))

    def test_fixtures_match_the_generator(self):
        """Committed PDFs must be what tools/make_fixtures.py produces."""
        before = {p.name: p.read_bytes() for p in sorted(FIXTURES.glob("*.pdf"))}
        subprocess.run(
            [sys.executable, str(ROOT / "tools" / "make_fixtures.py")],
            check=True, capture_output=True,
        )
        after = {p.name: p.read_bytes() for p in sorted(FIXTURES.glob("*.pdf"))}
        self.assertEqual(
            before, after,
            "fixtures are stale — re-run tools/make_fixtures.py and commit",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
