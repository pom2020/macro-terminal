# Deploy the Cloudflare intraday Worker

**Cost:** $0/month (Cloudflare Workers free tier: 100k req/day, 1k KV writes/day).
**Time:** ~5 minutes.
**Effect:** ticker bar refreshes every 60 seconds and the MOVE/VVIX/SKEW
volatility tiles in the Markets section go live.

---

## One-time setup

In Git Bash, **inside the `macro-terminal/worker/` folder**:

```bash
# 1. Install wrangler (Cloudflare's CLI)
npm install -g wrangler

# 2. Sign in to Cloudflare (opens browser; sign up free if you don't have an account)
wrangler login

# 3. Provision the KV namespace that caches Yahoo responses (60s TTL)
wrangler kv:namespace create CACHE
# It prints a line like:
#   id = "abcd1234ef5678..."
# Copy the id.

# 4. Paste the id into wrangler.toml — open the file and replace
#    REPLACE_ME_WITH_KV_NAMESPACE_ID with the id you just copied.

# 5. Deploy
wrangler deploy

# It prints something like:
#   ✨ Successfully published your Worker
#   https://macro-intraday.<your-handle>.workers.dev
```

Copy that URL — you'll paste it in step 6.

---

## Wire it into the live site

Edit `public/index.html` and find this block near the top:

```html
<script>
  window.MACRO_WORKER_URL = window.MACRO_WORKER_URL || "";
</script>
```

Replace the empty string with your Worker URL:

```html
<script>
  window.MACRO_WORKER_URL = "https://macro-intraday.<your-handle>.workers.dev";
</script>
```

Then in Git Bash:

```bash
git pull --rebase --autostash
git add public/index.html
git commit -m "chore: wire intraday Worker URL"
git push
```

Wait ~90 seconds for the Pages deploy. Hard-reload the live site
(Ctrl+F5). The ticker now refreshes every 60 seconds.

---

## What it does

* `GET /quote/^GSPC` — Yahoo intraday for any symbol with 60s KV cache
* `GET /stooq/^spx` — Stooq daily CSV converted to JSON, 5-min cache
* `GET /ticker` — pre-shaped 11-symbol ticker payload (SPX, Stoxx, Nikkei, HSI,
  DXY, VIX, WTI, Brent, Gold, Copper, BTC), 60s cache

CORS headers are open so any browser can hit the worker directly.

---

## Removing it later

Set `window.MACRO_WORKER_URL = ""` in `public/index.html` — the entry
script's `if (w) { ... }` check skips the auto-refresh when it's empty.
The hourly ETL keeps working unchanged.
