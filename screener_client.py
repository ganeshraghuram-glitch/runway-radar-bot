"""Shared screener.in scraping helpers: cash flow statement + top-ratios.

Both figures live on the same company page, so one request gets everything
this sub-agent needs about a stock — no separate API calls.
"""

import re
import time

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

session = requests.Session()
session.headers.update({"User-Agent": UA})

# screener rate-limits aggressive scraping. A full company-page fetch is
# heavier than the chart-API pacing daily-microcap-ticker uses, and we've
# already tripped its 429 threshold at 1.1s once — wider gap plus the retry
# in fetch_company() is the actual fix, this is just belt-and-suspenders.
REQUEST_GAP = 2.5

CASH_FLOW_SECTION_RE = re.compile(r'id="cash-flow".*?<table.*?</table>', re.S)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
LABEL_RE = re.compile(r"Company\.showSchedule\('([^']+)'")
CELL_RE = re.compile(r'<td class="">\s*([\-\d,]+)\s*</td>')

RATIOS_SECTION_RE = re.compile(r'id="top-ratios".*?</ul>', re.S)
LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S)
NAME_RE = re.compile(r'<span class="name">\s*([^<]+?)\s*</span>')
NUMBER_RE = re.compile(r'<span class="number">([\d,.]+)</span>')

RANGES_TABLE_RE = re.compile(
    r'<table class="ranges-table">\s*<tr>\s*<th colspan="2">([^<]+)</th>\s*</tr>(.*?)</table>',
    re.S,
)
RANGES_ROW_RE = re.compile(r"<td>([^<]+):</td>\s*<td>\s*([\-\d.]*)%?\s*</td>", re.S)
WANTED_RANGES = {
    "Compounded Sales Growth": "sales_growth",
    "Compounded Profit Growth": "profit_growth",
    "Return on Equity": "roe",
}

# Sector/industry breadcrumb, same source the sibling tickers already use.
CRUMB_RE = re.compile(r'href="/market/IN[^"]*"[^>]*>\s*([^<]{2,40})\s*</a>')


def parse_sector_industry(html):
    crumbs = [c.strip() for c in CRUMB_RE.findall(html)]
    if not crumbs:
        return {}
    out = {"sector": crumbs[0]}
    if len(crumbs) > 1:
        out["industry"] = crumbs[-1]
    return out

ROWS_WANTED = {
    "Cash from Operating Activity": "cfo",
    "Cash from Investing Activity": "cfi",
    "Cash from Financing Activity": "cff",
}


def _to_float(raw):
    try:
        return float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch_page(symbol_or_code, consolidated=True):
    """Raw HTML for a company page. Falls back to standalone if consolidated 404s."""
    base = f"https://www.screener.in/company/{symbol_or_code}/"
    url = base + "consolidated/" if consolidated else base
    resp = session.get(url, timeout=30)
    if resp.status_code == 404 and consolidated:
        return fetch_page(symbol_or_code, consolidated=False)
    resp.raise_for_status()
    return resp.text


def parse_cash_flow(html):
    """{'cfo': [..values oldest->newest], 'cfi': [...], 'cff': [...]} in Rs Cr."""
    match = CASH_FLOW_SECTION_RE.search(html)
    if not match:
        return {}
    out = {}
    for row in ROW_RE.findall(match.group(0)):
        label_m = LABEL_RE.search(row)
        if not label_m or label_m.group(1) not in ROWS_WANTED:
            continue
        values = [_to_float(v) for v in CELL_RE.findall(row)]
        values = [v for v in values if v is not None]
        if values:
            out[ROWS_WANTED[label_m.group(1)]] = values
    return out


def parse_top_ratios(html):
    """{'market_cap_cr': float, 'price': float, 'high_52w': float, 'low_52w': float}."""
    match = RATIOS_SECTION_RE.search(html)
    if not match:
        return {}
    out = {}
    for li in LI_RE.findall(match.group(0)):
        name_m = NAME_RE.search(li)
        if not name_m:
            continue
        label = name_m.group(1).strip()
        numbers = [_to_float(n) for n in NUMBER_RE.findall(li)]
        numbers = [n for n in numbers if n is not None]
        if not numbers:
            continue
        if label == "Market Cap":
            out["market_cap_cr"] = numbers[0]
        elif label == "Current Price":
            out["price"] = numbers[0]
        elif label == "High / Low" and len(numbers) >= 2:
            out["high_52w"], out["low_52w"] = numbers[0], numbers[1]
        elif label == "ROCE":
            out["roce"] = numbers[0]
    return out


def parse_growth_ranges(html):
    """Latest-period figure from the Compounded Sales/Profit Growth and ROE
    tables: {'sales_growth': %, 'profit_growth': %, 'roe': %}.

    Each table lists several periods (10/5/3 Years, TTM or Last Year) oldest
    first — the last row is the most recent, which is what a "is growth
    showing up now" check actually wants.
    """
    out = {}
    for title, body in RANGES_TABLE_RE.findall(html):
        key = WANTED_RANGES.get(title.strip())
        if not key:
            continue
        rows = RANGES_ROW_RE.findall(body)
        values = [_to_float(v) for _, v in rows if v]
        if values:
            out[key] = values[-1]
    return out


def fetch_company(symbol_or_code):
    """Fetch + parse everything for one company. Returns {} on any failure.

    screener.in rate-limits aggressively (HTTP 429) well before the pacing
    delay alone prevents it, so a failed fetch gets retried with backoff
    before being treated as genuinely missing data.
    """
    html = None
    for attempt in range(4):
        try:
            html = fetch_page(symbol_or_code)
            break
        except Exception:
            time.sleep(4 * (attempt + 1))
    if html is None:
        return {}

    data = {}
    data.update(parse_top_ratios(html))
    data.update(parse_growth_ranges(html))
    data.update(parse_sector_industry(html))
    cash_flow = parse_cash_flow(html)
    if cash_flow:
        data["cash_flow"] = cash_flow
    return data


def fetch_company_paced(symbol_or_code):
    result = fetch_company(symbol_or_code)
    time.sleep(REQUEST_GAP)
    return result
