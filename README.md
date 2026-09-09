# UMSI — US Market Sentiment Index Dashboard

A static, GitHub Pages-compatible market sentiment dashboard. **GitHub Actions fetches/calculates data; JSON stores state; GitHub Pages only displays it.** No paid server is required.

## What is included

- Fixed-composition UMSI Core and expanded UMSI Live
- Stress Index 0–100
- Fragility Index 0–100
- Market Regime
- S&P 500 snapshot
- 7-factor indicator table
- 1Y / 3Y / 5Y / 10Y / MAX UMSI chart
- Historical event table
- Forward-return table by UMSI zone
- Five-state research decision engine using QQQ plus SOXX/SMH trends
- Honest day-five confirmation, fixed in/out-of-sample validation and risk statistics
- Per-source timestamps and stale-data flags
- GitHub Actions automatic updates

## UMSI Core and Live

Historical charts and validation use **UMSI Core**, whose four dependable long-history factors and weights stay fixed. The weights are the original long-history portion normalized to 100%: term and breadth are each 30.77%; VIX is 23.08%; drawdown is 15.38%. Public high-yield OAS history is retained in UMSI Live because the free endpoint may return a shorter window.

**UMSI Live** adds the shorter AAII and equity put/call histories:

| Component | Weight |
|---|---:|
| VIX / VIX3M term structure | 20% |
| High-yield OAS | 20% |
| Market breadth | 20% |
| VIX level | 15% |
| Equity put/call | 10% |
| S&P 500 52-week drawdown | 10% |
| AAII bull-bear spread | 5% |

Formula:

`UMSI Live = 0.20T + 0.20C + 0.20B + 0.15V + 0.10P + 0.10D + 0.05A`

All sub-scores are normalized to 0–100 using rolling historical percentiles.

## Important V1 data note

The free/public data architecture deliberately does **not** fabricate missing history.

- FRED provides long histories for VIX, VIX3M and ICE BofA HY OAS.
- Yahoo Finance (with Stooq fallback) supplies long price histories used for S&P 500 and the RSP/SPY breadth proxy.
- Cboe provides the current equity put/call ratio. The repository accumulates this value over time.
- AAII's public page exposes recent weekly sentiment, while its complete long-history download can require membership. The repository accumulates public readings over time.

Therefore older rows may not contain AAII or put/call. This no longer changes the historical UMSI Core. UMSI Live still stores `live_quality` so the UI can disclose whether all seven live inputs were available.

This is preferable to inserting synthetic data.

## File structure

```text
/
├── index.html
├── css/
│   └── style.css
├── js/
│   ├── app.js
│   ├── charts.js
│   └── calculations.js
├── data/
│   ├── daily.json
│   ├── intraday.json
│   ├── history.json
│   ├── history.csv              # created by Action
│   └── source_history.json
├── scripts/
│   ├── common.py
│   ├── decision_engine.py
│   ├── fetch_intraday.py
│   ├── build_history.py
│   └── calculate_umsi.py
├── .github/
│   └── workflows/
│       └── update-data.yml
├── requirements.txt
├── .gitignore
├── .nojekyll
└── README.md
```

## Fastest GitHub setup

1. Create a new **public GitHub repository**.
2. Upload all files/folders from this project to the repository root.
3. Open **Actions → Update UMSI Data → Run workflow** once.
4. Wait for that run to finish and confirm `data/history.csv`, `daily.json`, `history.json`, and `intraday.json` were updated.
5. Open **Settings → Pages**.
6. Under **Build and deployment**, choose **Deploy from a branch**.
7. Select branch **main** and folder **/(root)**, then Save.
8. Open the GitHub Pages URL shown by GitHub.

No API key is required by the default configuration.

## Automatic update schedule

The workflow has two schedules:

- Every ~30 minutes during a broad US-market UTC window: refreshes `intraday.json`.
- Once after the US cash-market close: rebuilds long history, current UMSI, Stress, Fragility, event stats and forward returns.

GitHub scheduled Actions are not guaranteed to start at the exact cron minute. That is normal.

## Run locally

Python:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_intraday.py
python scripts/build_history.py
python scripts/calculate_umsi.py
```

Then serve the repository with a simple static server (opening `index.html` directly can be blocked by browser `fetch()` security):

```bash
python -m http.server 8000
```

Open `http://localhost:8000`.

## Data-source fallbacks / failure behavior

- A failed slow source does not delete prior valid readings.
- AAII and Cboe values are stored in `source_history.json` and retained when a fetch fails.
- Intraday VIX / VIX3M fall back to latest FRED daily observations if the Yahoo chart request fails.
- The browser displays `STALE DATA` if any important input is stale.
- Missing factors reduce `calculation_quality`; they do not crash the model.

## Breadth methodology in the free version

True constituent-level breadth requires a dependable constituent-history/market-data feed and introduces survivorship-bias issues. V1 therefore uses a transparent free-data proxy:

`breadth_raw = 60% × 63-day relative return(RSP/SPY) + 40% × 252-day relative return(RSP/SPY)`

A rising equal-weight index versus cap-weighted SPY is interpreted as broader participation. This adapter can later be replaced by `% of S&P 500 stocks above 50DMA/200DMA` without changing the front end.

## Research decision engine

The five states are `ADD_WATCH`, `ADD_CONFIRMED`, `HOLD_NO_CHASE`, `REDUCE_WATCH`, and `DEFENSIVE`. They combine UMSI Core, Stress, Fragility, breadth and trend scores calculated from unleveraged QQQ and SOXX/SMH prices. The trend score uses price versus 20/50/200-day averages, moving-average alignment and slope, and 63-day relative strength versus SPY.

Signals are calculated from closing data and are applied no earlier than the next session in simulations. The displayed exposure number is a normalized research score, not a recommendation or brokerage instruction.

Forward-return samples enter only on the fifth consecutive session in a UMSI zone. This removes the earlier look-ahead error that measured returns from day one after using the next four sessions to qualify the signal. Validation is split at 2018-01-01, and simulations include a 10-basis-point cost per full exposure turn plus maximum-drawdown and turnover statistics.

## Stress formula

Higher = more stress:

- 30% VIX stress
- 25% term-structure stress
- 25% high-yield credit stress
- 20% drawdown stress

## Fragility formula (V1)

Higher = more vulnerable / complacent:

- 25% narrow breadth
- 15% low-volatility complacency
- 10% VIX-curve complacency
- 10% AAII optimism
- 10% low put/call
- 15% cap-weight concentration proxy
- 15% S&P 500 stretch above 200DMA

A future V2 can add valuation, CFTC/NAAIM positioning, dealer gamma or other premium/credentialed feeds.

## Disclaimer

This is a research/visualization tool, not investment advice. Public market data can be delayed, changed, revised, rate-limited or temporarily unavailable. Backtests are sensitive to data availability, methodology and survivorship bias.
