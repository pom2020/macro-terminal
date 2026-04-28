"""Markets section — equity indices, OAS, vol, housing, sentiment.

Breadth / AAII / new-highs-lows are intentionally omitted (paywalled or
fragile). Tiles greyed out in UI.
"""
from __future__ import annotations

from etl._common import (fred, fred_latest, yoy_pct, trim, utcnow_iso,
                          write_json, safe, stooq_daily, yahoo_chart,
                          cnn_fear_greed, cboe_put_call_latest,
                          ytd_pct, yoy_change, moving_avg)


def _idx(symbol: str, label: str) -> dict | None:
    s = safe(stooq_daily, symbol, default=[])
    if not s:
        return None
    last = s[-1][1]
    prev = s[-2][1] if len(s) > 1 else last
    chg = round((last / prev - 1) * 100, 2) if prev else 0
    return {
        "label": label, "price": last, "chg_pct": chg,
        "ytd": ytd_pct(s),                  # NEW
        "yoy": yoy_change(s),                # NEW
        "series": trim(s, 252),
    }


def build() -> dict:
    spx = _idx("^spx", "S&P 500")
    stoxx = _idx("^stoxx50e", "Euro Stoxx 50")
    nikkei = _idx("^nkx", "Nikkei 225")
    hsi = _idx("^hsi", "Hang Seng")

    ig = fred("BAMLC0A0CM") or []
    hy = fred("BAMLH0A0HYM2") or []

    case_shiller = fred("CSUSHPINSA") or []
    cs_yoy = yoy_pct(case_shiller)
    mortgage = fred("MORTGAGE30US") or []
    starts = fred("HOUST") or []
    permits = fred("PERMIT") or []           # NEW: building permits
    # NYSE breadth proxy — A/D Line via Stooq (free)
    nyad = safe(stooq_daily, "^nyad", default=[]) or []
    # SPX for MA50/MA200 — use FRED's daily SP500 series
    spx_daily = fred("SP500") or []
    ma50_series  = moving_avg(spx_daily, 50)
    ma200_series = moving_avg(spx_daily, 200)

    fg = safe(cnn_fear_greed, default=None)
    pc = safe(cboe_put_call_latest, default=None)

    payload = {
        "asOf": utcnow_iso(),
        "narrative": {
            "stance": "neutral",
            "text": "S&P {:,.0f} ({:+.2f}%), VIX {:.1f}, IG OAS {:.0f}bp.".format(
                (spx or {}).get("price") or 0,
                (spx or {}).get("chg_pct") or 0,
                safe(fred_latest, "VIXCLS", default=("", 0))[1] or 0,
                (ig[-1][1] * 100) if ig else 0,
            ),
        },
        "equity": [x for x in (spx, stoxx, nikkei, hsi) if x],
        "spx_series": (spx or {}).get("series", []),
        "credit": {
            "ig_oas": ig[-1][1] if ig else None,
            "hy_oas": hy[-1][1] if hy else None,
            "series_ig": trim(ig, 60),
            "series_hy": trim(hy, 60),
        },
        "vol": {
            "vix": safe(fred_latest, "VIXCLS", default=("", None))[1],
            # MOVE / VVIX / SKEW are not on FRED — they come from the
            # Cloudflare intraday Worker (Yahoo) when configured. Until then,
            # show null and let the UI grey those tiles.
            "move": None,
            "vvix": None,
            "skew": None,
        },
        "housing": {
            "case_shiller_yoy": cs_yoy[-1][1] if cs_yoy else None,
            "mortgage_30y": mortgage[-1][1] if mortgage else None,
            "permits": permits[-1][1] if permits else None,           # NEW
            "starts_series": trim(starts, 60),
            "permits_series": trim(permits, 60),                       # NEW
            "series_mortgage": trim(mortgage, 60),
        },
        "ma": {                                                         # NEW
            "ma50":  ma50_series[-1][1] if ma50_series else None,
            "ma200": ma200_series[-1][1] if ma200_series else None,
            "spx_daily": trim(spx_daily, 24 * 21),  # ~2yr daily
            "ma50_series":  trim(ma50_series, 24 * 21),
            "ma200_series": trim(ma200_series, 24 * 21),
        },
        "breadth": {                                                    # NEW
            # NYSE A/D line — proxy for "% above MA". Above 0 means more
            # advancers than decliners on a 50-day rolling basis.
            "nyad_last": nyad[-1][1] if nyad else None,
            # Pct of last 50 days where line is rising = breadth health proxy
            "pct_above_50dma": (
                round(sum(1 for i in range(1, min(50, len(nyad)))
                           if nyad[-i][1] > nyad[-i-1][1]) / 50 * 100)
                if len(nyad) >= 50 else None
            ),
            # Pct of last 200 days rising
            "pct_above_200dma": (
                round(sum(1 for i in range(1, min(200, len(nyad)))
                           if nyad[-i][1] > nyad[-i-1][1]) / 200 * 100)
                if len(nyad) >= 200 else None
            ),
        },
        "technicals": {
            "fear_greed": fg.get("score") if fg else None,
            "fear_greed_rating": fg.get("rating") if fg else None,
            "put_call": pc[1] if pc else None,
            # Breadth tiles intentionally omitted (paywalled S&P 500 constituents).
            "pct_above_50dma": None,
            "ma50": None,
            "ma200": None,
            "new_highs_lows": None,
            "aaii_bull": None,
        },
    }
    return payload


if __name__ == "__main__":
    write_json("data/markets.json", build())
