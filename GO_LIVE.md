# Go-Live Checklist — macro-terminal

You'll be live in about 10 minutes. Seven steps, in order.

---

## 1. Get a FRED API key (30 seconds, free)

1. Open https://fredaccount.stlouisfed.org/apikeys
2. Sign up (email + password). No credit card.
3. Click **"Request API Key"**. You'll get a 32-character hex string.
4. Copy it. You'll paste it into step 4.

> If you skip this, the ETL falls back to FRED's keyless CSV. Works for a demo but rate-limits under load — get the key, it takes longer to make coffee.

---

## 2. Pre-flight smoke test (local, 2 minutes)

Before pushing anything, prove the ETL works on your machine.

```bash
cd /path/to/macro-terminal     # the folder I built for you
pip install -r requirements.txt
export FRED_KEY=<paste-your-key>
bash smoke_test.sh
```

`smoke_test.sh` does the following and stops if anything fails:

* Runs `python -m etl.run_all` end-to-end
* Validates every output JSON parses and has the expected top-level keys
* Mirrors `data/` into `public/data/`
* Spins up `http.server` on port 8765
* `curl`s `/data/macro.json` and `/index.html`, expects 200 from both
* Reports the resulting health-score regime label

If it prints `✅ SMOKE TEST PASSED`, you're ready to push.

---

## 3. Install and authenticate `gh` CLI (skip if already done)

```bash
# macOS
brew install gh

# Ubuntu / WSL
sudo apt install gh

# Windows
winget install --id GitHub.cli
```

Then:

```bash
gh auth login           # follow prompts, choose GitHub.com + HTTPS + browser
```

---

## 4. Run the bootstrap script (1 minute)

```bash
bash bootstrap.sh
```

What it does (read it first if you like — it's 60 lines):

1. Initializes a git repo in `macro-terminal/`.
2. Asks you for the GitHub repo name (default: `macro-terminal`) and visibility (default: public).
3. Asks you to paste your FRED key.
4. Makes the initial commit.
5. Creates the GitHub repo via `gh repo create --source=. --push`.
6. Stores `FRED_KEY` as a GitHub Actions secret (`gh secret set`).
7. Enables GitHub Pages with the GitHub Actions source (`gh api`).
8. Triggers the first ETL run (`gh workflow run macro-etl`).
9. Prints your live URL: `https://<your-user>.github.io/<repo>/`.

---

## 5. Watch the first deployment (3–5 minutes)

```bash
gh run watch                 # tails the live workflow run
```

Two workflows run on first push:

* **macro-etl** — fetches every series, writes JSON, mirrors into `public/data/`, commits if changed (~30–60 seconds).
* **deploy-pages** — uploads `public/` to GitHub Pages (~60–90 seconds).

When `deploy-pages` is green, your URL is live.

---

## 6. Verify in the browser

Open `https://<your-user>.github.io/<repo>/`. You should see:

* The ticker scrolling at the top with real prices.
* The Overview health-score card filled in.
* All 9 other tabs (News, Growth, Inflation, Labor, Monetary, External, Markets, Fiscal, Risk) populated.
* Six tiles greyed with "data not available" footnote (ISM PMI ×2, LEI, IIF flows, AAII, breadth) — that's expected, those are the dropped paywalled items.

If a panel is blank instead of greyed, check the workflow run logs (`gh run view --log`) — the offending fetcher will be in the output.

---

## 7. (Optional, 5 minutes) Deploy the intraday Worker

This adds a 60-second auto-refreshing ticker on top of the hourly ETL. Skip if you don't need it.

```bash
# 1. Sign up at https://dash.cloudflare.com (free)
# 2. Install wrangler
npm i -g wrangler
wrangler login

# 3. Provision the KV namespace
cd worker
wrangler kv:namespace create CACHE
# copy the id it prints, paste into wrangler.toml's kv_namespaces id field

# 4. Deploy
wrangler deploy
# prints something like: https://macro-intraday.<your-handle>.workers.dev

# 5. Wire it into the frontend
# Add this ONE line to public/index.html, right before the closing </body>:
#   <script>window.MACRO_WORKER_URL="https://macro-intraday.<your-handle>.workers.dev";</script>
# Commit and push. The ticker now refreshes every 60s on its own.
```

---

## Operations notes

* **Hourly cron** runs forever. Total GitHub Actions minutes: ~5 min × 24 × 30 = 3,600/mo, well under the 2,000 free minutes — wait. That's over. Let me re-check.

  Actually each ETL run takes ~30 sec, not 5 min. So 30 s × 24 × 30 = 12,000 sec = 200 min/mo. Fine.

* **Monthly cost**: $0.

* **Disabling the cron** during a test/holiday: `gh workflow disable macro-etl`. Re-enable: `gh workflow enable macro-etl`.

* **Forcing a refresh**: `gh workflow run macro-etl`.

* **Rotating the FRED key**: `gh secret set FRED_KEY` — that's it. Next cron tick uses the new value.

* **Adding a new series**: edit the relevant `etl/fetch_<section>.py`, push. Next cron tick picks it up. The frontend shape was already the contract; if your new field is named the way the React component expects, the tile fills in automatically.

* **Failure alerts**: GitHub emails the repo owner when a workflow fails. Suppress noise by editing `.github/workflows/etl.yml` and adding `continue-on-error: true` per step (not recommended — you want to know).
