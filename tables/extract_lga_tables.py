"""
Reads every cleaned Wikipedia HTML file in ../downloaded_source/ and extracts
LGA-level candidate results (where Wikipedia has them filled in) into
2019_governor_lga.csv, 2023_governor_lga.csv, and 2023_presidential_lga.csv.

There is no 2019_presidential_lga.csv: 2019 presidential results only exist as
a single national article on Wikipedia (no per-state pages, so no LGA tables
either -- see the note in ../2019_election_urls.md).

Senate is skipped: a senatorial district is a strict subset of a state's
LGAs, and Wikipedia's Senate articles for both years were found (in
extract_tables.py) to rarely carry vote counts at all, let alone broken down
further by LGA.

Many Wikipedia LGA-breakdown tables -- especially for 2023 governor races --
are unfilled "TBD" template placeholders; those are skipped and reported
rather than silently producing empty/garbage rows.

Usage:
    python extract_lga_tables.py
"""

from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

import pandas as pd

from extract_tables import classify, contains_tbd, flatten_col, to_number

SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_DIR = SCRIPT_DIR.parent / "downloaded_source"

LGA_COLUMN_LABELS = ("LGA", "Local government area")
NON_CANDIDATE_BLOCKS = ("Total valid votes", "Turnout Percentage", "Turnout (%)", "Turnout")
FOOTNOTE_RE = re.compile(r"\[[^\]]*\]")


def find_lga_table(tables: list[pd.DataFrame]) -> pd.DataFrame | None:
    for t in tables:
        cols = [flatten_col(c) for c in t.columns]
        if cols and cols[0] in LGA_COLUMN_LABELS:
            return t
    return None


def extract_lga_rows(table: pd.DataFrame) -> list[dict]:
    lga_col = table.columns[0]

    candidate_blocks: dict[str, dict[str, object]] = {}
    for c in table.columns:
        if not isinstance(c, tuple) or len(c) < 2:
            continue
        block = c[0]
        metric = c[-1]
        if block in NON_CANDIDATE_BLOCKS or str(block).startswith("Unnamed") or block == flatten_col(lga_col):
            continue
        candidate_blocks.setdefault(block, {})[metric] = c

    rows = []
    for _, r in table.iterrows():
        lga = r.get(lga_col)
        if lga is None or str(lga).strip().lower() in ("nan", "total", "totals", ""):
            continue
        lga_name = FOOTNOTE_RE.sub("", str(lga)).strip()

        for block, metrics in candidate_blocks.items():
            votes_col = metrics.get("Votes") or metrics.get("#")
            pct_col = metrics.get("Percentage") or metrics.get("%")
            if votes_col is None:
                continue
            votes = to_number(r.get(votes_col))
            if votes is None:
                continue
            if block == "Others":
                candidate, party = "Other candidates", None
            else:
                parts = str(block).split()
                candidate, party = " ".join(parts[:-1]), parts[-1]
            rows.append({
                "lga": lga_name,
                "candidate": candidate,
                "party": party,
                "votes": votes,
                "percent": to_number(r.get(pct_col)) if pct_col is not None else None,
            })
    return rows


def process_file(path: Path, state: str, issues: list[str]) -> list[dict]:
    try:
        tables = pd.read_html(io.StringIO(path.read_text(encoding="utf-8")))
    except ValueError:
        tables = []

    table = find_lga_table(tables)
    if table is None:
        issues.append(f"{path.name}: no LGA-level breakdown table on this page")
        return []
    if contains_tbd(table):
        issues.append(f"{path.name}: LGA breakdown table exists but is unfilled ('TBD' placeholders)")
        return []

    rows = extract_lga_rows(table)
    if not rows:
        issues.append(f"{path.name}: LGA table found but no usable rows extracted")
        return []

    for r in rows:
        r["state"] = state
    return rows


def main() -> int:
    files = sorted(SOURCE_DIR.glob("*.html"))
    buckets: dict[tuple[str, str], list[dict]] = {}
    issues: list[str] = []
    skipped_national_or_senate = 0

    for path in files:
        classified = classify(path.stem)
        if classified is None:
            continue
        office, year, state = classified
        if office == "senate" or state == "__NATIONAL__":
            skipped_national_or_senate += 1
            continue
        rows = process_file(path, state, issues)
        buckets.setdefault((office, year), []).extend(rows)

    for (office, year), rows in sorted(buckets.items()):
        out_path = SCRIPT_DIR / f"{year}_{office}_lga.csv"
        fieldnames = ["state", "lga", "candidate", "party", "votes", "percent"]
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in fieldnames})
        states_covered = len({r["state"] for r in rows})
        print(f"Wrote {out_path.name}: {len(rows)} rows across {states_covered} states")

    if issues:
        print(f"\n{len(issues)} file(s) had no usable LGA data:")
        for issue in issues:
            print(f"  - {issue}")

    report_path = SCRIPT_DIR / "extract_lga_report.md"
    lines = ["# LGA-level extraction report", ""]
    lines.append(f"{skipped_national_or_senate} senate/national files skipped (out of scope).")
    lines.append(f"{len(issues)} file(s) had no usable LGA data.")
    lines.append("")
    lines.append("## Files with no usable LGA data")
    lines.extend(f"- {issue}" for issue in issues)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
