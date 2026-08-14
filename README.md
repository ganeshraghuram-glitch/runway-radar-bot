# Runway Radar

Daily Telegram digest of small/microcap names that are funding real expansion
— new plants, stores, capacity — with fresh capital, while the share price
still hasn't caught up. The pattern this is built to catch: a company spends
heavily (Cash from Investing Activity deeply negative — capex), raises money
to pay for it (Cash from Financing Activity strongly positive — debt/equity),
and the market hasn't re-rated the stock for it yet. Pine Labs is the
reference case: FY25 investing outflow of ₹486cr and financing outflow of
₹201cr, then FY26 investing outflow jumped to ₹1,339cr funded by a ₹1,972cr
financing inflow, operating cash flow turned from ₹50cr to ₹395cr, and the
stock has run since — while still sitting well below its 52-week high of ₹284
at a CMP of ₹165.

**This is a data screen, not investment advice.** It surfaces a pattern and
the raw numbers behind it. Whether the capex will actually pay off is a
judgment call — the digest states the numbers so you can make it, not a
recommendation to buy anything. See the disclaimer on every message.

## The screen

1. **Investing outflow ≥ 8% of market cap** (latest FY Cash from Investing
   Activity, strongly negative) — real capex, not a rounding error.
2. **Financing inflow ≥ 8% of market cap** (latest FY Cash from Financing
   Activity, strongly positive) — the capex is funded by fresh capital, not
   draining existing cash.
3. Ranked by **discount from 52-week high** — names furthest into a drawdown
   surface first, since the whole point is catching the price before the
   market re-rates it.
4. Operating cash flow (this FY vs prior FY) is shown for every name as
   context, especially whether it just turned positive — the clearest sign
   the expansion is starting to pay off — but it is *not* a filter. A name
   can pass the screen with OCF still negative; that is shown plainly rather
   than hidden.

Thresholds are constants at the top of `ticker.py` (`CFI_PCT_THRESHOLD`,
`CFF_PCT_THRESHOLD`), not tuned against history — adjust them if the screen
surfaces too many or too few names on a normal day.

## Scorecard (AAA / AA / A / BBB / BB / B)

Every name that passes the screen also gets a letter grade, computed in
`score_card()` in `ticker.py`:

| Component | Weight | What it's a proxy for |
|---|---|---|
| ROCE (latest) | 40 pts | Is the capital actually earning a return, or is the company burning cash into assets that don't pay off? The standard "is this investment justified" metric. |
| Sales growth (latest period) | 30 pts | Demand that has already converted to revenue — the closest thing an automated financial-statement screen can measure as evidence the market wants this. |
| Operating cash flow trend | 20 pts | Heaviest weight goes to a flip from negative to positive OCF — the Pine Labs signature: the expansion starting to pay for itself in cash, not just on paper. |
| Profit growth (latest period) | 10 pts | Supporting signal, weighted low because profit growth off a small or negative base swings wildly and is easy to overweight. |

**What this grade is not: a TAM (total addressable market) estimate.** Real
market-sizing needs industry research — competitor share, category growth,
regulatory runway, customer surveys — that does not live in a financial
statement and is not fabricated here. The grade measures what's actually
measurable from screener.in: capital efficiency (ROCE) and demand that has
already shown up in revenue (sales growth), plus cash-flow evidence the bet
is starting to work. Treat "AAA" as "the numbers that are checkable check
out," not "guaranteed to grow." A name can score low simply because it has
incomplete data (pre-IPO companies, recent listings) — the digest flags this
explicitly rather than silently grading on a 0.

## Architecture

Two stages, deliberately not one script, because cash flow statements and
share prices change on completely different clocks:

- **`build_financials.py`** (run weekly, or after a big results season)
  scrapes screener.in's cash-flow table and top-ratios block for every name
  in `data/universe.json` into `data/financials.json`. ~219 requests, paced
  ~1.1s apart like the sibling microcap ticker, so a full run takes 4-5
  minutes.
- **`ticker.py`** (run daily) filters that cache locally — free, no
  network — then re-fetches live price/52-week range only for the names that
  actually pass the filter (typically a handful to a few dozen), ranks them,
  and sends the top 10 to Telegram. This keeps the daily run fast even though
  the underlying data covers the full universe.
- **`build_universe.py`** (run only when the sibling tickers' universes
  change) merges `daily-microcap-ticker/data/universe.json` (119 names,
  already resolved) with a fresh scrape of the Nifty Smallcap 100
  constituents (100 names), into `data/universe.json` — 219 names combined.
  **Local-only, not run in CI**: it reads a sibling folder
  (`../daily-microcap-ticker/data/universe.json`) that only exists inside the
  full `A2MarketMax` checkout on this Mac, not in Runway Radar's own GitHub
  repo. Run it locally, commit the resulting `data/universe.json`, push.

```
python3 build_universe.py        # rebuild the 219-name universe (local only)
python3 build_financials.py      # refresh the cash-flow cache (weekly)
python3 ticker.py --dry-run      # preview today's digest, no Telegram send
python3 ticker.py                # send today's digest
```

## Setup

Uses the **same Telegram bot** as the other A2MarketMax sub-agents
(`daily-smallcap-ticker`, `morning-brief`) — no new bot needed.

This runs as its own GitHub repo with two workflows, same pattern as
`daily-smallcap-ticker`:

- **`.github/workflows/weekly-financials.yml`** — Monday 08:00 IST, runs
  `build_financials.py` and commits the refreshed `data/financials.json`.
- **`.github/workflows/daily.yml`** — 17:45 IST weekdays (after the smallcap
  ticker's 17:00 run), runs `ticker.py` and sends the digest. Reads whatever
  `data/financials.json` the weekly job last committed — it does not scrape
  cash flow data itself.

Both need `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` set as repo secrets
(Settings → Secrets and variables → Actions), same values as the other two
tickers' repos. Running locally instead: reads the same two vars from
`A2MarketMax/.env` via `python-dotenv`, same as `daily_telegram.py`.

## Known limitations

- **Screener.in HTML scraping.** Same fragility as the sibling tickers — if
  screener changes its markup, `build_financials.py` fails loudly (aborts
  rather than writing a partial cache if more than 55% of names fail) instead
  of silently reporting wrong numbers.
- **The sparsest tail of the microcap universe has blank data on screener.in
  itself** — confirmed by inspecting the raw HTML, not a scraping bug: some
  ultra-illiquid names simply render `<span class="number"></span>` empty for
  price and market cap server-side. Expect roughly 30-40% of the 219-name
  universe to be unusable for this reason alone, concentrated in the
  microcap half. The smallcap100 half is far more complete since those names
  are, by definition, more liquid and more covered.
- **Financial companies (banks/NBFCs) mostly get skipped.** Their cash flow
  statements don't map cleanly onto "capex funded by financing" — a bank's
  financing activity is largely deposits, not expansion capital. Not a bug in
  today's run; a structural mismatch between this screen and financial-sector
  accounting.
- **Recently-listed companies with under 2 years of cash flow history** get
  skipped by the CFO trend check but can still pass the core CFI/CFF filter.
- **8%-of-market-cap thresholds are a starting point, not backtested.** A
  quiet day (few or zero names clearing both filters) is expected and stated
  as such in the digest, not padded to always show 10 names.
- **Consolidated financials preferred, standalone as fallback** — same as
  screener.in's own default.
- **Headlines** are the newest Google News RSS hit for the company, dropped
  if older than 3 days — most microcaps will show "no recent news," which is
  accurate, not a failure.
- **A stock passing the screen can still be a value trap** — heavy capex
  funded by debt with deteriorating operating cash flow is a real pattern
  this screen does not filter out. The OCF trend line exists specifically so
  you can catch that case yourself; it is shown, not screened.
- **`data/financials.json` can go stale** if `build_financials.py` isn't
  re-run after a results season — the digest states the cache's build date so
  this is visible, not silent.
