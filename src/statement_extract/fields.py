"""Map the labels a statement actually uses onto canonical field names.

No two preparers write a balance sheet the same way. "Total net assets,"
"total fund balance," and "total equity" are the same line. This module holds
the alias table that collapses them, plus the negative filters that stop a
label from matching something it merely contains.

That second part does most of the work. "Total assets" is a substring of
"total other assets," "total current assets," and "total fixed assets," and
matching any of those into total_assets silently understates the balance
sheet by an amount nobody will notice.
"""

from __future__ import annotations

import re

from .models import ExtractedFields

# Canonical field -> label fragments that indicate the line holds that field.
# Order matters inside a group: the first alias to match wins, so put the more
# specific wording first.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "total_assets": ("total assets",),
    "total_liabilities": ("total liabilities",),
    "total_net_assets": (
        "total net assets",
        "total fund balance",
        "total equity",
    ),
    "total_current_assets": ("total current assets",),
    "total_current_liabilities": ("total current liabilities",),

    "change_in_net_assets": (
        "change in net assets without donor",
        "change in net assets before",
        "change in net assets",
        "change in unrestricted net assets",
        "current year surplus",
        "operating surplus",
        "net operating income",
        "net income",
    ),
    "total_revenue": (
        "total revenue",
        "total operating revenues",
        "total income",
    ),
    "total_expenses": (
        "total expense",
        "total operating expenditures",
        "total expenses",
    ),
    "total_operating_expenses": (
        "total operating expenditures",
        "total expenses",
        "total expense",
    ),

    "unrestricted_cash": (
        "unrestricted cash",
        "cash available for operations",
        "cash and cash equivalents - operating",
        "cash & cash equivalents - operating",
        "operating cash",
    ),

    "loan_loss_allowance": (
        "loan loss allowance",
        "allowance for loan loss",
        "allowance for possible loan loss",
        "reserve for possible loan loss",
        "less reserve for possible loan loss",
        "loan loss reserve",
        "total allowance",
    ),
    # Deliberately excludes "loans receivable, current portion". That line is
    # one half of a classified balance sheet's portfolio, and matching it here
    # reports the current slice as the whole book — which then makes the
    # reserve ratio look several times worse than it is. The two halves are
    # recombined in _combine_loan_portions instead.
    "gross_loans_receivable": (
        "gross loans receivable",
        "total gross portfolio",
        "total loans receivable",
    ),
}

# Labels that contain an alias but mean something else. Without these, a
# subtotal gets read as the total.
NEGATIVE_FILTERS: dict[str, tuple[str, ...]] = {
    "total_assets": (
        "total other assets",
        "total current assets",
        "total long term assets",
        "total fixed assets",
    ),
    "total_liabilities": (
        "total current liabilities",
        "total long term liabilities",
        "total other liabilities",
        "total liabilities and equity",
        "total liabilities & equity",
        "total liabilities and net",
        "total liabilities & net",
        "total liabilities and capital",
        "total liabilities & capital",
    ),
    "total_net_assets": ("change in net assets",),
    "total_current_assets": ("total other current assets",),
    "loan_loss_allowance": ("total allowance for doubtful",),
    "gross_loans_receivable": ("loans receivable, net of current", "less reserve"),
}


def _normalize(text: str) -> str:
    """Reduce a label to letters and digits.

    PDF extraction drops and inserts whitespace unpredictably, so comparing
    raw strings gives false negatives. Ampersands become "and" first, since
    preparers use the two interchangeably.
    """
    return re.sub(r"[^a-z0-9]+", "", text.lower().replace("&", "and"))


def label_matches(label: str, alias: str, field_key: str) -> bool:
    """True when *label* means *field_key*, respecting the negative filters."""
    normalized = _normalize(label)
    if _normalize(alias) not in normalized:
        return False
    return not any(
        _normalize(bad) in normalized for bad in NEGATIVE_FILTERS.get(field_key, ())
    )


def map_to_fields(labeled_values: dict[str, float]) -> ExtractedFields:
    """Fold {label: amount} into canonical fields.

    Longer labels are considered first, on the principle that the more
    specific wording is the more likely intended match. Each label is consumed
    once, so a single line cannot populate two fields.
    """
    fields = ExtractedFields()
    used: set[str] = set()

    by_specificity = sorted(labeled_values, key=len, reverse=True)

    for field_key, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            for label in by_specificity:
                if label in used:
                    continue
                if label_matches(label, alias, field_key):
                    setattr(fields, field_key, labeled_values[label])
                    used.add(label)
                    break
            if getattr(fields, field_key) is not None:
                break

    _derive_missing(fields)
    _combine_loan_portions(fields, labeled_values)
    _find_operating_cash_by_account(fields, labeled_values)

    # Reserves are sometimes written as a negative because they reduce assets.
    # The magnitude is what later checks care about.
    if fields.loan_loss_allowance is not None:
        fields.loan_loss_allowance = abs(fields.loan_loss_allowance)

    return fields


def _derive_missing(fields: ExtractedFields) -> None:
    """Fill in a total that the statement left implicit.

    Both of these are definitional rather than estimates, so deriving them is
    safe. Anything that would require an assumption is left as None.
    """
    if (fields.total_net_assets is None
            and fields.total_assets is not None
            and fields.total_liabilities is not None):
        fields.total_net_assets = fields.total_assets - fields.total_liabilities

    if (fields.change_in_net_assets is None
            and fields.total_revenue is not None
            and fields.total_expenses is not None):
        fields.change_in_net_assets = fields.total_revenue - fields.total_expenses


def _combine_loan_portions(fields: ExtractedFields, labeled: dict[str, float]) -> None:
    """Rebuild the gross portfolio when it is split across current and non-current.

    Classified balance sheets report loans receivable in two lines and never
    state the total, so the only way to recover it is to add them back.
    """
    if fields.gross_loans_receivable is not None:
        return

    current = non_current = None
    for label, value in labeled.items():
        if "loans receivable" not in label:
            continue
        if "current portion" in label and "net of" not in label:
            current = value
        elif "net of current" in label:
            non_current = value

    if current is not None and non_current is not None:
        fields.gross_loans_receivable = abs(current) + abs(non_current)


def _find_operating_cash_by_account(
    fields: ExtractedFields, labeled: dict[str, float]
) -> None:
    """Last resort for operating cash: a chart-of-accounts style line.

    Some statements are exported straight from the accounting system and label
    cash by account rather than in prose, as "1000 Operations Bank" or
    similar. Only consulted when the normal aliases found nothing.
    """
    if fields.unrestricted_cash is not None:
        return

    for label, value in labeled.items():
        looks_like_cash = "bank" in label or "cash" in label
        looks_operating = "operating" in label or "operations" in label or "ops" in label
        if looks_like_cash and looks_operating:
            fields.unrestricted_cash = value
            return
