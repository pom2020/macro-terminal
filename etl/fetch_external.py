"""External sector — trade, current account, DXY, FX crosses, IMF reserves."""
from __future__ import annotations

from etl._common import (fred, fred_latest, yoy_pct, trim, utcnow_iso,
                          write_json, safe, frankfurter_latest, stooq_daily,
                          ecb_sdw, ytd_pct, imf_cofer_total_reserves)


def _fetch_reserves() -> list[dict]:
    """Top FX-reserve holding economies via IMF DataMapper."""
    countries = [
        ("China",       "CHN", 58),  # %USD heuristic, kept from seed
        ("Japan",       "JPN", 50),
        ("Switzerland", "CHE", 65),
        ("India",       "IND", 55),
        ("Russia",      "RUS", 30),
        ("Saudi Arabia","SAU", 55),
        ("Hong Kong",   "HKG", 70),
        ("Korea",       "KOR", 60),
    ]
    out: list[dict] = []
    for name, code, pct_usd in countries:
        v = safe(imf_cofer_total_reserves, code, default=None)
        if v is None:
            continue
        out.append({
            "country": name,
            "val": int(round(v)),    # USD billions
            "chg": 0,                # change since prior period — would need history
            "pct_usd": pct_usd,
        })
    return out


def build() -> dict:
    bal = fred("BOPGSTB") or []
    exp = fred("BOPGEXP") or []
    imp = fred("BOPGIMP") or []
    ca = fred("BOPBCA") or []
    dxy_series = safe(stooq_daily, "dx.f", default=[]) or fred("DTWEXBGS") or []

    # Add CHF + AUD to cover more pairs
    crosses = safe(frankfurter_latest, "USD",
                   to=["EUR", "JPY", "GBP", "CNY", "INR", "BRL",
                        "CHF", "AUD", "CAD", "MXN"],
                   default={}) or {}

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
            "balance":      bal[-1][1] if bal else None,
            "balance_prev": bal[-2][1] if len(bal) > 1 else None,    # NEW
            "exports":      exp[-1][1] if exp else None,
            "imports":      imp[-1][1] if imp else None,
            "balance_series": trim(bal, 60),
        },
        "current_account": {
            "val":   ca[-1][1] if ca else None,
            "prev":  ca[-2][1] if len(ca) > 1 else None,             # NEW
            "series": trim(ca, 60),
        },
        "fx": {
            "dxy": dxy_series[-1][1] if dxy_series else None,
            "dxy_ytd": ytd_pct(dxy_series),                           # NEW
            "dxy_series": trim(dxy_series, 252),  # ~1y of trading days
            "pairs": [
                {"pair": f"USD/{ccy}", "rate": rate}
                for ccy, rate in crosses.items()
            ],
        },
        # FX reserves (global) — IMF DataMapper, free public API
        "reserves": _fetch_reserves(),
        # Portfolio flows — left as seed; ECB BoP path needs per-country
        # SDMX key verification.
        "flows": {"eu_portfolio": []},
    }
    return payload


if __name__ == "__main__":
    write_json("data/external.json", build())
