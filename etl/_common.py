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
