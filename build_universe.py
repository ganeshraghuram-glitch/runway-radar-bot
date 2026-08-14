"""Build data/universe.json: the combined smallcap + microcap name list Runway
Radar scans.

Reuses what the sibling sub-agents already resolved rather than re-deriving
it:
  - daily-microcap-ticker/data/universe.json (119 names, already has
    symbol/url/sector/industry)
  - a fresh scrape of the Nifty Smallcap 100 constituent pages (same source
    daily-smallcap-ticker uses) for the other ~100 names

Run this whenever the sibling tickers' universes change (new microcap build,
smallcap index reshuffle) — not on every Runway Radar run.
"""

import html
import json
import os
import re
import time

import requests

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "data", "universe.json")
MICROCAP_UNIVERSE = os.path.join(
    HERE, "..", "daily-microcap-ticker", "data", "universe.json"
)
SCREENER_URL = "https://www.screener.in/company/CNXSMALLCA/?page={}"
PAGES = 4

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
ROW_RE = re.compile(r"<tr[^>]*data-row-company-id[^>]*>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
HREF_RE = re.compile(r'href="(/company/[^"]+)"')


def text(fragment):
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


def scrape_smallcap100():
    """{name: {'symbol': ..., 'url': ...}} for the 100 index constituents."""
    session = requests.Session()
    session.headers["User-Agent"] = UA
    out = {}
    for page in range(1, PAGES + 1):
        resp = session.get(SCREENER_URL.format(page), timeout=30)
        resp.raise_for_status()
        rows = ROW_RE.findall(resp.text)
        if not rows:
            raise RuntimeError(f"page {page}: no constituent rows found")
        for row in rows:
            cells = CELL_RE.findall(row)
            if len(cells) < 2:
                continue
            name = " ".join(text(cells[1]).split())
            href = HREF_RE.search(row)
            # href attribute values are HTML-escaped, so tickers containing
            # "&" (e.g. ARE&M) come through as "ARE&amp;M" unless unescaped —
            # that 404s every request for those names.
            path = html.unescape(href.group(1)) if href else ""
            parts = [p for p in path.split("/") if p]
            if not name or not path:
                continue
            out[name] = {
                "symbol": parts[1] if len(parts) > 1 else "",
                "url": "https://www.screener.in" + path,
            }
        time.sleep(1)
    return out


def load_microcap():
    with open(MICROCAP_UNIVERSE, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {
        name: {
            "symbol": info["symbol"],
            "url": info["url"],
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "cap_bucket": "microcap",
        }
        for name, info in raw.items()
    }


def main():
    universe = load_microcap()
    print(f"Loaded {len(universe)} microcap names from sibling ticker.")

    print("Scraping Nifty Smallcap 100 constituents...")
    smallcap = scrape_smallcap100()
    for name, info in smallcap.items():
        info["cap_bucket"] = "smallcap"
        universe.setdefault(name, info)
    print(f"Added smallcap constituents. Universe now {len(universe)} names.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(universe, fh, indent=1, sort_keys=True)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
