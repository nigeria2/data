"""
Build the tidy 2019 National Assembly results table the backend loads into
legislative_results: one row per (office, constituency, candidate).

Sources: 2019_house_inec.csv + 2019_senate_inec.csv (from extract_pdf_tables.py,
the INEC constituency/district result sheets). This step:
  - normalises gender (M/F) and party (upper),
  - ranks candidates within each constituency by votes (position, 1 = top),
  - marks the winner (`elected`): the candidate(s) whose INEC remark says elected
    -- tolerating the sheets' typos (EECTED/ELECETD/ELLECTED) and Supreme-Court
    wording -- else, where a sheet carried no remark at all, the top-voted candidate.

Writes 2019_legislative.csv (data repo) and ../../backend/app/data/legislative_2019.csv.

Usage: python build_legislative_2019.py
"""
from __future__ import annotations

import collections
import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLED = HERE.parent.parent / "backend" / "app" / "data" / "legislative_2019.csv"
OUT = HERE / "2019_legislative.csv"

FIELDS = ["election_type", "year", "state", "constituency", "code",
          "candidate", "gender", "party", "votes", "position", "elected"]


def norm_gender(g: str) -> str:
    g = (g or "").strip().upper()
    return "M" if g.startswith("M") else "F" if g.startswith("F") else ""


def votes_of(v: str) -> int:
    v = (v or "").replace(",", "").strip()
    return int(v) if v.isdigit() else 0


def remark_says_elected(remark: str) -> bool:
    t = (remark or "").upper().replace(" ", "")
    if not t or "WASTED" in t:  # "Votes Declared Wasted by the Supreme Court"
        return False
    return any(k in t for k in ("ELECTED", "EECTED", "ELECETD", "ELLECTED", "WINNER"))


def load(csv_path: Path, office: str, area_field: str) -> list[dict]:
    rows = [r for r in csv.DictReader(csv_path.open(encoding="utf-8"))]
    out: list[dict] = []
    by_con: dict[tuple, list[dict]] = collections.defaultdict(list)
    for r in rows:
        rec = {
            "election_type": office,
            "year": "2019",
            "state": r["state"].strip(),
            "constituency": r[area_field].strip(),
            "code": (r.get("code") or "").strip(),
            "candidate": (r.get("candidate") or "").strip(),
            "gender": norm_gender(r.get("gender")),
            "party": (r.get("party") or "").strip().upper(),
            "votes": votes_of(r.get("votes")),
            "_elected_remark": remark_says_elected(r.get("remark")),
        }
        out.append(rec)
        by_con[(rec["state"], rec["constituency"])].append(rec)

    for con, recs in by_con.items():
        recs.sort(key=lambda x: x["votes"], reverse=True)
        for i, rec in enumerate(recs, 1):
            rec["position"] = i
        flagged = [rec for rec in recs if rec["_elected_remark"]]
        winners = flagged if flagged else ([recs[0]] if recs else [])
        for rec in recs:
            rec["elected"] = rec in winners
    for rec in out:
        rec.pop("_elected_remark", None)
    return out


def main() -> int:
    rows = load(HERE / "2019_house_inec.csv", "house", "constituency")
    rows += load(HERE / "2019_senate_inec.csv", "senate", "district")
    # stable order: office, state, constituency, rank
    rows.sort(key=lambda x: (x["election_type"], x["state"], x["constituency"], x["position"]))
    for out in (OUT, BUNDLED):
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: (1 if r[k] is True else 0 if r[k] is False else r[k]) for k in FIELDS})

    for office in ("house", "senate"):
        sub = [r for r in rows if r["election_type"] == office]
        cons = {(r["state"], r["constituency"]) for r in sub}
        wins = sum(1 for r in sub if r["elected"])
        print(f"{office}: {len(sub)} candidates, {len(cons)} constituencies, {wins} winners")
    print(f"Wrote {OUT.name} and {BUNDLED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
