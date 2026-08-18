"""Command line entry point.

    statement-extract q3.pdf
    statement-extract q3.pdf --cross-check --json
    statement-extract *.pdf --quiet

Exit status is 0 when nothing needed review, 1 when something did. That makes
it usable as a gate in a pipeline rather than only for reading.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, read_statement
from .pdf import ocr_available


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="statement-extract",
        description="Pull figures out of financial statement PDFs and check them.",
    )
    parser.add_argument("paths", nargs="+", type=Path, help="PDF files to read")
    parser.add_argument(
        "--cross-check", action="store_true",
        help="OCR each balance-sheet page as well and compare the two readings",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--quiet", action="store_true", help="only report statements needing review",
    )
    parser.add_argument(
        "--current-column", type=int, default=-1, metavar="N",
        help="which value column is the current period in a summary table "
             "(default -1, the last)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cross_check and not ocr_available():
        print(
            "warning: --cross-check needs Tesseract, which was not found. Set "
            "TESSERACT_CMD or put it on PATH. Continuing without it.",
            file=sys.stderr,
        )

    reports = []
    for path in args.paths:
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            return 2
        reports.append(
            read_statement(path, cross_check=args.cross_check,
                           current_column=args.current_column)
        )

    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        for report in reports:
            if args.quiet and not report.needs_review:
                continue
            _print_report(report)

    return 1 if any(r.needs_review for r in reports) else 0


def _print_report(report) -> None:
    name = Path(report.source).name
    verdict = "needs review" if report.needs_review else "ok"
    print(f"\n{name}  —  {report.fields.found()} fields, "
          f"confidence {report.confidence:.0f}, {verdict}")

    for key, value in sorted(report.fields.to_dict().items()):
        print(f"    {key:32s} {value:>16,.2f}")

    if report.stated.any_found():
        print("  stated in the document:")
        for key, value in sorted(report.stated.to_dict().items()):
            print(f"    {key:32s} {value:>16.4f}")

    for note in report.reading.disagreements if report.reading else []:
        print(f"  [cross-check] {note}")
    for correction in report.validation.corrections:
        print(f"  [corrected]   {correction}")
    for warning in report.validation.warnings:
        print(f"  [review]      {warning}")


if __name__ == "__main__":
    raise SystemExit(main())
