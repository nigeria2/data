"""
Build the tidy per-LGA 2019 governorship table (state,lga,party,votes) that the
backend loads into lga_party_results (year=2019, election_type=governor).

Sources:
  1. 2019_governor_lga.csv -- the candidate-level extraction (extract_lga_tables.py)
     for the 17 states whose Wikipedia pages carry filled per-LGA tables in the
     block/multiindex format. Aggregated here to (state,lga,party,votes).
  2. Enugu -- its Wikipedia page uses a wide "LOCAL GOVERNMENT AREA + candidate
     columns" layout the shared extractor doesn't handle, so it's parsed directly.

Writes:
  - 2019_governor_lga_tidy.csv          (data repo, tidy)
  - ../../backend/app/data/gov_2019_lga.csv  (bundled seed source)

Usage: python build_2019_governor_tidy.py
"""
from __future__ import annotations

import collections
import csv
import io
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE / "2019_governor_lga.csv"
SOURCE_DIR = HERE.parent / "downloaded_source"


def from_extraction() -> dict[tuple[str, str, str], int]:
    """Aggregate the candidate-level extraction to (state, lga, party) -> votes."""
    agg: dict[tuple[str, str, str], int] = collections.defaultdict(int)
    with SRC.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            party = (r["party"] or "").strip().upper()
            if not party:
                party = "IND"
            try:
                votes = int(float(r["votes"]))
            except (ValueError, TypeError):
                continue
            # wiki appends ", <State>" to short disambiguated LGA names (e.g. "Ika, Akwa Ibom")
            lga = r["lga"].split(",")[0].strip()
            agg[(r["state"].strip(), lga, party)] += votes
    return agg


def enugu_rows() -> dict[tuple[str, str, str], int]:
    """Parse Enugu's wide per-LGA table: header + '<NAME> (<PARTY>)' candidate cols."""
    path = SOURCE_DIR / "2019_Enugu_State_gubernatorial_election.html"
    tables = pd.read_html(io.StringIO(path.read_text(encoding="utf-8")))
    tbl = next(t for t in tables if str(t.columns[0]).upper().startswith("LOCAL GOVERNMENT"))
    # column headers like "IFEANYI UGWUANYI (PDP)" -> party in the parentheses
    party_cols: list[tuple[str, str]] = []
    for c in tbl.columns[1:]:
        s = str(c)
        party = s[s.find("(") + 1 : s.find(")")].strip().upper() if "(" in s else s.strip().upper()
        party_cols.append((c, party))
    out: dict[tuple[str, str, str], int] = {}
    for _, row in tbl.iterrows():
        lga = str(row[tbl.columns[0]]).strip()
        if lga.upper() in ("TOTAL", "TOTALS", "NAN", ""):
            continue
        # normalise the SCREAMING/hyphenated wiki names to Title Case
        name = " ".join(w.capitalize() for w in lga.replace("-", " ").split())
        for col, party in party_cols:
            try:
                votes = int(float(row[col]))
            except (ValueError, TypeError):
                continue
            out[("Enugu", name, party)] = votes
    return out


def main() -> int:
    agg = from_extraction()
    agg.update(enugu_rows())

    rows = sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1], -kv[1]))
    tidy = HERE / "2019_governor_lga_tidy.csv"
    bundled = HERE.parent.parent / "backend" / "app" / "data" / "gov_2019_lga.csv"
    for out in (tidy, bundled):
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["state", "lga", "party", "votes"])
            for (state, lga, party), votes in rows:
                w.writerow([state, lga, party, votes])

    states = collections.Counter(k[0] for k in agg)
    print(f"Wrote {len(rows)} rows across {len(states)} states -> {tidy.name}, {bundled}")
    for s, n in sorted(states.items()):
        print(f"  {s}: {n} party-rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
