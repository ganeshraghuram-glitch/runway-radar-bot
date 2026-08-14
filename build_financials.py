"""Build/refresh data/financials.json: cash flow + top-ratios for every name
in data/universe.json.

Cash flow statements only change when a company files results — there is no
reason to re-scrape 219 pages every single day. Run this weekly (or after a
big results season) and let ticker.py run daily off the cache, refreshing
only price/52-week range for the shortlist that actually passes the screen.

Usage:
    python3 build_financials.py            # full universe
    python3 build_financials.py --limit 10 # smoke test on first N names
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import screener_client as sc

HERE = os.path.dirname(__file__)
UNIVERSE = os.path.join(HERE, "data", "universe.json")
OUT = os.path.join(HERE, "data", "financials.json")


def load_universe():
    if not os.path.exists(UNIVERSE):
        raise RuntimeError(f"{UNIVERSE} missing — run build_universe.py first")
    with open(UNIVERSE, encoding="utf-8") as fh:
        return json.load(fh)


def main(limit=None):
    universe = load_universe()
    names = list(universe.items())
    if limit:
        names = names[:limit]

    out = {}
    failures = []
    for i, (name, info) in enumerate(names, 1):
        code = info["symbol"] or info["url"].rstrip("/").rsplit("/", 1)[-1]
        data = sc.fetch_company_paced(code)
        cash_flow = data.get("cash_flow", {})
        if not cash_flow.get("cfi") or not cash_flow.get("cff") or not data.get("market_cap_cr"):
            failures.append(name)
            print(f"[{i:3}/{len(names)}] {name}: incomplete data, skipped", file=sys.stderr)
            continue

        out[name] = {
            **info,
            # smallcap100-sourced entries have no sector/industry from
            # build_universe.py; this live fetch fills it in for everyone.
            "sector": data.get("sector") or info.get("sector", ""),
            "industry": data.get("industry") or info.get("industry", ""),
            "market_cap_cr": data["market_cap_cr"],
            "price_at_build": data.get("price"),
            "high_52w_at_build": data.get("high_52w"),
            "low_52w_at_build": data.get("low_52w"),
            "roce": data.get("roce"),
            "roe": data.get("roe"),
            "sales_growth": data.get("sales_growth"),
            "profit_growth": data.get("profit_growth"),
            "cfo": cash_flow.get("cfo", []),
            "cfi": cash_flow.get("cfi", []),
            "cff": cash_flow.get("cff", []),
        }
        print(f"[{i:3}/{len(names)}] {name}: ok")

    # The sparsest tail of the microcap universe genuinely has blank
    # price/cash-flow fields server-side on screener.in (confirmed by
    # inspecting the raw HTML, not a scraping bug) — a ~30-40% skip rate is
    # normal at this end of the market, not a sign something broke. This
    # threshold exists to catch a real outage/markup change, not to demand a
    # complete universe.
    if len(failures) > len(names) * 0.55:
        raise RuntimeError(
            f"{len(failures)}/{len(names)} names failed — screener markup may "
            f"have changed or requests are being blocked. Aborting rather than "
            f"writing a partial file."
        )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    payload = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "companies": out,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
    print(f"\nWrote {OUT}: {len(out)} companies ({len(failures)} skipped)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Runway Radar financials cache")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N names (smoke test)")
    args = parser.parse_args()
    start = time.time()
    main(limit=args.limit)
    print(f"Took {time.time() - start:.0f}s")
