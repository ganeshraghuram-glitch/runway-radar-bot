"""Runway Radar: daily digest of small/microcap names funding expansion with
fresh capital while the share price is still down.

The screen (confirmed with the user before building this):
  - Cash from Investing Activity (latest FY) strongly NEGATIVE relative to
    market cap  -> real money going out into capex: plants, stores, capacity.
  - Cash from Financing Activity (latest FY) strongly POSITIVE relative to
    market cap  -> that capex is being funded by fresh debt/equity, not by
    draining the balance sheet.
  - Ranked by how far the current price sits below its 52-week high, so the
    ones "caught" furthest into a drawdown come first.

This is a data screen, not a recommendation. The digest states the raw
numbers and trend so the reader draws their own conclusion — see the
disclaimer appended to every message.

Two-stage design, deliberately not one script:
  - data/financials.json is built by build_financials.py, run weekly (cash
    flow statements don't change daily — there is nothing to gain by
    re-scraping 219 pages every morning).
  - This script filters that cache locally (free), then only re-fetches live
    price/52-week range for the names that actually pass the filter, which
    keeps a daily run cheap and fast.

Env vars required (same bot as the other A2MarketMax sub-agents):
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import screener_client as sc

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(__file__)
FINANCIALS = os.path.join(HERE, "data", "financials.json")

# Both expressed as % of market cap, latest FY. Defaults are a starting
# point, not a tuned backtest — tighten/loosen based on how many names the
# screen surfaces on a normal day.
CFI_PCT_THRESHOLD = -8.0   # investing outflow must be at least 8% of mkt cap
CFF_PCT_THRESHOLD = 8.0    # financing inflow must be at least 8% of mkt cap
TOP_N = 10
NEWS_MAX_AGE_DAYS = 3
UA = sc.UA

# --- Scorecard -------------------------------------------------------------
# Grades whether the capex looks justified and whether demand is actually
# showing up — NOT a TAM/market-sizing estimate. Real total-addressable-market
# analysis needs industry research (competitor share, category growth,
# regulatory runway) that isn't in a financial statement and isn't
# fabricated here. What this measures, using only screener.in fundamentals:
#   - ROCE (40 pts): is capital deployed actually earning a return, or is the
#     company just burning cash into assets? The standard "is this investment
#     justified" metric.
#   - Sales growth, latest period (30 pts): the closest automatable proxy for
#     "the market wants this" — revenue is demand that already converted,
#     unlike TAM which is demand that might.
#   - Operating cash flow trend (20 pts): rewards the Pine-Labs-style flip
#     from negative to positive OCF most heavily, since that is the clearest
#     sign the expansion is starting to pay for itself in cash, not just on
#     paper.
#   - Profit growth, latest period (10 pts): supporting signal, capped low
#     because profit growth off a small/negative base swings wildly.
SCORE_BANDS = [
    (85, "AAA"), (70, "AA"), (55, "A"), (40, "BBB"), (25, "BB"), (0, "B"),
]


def score_card(info, cfo_latest, cfo_prior):
    roce = info.get("roce")
    sales_growth = info.get("sales_growth")
    profit_growth = info.get("profit_growth")
    missing = [n for n, v in (("ROCE", roce), ("sales growth", sales_growth)) if v is None]

    roce_pts = min(40.0, max(0.0, (roce or 0) / 25.0 * 40.0))
    sales_pts = min(30.0, max(0.0, (sales_growth or 0) / 25.0 * 30.0))
    profit_pts = min(10.0, max(0.0, (profit_growth or 0) / 50.0 * 10.0))

    if cfo_latest is None:
        ocf_pts = 0.0
    elif cfo_prior is not None and cfo_prior <= 0 < cfo_latest:
        ocf_pts = 20.0  # turned positive this FY — the Pine Labs signature
    elif cfo_latest > 0 and cfo_prior is not None and cfo_latest > cfo_prior:
        ocf_pts = 14.0
    elif cfo_latest > 0:
        ocf_pts = 8.0
    elif cfo_prior is not None and cfo_latest > cfo_prior:
        ocf_pts = 4.0  # still negative, but the loss is narrowing
    else:
        ocf_pts = 0.0

    total = roce_pts + sales_pts + profit_pts + ocf_pts
    grade = next(g for threshold, g in SCORE_BANDS if total >= threshold)
    return {
        "grade": grade,
        "score": total,
        "roce": roce,
        "sales_growth": sales_growth,
        "profit_growth": profit_growth,
        "incomplete": bool(missing),
    }


def load_financials():
    if not os.path.exists(FINANCIALS):
        raise RuntimeError(f"{FINANCIALS} missing — run build_financials.py first")
    with open(FINANCIALS, encoding="utf-8") as fh:
        return json.load(fh)


# Lenders/insurers/AMCs raise and deploy money as their core business — a
# bank's "financing activity" is deposits, an NBFC's is its borrowing cycle
# for on-lending, not capex into plants/stores. Their CFI/CFF numbers are
# structurally unrelated to the "buying more factories, stores, expanding"
# pattern this screen is built to catch, and routinely dwarf every industrial
# name on a %-of-market-cap basis (seen live: an NBFC with financing inflow
# at 118% of market cap from its normal loan book). Filtered by sector where
# known, and by name for financial-sector smallcap100 names that arrive
# without a sector tag (see README: sector/industry is only populated for
# the microcap half of the universe today).
FINANCIAL_SECTOR = {"financials", "financial services"}
FINANCIAL_NAME_HINTS = (
    "bank", "finance", "financial", "nbfc", "insurance", "housing fin",
    "capital", "asset management", "amc", "chit fund",
)


def is_financial(name, info):
    sector = (info.get("sector") or "").strip().lower()
    if sector in FINANCIAL_SECTOR:
        return True
    lname = name.lower()
    return any(hint in lname for hint in FINANCIAL_NAME_HINTS)


def screen(companies):
    """Cache-only filter: names whose latest FY CFI/CFF clear both thresholds."""
    candidates = []
    for name, info in companies.items():
        if is_financial(name, info):
            continue
        cfi, cff, cfo = info.get("cfi") or [], info.get("cff") or [], info.get("cfo") or []
        mcap = info.get("market_cap_cr")
        if not cfi or not cff or not mcap:
            continue
        cfi_latest, cff_latest = cfi[-1], cff[-1]
        cfi_pct = cfi_latest / mcap * 100
        cff_pct = cff_latest / mcap * 100
        if cfi_pct > CFI_PCT_THRESHOLD or cff_pct < CFF_PCT_THRESHOLD:
            continue
        candidates.append(
            {
                "name": name,
                "info": info,
                "cfi_latest": cfi_latest,
                "cff_latest": cff_latest,
                "cfi_pct": cfi_pct,
                "cff_pct": cff_pct,
                "cfo_latest": cfo[-1] if cfo else None,
                "cfo_prior": cfo[-2] if len(cfo) >= 2 else None,
            }
        )
    return candidates


def headline(query):
    """Newest headline for a company, if recent enough to be relevant."""
    q = requests.utils.quote(f"{query} share price")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
    except Exception:
        return "", None

    best = None
    for item in re.findall(r"<item>(.*?)</item>", resp.text, re.S):
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
        if not title or not pub:
            continue
        try:
            when = parsedate_to_datetime(pub.group(1).strip())
        except Exception:
            continue
        if best is None or when > best[0]:
            best = (when, re.sub(r"<[^>]+>", "", title.group(1)).strip())

    if best is None:
        return "", None
    age_days = (datetime.now(timezone.utc) - best[0]).days
    if age_days > NEWS_MAX_AGE_DAYS:
        return "", None
    return best[1], age_days


def enrich_live(candidates):
    """Refresh price/52w range for the shortlist only, then rank by discount
    from 52-week high (deepest drawdown first)."""
    enriched = []
    for c in candidates:
        code = c["info"]["symbol"] or c["info"]["url"].rstrip("/").rsplit("/", 1)[-1]
        live = sc.fetch_company_paced(code)
        price, high = live.get("price"), live.get("high_52w")
        if not price or not high:
            continue
        c["price"] = price
        c["high_52w"] = high
        c["low_52w"] = live.get("low_52w")
        c["discount_from_high"] = (high - price) / high * 100
        enriched.append(c)
    enriched.sort(key=lambda c: c["discount_from_high"], reverse=True)
    return enriched


def format_entry(rank, c):
    info = c["info"]
    card = score_card(info, c["cfo_latest"], c["cfo_prior"])
    flag = "  (incomplete data — grade partly defaulted to 0)" if card["incomplete"] else ""
    lines = [
        f"{rank}. {c['name']} — {info.get('sector', '?')} / {info.get('industry', '?')}",
        f"   Scorecard: {card['grade']} ({card['score']:.0f}/100){flag}",
        f"   CMP ₹{c['price']:,.2f}  (↓{c['discount_from_high']:.1f}% from 52w high "
        f"₹{c['high_52w']:,.2f}, low ₹{c['low_52w']:,.2f})",
        f"   Investing outflow (capex etc): ₹{c['cfi_latest']:,.0f}cr "
        f"({c['cfi_pct']:+.1f}% of mkt cap)",
        f"   Financing inflow (debt/equity raised): ₹{c['cff_latest']:,.0f}cr "
        f"({c['cff_pct']:+.1f}% of mkt cap)",
        f"   ROCE {card['roce']:.1f}%" if card["roce"] is not None else "   ROCE: n/a",
        f"   Sales growth (latest): {card['sales_growth']:+.1f}%"
        if card["sales_growth"] is not None else "   Sales growth: n/a",
    ]
    if c["cfo_latest"] is not None:
        if c["cfo_prior"] is not None:
            trend = c["cfo_latest"] - c["cfo_prior"]
            arrow = "↑ improving" if trend > 0 else "↓ weaker" if trend < 0 else "flat"
            flip = c["cfo_prior"] <= 0 < c["cfo_latest"]
            note = "  ← turned positive this FY" if flip else ""
            lines.append(
                f"   Operating cash flow: ₹{c['cfo_latest']:,.0f}cr "
                f"(prior FY ₹{c['cfo_prior']:,.0f}cr, {arrow}){note}"
            )
        else:
            lines.append(f"   Operating cash flow: ₹{c['cfo_latest']:,.0f}cr")

    news, age = headline(info.get("symbol") or c["name"])
    if news:
        when = "today" if age == 0 else f"{age}d ago"
        lines.append(f"   📰 [{when}] {news[:140]}")
    return "\n".join(lines)


TELEGRAM_BODY_LIMIT = 4096


def chunk_message(parts, limit=TELEGRAM_BODY_LIMIT - 100):
    """Pack paragraph-sized parts into messages under Telegram's limit.

    Ten detailed entries plus header/footer comfortably exceeds 4096 chars,
    so this splits on entry boundaries rather than mid-entry — each message
    still reads as complete paragraphs, never a stock's data cut in half.
    """
    chunks, current = [], []
    length = 0
    for part in parts:
        add = len(part) + 2
        if current and length + add > limit:
            chunks.append("\n\n".join(current))
            current, length = [], 0
        current.append(part)
        length += add
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def telegram(method, data=None, files=None):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        files=files,
        timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f"Telegram {method} failed: {resp.status_code} {resp.text}")
    return resp.json()


def main(dry_run=False):
    now = datetime.now(IST)
    pretty = now.strftime("%d %b %Y")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    cache = load_financials()
    companies = cache["companies"]
    built_at = cache.get("built_at", "unknown")

    candidates = screen(companies)
    print(f"{len(candidates)}/{len(companies)} names clear the CFI/CFF thresholds.")

    ranked = enrich_live(candidates)
    top = ranked[:TOP_N]

    header = [
        f"🛫 Runway Radar — {pretty}",
        f"Small/microcap names funding expansion with fresh capital, price still down.",
        f"Screen: FY investing outflow ≥ {abs(CFI_PCT_THRESHOLD):.0f}% of mkt cap, "
        f"financing inflow ≥ {CFF_PCT_THRESHOLD:.0f}% of mkt cap. "
        f"Ranked by discount from 52-week high.",
        f"Scorecard (AAA best → B weakest): ROCE + sales growth + OCF trend + "
        f"profit growth — is the capex earning a return and is demand showing "
        f"up in revenue. NOT a TAM/market-size estimate; that needs real "
        f"industry research this screen can't do from financial statements alone.",
        f"Universe: {len(companies)} names (financials cache built {built_at[:10]}).",
        "",
    ]

    if not top:
        body = ["No names cleared both thresholds today. Screen stays quiet rather "
                "than padding the list — this is normal, not a bug."]
    else:
        body = [format_entry(i, c) for i, c in enumerate(top, 1)]

    footer = (
        "⚠️ This is a data screen, not investment advice or a buy recommendation. "
        "Heavy capex funded by fresh capital is a pattern, not a guarantee — verify "
        "the numbers on screener.in and do your own diligence before acting."
    )

    parts = header[:-1] + body + [footer]
    messages = chunk_message(parts)

    if dry_run:
        print(f"\n\n{'—' * 20} [message break] {'—' * 20}\n\n".join(messages))
        return

    if not chat_id or not os.environ.get("TELEGRAM_BOT_TOKEN"):
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID required")
    for msg in messages:
        telegram("sendMessage", {"chat_id": chat_id, "text": msg})
    print(f"Sent digest: {len(top)} names across {len(messages)} message(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Runway Radar daily screen")
    parser.add_argument("--dry-run", action="store_true", help="Print digest; do not send Telegram")
    args = parser.parse_args()
    try:
        main(dry_run=args.dry_run)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
