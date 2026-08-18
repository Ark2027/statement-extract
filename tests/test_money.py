"""Tests for the number parsing, which is where the sharp edges are.

Most of these are regressions. Each one is a case that produced a wrong
number rather than an error, which is the failure mode worth defending
against — a parser that raises gets noticed, and one that returns 2026
instead of 41,829,331 does not.

    python tests/test_money.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from statement_extract.money import (  # noqa: E402
    extract_labeled_values,
    find_dollar_on_line,
    parse_dollar,
)


class ParseDollarTests(unittest.TestCase):
    def test_plain_amounts(self):
        self.assertEqual(parse_dollar("1,140,557"), 1140557.0)
        self.assertEqual(parse_dollar("$2,814,998"), 2814998.0)
        self.assertEqual(parse_dollar("41829331"), 41829331.0)

    def test_space_inside_the_number(self):
        """PDF extraction drops spaces into the middle of figures."""
        self.assertEqual(parse_dollar("$ 6 ,873,260"), 6873260.0)
        self.assertEqual(parse_dollar("$ 4 ,182,933"), 4182933.0)

    def test_accounting_negatives(self):
        self.assertEqual(parse_dollar("(180,585)"), -180585.0)
        self.assertEqual(parse_dollar("($1,234)"), -1234.0)
        self.assertEqual(parse_dollar("$ ( 1,351,000 )"), -1351000.0)
        self.assertEqual(parse_dollar("-$ 430,782.99"), -430782.99)

    def test_unicode_minus_is_a_minus(self):
        self.assertEqual(parse_dollar("−430,782"), -430782.0)

    def test_ocr_comma_read_as_decimal_point(self):
        """A trailing two-digit group means the last comma was a decimal."""
        self.assertEqual(parse_dollar("9,946,968,55"), 9946968.55)
        self.assertEqual(parse_dollar("23,006,131,55"), 23006131.55)

    def test_genuine_thousands_are_left_alone(self):
        """Three-digit groups are separators, not a misread decimal."""
        self.assertEqual(parse_dollar("9,946,968"), 9946968.0)
        self.assertEqual(parse_dollar("1,234"), 1234.0)

    def test_existing_decimal_point_disables_the_repair(self):
        self.assertEqual(parse_dollar("1,234.56"), 1234.56)

    def test_dash_is_zero(self):
        for dash in ("-", "–", "—"):
            self.assertEqual(parse_dollar(dash), 0.0, dash)

    def test_currency_symbol_alone_is_not_a_number(self):
        """This returned 0.0 once, which silently zeroed a real balance."""
        self.assertIsNone(parse_dollar("$"))
        self.assertIsNone(parse_dollar("$ "))

    def test_non_numbers(self):
        self.assertIsNone(parse_dollar(""))
        self.assertIsNone(parse_dollar("   "))
        self.assertIsNone(parse_dollar("Total assets"))


class FindDollarOnLineTests(unittest.TestCase):
    def test_currency_marked_amount_wins(self):
        line = "Total assets $ 41,829,331"
        self.assertEqual(find_dollar_on_line(line), 41829331.0)

    def test_parenthesized_negative_is_read_whole(self):
        """The bare '$ ' used to match one character to the left and win."""
        line = "Less reserve for possible loan losses $ ( 1,614,185 )"
        self.assertEqual(find_dollar_on_line(line), -1614185.0)

    def test_year_in_the_label_does_not_become_the_value(self):
        line = "2026 Total assets 41,829,331"
        self.assertEqual(find_dollar_on_line(line), 41829331.0)

    def test_year_is_used_when_it_is_the_only_number(self):
        """Skipping years unconditionally would lose a real value of 2026."""
        self.assertEqual(find_dollar_on_line("Fiscal year 2026"), 2026.0)

    def test_signed_currency(self):
        self.assertEqual(find_dollar_on_line("Interest expense -$12,400"), -12400.0)

    def test_trailing_dash_is_not_assumed_to_be_zero(self):
        """Ambiguous at line level: could be an amount column or a hyphen."""
        self.assertIsNone(find_dollar_on_line("Deferred revenue -"))

    def test_line_without_an_amount(self):
        self.assertIsNone(find_dollar_on_line("Statement of Financial Position"))
        self.assertIsNone(find_dollar_on_line(""))

    def test_two_column_statement_takes_the_current_period(self):
        """Leftmost value column is the current period in these statements."""
        line = "Total assets $ 41,829,331 $ 39,041,100"
        self.assertEqual(find_dollar_on_line(line), 41829331.0)


class ExtractLabeledValuesTests(unittest.TestCase):
    def test_label_and_value_split(self):
        text = "Total assets $ 41,829,331\nTotal liabilities 18,823,199"
        values = extract_labeled_values(text)
        self.assertEqual(values["total assets"], 41829331.0)
        self.assertEqual(values["total liabilities"], 18823199.0)

    def test_period_prefix_does_not_swallow_the_label(self):
        """The split landed at position zero and dropped the row entirely."""
        values = extract_labeled_values("2026 Total assets 41,829,331")
        self.assertIn("total assets", values)
        self.assertEqual(values["total assets"], 41829331.0)

    def test_trailing_punctuation_is_trimmed_from_labels(self):
        values = extract_labeled_values("Change in net assets (180,585)")
        self.assertIn("change in net assets", values)
        self.assertEqual(values["change in net assets"], -180585.0)

    def test_first_occurrence_wins(self):
        text = "Total assets 41,829,331\nTotal assets 39,041,100"
        self.assertEqual(extract_labeled_values(text)["total assets"], 41829331.0)

    def test_lines_without_amounts_are_skipped(self):
        text = "Statement of Financial Position\nAssets\nTotal assets 41,829,331"
        values = extract_labeled_values(text)
        self.assertEqual(list(values), ["total assets"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
