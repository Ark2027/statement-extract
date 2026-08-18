"""Build the statement PDFs the tests run against.

    python tools/make_fixtures.py

Everything here is invented. The organizations do not exist and the figures
are chosen to exercise particular parsing and validation paths, not to
resemble any real entity's books.

The PDFs are written by hand rather than with a PDF library. That keeps the
package dependency-free for anyone who only wants the parsing functions, and
a text PDF is a small enough format to emit directly: a catalog, a page tree,
one content stream of positioned text, and an xref table.

Six fixtures, each aimed at something specific:

    clean.pdf              everything present and consistent
    unbalanced.pdf         net assets off by an amount that is safe to correct
    ambiguous.pdf          off by too much to correct safely, must warn
    summary_table.pdf      states its own ratios, which agree with the figures
    contradictory.pdf      states its own ratios, which do not agree
    artifacts.pdf          spaces inside numbers, OCR-style comma decimals
"""

from __future__ import annotations

import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures"

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
LEFT = 62
COL_CURRENT = 372
COL_PRIOR = 468
COL_NOTE = 372


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def write_pdf(path: Path, placements: list[tuple[float, float, int, str]]) -> None:
    """Emit a one-page PDF. Placements are (x, y, size, text), origin bottom-left."""
    stream = ["BT"]
    for x, y, size, text in placements:
        stream.append(f"/F1 {size} Tf")
        stream.append(f"1 0 0 1 {x} {y} Tm")
        stream.append(f"({_escape(text)}) Tj")
    stream.append("ET")
    content = "\n".join(stream).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ).encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")

    xref_at = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode())
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets:
        buffer.write(f"{offset:010d} 00000 n \n".encode())
    buffer.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n".encode()
    )

    path.write_bytes(buffer.getvalue())


class Sheet:
    """Lays out lines down a page so the fixtures stay readable to write."""

    def __init__(self, title: str, subtitle: str):
        self.rows: list[tuple[float, float, int, str]] = []
        self.y = 726.0
        self.line(title, size=13)
        self.line(subtitle, size=10)
        self.gap(8)

    def line(self, text: str, size: int = 9, x: float = LEFT) -> None:
        self.rows.append((x, self.y, size, text))
        self.y -= size + 5

    def gap(self, points: float = 10) -> None:
        self.y -= points

    def heading(self, text: str) -> None:
        self.gap(6)
        self.line(text, size=10)

    def amounts(self, label: str, current: str, prior: str | None = None) -> None:
        """A label with one or two right-hand value columns."""
        y = self.y
        self.rows.append((LEFT, y, 9, label))
        self.rows.append((COL_CURRENT, y, 9, current))
        if prior is not None:
            self.rows.append((COL_PRIOR, y, 9, prior))
        self.y -= 14

    def covenant(self, label: str, prior: str, current: str, threshold: str) -> None:
        """A summary-table row: label, prior, current, then threshold prose."""
        y = self.y
        self.rows.append((LEFT, y, 9, label))
        self.rows.append((248, y, 9, prior))
        self.rows.append((330, y, 9, current))
        self.rows.append((410, y, 9, threshold))
        self.y -= 14

    def save(self, name: str) -> Path:
        path = OUT / name
        write_pdf(path, self.rows)
        return path


def _balance_sheet(title: str, assets: str, liabilities: str, net_assets: str,
                   current_assets: str = "4,182,933",
                   current_liabilities: str = "3,346,346") -> Sheet:
    sheet = Sheet(title, "Statement of Financial Position")
    sheet.rows.append((COL_CURRENT, sheet.y + 4, 8, "Sep 30, 2026"))
    sheet.rows.append((COL_PRIOR, sheet.y + 4, 8, "Sep 30, 2025"))
    sheet.gap(12)

    sheet.heading("Assets")
    sheet.amounts("Cash and cash equivalents", "$ 2,914,220", "$ 2,655,180")
    sheet.amounts("Operating cash", "1,486,905", "1,301,774")
    sheet.amounts("Grants receivable", "781,808", "947,156")
    sheet.amounts("Total current assets", f"$ {current_assets}", "$ 3,904,110")
    sheet.amounts("Loans receivable, current portion", "6,873,260", "6,204,881")
    sheet.amounts("Loans receivable, net of current portion", "21,410,447", "20,338,904")
    sheet.amounts("Less reserve for possible loan losses", "(1,614,185)", "(1,470,220)")
    sheet.amounts("Property and equipment, net", "1,208,441", "1,265,933")
    sheet.amounts("Total other assets", "3,142,890", "2,908,112")
    sheet.amounts("Total assets", f"$ {assets}", "$ 39,041,100")

    sheet.heading("Liabilities and Net Assets")
    sheet.amounts("Accounts payable", "412,006", "388,441")
    sheet.amounts("Accrued expenses", "690,340", "701,922")
    sheet.amounts("Total current liabilities", f"{current_liabilities}", "3,123,288")
    sheet.amounts("Notes payable", "15,476,853", "14,445,207")
    sheet.amounts("Total liabilities", f"$ {liabilities}", "$ 17,568,495")
    sheet.amounts("Net assets without donor restrictions", "18,204,918", "17,006,441")
    sheet.amounts("Net assets with donor restrictions", "4,801,214", "4,466,164")
    sheet.amounts("Total net assets", f"{net_assets}", "21,472,605")
    sheet.amounts("Total liabilities and net assets", f"$ {assets}", "$ 39,041,100")
    return sheet


def _income_lines(sheet: Sheet, revenue: str, expenses: str, change: str) -> None:
    sheet.heading("Statement of Activities")
    sheet.amounts("Contributions and grants", "4,918,220", "4,455,180")
    sheet.amounts("Interest income on loans", "2,140,663", "1,988,204")
    sheet.amounts("Total revenue", f"$ {revenue}", "$ 6,443,384")
    sheet.amounts("Program expenses", "4,806,112", "4,442,908")
    sheet.amounts("Management and general", "1,188,904", "1,102,441")
    sheet.amounts("Total operating expenditures", f"$ {expenses}", "$ 5,545,349")
    sheet.amounts("Change in net assets", f"{change}", "898,035")


def build_clean() -> Path:
    """Balanced, complete, no surprises. 41,829,331 = 18,823,199 + 23,006,132."""
    sheet = _balance_sheet(
        "Cardinal Community Capital",
        assets="41,829,331", liabilities="18,823,199", net_assets="23,006,132",
    )
    _income_lines(sheet, revenue="7,058,883", expenses="5,995,016", change="1,063,867")
    return sheet.save("clean.pdf")


def build_unbalanced() -> Path:
    """Net assets understated by about 8% — inside the safe-correction band."""
    sheet = _balance_sheet(
        "Harbor Point Fund",
        assets="41,829,331", liabilities="18,823,199", net_assets="21,204,880",
    )
    _income_lines(sheet, revenue="7,058,883", expenses="5,995,016", change="1,063,867")
    return sheet.save("unbalanced.pdf")


def build_ambiguous() -> Path:
    """Off by roughly 45%, and liabilities exceed assets. Not safely correctable."""
    sheet = _balance_sheet(
        "Ridgeline Development Finance",
        assets="18,204,000", liabilities="30,918,447", net_assets="23,006,132",
    )
    _income_lines(sheet, revenue="7,058,883", expenses="5,995,016", change="1,063,867")
    return sheet.save("ambiguous.pdf")


def build_summary_table() -> Path:
    """Includes a covenant summary whose ratios agree with the statements.

    Net assets / assets = 23,006,132 / 41,829,331 = 0.5500
    Current ratio       =  4,182,933 /  3,346,346 = 1.25
    Reserve / portfolio =  1,614,185 / 28,283,707 = 0.0571
    """
    sheet = _balance_sheet(
        "Northgate Community Lenders",
        assets="41,829,331", liabilities="18,823,199", net_assets="23,006,132",
    )
    _income_lines(sheet, revenue="7,058,883", expenses="5,995,016", change="1,063,867")
    sheet.gap(10)
    sheet.line("Covenants and Ratios", size=10)
    sheet.covenant("Total Net Asset Ratio", "52.4%", "55.0%", "should be > 25%")
    sheet.covenant("Current Ratio", "1.19", "1.25", "must be > 1.00")
    sheet.covenant("Operating Liquidity", "1.5", "1.7", "must be > 1")
    sheet.covenant("Allowance for Loan Losses", "5.4%", "5.7%", "must be > 4%")
    sheet.covenant("Change in Net Assets", "898,035", "1,063,867", "YTD must be positive")
    return sheet.save("summary_table.pdf")


def build_contradictory() -> Path:
    """States a net asset ratio of 31.8% while its figures say 55.0%."""
    sheet = _balance_sheet(
        "Blue Mesa Capital",
        assets="41,829,331", liabilities="18,823,199", net_assets="23,006,132",
    )
    _income_lines(sheet, revenue="7,058,883", expenses="5,995,016", change="1,063,867")
    sheet.gap(10)
    sheet.line("Covenants and Ratios", size=10)
    sheet.covenant("Total Net Asset Ratio", "30.2%", "31.8%", "should be > 25%")
    sheet.covenant("Current Ratio", "1.19", "1.25", "must be > 1.00")
    sheet.covenant("Allowance for Loan Losses", "5.4%", "5.7%", "must be > 4%")
    return sheet.save("contradictory.pdf")


def build_artifacts() -> Path:
    """Carries the extraction damage the parser is meant to survive.

    A space inside a number, an OCR comma standing in for a decimal point, a
    currency symbol outside the parentheses on a negative, and a year sitting
    to the left of the value it labels.
    """
    sheet = Sheet("Trailhead Business Capital", "Statement of Financial Position")
    sheet.gap(10)
    sheet.heading("Assets")
    sheet.amounts("Total current assets", "$ 4 ,182,933")
    sheet.amounts("Loans receivable, current portion", "6,873,260")
    sheet.amounts("Loans receivable, net of current portion", "21,410,447")
    sheet.amounts("Less reserve for possible loan losses", "$ ( 1,614,185 )")
    sheet.amounts("2026 Total assets", "41,829,331")
    sheet.heading("Liabilities and Net Assets")
    sheet.amounts("Total current liabilities", "3,346,346")
    sheet.amounts("Total liabilities", "18,823,199")
    sheet.amounts("Total net assets", "23,006,131,55")
    sheet.heading("Statement of Activities")
    sheet.amounts("Total revenue", "7,058,883")
    sheet.amounts("Total operating expenditures", "5,995,016")
    sheet.amounts("Change in net assets", "(180,585)")
    return sheet.save("artifacts.pdf")


BUILDERS = (
    build_clean,
    build_unbalanced,
    build_ambiguous,
    build_summary_table,
    build_contradictory,
    build_artifacts,
)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for builder in BUILDERS:
        path = builder()
        print(f"  {path.relative_to(ROOT)}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
