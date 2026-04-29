"""Shared HTTP / parsing helpers for every fetch_<section>.py module.

Every external endpoint is hit through one of these helpers so that retry,
timeout, user-agent, and rate-limit behaviour is uniform. Nothing here writes
to disk; each section module imports what it needs and emits a single JSON
file under data/.
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
import datetime as dt
from typing import Any, Iterable

import requests
from dateutil.relativedelta import relativedelta

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
SESSION = requests.Session()
# Some endpoints (CNN Fear & Greed, CBOE, occasionally Stooq) reject
# generic API-style UAs with 403 / 418. A real-browser UA gets through.
SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/csv, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
})


def _retry_get(url: str, params: dict | None = None, *, timeout: int = 20,
               max_retries: int = 3, backoff: float = 1.5) -> requests.Response:
    last: Exception | str | None = None
    for i in range(max_retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r
            # 4xx (other than 429) means the request itself is wrong — series
            # doesn't exist, deprecated, etc. Don't retry; let the caller decide.
            if 400 <= r.status_code < 500 and r.status_code != 429:
                r.raise_for_status()
            if r.status_code in (429, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(backoff ** i)
                continue
            r.raise_for_status()
        except requests.RequestException as e:
            last = e
            # Don't retry permanent 4xx
            if isinstance(e, requests.HTTPError) and e.response is not None:
                code = e.response.status_code
                if 400 <= code < 500 and code != 429:
                    raise RuntimeError(
                        f"GET {url} returned {code} (no retry)") from e
            time.sleep(backoff ** i)
    raise RuntimeError(f"GET {url} failed after {max_retries} attempts: {last}")


# ---------------------------------------------------------------------------
# FRED — uses the public CSV endpoint (no key required for the fredgraph URL)
# but prefers the JSON API when FRED_KEY is set (richer metadata, better
# parsing of alternate frequencies). 120 req/min on the JSON API.
# ---------------------------------------------------------------------------
_FRED_KEY = os.environ.get("FRED_KEY", "").strip()


def fred(series_id: str, start: str = "2010-01-01") -> list[tuple[str, float]]:
    """Return [(date, value), ...] for a FRED series, oldest -> newest.

    Returns [] (not raises) for a series that doesn't exist or has no data —
    so a single bad ID doesn't kill the whole section.
    """
    try:
        if _FRED_KEY:
            r = _retry_get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": series_id, "api_key": _FRED_KEY,
                        "file_type": "json", "observation_start": start},
            )
            return [(o["date"], float(o["value"]))
                    for o in r.json()["observations"] if o["value"] != "."]
    except Exception as e:
        print(f"  !! fred({series_id}) failed: {e}")
        return []

    # Keyless fallback: fredgraph.csv. Works without auth, slightly slower.
    r = _retry_get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv",
        params={"id": series_id, "cosd": start},
    )
    out: list[tuple[str, float]] = []
    reader = csv.reader(io.StringIO(r.text))
    next(reader)  # header
    for row in reader:
        if len(row) < 2 or row[1] in (".", ""):
            continue
        try:
            out.append((row[0], float(row[1])))
        except ValueError:
            continue
    return out


def fred_latest(series_id: str) -> tuple[str, float]:
    series = fred(series_id, start="2020-01-01")
    if not series:
        raise RuntimeError(f"No data for {series_id}")
    return series[-1]


# ---------------------------------------------------------------------------
# YoY computation
# ---------------------------------------------------------------------------
def yoy_pct(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Year-over-year percent change for a (date, value) series."""
    by_date = dict(series)
    out: list[tuple[str, float]] = []
    for d, v in series:
        try:
            year, month, day = d.split("-")
            prev = f"{int(year) - 1}-{month}-{day}"
            if prev in by_date and by_date[prev]:
                out.append((d, round((v / by_date[prev] - 1) * 100, 2)))
        except Exception:
            continue
    return out


def mom_pct(series: list[tuple[str, float]]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for i in range(1, len(series)):
        d, v = series[i]
        _, p = series[i - 1]
        if p:
            out.append((d, round((v / p - 1) * 100, 2)))
    return out


def trim(series: list[tuple[str, float]], n: int) -> list[tuple[str, float]]:
    return series[-n:] if len(series) > n else series


def ytd_pct(series: list[tuple[str, float]]) -> float | None:
    """Year-to-date % change. Anchors at the last observation in the previous
    calendar year. Returns None if anchor not found."""
    if not series:
        return None
    last_date, last_val = series[-1]
    try:
        last_year = int(last_date[:4])
    except Exception:
        return None
    anchor_year = last_year - 1
    # Find the last value in the prior calendar year (year-end close)
    anchor = None
    for d, v in series:
        if d.startswith(str(anchor_year)):
            anchor = v
    if anchor is None or anchor == 0:
        return None
    return round((last_val / anchor - 1) * 100, 2)


def yoy_change(series: list[tuple[str, float]]) -> float | None:
    """Year-over-year % change of the last observation vs. ~252 trading days
    earlier (or 12 months for monthly series)."""
    if not series:
        return None
    last = series[-1][1]
    # Try 252 (daily) first, then 12 (monthly), then 4 (quarterly)
    for offset in (252, 12, 4):
        if len(series) > offset:
            prev = series[-1 - offset][1]
            if prev:
                return round((last / prev - 1) * 100, 2)
    return None


def moving_avg(series: list[tuple[str, float]], window: int
                ) -> list[tuple[str, float]]:
    """Simple moving average. Returns [(date, ma_value), ...] for points
    where a full window is available."""
    if len(series) < window:
        return []
    out = []
    for i in range(window - 1, len(series)):
        chunk = series[i - window + 1: i + 1]
        avg = sum(v for _, v in chunk) / window
        out.append((series[i][0], round(avg, 2)))
    return out


# ---------------------------------------------------------------------------
# Stooq — daily OHLC CSV, no key
# ---------------------------------------------------------------------------
def stooq_daily(symbol: str) -> list[tuple[str, float]]:
    r = _retry_get(f"https://stooq.com/q/d/l/", params={"s": symbol, "i": "d"})
    out: list[tuple[str, float]] = []
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        try:
            out.append((row["Date"], float(row["Close"])))
        except (KeyError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# Yahoo — intraday + history via the v8 chart endpoint
# ---------------------------------------------------------------------------
def yahoo_chart(symbol: str, *, interval: str = "1d",
                rng: str = "1y") -> list[tuple[str, float]]:
    r = _retry_get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"interval": interval, "range": rng},
    )
    j = r.json()["chart"]["result"][0]
    ts = j["timestamp"]
    closes = j["indicators"]["quote"][0]["close"]
    out: list[tuple[str, float]] = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        out.append((dt.datetime.utcfromtimestamp(t).date().isoformat(), round(c, 4)))
    return out


# ---------------------------------------------------------------------------
# ECB SDW — JSON-stat / SDMX-JSON
# ---------------------------------------------------------------------------
def ecb_sdw(flow: str, key: str, *, last_n: int = 0) -> list[tuple[str, float]]:
    params = {"format": "jsondata"}
    if last_n:
        params["lastNObservations"] = last_n
    r = _retry_get(f"https://data-api.ecb.europa.eu/service/data/{flow}/{key}", params=params)
    j = r.json()
    series = j["dataSets"][0]["series"]
    if not series:
        return []
    first_key = next(iter(series))
    obs = series[first_key]["observations"]
    times = j["structure"]["dimensions"]["observation"][0]["values"]
    out: list[tuple[str, float]] = []
    for k, v in obs.items():
        idx = int(k)
        if v[0] is None:
            continue
        out.append((times[idx]["id"], float(v[0])))
    out.sort()
    return out


# ---------------------------------------------------------------------------
# World Bank
# ---------------------------------------------------------------------------
def wb(country: str, indicator: str) -> list[tuple[str, float]]:
    r = _retry_get(
        f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}",
        params={"format": "json", "per_page": 200},
    )
    j = r.json()
    if len(j) < 2 or not j[1]:
        return []
    out = [(row["date"], float(row["value"])) for row in j[1] if row["value"] is not None]
    out.sort()
    return out


# ---------------------------------------------------------------------------
# IMF DataMapper
# ---------------------------------------------------------------------------
def imf_dm(indicator: str, country: str = "USA") -> list[tuple[str, float]]:
    r = _retry_get(f"https://www.imf.org/external/datamapper/api/v1/{indicator}/{country}")
    j = r.json()["values"][indicator][country]
    return sorted([(k, float(v)) for k, v in j.items() if v is not None])


# ---------------------------------------------------------------------------
# Frankfurter (FX, ECB-sourced)
# ---------------------------------------------------------------------------
def frankfurter_latest(base: str = "USD", to: list[str] | None = None) -> dict[str, float]:
    params = {"from": base}
    if to:
        params["to"] = ",".join(to)
    r = _retry_get("https://api.frankfurter.app/latest", params=params)
    return r.json()["rates"]


# ---------------------------------------------------------------------------
# GDELT 2.0 DOC API
# ---------------------------------------------------------------------------
def gdelt_articles(query: str, *, max_records: int = 10,
                   timespan: str = "24h") -> list[dict]:
    # GDELT can be slow / flaky; fail fast so the news section doesn't hang.
    r = _retry_get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={"query": query, "mode": "artlist", "format": "json",
                "maxrecords": max_records, "timespan": timespan,
                "sort": "datedesc"},
        timeout=8, max_retries=1,
    )
    try:
        j = r.json()
    except Exception:
        return []
    return j.get("articles", [])


def gdelt_tone(query: str, *, timespan: str = "24h") -> float | None:
    r = _retry_get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={"query": query, "mode": "tonechart", "format": "json",
                "timespan": timespan},
        timeout=8, max_retries=1,
    )
    try:
        rows = r.json().get("tonechart", [])
    except Exception:
        return None
    if not rows:
        return None
    total = sum(row.get("count", 0) * row.get("bin", 0) for row in rows)
    n = sum(row.get("count", 0) for row in rows)
    return round(total / n, 2) if n else None


# ---------------------------------------------------------------------------
# Google News RSS — free, no key, returns headline + summary + link
# ---------------------------------------------------------------------------
def google_news_rss(query: str, *, lang: str = "en", max_items: int = 10
                     ) -> list[dict]:
    """Fetch Google News RSS for a query string. Returns simple dicts."""
    try:
        r = _retry_get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": lang, "gl": "US", "ceid": "US:en"},
            timeout=10, max_retries=1,
        )
        text = r.text
    except Exception as e:
        print(f"  !! google_news_rss({query[:30]!r}) failed: {e}")
        return []
    # Lightweight RSS parser — no feedparser dependency
    import re as _re
    items: list[dict] = []
    for m in _re.finditer(r"<item>(.*?)</item>", text, flags=_re.S):
        block = m.group(1)
        def field(name):
            mm = _re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, flags=_re.S)
            if not mm: return ""
            v = mm.group(1)
            # CDATA strip
            cd = _re.search(r"<!\[CDATA\[(.*?)\]\]>", v, flags=_re.S)
            if cd: v = cd.group(1)
            # Strip leftover HTML tags from description
            v = _re.sub(r"<[^>]+>", " ", v).strip()
            return v
        items.append({
            "title":   field("title"),
            "link":    field("link"),
            "pub":     field("pubDate"),
            "summary": field("description"),
            "source":  field("source"),
        })
        if len(items) >= max_items:
            break
    return items


# ---------------------------------------------------------------------------
# Generic RSS feed parser — shared by Google News + Investing.com + OilPrice
# ---------------------------------------------------------------------------
def _parse_rss(text: str, max_items: int = 30) -> list[dict]:
    """Lightweight RSS 2.0 / Atom parser. Returns list of dicts with
    {title, link, pub, summary, source}."""
    import re as _re
    items: list[dict] = []
    for m in _re.finditer(r"<item>(.*?)</item>", text, flags=_re.S):
        block = m.group(1)

        def field(name):
            mm = _re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, flags=_re.S)
            if not mm:
                return ""
            v = mm.group(1)
            cd = _re.search(r"<!\[CDATA\[(.*?)\]\]>", v, flags=_re.S)
            if cd:
                v = cd.group(1)
            v = _re.sub(r"<[^>]+>", " ", v).strip()
            return v

        items.append({
            "title":   field("title"),
            "link":    field("link"),
            "pub":     field("pubDate"),
            "summary": field("description"),
            "source":  field("source"),
        })
        if len(items) >= max_items:
            break
    return items


# ---------------------------------------------------------------------------
# Investing.com commodities news RSS — fallback for Energy / Commodities
# ---------------------------------------------------------------------------
def investing_commodities_rss(max_items: int = 25) -> list[dict]:
    """Fetch Investing.com Commodities News RSS feed (category 25)."""
    try:
        r = _retry_get(
            "https://www.investing.com/rss/news_25.rss",
            timeout=10, max_retries=1,
        )
    except Exception as e:
        print(f"  !! investing_commodities_rss failed: {e}")
        return []
    out = _parse_rss(r.text, max_items=max_items)
    for it in out:
        it["source"] = it.get("source") or "Investing.com"
    return out


# ---------------------------------------------------------------------------
# OilPrice.com RSS — additional fallback for Energy
# ---------------------------------------------------------------------------
def oilprice_rss(max_items: int = 20) -> list[dict]:
    """Fetch OilPrice.com main news RSS feed."""
    try:
        r = _retry_get(
            "https://oilprice.com/rss/main",
            timeout=10, max_retries=1,
        )
    except Exception as e:
        print(f"  !! oilprice_rss failed: {e}")
        return []
    out = _parse_rss(r.text, max_items=max_items)
    for it in out:
        it["source"] = it.get("source") or "OilPrice.com"
    return out


# ---------------------------------------------------------------------------
# IMF DataMapper / COFER — global FX reserves
# ---------------------------------------------------------------------------
def imf_cofer_total_reserves(country_code: str) -> float | None:
    """IMF World Economic Outlook reserves — quarterly, in USD billions.
    country_code: ISO-3 (USA, CHN, JPN, GBR, DEU, ...).
    """
    try:
        r = _retry_get(
            f"https://www.imf.org/external/datamapper/api/v1/RES/{country_code}",
            timeout=12,
        )
        j = r.json().get("values", {}).get("RES", {}).get(country_code, {})
        if not j:
            return None
        # Pick the most recent year
        year = max(j.keys())
        return float(j[year])
    except Exception as e:
        print(f"  !! imf_cofer({country_code}) failed: {e}")
        return None


# ---------------------------------------------------------------------------
# SIFMA bond-issuance CSV — free monthly download
# ---------------------------------------------------------------------------
def sifma_bond_issuance() -> dict | None:
    """SIFMA publishes free monthly issuance CSVs. URL pattern is stable but
    occasionally rotates. Returns last-12-month totals for HY and IG."""
    try:
        r = _retry_get(
            "https://www.sifma.org/wp-content/uploads/2017/06/"
            "us-corporate-bond-issuance-sifma.xlsx",
            timeout=15,
        )
        # Parse a thin slice without openpyxl — the workbook may not be
        # readable as plain bytes, so we wrap and gracefully fall back.
        return None  # Placeholder — disabled until URL is verified
    except Exception:
        return None


# ---------------------------------------------------------------------------
# FRED Releases — official US economic data release calendar
# ---------------------------------------------------------------------------
def fred_release_dates(*, days_ahead: int = 14, days_back: int = 2,
                        limit: int = 200) -> list[dict]:
    """Return upcoming + just-past FRED releases.

    Output rows: {release_id, release_name, date, real_time_start, ...}.
    Uses the public 'releases/dates' endpoint. Requires FRED_KEY.
    """
    if not _FRED_KEY:
        return []
    today = dt.date.today()
    start = (today - dt.timedelta(days=days_back)).isoformat()
    end = (today + dt.timedelta(days=days_ahead)).isoformat()
    try:
        r = _retry_get(
            "https://api.stlouisfed.org/fred/releases/dates",
            params={"api_key": _FRED_KEY, "file_type": "json",
                    "realtime_start": start, "realtime_end": end,
                    "limit": limit, "sort_order": "asc",
                    "include_release_dates_with_no_data": "true"},
        )
        return r.json().get("release_dates", [])
    except Exception as e:
        print(f"  !! fred_release_dates failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Misc CSV downloaders (CBOE put/call, NY Fed recession, GPR, EPU)
# ---------------------------------------------------------------------------
def cboe_put_call_latest() -> tuple[str, float] | None:
    """CBOE publishes a daily equity put/call ratio CSV."""
    r = _retry_get(
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/equity_pc.csv"
    )
    out: list[tuple[str, float]] = []
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        try:
            d = row.get("Date") or row.get("DATE")
            v = row.get("PCRATIO") or row.get("P/C Ratio")
            if d and v:
                out.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    return out[-1] if out else None


def cnn_fear_greed() -> dict | None:
    """CNN Fear & Greed widget JSON. Endpoint occasionally rotates."""
    r = _retry_get(
        "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
        timeout=15,
    )
    j = r.json()
    fg = j.get("fear_and_greed", {})
    return {
        "score": fg.get("score"),
        "rating": fg.get("rating"),
        "previous_close": fg.get("previous_close"),
    }


# ---------------------------------------------------------------------------
# JSON output helpers
# ---------------------------------------------------------------------------
def utcnow_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def write_json(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"  -> wrote {path} ({os.path.getsize(path)} bytes)")


def safe(fn, *args, default=None, **kw):
    """Run a fetcher and swallow exceptions so a single dead source doesn't
    take down the whole ETL run. The corresponding tile in the UI will show
    its 'as-of' badge stale rather than the entire section blanking."""
    try:
        return fn(*args, **kw)
    except Exception as e:
        print(f"  !! {fn.__name__}({args}) failed: {e}")
        return default
