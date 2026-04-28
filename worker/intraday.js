/**
 * macro-terminal intraday Worker
 * Cloudflare Workers free tier: 100k req/day, 10ms CPU.
 *
 * Routes:
 *   GET /quote/:symbol            -> Yahoo v8 chart, 60s KV cache
 *   GET /stooq/:symbol            -> Stooq daily CSV -> JSON, 5min KV cache
 *   GET /ticker                   -> Pre-shaped ticker payload
 *
 * Bind a KV namespace called CACHE in wrangler.toml.
 *
 * The frontend calls these endpoints during US/EU/Asia trading hours; macro
 * series come from /data/*.json (committed by the GitHub Actions ETL).
 */

const TICKER_SYMBOLS = [
  { sym: "SPX",    yahoo: "^GSPC" },
  { sym: "SX5E",   yahoo: "^STOXX50E" },
  { sym: "NKY",    yahoo: "^N225" },
  { sym: "HSI",    yahoo: "^HSI" },
  { sym: "DXY",    yahoo: "DX-Y.NYB" },
  { sym: "VIX",    yahoo: "^VIX" },
  { sym: "WTI",    yahoo: "CL=F" },
  { sym: "BRENT",  yahoo: "BZ=F" },
  { sym: "GOLD",   yahoo: "GC=F" },
  { sym: "COPPER", yahoo: "HG=F" },
  { sym: "BTC",    yahoo: "BTC-USD" },
];

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Max-Age": "86400",
};

function json(body, ttl = 60) {
  return new Response(JSON.stringify(body), {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": `public, max-age=${ttl}`,
      ...CORS,
    },
  });
}

async function cached(env, key, ttl, build) {
  const hit = await env.CACHE.get(key, "json");
  if (hit) return hit;
  const fresh = await build();
  // putting JSON, not raw bytes, so we can read it back as JSON
  await env.CACHE.put(key, JSON.stringify(fresh), { expirationTtl: ttl });
  return fresh;
}

async function yahooQuote(symbol) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1d&interval=1m`;
  const r = await fetch(url, {
    cf: { cacheTtl: 30 },
    headers: { "User-Agent": "macro-terminal-worker/1.0" },
  });
  if (!r.ok) throw new Error(`Yahoo ${r.status}`);
  const j = await r.json();
  const result = j?.chart?.result?.[0];
  if (!result) throw new Error("Yahoo: no result");
  const meta = result.meta || {};
  const closes = result.indicators?.quote?.[0]?.close || [];
  const ts = result.timestamp || [];
  const series = ts.map((t, i) => [t, closes[i]]).filter((p) => p[1] != null);
  return {
    symbol,
    price: meta.regularMarketPrice ?? series.at(-1)?.[1] ?? null,
    prevClose: meta.chartPreviousClose ?? null,
    currency: meta.currency ?? null,
    series,
    asOf: meta.regularMarketTime || Math.floor(Date.now() / 1000),
  };
}

async function stooqDaily(symbol) {
  const url = `https://stooq.com/q/d/l/?s=${encodeURIComponent(symbol)}&i=d`;
  const r = await fetch(url, { cf: { cacheTtl: 300 } });
  if (!r.ok) throw new Error(`Stooq ${r.status}`);
  const text = await r.text();
  const lines = text.trim().split(/\r?\n/);
  if (lines.length < 2) return { symbol, series: [] };
  const header = lines[0].split(",");
  const idxDate = header.indexOf("Date");
  const idxClose = header.indexOf("Close");
  const series = lines.slice(1).map((row) => {
    const cols = row.split(",");
    const v = parseFloat(cols[idxClose]);
    return Number.isFinite(v) ? [cols[idxDate], v] : null;
  }).filter(Boolean);
  return { symbol, series };
}

async function buildTicker() {
  const results = await Promise.all(
    TICKER_SYMBOLS.map(async (t) => {
      try {
        const q = await yahooQuote(t.yahoo);
        const chg = q.prevClose ? ((q.price / q.prevClose - 1) * 100) : 0;
        return { sym: t.sym, val: q.price, chg: +chg.toFixed(2) };
      } catch {
        return null;
      }
    })
  );
  return { asOf: new Date().toISOString(), items: results.filter(Boolean) };
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const url = new URL(request.url);
    const parts = url.pathname.replace(/^\/+|\/+$/g, "").split("/");

    try {
      if (parts[0] === "quote" && parts[1]) {
        const sym = decodeURIComponent(parts[1]);
        const data = await cached(env, `q:${sym}`, 60, () => yahooQuote(sym));
        return json(data, 60);
      }
      if (parts[0] === "stooq" && parts[1]) {
        const sym = decodeURIComponent(parts[1]);
        const data = await cached(env, `s:${sym}`, 300, () => stooqDaily(sym));
        return json(data, 300);
      }
      if (parts[0] === "ticker") {
        const data = await cached(env, "ticker:v1", 60, buildTicker);
        return json(data, 60);
      }
      return json({ error: "not found" }, 0);
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e) }), {
        status: 502,
        headers: { "Content-Type": "application/json", ...CORS },
      });
    }
  },
};
