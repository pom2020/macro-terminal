"""Inflation section — CPI/PPI, expectations, breakevens, sticky-vs-flex,
   commodity basket. All sources free."""
from __future__ import annotations

from etl._common import (fred, fred_latest, yoy_pct, mom_pct, trim,
                          utcnow_iso, write_json, safe, stooq_daily)


def build() -> dict:
    cpi = fred("CPIAUCSL") or []
    core = fred("CPILFESL") or []
    ppi = fred("PPIACO") or []
    pce = fred("PCEPI") or []           # PCE price index (Fed's preferred)
    pce_core = fred("PCEPILFE") or []   # Core PCE
    sticky = fred("STICKCPIM157SFRBATL") or []
    # Atlanta Fed Flexible CPI 12-month % change. Try the canonical ID first,
    # fall back to alternates if it's been retired.
    flex = (fred("CORESTICKM158SFRBATL") or
            fred("CRESTKCPIXSLTRM158SFRBATL") or [])
    deflator = fred("GDPDEF") or []

    cpi_yoy = yoy_pct(cpi)
    core_yoy = yoy_pct(core)
    ppi_yoy = yoy_pct(ppi)
    pce_yoy = yoy_pct(pce)
    pce_core_yoy = yoy_pct(pce_core)
    cpi_mom = mom_pct(cpi)
    core_mom = mom_pct(core)
    ppi_mom = mom_pct(ppi)

    # Supercore (services ex shelter) — Atlanta Fed publishes via FRED
    supercore_3m = trim(mom_pct(fred("CUSR0000SASLE") or []), 3)
    supercore_3m_avg = (sum(v for _, v in supercore_3m) / len(supercore_3m)
                        if supercore_3m else None)

    # Commodity basket (bcom proxy, oil, gold, copper)
    def last_close(symbol: str) -> float | None:
        s = safe(stooq_daily, symbol, default=[])
        return s[-1][1] if s else None

    payload = {
        "asOf": utcnow_iso(),
        "narrative": {
            "stance": "hawk" if (cpi_yoy and cpi_yoy[-1][1] > 3.0) else "neutral",
            "text": "Headline CPI {:.1f}% YoY, core {:.1f}% YoY, sticky 3m {:.1f}%.".format(
                cpi_yoy[-1][1] if cpi_yoy else 0,
                core_yoy[-1][1] if core_yoy else 0,
                sticky[-1][1] if sticky else 0,
            ),
        },
        "cpi": {
            "headline": cpi_yoy[-1][1] if cpi_yoy else None,
            "headline_mom": cpi_mom[-1][1] if cpi_mom else None,
            "core": core_yoy[-1][1] if core_yoy else None,
            "core_mom": core_mom[-1][1] if core_mom else None,
            "supercore_3m": (round(supercore_3m_avg * 12, 2)
                             if supercore_3m_avg is not None else None),
            "sticky": sticky[-1][1] if sticky else None,
            "series": trim(cpi_yoy, 60),
            "core_series": trim(core_yoy, 60),  # NEW: core CPI YoY series
            "labels": [d for d, _ in trim(cpi_yoy, 60)],
        },
        "pce": {
            "headline": pce_yoy[-1][1] if pce_yoy else None,
            "core": pce_core_yoy[-1][1] if pce_core_yoy else None,
        },
        "ppi": {
            "yoy": ppi_yoy[-1][1] if ppi_yoy else None,
            "mom": ppi_mom[-1][1] if ppi_mom else None,
            "series": trim(ppi_yoy, 60),
        },
        "deflator": {
            "yoy": yoy_pct(deflator)[-1][1] if yoy_pct(deflator) else None,
        },
        "expectations": {
            "umich_1y": safe(fred_latest, "MICH", default=("", None))[1],
            # Michigan 5y exp is not on FRED with a single ID we can rely on;
            # use the 5-year breakeven as a proxy for long-run expectations.
            "umich_5y": None,
            "ny_fed":   safe(fred_latest, "EXPINF1YR", default=("", None))[1],
            "breakeven_5y":   safe(fred_latest, "T5YIE", default=("", None))[1],
            "breakeven_10y":  safe(fred_latest, "T10YIE", default=("", None))[1],
            "breakeven_5y5y": safe(fred_latest, "T5YIFR", default=("", None))[1],
        },
        "sticky_vs_flex": {
            "sticky": sticky[-1][1] if sticky else None,
            "flexible": flex[-1][1] if flex else None,
            "series_sticky": trim(sticky, 60),
            "series_flex": trim(flex, 60),
        },
        "commodities": [
            {"name": "WTI Crude",  "price": last_close("cl.f"),     "unit": "USD/bbl"},
            {"name": "Brent",      "price": last_close("cb.f"),     "unit": "USD/bbl"},
            {"name": "Nat Gas",    "price": last_close("ng.f"),     "unit": "USD/MMBtu"},
            {"name": "Gold",       "price": last_close("gc.f"),     "unit": "USD/oz"},
            {"name": "Copper",     "price": last_close("hg.f"),     "unit": "USD/lb"},
            {"name": "Wheat",      "price": last_close("zw.f"),     "unit": "USc/bu"},
        ],
    }
    return payload


if __name__ == "__main__":
    write_json("data/inflation.json", build())
