# macro-terminal

Live, zero-cost build of the MACRO Global Economic Terminal prototype.

The frontend is the prototype's compiled React app, surgically patched to fetch
`./data/macro.json` instead of using a hardcoded `MACRO` constant. The backend
is a small Python ETL package that hits 100 % free, public APIs (FRED, ECB SDW,
Stooq, Yahoo, GDELT, World Bank, IMF, Frankfurter, Treasury Fiscal Data, CNN
Fear & Greed, CBOE, Iacoviello GPR) and writes one JSON file per section.

## Run locally

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. (Optional but recommended) get a free FRED API key
#    https://fredaccount.stlouisfed.org/apikeys
export FRED_KEY=...   # without this, ETL falls back to FRED's keyless CSV

# 3. Run the ETL
python -m etl.run_all
# → writes data/{growth,inflation,labor,monetary,external,markets,
#                fiscal,risk,news,overview,macro}.json

# 4. Mirror into public/ and serve
cp -f data/*.json public/data/
python -m http.server -d public 8000
# open http://localhost:8000
```

## Architecture

```
   GitHub Actions (cron, hourly)        Cloudflare Pages / GitHub Pages
   ┌──────────────────────────┐         ┌────────────────────────────┐
   │ etl/run_all.py           │ writes  │ public/index.html          │
   │  fetch_growth.py         │────────▶│  (fetches ./data/*.json)   │
   │  fetch_inflation.py      │  JSON   │ public/data/*.json         │
   │  fetch_labor.py          │         └────────────────────────────┘
   │  fetch_monetary.py       │                       │
   │  fetch_external.py       │                       │ (optional)
   │  fetch_markets.py        │                       ▼
   │  fetch_fiscal.py         │         ┌────────────────────────────┐
   │  fetch_risk.py           │         │ Cloudflare Worker          │
   │  fetch_news.py           │         │  /quote/:sym, /ticker      │
   │  fetch_overview.py       │         │  60s KV cache              │
   └──────────────────────────┘         └────────────────────────────┘
```

* **Cost: $0/month.** Two free API keys (FRED, EIA — both optional). Free
  tiers: GitHub Actions 2,000 min/mo, GitHub Pages unlimited static, CF
  Workers 100k req/day.
* **Refresh**: ETL is hourly. Most macro series update monthly so this is
  generous. Markets / news refresh every run.
* **Storage**: ~25 KB per section JSON, 200 KB total bundle.

## What's covered

100 % external free data, no scraping, no proxies. ~94 % of the prototype
panels map cleanly. The 6 % that requires paywalled sources is intentionally
omitted (greyed in UI):

* Dropped: ISM PMI Manufacturing, ISM PMI Services, Conference Board LEI,
  IIF EM portfolio flows, AAII Sentiment, S&P 500 breadth (% above 50/200 DMA,
  new highs/lows).

Everything else is live: GDP, CPI/PCE, NFP, JOLTS, full Treasury curve,
IG/HY OAS, M1/M2, STLFSI, NY Fed recession prob, Case-Shiller, mortgage
rates, BoP, debt & deficit, equity indices, VIX/MOVE/SKEW, Fear & Greed,
put/call, GPR, GDELT news flashpoints.

## File layout

```
macro-terminal/
├── README.md
├── requirements.txt
├── etl/
│   ├── __init__.py
│   ├── _common.py           ← shared HTTP / FRED / Stooq / Yahoo helpers
│   ├── fetch_growth.py
│   ├── fetch_inflation.py
│   ├── fetch_labor.py
│   ├── fetch_monetary.py
│   ├── fetch_external.py
│   ├── fetch_markets.py
│   ├── fetch_fiscal.py
│   ├── fetch_risk.py
│   ├── fetch_news.py
│   ├── fetch_overview.py    ← composes health-score + ticker
│   └── run_all.py           ← entrypoint
├── data/                    ← canonical ETL output
│   ├── macro.json
│   ├── overview.json
│   ├── growth.json … risk.json
│   └── news.json
├── public/                  ← deployable static site (CF Pages / GH Pages)
│   ├── index.html           ← prototype, patched to fetch ./data/macro.json
│   └── data/                ← mirror of /data/, written by CI
├── worker/
│   ├── intraday.js          ← Cloudflare Worker for live ticker / quotes
│   └── wrangler.toml
└── .github/workflows/
    ├── etl.yml              ← hourly cron, runs run_all.py, commits /data/
    └── pages.yml            ← deploys /public/ to GitHub Pages
```

## Wiring the intraday Worker (optional)

```bash
cd worker
wrangler kv:namespace create CACHE      # paste id into wrangler.toml
wrangler deploy
```

Then in the deployed HTML, set `window.MACRO_WORKER_URL = "https://<your-worker>.workers.dev"` (a one-line `<script>` injected before the bundle, or commit it directly into `public/index.html`). The frontend auto-refreshes the ticker every 60 seconds when this is set.

## Adding / replacing a series

Each section file is ~50 lines. To add a new tile:

1. Pick a FRED series ID (or any other free source helper in `_common.py`).
2. Add it to the relevant `fetch_<section>.py` payload.
3. Push — ETL picks it up on the next cron tick.

The frontend reads the JSON shape directly; if a tile expects `{val, series, delta}` and you provide it, the existing component renders it without changes.

## License & attribution

Data is sourced from public agencies and remains under their respective terms
(FRED, ECB SDW, Stooq, Yahoo Finance, GDELT, World Bank, IMF, Frankfurter,
U.S. Treasury, CNN, CBOE, Iacoviello GPR). Display attribution accordingly
when shipping a public version.
