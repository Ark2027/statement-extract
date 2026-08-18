"""Parse dollar amounts out of text pulled from a financial statement PDF.

Text extraction from PDFs is lossy in ways that break naive number parsing.
The functions here handle the failure modes that actually showed up in
practice, rather than the ones a clean-room implementation would expect:

    '$ 6 ,873,260'   a space landed inside the number
    '(180,585)'      accounting notation for a negative
    '($1,234)'       same, with the sign inside the parentheses
    '9,946,968,55'   OCR read the decimal point as a comma
    '2025 Total assets 41,829,331'   a year sitting to the left of the value

The last one is the one that quietly corrupts a whole run. A regex that grabs
the first number on the line returns 2025, which is a plausible-looking dollar
amount, so nothing downstream complains.
"""

from __future__ import annotations

import re

# The digits of an amount. Written so it cannot match without at least one
# digit — a currency symbol followed by a space is not a number, and letting
# it match produced a silent zero.
_AMOUNT = r"[\d, ]*\d[\d, ]*"

# Patterns that include a currency symbol. A match here is high confidence,
# because a bare number on a financial statement line could be a year, a note
# reference, or a percentage.
#
# Order is deliberate: the parenthesized forms first so an accounting negative
# is read whole, then the explicitly signed form, then the plain one. A
# pattern that led with optional whitespace used to start one character to the
# left of the correct match and win the leftmost comparison, so none of these
# begin with \s*.
_DOLLAR_PATTERNS = (
    rf"[\$]\s*\(\s*{_AMOUNT}\.?\d*\s*\)",       # $ ( 1,351,000 )
    rf"\(\s*[\$]\s*{_AMOUNT}\.?\d*\s*\)",       # ( $1,234 )
    rf"[\-−]\s*[\$]\s*{_AMOUNT}\.?\d*",    # -$1,234
    rf"[\$]\s*[\-−]?\s*{_AMOUNT}\.?\d*",   # $1,234 or $ 1,234
)

# Fallback patterns for statements that omit the currency symbol.
_BARE_PATTERNS = (
    rf"\(\s*{_AMOUNT}\.?\d*\s*\)",              # (1,234)
    r"[\-−]?\s*[\d,]+\.\d{2}\b",           # 1,234.56
    r"[\-−]?\s*[\d,]{2,}\b",               # 1,234
)

_MINUS_SIGNS = ("-", "−")  # ASCII hyphen and the real minus sign

# A four-digit year standing alone. Statements put these in column headers and
# sometimes at the front of a label, where they are the leftmost number on the
# line and get mistaken for the value.
_BARE_YEAR = re.compile(r"^(?:19|20)\d{2}$")


def parse_dollar(raw: str) -> float | None:
    """Turn one dollar string into a float, or None if it isn't a number.

    >>> parse_dollar("$ 6 ,873,260")
    6873260.0
    >>> parse_dollar("(180,585)")
    -180585.0
    >>> parse_dollar("9,946,968,55")
    9946968.55

    A dash on its own is how a statement writes zero in an amount column, and
    is read as such. That only applies once a token has been isolated as the
    amount — `find_dollar_on_line` will not infer zero from a trailing dash on
    a line, because at that point it cannot tell an amount column from a
    hyphenated label.
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip()
    negative = False

    # Accounting negatives wrap the number in parentheses, and the currency
    # symbol lands on either side depending on the tool that wrote the PDF.
    without_symbol = text.replace("$", "").strip()
    if without_symbol.startswith("(") and without_symbol.endswith(")"):
        negative = True
        text = without_symbol[1:-1]
    elif text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    text = text.replace("$", "").replace(" ", "").replace("\xa0", "").strip()

    # A dash in an amount column is how statements write a zero. Checked
    # before the sign is stripped, or a lone dash reads as a negative with
    # nothing after it and falls through as unparseable.
    if text in ("-", "–", "—"):
        return 0.0

    if text.startswith(_MINUS_SIGNS):
        negative = True
        text = text[1:]

    # Anything without a digit is not an amount. Returning zero here would
    # turn a stray currency symbol into a real-looking figure.
    if not any(char.isdigit() for char in text):
        return None

    text = _repair_ocr_decimal(text)
    text = text.replace(",", "")

    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def _repair_ocr_decimal(text: str) -> str:
    """Fix a decimal point that OCR read as a comma.

    '9,946,968,55' should be 9,946,968.55. The giveaway is the last group
    having two digits where a thousands separator requires three. Only applies
    when every earlier group is a proper three-digit group, so a genuine
    thousands-separated integer is left alone.
    """
    if "," not in text or "." in text:
        return text

    groups = text.split(",")
    if len(groups) < 2:
        return text
    if len(groups[-1]) == 2 and all(len(g) == 3 for g in groups[1:-1]):
        return ",".join(groups[:-1]) + "." + groups[-1]
    return text


def find_dollar_on_line(line: str) -> float | None:
    """Pull the primary dollar amount off a single statement line.

    Currency-marked amounts win over bare numbers, and among equals the
    leftmost wins. That ordering is what stops a year or a note reference in
    the label from being read as the value.
    """
    best_value = None
    best_position = len(line) + 1

    for pattern in _DOLLAR_PATTERNS:
        match = re.search(pattern, line)
        if match and match.start() < best_position:
            value = parse_dollar(match.group())
            if value is not None:
                best_value = value
                best_position = match.start()

    if best_value is not None:
        return best_value

    return _leftmost_bare_amount(line)


def _leftmost_bare_amount(line: str) -> float | None:
    """Leftmost bare number, skipping a year unless it is the only candidate.

    "2026 Total assets 41,829,331" has its label prefixed with the period,
    which is the leftmost number and looks like a perfectly ordinary amount.
    A bare four-digit year is only skipped when something else on the line
    could be the value, so a line whose real amount happens to be 2026 still
    reads correctly.
    """
    candidates: list[tuple[int, str]] = []
    for pattern in _BARE_PATTERNS:
        for match in re.finditer(pattern, line):
            candidates.append((match.start(), match.group()))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    parsed = [(pos, raw, parse_dollar(raw)) for pos, raw in candidates]
    parsed = [item for item in parsed if item[2] is not None]
    if not parsed:
        return None

    non_year = [item for item in parsed if not _BARE_YEAR.match(item[1].strip())]
    if non_year:
        return non_year[0][2]
    return parsed[0][2]


# Splits a line at the first currency symbol or number cluster, so what's left
# on the front is the label. The lookbehind keeps it from cutting inside a word
# that happens to contain a digit.
_LABEL_SPLIT = re.compile(r"[\$]|(?<![a-zA-Z])[\-−]?\d[\d, ]*\.?\d*")

# A period prefix on the label, as in "2026 Total assets 41,829,331". Left in
# place it becomes the first number on the line, the split happens at position
# zero, and the label comes out empty — so the row is dropped entirely.
_LEADING_PERIOD = re.compile(r"^(?:19|20)\d{2}\s+(?=\D)")


def extract_labeled_values(text: str) -> dict[str, float]:
    """Read a statement into {label: amount}.

    Lines with no recognizable amount are skipped. Where the same label appears
    more than once the first occurrence wins, which on a two-column statement
    is the current period.
    """
    results: dict[str, float] = {}

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        value = find_dollar_on_line(stripped)
        if value is None:
            continue

        label = _LABEL_SPLIT.split(_LEADING_PERIOD.sub("", stripped))[0]
        # Trailing punctuation is left behind when the split lands just after
        # an opening bracket or a leader dot: "change in net assets (" .
        label = re.sub(r"\s+", " ", label).strip(" .$(-–—:")

        if len(label) > 2:
            results.setdefault(label.lower(), value)

    return results
