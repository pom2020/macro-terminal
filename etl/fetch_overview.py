"""Overview / health-score / ticker — assembled AFTER the section files."""
from __future__ import annotations

import json
import os
from etl._common import (utcnow_iso, write_json, fred_latest, fred, safe,
                          stooq_daily)


def _load(section: str) -> dict:
    path = f"data/{section}.json"
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _score(z: float, neutral: float = 50, span: float = 25) -> float:
    """Map a z-score-like input to 0..100, where 50 is neutral.

    Rounded to 1 decimal so the React table doesn't show IEEE-754 noise like
    '59.833333333333336' for what should display as '59.8'.
    """
    return round(max(0, min(100, neutral + z * span)), 1)


def _composite(metrics: list[float | None]) -> int:
    """Equal-weight average of available 0..100 scores."""
    pts = [m for m in metrics if m is not None]
    return round(sum(pts) / len(pts)) if pts else 50


def build() -> dict:
    growth = _load("growth")
    inflation = _load("inflation")
    labor = _load("labor")
    monetary = _load("monetary")
    markets = _load("markets")
    risk = _load("risk")

    # Component scores (0..100, higher = healthier macro)
    real_yoy = (growth.get("gdp") or {}).get("real_yoy")
    cpi = (inflation.get("cpi") or {}).get("headline")
    u3 = (labor.get("unemployment") or {}).get("u3")
    curve = (monetary.get("curve") or {}).get("current_2s10s_bp")
    stlfsi = (risk.get("stress") or {}).get("stlfsi")

    components = {
        "growth":    _score((real_yoy - 2) / 1.5, 60) if real_yoy is not None else 50,
        "inflation": _score(-(cpi - 2) / 1.5, 60) if cpi is not None else 50,
        "labor":     _score(-(u3 - 4) / 1.5, 60) if u3 is not None else 50,
        "monetary":  _score(curve / 100, 50) if curve is not None else 50,
        "stress":    _score(-stlfsi, 50) if stlfsi is not None else 50,
    }
    composite = _composite(list(components.values()))

    if composite >= 65:
        regime, color = "EXPANSION", "green"
    elif composite >= 50:
        regime, color = "LATE-CYCLE EXPANSION", "amber"
    elif composite >= 35:
        regime, color = "SLOWDOWN", "amber"
    else:
        regime, color = "CONTRACTION", "red"

    # Ticker — primary source FRED (works through corporate firewalls and
    # is rock-solid). Stooq is a fallback for symbols FRED doesn't carry.
    def _ticker_item(label: str, last: float, prev: float) -> dict:
        # Match the prototype's ticker item shape — val and chg are pre-
        # formatted strings, dir is "up"/"down". The React ticker bar reads
        # these directly without parsing.
        chg_pct = ((last / prev - 1) * 100) if prev else 0
        # Format the value: 2 decimals if >= 100, more precision if smaller
        if last >= 100:
            val_str = f"{last:,.2f}"
        elif last >= 1:
            val_str = f"{last:,.3f}"
        else:
            val_str = f"{last:,.4f}"
        chg_str = f"{chg_pct:+.2f}%"
        return {"sym": label, "val": val_str, "chg": chg_str,
                "dir": "up" if chg_pct >= 0 else "down"}

    def _from_fred(series_id: str, label: str) -> dict | None:
        s = safe(fred, series_id, default=[])
        if not s or len(s) < 2:
            return None
        return _ticker_item(label, s[-1][1], s[-2][1])

    def _from_stooq(symbol: str, label: str) -> dict | None:
        s = safe(stooq_daily, symbol, default=[])
        if not s or len(s) < 2:
            return None
        return _ticker_item(label, s[-1][1], s[-2][1])

    fred_ticker_specs = [
        ("SP500",                  "SPX"),     # S&P 500
        ("DJIA",                   "DJI"),     # Dow Jones
        ("NASDAQCOM",              "NDX"),     # NASDAQ Composite
        ("VIXCLS",                 "VIX"),     # VIX
        ("DCOILWTICO",             "WTI"),     # WTI Cushing
        ("DCOILBRENTEU",           "BRENT"),   # Brent (Europe)
        ("GOLDAMGBD228NLBM",       "GOLD"),    # London PM Gold Fix
        ("DTWEXBGS",               "DXY"),     # Trade-weighted USD (proxy)
        ("DGS10",                  "US10Y"),   # 10y Treasury yield
        ("T10Y2Y",                 "2s10s"),   # Curve spread (bp)
    ]
    ticker = [t for t in
              (_from_fred(sid, lbl) for sid, lbl in fred_ticker_specs)
              if t]

    # Optional Stooq additions if reachable (Asia indices, copper, natgas)
    for sym, lbl in [("^stoxx50e", "SX5E"), ("^nkx", "NKY"),
                     ("^hsi", "HSI"), ("hg.f", "COPPER"), ("ng.f", "NATGAS")]:
        t = _from_stooq(sym, lbl)
        if t:
            ticker.append(t)

    return {
        "asOf": utcnow_iso(),
        "regions": {
            "US": {"name": "United States", "flag": "US"},
            "EU": {"name": "Euro Area", "flag": "EU"},
            "CN": {"name": "China", "flag": "CN"},
            "EM": {"name": "Emerging Mkts", "flag": "EM"},
            "GL": {"name": "Global", "flag": "GL"},
        },
        "healthScore": {
            "composite": composite,
            "trend": 0,  # delta vs 3 months ago — to track once we have history
            "components": components,
            "regimeLabel": regime,
            "regimeColor": color,
        },
        "ticker": ticker,
    }


if __name__ == "__main__":
    write_json("data/overview.json", build())
