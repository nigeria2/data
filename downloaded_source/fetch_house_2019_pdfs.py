"""
Downloads every per-federal-constituency PDF result sheet linked from INEC's
2019 House of Representatives results index page into
./house_2019_pdfs/{State}_{Constituency}.pdf, for later scraping.

Source index: https://www.inecnigeria.org/2019-house-of-representatives-election/
That page is a series of state accordions (Elementor tabs), each listing its
federal constituencies as links to /wp-content/uploads/2019/10/*.pdf. The
accordions appear in strict alphabetical state order (36 states + FCT between
Enugu and Gombe), which is used here to label each PDF with its state since
the accordion header text itself has typos (e.g. "aba state" for Abia).

Note: inecnigeria.org's TLS certificate does not validate cleanly, so requests
are made with verify=False (read-only, no credentials involved).

This does NOT parse/scrape the PDFs -- only downloads them.

Usage:
    python fetch_house_2019_pdfs.py
"""

from __future__ import annotations

import csv
import re
import sys
import time
import urllib3
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INDEX_URL = "https://www.inecnigeria.org/2019-house-of-representatives-election/"
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "house_2019_pdfs"
REPORT_PATH = OUTPUT_DIR / "fetch_report.md"

USER_AGENT = "Nigeria2.0-DataBot/1.0 (https://github.com/nigeria2/data; contact: markessien@gmail.com)"
REQUEST_DELAY_SECONDS = 0.4
REQUEST_TIMEOUT_SECONDS = 30

# Accordions appear in this exact alphabetical order on the page (verified by
# inspection); FCT sorts between Enugu and Gombe.
STATE_ORDER = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue",
    "Borno", "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "FCT",
    "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi",
    "Kwara", "Lagos", "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo",
    "Plateau", "Rivers", "Sokoto", "Taraba", "Yobe", "Zamfara",
]


def fetch_index() -> list[tuple[str, str, str]]:
    """Returns (state, constituency_label, pdf_url) for every linked PDF."""
    resp = requests.get(INDEX_URL, headers={"User-Agent": USER_AGENT},
                        timeout=REQUEST_TIMEOUT_SECONDS, verify=False)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    titles = soup.find_all("div", class_="elementor-tab-title")
    if len(titles) != len(STATE_ORDER):
        raise RuntimeError(
            f"Expected {len(STATE_ORDER)} state accordions, found {len(titles)}. "
            "Page structure may have changed -- verify before relying on STATE_ORDER."
        )

    entries = []
    for state, title in zip(STATE_ORDER, titles):
        num = title.get("id", "").rsplit("-", 1)[-1]
        content = soup.find("div", id=f"elementor-tab-content-{num}")
        if content is None:
            continue
        for a in content.find_all("a", href=True):
            url = urljoin(INDEX_URL, a["href"])
            label = a.get_text(strip=True) or Path(url).stem
            entries.append((state, label, url))
    return entries


def slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", text.strip()).strip("-")
    return text


def main() -> int:
    OUTPUT_DIR.mkdir(exist_ok=True)
    entries = fetch_index()
    print(f"Found {len(entries)} constituency PDF links across {len(STATE_ORDER)} states.")

    results = []
    for i, (state, label, url) in enumerate(entries, start=1):
        fname = f"{slug(state)}_{Path(url).stem}.pdf"
        out_path = OUTPUT_DIR / fname
        print(f"[{i}/{len(entries)}] {state} - {label}: {url}")

        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                                 timeout=REQUEST_TIMEOUT_SECONDS, verify=False)
        except requests.RequestException as exc:
            print(f"    FAILED: {exc}")
            results.append({"state": state, "label": label, "url": url, "file": fname,
                             "ok": False, "reason": str(exc)})
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        if resp.status_code != 200:
            print(f"    FAILED: HTTP {resp.status_code}")
            results.append({"state": state, "label": label, "url": url, "file": fname,
                             "ok": False, "reason": f"HTTP {resp.status_code}"})
        elif not resp.content.startswith(b"%PDF"):
            print(f"    FAILED: response is not a PDF")
            results.append({"state": state, "label": label, "url": url, "file": fname,
                             "ok": False, "reason": "not a PDF (bad magic bytes)"})
        else:
            out_path.write_bytes(resp.content)
            results.append({"state": state, "label": label, "url": url, "file": fname,
                             "ok": True, "reason": None, "bytes": len(resp.content)})

        time.sleep(REQUEST_DELAY_SECONDS)

    write_report(results)
    failures = [r for r in results if not r["ok"]]
    print(f"\nDone. {len(results) - len(failures)}/{len(results)} downloaded.")
    if failures:
        print(f"{len(failures)} failure(s):")
        for r in failures:
            print(f"  - {r['state']} - {r['label']} ({r['url']}): {r['reason']}")
    return 1 if failures else 0


def write_report(results: list[dict]) -> None:
    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    lines = ["# House of Representatives 2019 PDF download report", ""]
    lines.append(f"{len(ok)}/{len(results)} downloaded, {len(bad)} failed.")
    lines.append("")
    if bad:
        lines.append("## Failures")
        lines.append("")
        lines.append("| State | Constituency | URL | Reason |")
        lines.append("| --- | --- | --- | --- |")
        for r in bad:
            lines.append(f"| {r['state']} | {r['label']} | {r['url']} | {r['reason']} |")
        lines.append("")
    lines.append("## All files")
    lines.append("")
    lines.append("| State | Constituency | File |")
    lines.append("| --- | --- | --- |")
    for r in results:
        lines.append(f"| {r['state']} | {r['label']} | {r['file']} |")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with (OUTPUT_DIR / "fetch_report.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["state", "label", "url", "file", "ok", "reason"])
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in writer.fieldnames})


if __name__ == "__main__":
    sys.exit(main())
