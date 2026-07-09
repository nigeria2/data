"""
Fetches every Wikipedia article URL listed in ../2019_election_urls.md and
../2023_election_urls.md, strips scripts/styles/images/other non-text
markup, and saves the cleaned HTML here so tables can be extracted later.

This step does NOT extract any election data — it only downloads and cleans
the raw source pages, then validates each saved file has a real <title> and
at least one <table>.

Usage:
    python fetch_wiki_sources.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup, Comment

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
SOURCE_FILES = [DATA_DIR / "2019_election_urls.md", DATA_DIR / "2023_election_urls.md"]
OUTPUT_DIR = SCRIPT_DIR
REPORT_PATH = OUTPUT_DIR / "fetch_report.md"

USER_AGENT = "Nigeria2.0-DataBot/1.0 (https://github.com/nigeria2/data; contact: markessien@gmail.com)"
REQUEST_DELAY_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 20

# Tags that never carry article text/table content and should be dropped outright.
TAGS_TO_REMOVE = [
    "script", "style", "noscript", "img", "picture", "source", "svg", "video",
    "audio", "iframe", "link", "meta", "canvas", "embed", "object", "track",
    "form", "input", "button", "label", "select", "textarea", "nav", "header", "footer",
]

# CSS classes for MediaWiki chrome/boilerplate that isn't article content: edit
# links, bottom navigation boxes, maintenance banners, sister-project boxes, etc.
CLASSES_TO_REMOVE = [
    "mw-editsection", "navbox", "vertical-navbox", "metadata", "ambox", "tmbox",
    "ombox", "noprint", "catlinks", "printfooter", "mw-jump-link", "sistersitebox",
    "side-box", "navbar", "hatnote", "mw-indicators",
]

MD_TABLE_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*(https://en\.wikipedia\.org/wiki/\S+?)\s*\|\s*$",
    re.MULTILINE,
)


def extract_urls() -> list[tuple[str, str]]:
    """Returns a de-duplicated list of (label, url) pulled from the markdown tables."""
    seen: set[str] = set()
    urls: list[tuple[str, str]] = []
    for path in SOURCE_FILES:
        text = path.read_text(encoding="utf-8")
        for label, url in MD_TABLE_ROW_RE.findall(text):
            if url in seen:
                continue
            seen.add(url)
            urls.append((label, url))
    return urls


def filename_for(url: str) -> str:
    slug = urlsplit(url).path.rsplit("/", 1)[-1]
    slug = unicodedata.normalize("NFKD", slug)
    return f"{slug}.html"


def clean_html(raw_html: str) -> BeautifulSoup:
    full_soup = BeautifulSoup(raw_html, "html.parser")

    title_text = full_soup.title.get_text(strip=True) if full_soup.title else ""
    content = full_soup.find("div", id="mw-content-text")

    # Fall back to the whole document if MediaWiki's content container is ever
    # missing (e.g. a non-standard page) rather than silently emitting nothing.
    if content is None:
        content = full_soup.body or full_soup

    for tag in content.find_all(TAGS_TO_REMOVE):
        tag.decompose()

    for class_name in CLASSES_TO_REMOVE:
        for tag in content.find_all(class_=class_name):
            tag.decompose()

    for comment in content.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    # Drop noisy inline styling/handler attributes but keep class/id (later table
    # extraction will likely key off class="wikitable"/"infobox").
    for tag in content.find_all(True):
        for attr in list(tag.attrs):
            if attr == "style" or attr.startswith("on"):
                del tag.attrs[attr]

    # Rebuild a minimal standalone document: just <title> + the cleaned article body.
    out = BeautifulSoup("<html><head></head><body></body></html>", "html.parser")
    title_tag = out.new_tag("title")
    title_tag.string = title_text
    out.head.append(title_tag)
    out.body.append(content)

    return out


def fetch(url: str) -> tuple[bool, str]:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return False, f"request error: {exc}"

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"

    return True, resp.text


def validate(soup: BeautifulSoup) -> tuple[bool, str, int]:
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    table_count = len(soup.find_all("table"))

    if not title:
        return False, "missing <title>", table_count
    if "does not have an article with this exact name" in soup.get_text():
        return False, f"missing-article page ({title})", table_count
    return True, title, table_count


def main() -> int:
    urls = extract_urls()
    print(f"Found {len(urls)} unique article URLs across {len(SOURCE_FILES)} source files.")

    results = []
    for i, (label, url) in enumerate(urls, start=1):
        fname = filename_for(url)
        out_path = OUTPUT_DIR / fname
        print(f"[{i}/{len(urls)}] {label}: {url}")

        ok, payload = fetch(url)
        if not ok:
            print(f"    FAILED to fetch: {payload}")
            results.append({"label": label, "url": url, "file": fname, "fetched": False,
                             "reason": payload, "valid": False, "tables": 0})
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        soup = clean_html(payload)
        out_path.write_text(str(soup), encoding="utf-8")

        valid, title_or_reason, table_count = validate(soup)
        if not valid:
            print(f"    downloaded but INVALID: {title_or_reason}")
        results.append({"label": label, "url": url, "file": fname, "fetched": True,
                         "reason": None, "valid": valid, "title": title_or_reason,
                         "tables": table_count})

        time.sleep(REQUEST_DELAY_SECONDS)

    write_report(results)

    failures = [r for r in results if not r["fetched"] or not r["valid"]]
    print(f"\nDone. {len(results) - len(failures)}/{len(results)} succeeded.")
    if failures:
        print(f"{len(failures)} problem(s):")
        for r in failures:
            reason = r["reason"] if not r["fetched"] else "downloaded but failed validation"
            print(f"  - {r['label']} ({r['url']}): {reason}")
    return 1 if failures else 0


def write_report(results: list[dict]) -> None:
    lines = ["# Wikipedia source fetch report", ""]
    ok = [r for r in results if r["fetched"] and r["valid"]]
    bad = [r for r in results if not r["fetched"] or not r["valid"]]
    lines.append(f"{len(ok)}/{len(results)} succeeded, {len(bad)} failed.")
    lines.append("")
    if bad:
        lines.append("## Failures")
        lines.append("")
        lines.append("| Label | URL | Reason |")
        lines.append("| --- | --- | --- |")
        for r in bad:
            reason = r["reason"] if not r["fetched"] else "downloaded but failed validation"
            lines.append(f"| {r['label']} | {r['url']} | {reason} |")
        lines.append("")
    lines.append("## All results")
    lines.append("")
    lines.append("| Label | File | Tables found | Title |")
    lines.append("| --- | --- | --- | --- |")
    for r in results:
        title = r.get("title", "") or ""
        lines.append(f"| {r['label']} | {r['file']} | {r['tables']} | {title} |")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUTPUT_DIR / "fetch_report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
