# statement-extract

[![tests](https://github.com/Ark2027/statement-extract/actions/workflows/tests.yml/badge.svg)](https://github.com/Ark2027/statement-extract/actions/workflows/tests.yml)

Pulls figures out of financial statement PDFs, then checks them against the identities they have to satisfy before handing them back.

One dependency. `pdfplumber` for reading PDFs, and nothing else unless you want OCR.

## The problem this exists for

A parser that crashes gets fixed. A parser that returns `2026` where the total assets should be does not, because `2026` is a number, it lands in a numeric field, and every report downstream renders it without complaint.

That is the normal failure mode here, and these are the ones that actually showed up:

| The line | A naive read gives you | Why |
|---|---|---|
| `2026 Total assets 41,829,331` | `2026` | The period label is the leftmost number |
| `Less reserve $ ( 1,614,185 )` | `0` | `$ ` matched before the parenthesized amount |
| `$ 6 ,873,260` | `6` | Extraction dropped a space into the middle |
| `9,946,968,55` | `994696855` | OCR read the decimal point as a comma |
| `Total other assets 3,142,890` | assigned to `total_assets` | It contains the string "total assets" |

Every one produces a plausible number. None raises anything.

## What it does about it

```bash
pip install statement-extract
statement-extract q3.pdf
```

```
q3.pdf  —  11 fields, confidence 90, needs review
    change_in_net_assets                 1,063,867.00
    gross_loans_receivable              28,283,707.00
    loan_loss_allowance                  1,614,185.00
    total_assets                        41,829,331.00
    total_liabilities                   18,823,199.00
    total_net_assets                    23,006,132.00
  stated in the document:
    net_asset_ratio                            0.3180
  [review]      The statement reports a net asset ratio of 0.318, but its own
                figures work out to 0.550. Either a field was extracted from
                the wrong line or the summary table disagrees with the
                statements behind it.
```

It exits non-zero when anything needs review, so it works as a gate and not just as a reader.

```python
from statement_extract import read_statement

report = read_statement("q3.pdf", cross_check=True)
report.fields.total_assets     # 41829331.0
report.needs_review            # True
report.validation.warnings     # ['The statement reports a net asset ratio of ...']
```

## Three checks that need nothing but the document

**The balance sheet has to balance.** Assets equal liabilities plus net assets, by construction, so any gap is an extraction error. When assets and liabilities are both sound and net assets is the only value that can be wrong, the fix is arithmetic and gets applied. When two of the three could be the culprit, correcting one would trade a visible problem for an invisible one, so it warns and leaves the numbers alone.

```
[corrected]  Total net assets corrected from 21,204,880 to 23,006,132
             so the balance sheet balances (was off by 4.3%).

[review]     Balance sheet does not balance: assets 18,204,000 against
             liabilities 30,918,447 plus net assets 23,006,132 = 53,924,579.
             Which figure is wrong cannot be determined from the statement alone.
```

**Where the statement states its own ratios, they have to match its own numbers.** Preparers often attach a covenant summary with the ratios already worked out. Recomputing those from the balance sheet and comparing is the strongest check available, because it needs no outside reference — and when they disagree, either the extraction is wrong or the document contradicts itself. Both are worth a look.

Reading that table is harder than it looks. The threshold text contains numbers:

```
Total Net Asset Ratio     52.4%     55.0%     should be > 25%
```

Grab every number and you get 52.4, 55.0 and 25. Take the last and you have read the threshold instead of the value. Each line is truncated at the word introducing the threshold before any number is read.

**Two independent readings of the same page should agree.** On a digital PDF, `--cross-check` OCRs the page as well and compares the key totals. Native extraction and OCR fail in unrelated ways, so agreement is real evidence and disagreement tells you which page to look at:

```
[cross-check] page 2: 'total assets' reads 41,829,331 from the PDF text
              but 41,329,331 from OCR (1.2% apart)
```

This costs a render and an OCR pass per page, so it is off by default.

## Missing is not zero

A field that could not be found stays `None`. It never becomes `0.0`, because on a report those look identical and mean opposite things — one is an organization with nothing in that account, the other is an organization nobody has data for.

The same rule applies further down. A currency symbol with no digits after it used to parse as zero, which silently zeroed a real balance. It returns `None` now, and there is a test named after it.

The one place a dash does mean zero is when it sits in an amount column, and that only holds once a token has been isolated as the amount. On a whole line, `Deferred revenue -` is ambiguous — that could be an amount column or a hyphenated label — so it declines to guess.

## Confidence

Every report carries a 0–100 score that drops for OCR use, missing fields, cross-check disagreements, and failed validation. It is deliberately crude, and it is for ranking a stack of documents by which to read first. It is not a probability of correctness and the code says so where it is calculated.

`needs_review` is the number to actually branch on.

## Installing

```bash
pip install statement-extract
```

OCR is optional and needs the Tesseract binary, which is not a Python package:

```bash
pip install "statement-extract[ocr]"
export TESSERACT_CMD=/path/to/tesseract   # only if it isn't on PATH
```

Without it, digital PDFs work normally and `--cross-check` warns that it is doing nothing.

## Tests

```bash
python tests/test_money.py
python tests/test_pdf.py
python tests/test_pipeline.py
```

62 tests, standard library only. Most of them are regressions — each one is a case that returned a wrong number rather than an error.

The fixtures are six PDFs generated by `tools/make_fixtures.py`, each aimed at one behavior: a clean statement, one off by an amount that is safe to correct, one off by too much, one that states ratios agreeing with its figures, one that states ratios that don't, and one carrying every extraction artifact at once. A test regenerates them and fails if the committed files have drifted.

They are written by hand rather than with a PDF library — a catalog, a page tree, one content stream of positioned text, an xref table. That keeps the package at a single dependency, and it means the fixtures exercise a real PDF rather than a mock.

## What I'd change

**The alias table is a list, not a model.** Matching labels by substring with a negative-filter escape hatch works, and it is auditable in a way a classifier would not be, but every new statement format is a new entry. Somewhere past a few hundred formats that stops scaling and the answer is probably a learned matcher with this table as its training set and its fallback.

**Column selection is a parameter, not a detection.** Which value column holds the current period is passed in, defaulting to the last. Detecting it from the column headers is doable and I didn't do it, so getting it wrong produces a number that looks entirely reasonable.

**The cross-check only compares totals.** It checks seven labels that feed a validation identity. A misread on any other line passes through, which is the right trade at one OCR pass per page but is worth knowing.

**No multi-page table handling.** A statement that continues a table across a page break is read as two unrelated pages. None of the documents this was built for did that.

## License

MIT
