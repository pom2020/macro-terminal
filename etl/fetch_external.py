"""External sector — trade, current account, DXY, FX crosses, IMF reserves."""
from __future__ import annotations

from etl._common import (fred, fred_latest, yoy_pct, trim, utcnow_iso,
                          write_json, safe, frankfurter_latest, stooq_daily,
                          ecb_sdw)


def build() -> dict:
    bal = fred("BOPGSTB") or []
    exp = fred("BOPGEXP") or []
    imp = fred("BOPGIMP") or []
    ca = fred("BOPBCA") or []
    dxy_series = safe(stooq_daily, "dx.f", default=[]) or fred("DTWEXBGS") or []

    crosses = safe(frankfurter_latest, "USD",
                   to=["EUR", "JPY", "GBP", "CNY", "INR", "BRL"], default={}) or {}

    payload = {
        "asOf": utcnow_iso(),
        "narrative": {
            "stance": "neutral",
            "text": "Trade balance {:+,.1f}B, current account {:+,.1f}% of GDP.".format(
                bal[-1][1] / 1000 if bal else 0,
                ca[-1][1] / 1000 if ca else 0,
            ),
        },
        "trade": {
            "balance": bal[-1][1] if bal else None,
            "exports": exp[-1][1] if exp else None,
            "imports": imp[-1][1] if imp else None,
            "balance_series": trim(bal, 60),
        },
        "current_account": {
            "val": ca[-1][1] if ca else None,
            "series": trim(ca, 60),
        },
        "fx": {
            "dxy": dxy_series[-1][1] if dxy_series else None,
            "dxy_series": trim(dxy_series, 252),  # ~1y of trading days
            "pairs": [
                {"pair": f"USD/{ccy}", "rate": rate}
                for ccy, rate in crosses.items()
            ],
        },
        # FX reserves (global) — IMF COFER is quarterly; can be added in v2
        "reserves": [],
        # Portfolio flows — ECB BoP. Skipped: the BP6 dataflow path needs
        # a per-EA-country verified key. Tile renders as "no data" for now.
        "flows": {"eu_portfolio": []},
    }
    return payload


if __name__ == "__main__":
    write_json("data/external.json", build())
