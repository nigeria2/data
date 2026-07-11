"""
Parses every INEC result PDF in ../downloaded_source/house_2019_pdfs/ and
../downloaded_source/senate_2019_pdfs/ into two consolidated CSVs:
2019_house_inec.csv and 2019_senate_inec.csv.

Each PDF is one federal constituency (House) or senatorial district (Senate)
result sheet, laid out as:

    NAME OF FC: <name> CODE: <code>          (or "NAME OF SD:" for Senate)
    S/N  NAME OF CANDIDATE  GENDER  PARTY  VOTES RECEIVED  REMARK

pdfplumber's grid-based extract_tables() reproduces this reliably (verified
against several samples, including multi-page PDFs where the table
continues without repeating the header). Blank NAME/GENDER cells for some
candidates are a genuine feature of the source PDFs (minor candidates whose
names were never entered on the collation sheet), not an extraction bug --
left blank here rather than guessed at.

This is source-level extraction only: no cross-file aggregation, no
LGA/ward detail (these PDFs are already at the constituency/district level).

Usage:
    python extract_pdf_tables.py
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import pdfplumber

SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = SCRIPT_DIR.parent / "downloaded_source"

STATE_FIX = {"Akwa-Ibom": "Akwa Ibom", "Cross-River": "Cross River"}

# The header line varies across these PDFs: label is "FC"/"SD"/"SC" (SC is used
# inconsistently -- sometimes it really means Federal Constituency despite the
# label, so the CODE prefix, not the label word, is what's trusted for
# classifying a file's actual office. NAME and CODE also appear in either
# order, and some files drop "OF FC/SD/SC:" entirely.
CODE_RE = re.compile(r"\b(FC|SD|SC)[/-]\s*(\d+)[/-]\s*([A-Z]{1,4})\b", re.IGNORECASE)
# Matched against `flat` (newlines already collapsed to spaces), so a bare
# "till end of line" group like [^\n]+ is meaningless there -- every group
# below is bounded by an explicit lookahead for the next CODE/S-N marker (or
# end of string) so it can never run past the constituency name into the
# candidate table itself.
_NAME_END = r"(?=\s*(?:CODE\s*:|S\s*/\s*N\b|$))"
NAME_PATTERNS = [
    re.compile(r"NAME OF (?:FC|SD|SC)\s*:\s*(.+?)" + _NAME_END, re.IGNORECASE),
    re.compile(r"CODE\s*:\s*\S+\s*NAME OF (?:FC|SD|SC)\s*:\s*(.+?)" + _NAME_END, re.IGNORECASE),
    re.compile(r"\bNAME\s+(?:OF\s+(?:FC|SD|SC)\s*:?\s*)?([A-Z][A-Z0-9/&,.'\- ]+?)" + _NAME_END, re.IGNORECASE),
]

OFFICE_BY_CODE_PREFIX = {"FC": "house", "SD": "senate", "SC": "state_assembly"}


def state_from_filename(path: Path) -> str:
    prefix = path.stem.split("_", 1)[0]
    return STATE_FIX.get(prefix, prefix)


def name_from_filename(path: Path) -> str:
    rest = path.stem.split("_", 1)[1] if "_" in path.stem else path.stem
    rest = re.sub(r"-\d+$", "", rest)  # drop trailing de-dupe suffix like "-1"
    return rest.replace("-", "/").replace(".", " ").strip().title()


def to_votes(value: str) -> int | None:
    s = (value or "").replace(",", "").strip()
    if not s.isdigit():
        return None
    return int(s)


def extract_pdf(path: Path, expected_office: str) -> tuple[str, str, list[dict], str | None]:
    """Returns (name, code, rows, error). error is None on success; a non-None
    error means the file was skipped (either unparseable or genuinely the
    wrong office's data mislabeled on INEC's own site)."""
    with pdfplumber.open(path) as pdf:
        full_text = pdf.pages[0].extract_text() or ""
        flat = full_text.replace("\n", " ")

        code_m = CODE_RE.search(flat)
        code = f"{code_m.group(1).upper()}/{code_m.group(2)}/{code_m.group(3).upper()}" if code_m else ""
        code_prefix = code_m.group(1).upper() if code_m else ""
        office = OFFICE_BY_CODE_PREFIX.get(code_prefix)

        if office is not None and office != expected_office:
            return "", code, [], f"wrong office: code {code} looks like {office} data, not {expected_office}"

        name = ""
        for pattern in NAME_PATTERNS:
            m = pattern.search(flat)
            if m:
                candidate_name = m.group(1).strip().strip(":").strip()
                # A real constituency name is a few words; anything this long
                # means the lookahead boundary never found a CODE/S-N marker to
                # stop at (e.g. a PDF with a genuinely garbled/interleaved text
                # layer) and swallowed part of the candidate table instead.
                if 0 < len(candidate_name) <= 80:
                    name = candidate_name
                    break
        if not name:
            name = name_from_filename(path)

        if office is None and "REPRESENTATIVE" not in flat.upper() and expected_office == "house":
            return "", code, [], f"could not confirm office (no FC/SD/SC code, title doesn't say Representatives)"
        if office is None and "SENATORIAL" not in flat.upper() and "SENATE" not in flat.upper() and expected_office == "senate":
            return "", code, [], f"could not confirm office (no FC/SD/SC code, title doesn't mention Senate)"

        rows = []
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    cells = [(c or "").replace("\n", " ").strip() for c in row]
                    if len(cells) < 6:
                        cells += [""] * (6 - len(cells))
                    sn, candidate, gender, party, votes, remark = cells[:6]
                    if sn.strip().upper() in ("S/N", "") and not sn.strip().isdigit():
                        continue  # header row (shouldn't repeat, but just in case)
                    if not party and not candidate:
                        continue
                    rows.append({
                        "sn": sn.strip(),
                        "candidate": candidate or None,
                        "gender": gender or None,
                        "party": party or None,
                        "votes": to_votes(votes),
                        "remark": remark or None,
                    })
        return name, code, rows, None


def process_office(primary: Path, secondary: Path, office: str, issues: list[str]) -> list[dict]:
    """Parse every sheet for `office`: the whole primary folder, then any sheet
    mis-filed in the secondary folder whose code isn't already present. Only
    cross-folder duplicates are dropped -- same-folder files that happen to share
    a (mistyped) code are all kept, since they're distinct constituencies."""
    all_rows: list[dict] = []
    seen_codes: set[str] = set()

    def take(path: Path, allow_dupe: bool) -> None:
        state = state_from_filename(path)
        name, code, rows, error = extract_pdf(path, office)
        if error:
            issues.append(f"{path.name}: {error}")
            return
        if not rows:
            issues.append(f"{path.name}: parsed header ({name}) but found zero candidate rows")
            return
        if code and code in seen_codes and not allow_dupe:
            return  # already have this sheet from the primary folder
        if code:
            seen_codes.add(code)
        for r in rows:
            r["state"] = state
            r["constituency" if office == "house" else "district"] = name
            r["code"] = code
            r["source_file"] = path.name
            all_rows.append(r)

    for path in sorted(primary.glob("*.pdf")):
        take(path, allow_dupe=True)
    for path in sorted(secondary.glob("*.pdf")):
        take(path, allow_dupe=False)
    return all_rows


def write_csv(rows: list[dict], office: str, out_path: Path) -> None:
    area_field = "constituency" if office == "house" else "district"
    fieldnames = ["state", area_field, "code", "sn", "candidate", "gender", "party",
                  "votes", "remark", "source_file"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") if r.get(k) is not None else "" for k in fieldnames})


def main() -> int:
    issues: list[str] = []

    # Both folders contain a few sheets mis-filed under the wrong office (INEC's
    # own broken index links), so each office pass also scans the other folder
    # and picks up any correctly-coded sheet not already seen.
    house_dir = SOURCE_ROOT / "house_2019_pdfs"
    senate_dir = SOURCE_ROOT / "senate_2019_pdfs"

    house_rows = process_office(house_dir, senate_dir, "house", issues)
    write_csv(house_rows, "house", SCRIPT_DIR / "2019_house_inec.csv")
    print(f"Wrote 2019_house_inec.csv: {len(house_rows)} rows")

    senate_rows = process_office(senate_dir, house_dir, "senate", issues)
    write_csv(senate_rows, "senate", SCRIPT_DIR / "2019_senate_inec.csv")
    print(f"Wrote 2019_senate_inec.csv: {len(senate_rows)} rows")

    if issues:
        print(f"\n{len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")

    report_path = SCRIPT_DIR / "extract_pdf_report.md"
    lines = ["# INEC PDF extraction report", "",
             f"House: {len(house_rows)} candidate rows.",
             f"Senate: {len(senate_rows)} candidate rows.",
             f"{len(issues)} issue(s).", ""]
    if issues:
        lines.append("## Issues")
        lines.extend(f"- {issue}" for issue in issues)
    (SCRIPT_DIR / "extract_pdf_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
