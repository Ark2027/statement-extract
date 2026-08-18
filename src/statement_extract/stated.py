"""Read ratios that the statement worked out for itself.

Some preparers attach a summary table with their own ratios already
calculated, alongside the threshold each one is measured against:

    Total Net Asset Ratio        31.8%     29.8%     should be > 25%
    Operating Liquidity            1.5       1.7      must be > 1
    Allowance for Loan Losses     4.7%      4.0%      must be > 4%
    Change in Net Assets     9,855,992  1,140,557     YTD, must be positive

Two things make this harder than it looks.

The threshold text contains numbers. Grabbing every number on the first line
yields 31.8, 29.8 and 25, and taking the last one hands you the threshold
instead of the value. So each line is truncated at the word that introduces
the threshold before any number is read.

The value columns are prior period then current period, and which one you want
is a property of the document rather than something that can be inferred. It
is a parameter here, defaulting to the last value column, and stated plainly
because getting it backwards produces a number that looks entirely reasonable.
"""

from __future__ import annotations

import re

from .models import StatedRatios

# Words that introduce the threshold. Everything from here to the end of the
# line is commentary, not data.
_THRESHOLD_MARKERS = r"\b(?:should|must|minimum|maximum|target|ytd|required)\b"

_PERCENT = re.compile(r"([\d.]+)\s*%")
_DECIMAL = re.compile(r"\b([\d]+\.[\d]+)\b")
_LARGE_NUMBER = re.compile(r"[\d,]{4,}")

# The heading that tells us a summary table is present at all. Without it,
# ordinary statement lines would be misread as stated ratios.
_TABLE_HEADINGS = ("covenants and ratios", "covenant ratios", "ratio summary",
                   "financial covenants")


def has_summary_table(text: str) -> bool:
    lowered = text.lower()
    return any(heading in lowered for heading in _TABLE_HEADINGS)


def _values_before_threshold(line: str, pattern: re.Pattern[str],
                             low: float, high: float) -> list[float]:
    """Numbers on the line, stopping before the threshold commentary."""
    body = re.split(_THRESHOLD_MARKERS, line, maxsplit=1)[0]
    # PDF extraction sometimes drops a space into a number: "9 ,855,992".
    body = re.sub(r"(\d)\s+,", r"\1,", body)

    values = []
    for raw in pattern.findall(body):
        try:
            number = float(raw.replace(",", ""))
        except ValueError:
            continue
        if low < number < high:
            values.append(number)
    return values


def _pick(values: list[float], column: int) -> float | None:
    """Take the requested column, falling back to the only value present."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    if column < 0:
        column = len(values) + column
    if 0 <= column < len(values):
        return values[column]
    return values[-1]


def parse_stated_ratios(text: str, current_column: int = -1) -> StatedRatios:
    """Pull the stated ratios out of a summary table.

    *current_column* selects which value column is the current period, as an
    index into the numbers found on each line. It defaults to the last one.
    Set it to 0 if the document puts the current period first.
    """
    stated = StatedRatios()
    if not has_summary_table(text):
        return stated

    for line in text.split("\n"):
        lowered = line.lower().strip()
        if not lowered:
            continue

        # Percentages are written as 31.8%, so divide back to a fraction.
        if "net asset ratio" in lowered and "unrestricted" not in lowered:
            values = _values_before_threshold(lowered, _PERCENT, 1, 100)
            picked = _pick(values, current_column)
            if picked is not None:
                stated.net_asset_ratio = picked / 100

        if "current ratio" in lowered or "acid ratio" in lowered:
            values = _values_before_threshold(lowered, _DECIMAL, 0.01, 100)
            picked = _pick(values, current_column)
            if picked is not None:
                stated.current_ratio = picked

        if "operating liquidity" in lowered:
            values = _values_before_threshold(lowered, _DECIMAL, 0.01, 50)
            picked = _pick(values, current_column)
            if picked is not None:
                stated.operating_liquidity = picked

        if "allowance for loan" in lowered and "loss" in lowered:
            values = _values_before_threshold(lowered, _PERCENT, 0, 100)
            picked = _pick(values, current_column)
            if picked is not None:
                stated.loan_loss_reserve_ratio = picked / 100

    return stated


def parse_stated_change_in_net_assets(text: str, current_column: int = -1) -> float | None:
    """The change-in-net-assets line from a summary table, if present.

    Kept separate from the ratios because it is a dollar amount rather than a
    ratio, and it belongs on ExtractedFields instead.
    """
    if not has_summary_table(text):
        return None

    for line in text.split("\n"):
        lowered = line.lower().strip()
        if "change in net assets" in lowered and re.search(_THRESHOLD_MARKERS, lowered):
            values = _values_before_threshold(lowered, _LARGE_NUMBER, 100, float("inf"))
            picked = _pick(values, current_column)
            if picked is not None:
                return picked
    return None
