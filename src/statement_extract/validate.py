"""Check extracted fields against the identities they have to satisfy.

Extraction can be confidently wrong. A label matched the wrong line, a column
was read from the prior year, OCR turned an 8 into a 3 — none of that raises
an error, it just produces a number. The only defense is to check the numbers
against relationships that must hold, and the balance sheet gives you one for
free: assets equal liabilities plus net assets, always, by construction.

The rule this module follows is that a correction has to be provably safe.
When assets and liabilities are both sound and net assets is the odd one out,
the fix is arithmetic and gets applied. When any two of the three could be the
culprit, guessing which to change would trade a visible problem for an
invisible one, so it warns instead and leaves the numbers alone.
"""

from __future__ import annotations

from .models import ExtractedFields, StatedRatios, ValidationResult

# Rounding in published statements is real. Below these, a difference is not
# evidence of an extraction error.
BALANCE_TOLERANCE_PCT = 0.005
BALANCE_TOLERANCE_FLOOR = 5_000.0

# Revenue minus expenses rarely equals change in net assets exactly, because
# non-operating items legitimately sit between them. The tolerance is wide on
# purpose; this check is looking for a wrong column, not a rounding gap.
INCOME_TOLERANCE_PCT = 0.10
INCOME_TOLERANCE_FLOOR = 50_000.0

# Stated ratios are usually rounded to one decimal, so an exact match is not
# expected. A gap beyond this means the two disagree about something real.
STATED_RATIO_TOLERANCE = 0.015


def validate(
    fields: ExtractedFields,
    stated: StatedRatios | None = None,
    prior: ExtractedFields | None = None,
) -> ValidationResult:
    """Run every check that the available fields support.

    Modifies *fields* in place where a correction is safe. Checks that lack
    their inputs are skipped rather than failed, so a partial extraction gets
    partial validation instead of a wall of noise.
    """
    result = ValidationResult()

    _check_balance_sheet(fields, result)
    _check_income_statement(fields, result)
    _check_signs(fields, result)
    if stated is not None and stated.any_found():
        _check_stated_against_derived(fields, stated, result)
    if prior is not None:
        _check_period_over_period(fields, prior, result)

    return result


def _check_balance_sheet(fields: ExtractedFields, result: ValidationResult) -> None:
    """Assets = Liabilities + Net Assets."""
    assets = fields.total_assets
    liabilities = fields.total_liabilities
    net_assets = fields.total_net_assets

    if assets is None or liabilities is None or net_assets is None:
        return

    expected = liabilities + net_assets
    difference = abs(assets - expected)
    tolerance = max(abs(assets) * BALANCE_TOLERANCE_PCT, BALANCE_TOLERANCE_FLOOR)

    if difference <= tolerance:
        return

    off_by = (difference / abs(assets)) * 100 if assets else 0.0

    # Safe to correct only when assets and liabilities are both plausible and
    # the implied net assets is positive. Outside that, which value is wrong
    # is a guess.
    correctable = (
        2 < off_by <= 15
        and assets > 0
        and liabilities > 0
        and assets > liabilities
    )
    if correctable:
        corrected = assets - liabilities
        if corrected > 0:
            result.corrections.append(
                f"Total net assets corrected from {net_assets:,.0f} to "
                f"{corrected:,.0f} so the balance sheet balances "
                f"(was off by {off_by:.1f}%)."
            )
            fields.total_net_assets = corrected
            return

    result.warnings.append(
        f"Balance sheet does not balance: assets {assets:,.0f} against "
        f"liabilities {liabilities:,.0f} plus net assets {net_assets:,.0f} "
        f"= {expected:,.0f}, a difference of {difference:,.0f} ({off_by:.1f}%). "
        f"Which figure is wrong cannot be determined from the statement alone."
    )
    if off_by > 20:
        result.confidence_adjustment -= 5.0


def _check_income_statement(fields: ExtractedFields, result: ValidationResult) -> None:
    """Revenue - Expenses should land near Change in Net Assets."""
    revenue = fields.total_revenue
    expenses = fields.total_expenses or fields.total_operating_expenses
    change = fields.change_in_net_assets

    if revenue is None or expenses is None or change is None:
        return

    expected = revenue - expenses
    difference = abs(change - expected)
    tolerance = max(abs(revenue) * INCOME_TOLERANCE_PCT, INCOME_TOLERANCE_FLOOR)

    if difference <= tolerance:
        return

    off_by = (difference / max(abs(revenue), 1)) * 100
    result.warnings.append(
        f"Change in net assets ({change:,.0f}) does not follow from revenue "
        f"({revenue:,.0f}) less expenses ({expenses:,.0f}) = {expected:,.0f}, "
        f"a difference of {difference:,.0f} ({off_by:.1f}%). Non-operating "
        f"items explain some of this; a gap this size may mean a column was "
        f"read from the wrong period."
    )
    if off_by > 50:
        result.confidence_adjustment -= 5.0


def _check_signs(fields: ExtractedFields, result: ValidationResult) -> None:
    """Catch values whose sign makes them impossible."""
    for name, value in (
        ("total assets", fields.total_assets),
        ("total current assets", fields.total_current_assets),
        ("gross loans receivable", fields.gross_loans_receivable),
    ):
        if value is not None and value < 0:
            result.warnings.append(
                f"{name.capitalize()} came out negative ({value:,.0f}), which "
                f"is not a state a statement can be in. Most likely an "
                f"accounting-negative was read from the wrong column."
            )
            result.confidence_adjustment -= 10.0

    if (fields.loan_loss_allowance is not None
            and fields.gross_loans_receivable is not None
            and fields.gross_loans_receivable > 0
            and fields.loan_loss_allowance > fields.gross_loans_receivable):
        result.warnings.append(
            f"Loan loss allowance ({fields.loan_loss_allowance:,.0f}) exceeds "
            f"the gross portfolio ({fields.gross_loans_receivable:,.0f}). One "
            f"of the two was read from the wrong line."
        )
        result.confidence_adjustment -= 10.0


def _check_stated_against_derived(
    fields: ExtractedFields, stated: StatedRatios, result: ValidationResult
) -> None:
    """Compare the preparer's own ratios against what their numbers imply.

    This is the strongest check available, because it needs no outside
    reference. When the two disagree, either the extraction is wrong or the
    statement contradicts itself, and both are worth knowing about.
    """
    derived_net_asset = None
    if fields.total_net_assets is not None and fields.total_assets:
        derived_net_asset = fields.total_net_assets / fields.total_assets

    derived_current = None
    if fields.total_current_assets is not None and fields.total_current_liabilities:
        derived_current = fields.total_current_assets / fields.total_current_liabilities

    derived_reserve = None
    if fields.loan_loss_allowance is not None and fields.gross_loans_receivable:
        derived_reserve = fields.loan_loss_allowance / fields.gross_loans_receivable

    comparisons = (
        ("net asset ratio", stated.net_asset_ratio, derived_net_asset),
        ("current ratio", stated.current_ratio, derived_current),
        ("loan loss reserve ratio", stated.loan_loss_reserve_ratio, derived_reserve),
    )

    for name, claimed, derived in comparisons:
        if claimed is None or derived is None:
            continue
        if abs(claimed - derived) <= STATED_RATIO_TOLERANCE:
            continue
        result.warnings.append(
            f"The statement reports a {name} of {claimed:.3f}, but its own "
            f"figures work out to {derived:.3f}. Either a field was extracted "
            f"from the wrong line or the summary table disagrees with the "
            f"statements behind it."
        )
        result.confidence_adjustment -= 8.0


def _check_period_over_period(
    fields: ExtractedFields, prior: ExtractedFields, result: ValidationResult
) -> None:
    """Flag a total that moved further than a quarter plausibly allows.

    A balance sheet total doubling or halving between quarters is possible but
    unusual, and it is exactly what a misread column looks like.
    """
    for name, current, previous in (
        ("Total assets", fields.total_assets, prior.total_assets),
        ("Total liabilities", fields.total_liabilities, prior.total_liabilities),
        ("Gross loans receivable", fields.gross_loans_receivable,
         prior.gross_loans_receivable),
    ):
        if current is None or previous is None or previous == 0:
            continue
        change = (current - previous) / abs(previous)
        if abs(change) > 0.5:
            result.warnings.append(
                f"{name} moved {change * 100:+.0f}% from the prior period "
                f"({previous:,.0f} to {current:,.0f}). Verify against the "
                f"source before relying on it."
            )
            result.confidence_adjustment -= 3.0
