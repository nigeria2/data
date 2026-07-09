"""
Reads every cleaned Wikipedia HTML file in ../downloaded_source/ and extracts
the core election results (candidate, party, votes, %) into consolidated CSVs
here, one per office+year: 2019_governor.csv, 2019_senate.csv,
2019_presidential.csv, 2023_governor.csv, 2023_senate.csv, 2023_presidential.csv.

Wikipedia's election articles are inconsistent about *where* the real,
filled-in state-level totals live:
  - Most 2019 articles have one clean "Party/Candidate/Votes/%" results table.
  - Many 2023 articles instead mix in party-primary tables (single party per
    table) and an empty "candidates" roster (no votes at all), with the real
    totals sitting in a "Totals" row of a wide by-senatorial-district/LGA
    breakdown table -- which itself is sometimes left as unfilled "TBD"
    placeholders by whoever set up the template.

So each state/office is resolved through a priority chain, cheapest and most
reliable first:
  1. A wide geographic-breakdown table's "Totals" row (richest: every major
     candidate, real vote counts).
  2. A direct results table, but only if it has >1 distinct party (excludes
     single-party primary tables) and a plausible vote total.
  3. The infobox (Nominee/Party/Popular vote or Percentage) -- always present,
     but usually only the top 2-3 candidates.
  4. For Senate only, the district/elected-senator summary table (winner name
     + party, no vote counts) when nothing richer is available.

Every row is tagged with which tier produced it (`source` column) so lower
confidence rows (infobox/winner_only) are easy to spot or filter out later.

This does NOT extract LGA/ward/federal-constituency-level breakdowns, opinion
polls, or party primaries -- only the top-line result per state (per
senatorial district for Senate).

Usage:
    python extract_tables.py
"""

from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_DIR = SCRIPT_DIR.parent / "downloaded_source"

NON_CANDIDATE_LABELS = re.compile(
    r"total|valid votes|invalid|turnout|registered|hold$|gain$|majority|source:",
    re.IGNORECASE,
)

MIN_PLAUSIBLE_VOTE_SUM = 1000

BREAKDOWN_LEVELS = ["Senatorial District", "Federal Constituency", "LGA", "Local government area"]

FILENAME_PATTERNS = [
    ("governor", re.compile(r"^(\d{4})_(.+)_State_gubernatorial_election$")),
    ("senate", re.compile(r"^(\d{4})_Nigerian_Senate_elections_in_(.+)_State$")),
    ("senate", re.compile(r"^(\d{4})_Nigerian_Senate_election_in_the_Federal_Capital_Territory$")),
    ("presidential", re.compile(r"^(\d{4})_Nigerian_presidential_election_in_(.+)_State$")),
    ("presidential", re.compile(r"^(\d{4})_Nigerian_presidential_election_in_the_Federal_Capital_Territory$")),
]


def classify(stem: str) -> tuple[str, str, str] | None:
    """Returns (office, year, state) for a downloaded_source filename stem."""
    if stem == "2019_Nigerian_general_election":
        return ("presidential", "2019", "__NATIONAL__")
    for office, pattern in FILENAME_PATTERNS:
        m = pattern.match(stem)
        if not m:
            continue
        if "Federal_Capital_Territory" in stem:
            year = m.group(1)
            return (office, year, "FCT")
        year, state = m.groups()
        return (office, year, state.replace("_", " "))
    return None


def to_number(value) -> float | None:
    if value is None:
        return None
    s = str(value).replace(",", "").replace("%", "").strip()
    if s == "" or s.lower() in ("nan", "tbd", "unknown"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def flatten_col(col) -> str:
    return str(col[0] if isinstance(col, tuple) else col)


def contains_tbd(table: pd.DataFrame) -> bool:
    return table.astype(str).apply(lambda s: s.str.contains("TBD", na=False)).to_numpy().any()


def is_real_candidate_row(row: dict) -> bool:
    candidate = str(row.get("candidate") or "")
    if not candidate or candidate.lower() == "nan":
        return False
    if NON_CANDIDATE_LABELS.search(candidate):
        return False
    return row.get("votes") is not None


def find_result_tables(tables: list[pd.DataFrame]) -> list[pd.DataFrame]:
    """Tables whose columns look like a candidate/party/votes result table,
    excluding LGA/ward/zone/district/constituency breakdowns."""
    found = []
    exclude_tokens = ("lga", "ward", "zone", "constituency", "senatorial district",
                       "local government")
    for t in tables:
        cols = [str(c[0] if isinstance(c, tuple) else c).lower() for c in t.columns]
        joined = " ".join(cols)
        if any(tok in joined for tok in exclude_tokens):
            continue
        if "candidate" in joined and "votes" in joined and "party" in joined:
            found.append(t)
    return found


def extract_result_rows(table: pd.DataFrame) -> list[dict]:
    cols = {flatten_col(c).lower(): c for c in table.columns}
    party_col = cols.get("party.1", cols.get("party"))
    candidate_col = cols.get("candidate.1", cols.get("candidate"))
    votes_col = cols.get("votes")
    pct_col = cols.get("%")

    rows = []
    for _, r in table.iterrows():
        candidate = r.get(candidate_col) if candidate_col is not None else None
        # "Candidate" (first, unsuffixed) sometimes holds the row *label*
        # ("Total", "Valid votes"...) instead of a name; prefer it when present
        # and non-empty, since real candidate rows leave it blank (NaN).
        label_col = cols.get("candidate") if candidate_col != cols.get("candidate") else None
        label = r.get(label_col) if label_col is not None else None
        if label is not None and str(label).strip().lower() not in ("nan", ""):
            candidate = label

        row = {
            "candidate": None if candidate is None or str(candidate).lower() == "nan" else str(candidate).strip(),
            "party": None if party_col is None or str(r.get(party_col)).lower() == "nan" else str(r.get(party_col)).strip(),
            "votes": to_number(r.get(votes_col)) if votes_col is not None else None,
            "percent": to_number(r.get(pct_col)) if pct_col is not None else None,
        }
        if is_real_candidate_row(row):
            rows.append(row)
    return rows


def best_results_table(tables: list[pd.DataFrame]) -> list[dict]:
    """Tier 2: a direct results table with >1 distinct party and a plausible
    vote total (excludes single-party primaries and empty candidate rosters)."""
    candidates = []
    for t in find_result_tables(tables):
        if contains_tbd(t):
            continue
        rows = extract_result_rows(t)
        parties = {r["party"] for r in rows if r["party"]}
        vote_sum = sum(r["votes"] or 0 for r in rows)
        if len(parties) >= 2 and vote_sum >= MIN_PLAUSIBLE_VOTE_SUM:
            candidates.append((vote_sum, rows))
    if not candidates:
        return []
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def find_breakdown_totals_row(tables: list[pd.DataFrame]) -> list[dict]:
    """Tier 1: melt a wide geographic-breakdown table's 'Totals' row into
    per-candidate rows, trying the coarsest available level first."""
    for level in BREAKDOWN_LEVELS:
        for t in tables:
            cols = [flatten_col(c) for c in t.columns]
            if not cols or cols[0] != level:
                continue

            total_row = None
            first_col = t.columns[0]
            for _, r in t.iterrows():
                label = str(r.get(first_col)).strip().lower()
                if label in ("totals", "total"):
                    total_row = r
            if total_row is None:
                continue

            candidate_blocks: dict[str, dict[str, object]] = {}
            for c in t.columns:
                if not isinstance(c, tuple) or len(c) < 2:
                    continue
                block = c[0]
                metric = c[-1]
                if block in (level, "Total valid votes", "Turnout Percentage") or str(block).startswith("Unnamed"):
                    continue
                candidate_blocks.setdefault(block, {})[metric] = c

            rows = []
            for block, metrics in candidate_blocks.items():
                votes_col = metrics.get("Votes")
                pct_col = metrics.get("Percentage") or metrics.get("%")
                if votes_col is None:
                    continue
                votes = to_number(total_row.get(votes_col))
                if votes is None:
                    continue
                if block == "Others":
                    candidate, party = "Other candidates", None
                else:
                    parts = str(block).split()
                    candidate, party = " ".join(parts[:-1]), parts[-1]
                rows.append({
                    "candidate": candidate,
                    "party": party,
                    "votes": votes,
                    "percent": to_number(total_row.get(pct_col)) if pct_col is not None else None,
                })
            if rows:
                return rows
    return []


def extract_infobox_fallback(soup: BeautifulSoup) -> list[dict]:
    """Tier 3: pull Nominee/Party/Popular-vote-or-Percentage rows straight out
    of the infobox. Always available, but usually top 2-3 candidates only."""
    for table in soup.find_all("table"):
        labels = [c.get_text(strip=True) for tr in table.find_all("tr") for c in [tr.find(["th", "td"])] if c]
        if "Nominee" not in labels:
            continue
        data = {}
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(strip=True)
            values = [c.get_text(" ", strip=True) for c in cells[1:]]
            if label in ("Nominee", "Party", "Popular vote", "Percentage"):
                data[label] = values
        if "Nominee" not in data or "Party" not in data:
            continue
        names = data["Nominee"]
        parties = data["Party"]
        votes = data.get("Popular vote", [])
        pcts = data.get("Percentage", [])
        rows = []
        for i, name in enumerate(names):
            rows.append({
                "candidate": name,
                "party": parties[i] if i < len(parties) else None,
                "votes": to_number(votes[i]) if i < len(votes) else None,
                "percent": to_number(pcts[i]) if i < len(pcts) else None,
            })
        return rows
    return []


CANDIDATE_CELL_RE = re.compile(r"([A-Za-z][A-Za-z .'\-]*?)\s*\(([A-Z]{2,6})\)")


def extract_senate_district_winners(tables: list[pd.DataFrame]) -> list[dict]:
    """Tier 4 (Senate only): District summary table -- no vote counts, but
    always present. Handles both the 2019 template (District/Elected Senator/
    Party columns) and the 2023 template (District/Results-Candidates, a
    single cell listing every candidate with a winner marker)."""
    for t in tables:
        cols = [flatten_col(c) for c in t.columns]
        if "District" not in cols:
            continue
        district_col = next(c for c in t.columns if flatten_col(c) == "District")

        if "Elected Senator" in cols:
            senator_col = next(c for c in t.columns if flatten_col(c) == "Elected Senator")
            party_cols = [c for c in t.columns if flatten_col(c) == "Party"]
            party_col = party_cols[-1] if party_cols else None
            rows = []
            for _, r in t.iterrows():
                district = r.get(district_col)
                senator = r.get(senator_col)
                if district is None or str(district).lower() == "nan":
                    continue
                rows.append({
                    "district": str(district).strip(),
                    "candidate": str(senator).strip() if senator is not None else None,
                    "party": str(r.get(party_col)).strip() if party_col is not None else None,
                    "votes": None,
                    "percent": None,
                })
            if rows:
                return rows

        def col_last(c):
            return str(c[-1] if isinstance(c, tuple) else c)

        candidates_cols = [c for c in t.columns if col_last(c) == "Candidates"]
        if candidates_cols:
            candidates_col = candidates_cols[-1]
            rows = []
            for _, r in t.iterrows():
                district = r.get(district_col)
                cell = r.get(candidates_col)
                if district is None or str(district).lower() == "nan" or cell is None:
                    continue
                for name, party in CANDIDATE_CELL_RE.findall(str(cell)):
                    name = re.sub(r"^Y\s+", "", name.strip())
                    rows.append({
                        "district": str(district).strip(),
                        "candidate": name,
                        "party": party,
                        "votes": None,
                        "percent": None,
                    })
            if rows:
                return rows
    return []


def extract_senate_districts(tables: list[pd.DataFrame]) -> list[str]:
    for t in tables:
        for col in t.columns:
            if flatten_col(col) == "District":
                series = t[col]
                if isinstance(series, pd.DataFrame):
                    series = series.iloc[:, 0]
                return [str(v) for v in series.tolist()]
    return []


def extract_presidential_2019_national(soup: BeautifulSoup) -> list[dict]:
    tables = pd.read_html(io.StringIO(str(soup)))
    state_table = None
    for t in tables:
        cols = [flatten_col(c) for c in t.columns]
        if cols and cols[0] == "State" and any("Votes" in flatten_col(c) or (
                isinstance(c, tuple) and len(c) > 2 and c[2] == "Votes") for c in t.columns):
            state_table = t
            break
    if state_table is None:
        return []

    candidate_blocks = {}
    for c in state_table.columns:
        if not isinstance(c, tuple) or len(c) < 3:
            continue
        block, _, metric = c
        if block in ("Margin", "Total valid votes") or str(block).startswith("Unnamed"):
            continue
        candidate_blocks.setdefault(block, {})[metric] = c

    rows = []
    for _, r in state_table.iterrows():
        state = r.get(("State", "State", "State"))
        if state is None or str(state).lower() in ("nan", "total"):
            continue
        for block, metrics in candidate_blocks.items():
            votes_col = metrics.get("Votes")
            pct_col = metrics.get("%")
            if votes_col is None:
                continue
            votes = to_number(r.get(votes_col))
            if votes is None:
                continue
            *name_parts, party = str(block).split()
            rows.append({
                "state": str(state).strip(),
                "candidate": " ".join(name_parts) if name_parts else str(block),
                "party": party if name_parts else None,
                "votes": votes,
                "percent": to_number(r.get(pct_col)) if pct_col is not None else None,
                "source": "national_by_state_table",
            })
    return rows


def process_file(path: Path, office: str, year: str, state: str, issues: list[str]) -> list[dict]:
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    if state == "__NATIONAL__":
        rows = extract_presidential_2019_national(soup)
        if not rows:
            issues.append(f"{path.name}: could not find national by-state presidential table")
        return rows

    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        tables = []

    if office == "senate":
        result_tables = find_result_tables(tables)
        good_tables = []
        for t in result_tables:
            if contains_tbd(t):
                continue
            rows = extract_result_rows(t)
            parties = {r["party"] for r in rows if r["party"]}
            vote_sum = sum(r["votes"] or 0 for r in rows)
            if len(parties) >= 2 and vote_sum >= MIN_PLAUSIBLE_VOTE_SUM:
                good_tables.append(rows)

        districts = extract_senate_districts(tables)
        if districts and len(districts) == len(good_tables):
            rows = []
            for district, drows in zip(districts, good_tables):
                for r in drows:
                    r["state"], r["district"], r["source"] = state, district, "results_table"
                    rows.append(r)
            return rows

        winners = extract_senate_district_winners(tables)
        if winners:
            issues.append(f"{path.name}: no clean per-district results table, used winner-only summary (no vote counts)")
            for r in winners:
                r["state"], r["source"] = state, "winner_only"
            return winners

        issues.append(f"{path.name}: no senate result table or winner summary found (needs manual review)")
        return []

    # governor / presidential-per-state
    rows = find_breakdown_totals_row(tables)
    if rows:
        for r in rows:
            r["state"], r["source"] = state, "breakdown_total_row"
        return rows

    rows = best_results_table(tables)
    if rows:
        for r in rows:
            r["state"], r["source"] = state, "results_table"
        return rows

    rows = extract_infobox_fallback(soup)
    if rows:
        issues.append(f"{path.name}: no usable results/breakdown table, used infobox fallback ({len(rows)} candidates only)")
        for r in rows:
            r["state"], r["source"] = state, "infobox"
        return rows

    issues.append(f"{path.name}: no results table, breakdown table, or infobox found (needs manual review)")
    return []


def main() -> int:
    files = sorted(SOURCE_DIR.glob("*.html"))
    buckets: dict[tuple[str, str], list[dict]] = {}
    issues: list[str] = []
    unclassified: list[str] = []

    for path in files:
        classified = classify(path.stem)
        if classified is None:
            unclassified.append(path.name)
            continue
        office, year, state = classified
        rows = process_file(path, office, year, state, issues)
        if not rows:
            issues.append(f"{path.name}: zero rows extracted")
        buckets.setdefault((office, year), []).extend(rows)

    for (office, year), rows in sorted(buckets.items()):
        out_path = SCRIPT_DIR / f"{year}_{office}.csv"
        fieldnames = ["state"] + (["district"] if office == "senate" else []) + ["candidate", "party", "votes", "percent", "source"]
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in fieldnames})
        print(f"Wrote {out_path.name}: {len(rows)} rows")

    if unclassified:
        print(f"\n{len(unclassified)} file(s) did not match any known naming pattern:")
        for name in unclassified:
            print(f"  - {name}")

    if issues:
        print(f"\n{len(issues)} issue(s) flagged for manual review:")
        for issue in issues:
            print(f"  - {issue}")

    report_path = SCRIPT_DIR / "extract_report.md"
    lines = ["# Table extraction report", ""]
    lines.append(f"{len(files)} source files processed, {len(unclassified)} unclassified, {len(issues)} issues flagged.")
    lines.append("")
    if unclassified:
        lines.append("## Unclassified files")
        lines.extend(f"- {name}" for name in unclassified)
        lines.append("")
    if issues:
        lines.append("## Issues")
        lines.extend(f"- {issue}" for issue in issues)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
