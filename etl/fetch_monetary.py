"""Monetary section — policy rates, curve, OAS spreads, money supply, FCI."""
from __future__ import annotations

from etl._common import (fred, fred_latest, yoy_pct, trim, utcnow_iso,
                          write_json, safe)


def build() -> dict:
    dgs10 = fred("DGS10") or []
    dgs2 = fred("DGS2") or []
    t10y2y = fred("T10Y2Y") or []
    ig = fred("BAMLC0A0CM") or []
    hy = fred("BAMLH0A0HYM2") or []
    m1 = fred("M1SL") or []
    m2 = fred("M2SL") or []
    stlfsi = fred("STLFSI4") or []
    nfci = fred("NFCI") or []
    busloans = fred("BUSLOANS") or []

    payload = {
        "asOf": utcnow_iso(),
        "narrative": {
            "stance": "hawk" if (stlfsi and stlfsi[-1][1] > 0) else "neutral",
            "text": "10y Treasury {:.2f}%, 2s10s {:+.0f}bp, IG OAS {:.0f}bp, HY OAS {:.0f}bp.".format(
                dgs10[-1][1] if dgs10 else 0,
                (t10y2y[-1][1] * 100) if t10y2y else 0,
                (ig[-1][1] * 100) if ig else 0,
                (hy[-1][1] * 100) if hy else 0,
            ),
        },
        "policy_rates": [
            {"region": "US",  "rate": safe(fred_latest, "DFF",     default=("", None))[1], "stance": "hold"},
            {"region": "EA",  "rate": safe(fred_latest, "ECBDFR",  default=("", None))[1], "stance": "hold"},
            {"region": "UK",  "rate": safe(fred_latest, "IUDSOIA", default=("", None))[1], "stance": "hold"},
            {"region": "JP",  "rate": safe(fred_latest, "INTDSRJPM193N", default=("", None))[1], "stance": "hold"},
        ],
        "curve": {
            "series_2s10s": trim(t10y2y, 60),
            "series_10y": trim(dgs10, 60),
            "series_2y": trim(dgs2, 60),
            "current_2s10s_bp": round(t10y2y[-1][1] * 100, 1) if t10y2y else None,
            "current_10y": dgs10[-1][1] if dgs10 else None,
            "current_2y": dgs2[-1][1] if dgs2 else None,
        },
        "spreads": {
            "ig_oas": ig[-1][1] if ig else None,
            "hy_oas": hy[-1][1] if hy else None,
            "series_ig": trim(ig, 60),
            "series_hy": trim(hy, 60),
        },
        "real_rates": {
            "tips_5y": safe(fred_latest, "DFII5",  default=("", None))[1],
            "tips_10y": safe(fred_latest, "DFII10", default=("", None))[1],
            "fed_rrp": safe(fred_latest, "RRPONTSYAWARD", default=("", None))[1],
        },
        "money": {
            "m1_yoy": yoy_pct(m1)[-1][1] if yoy_pct(m1) else None,
            "m2_yoy": yoy_pct(m2)[-1][1] if yoy_pct(m2) else None,
            "m2_series": trim(m2, 60),
        },
        "credit": {
            "c_and_i": busloans[-1][1] if busloans else None,
            "c_and_i_yoy": yoy_pct(busloans)[-1][1] if yoy_pct(busloans) else None,
        },
        "fci": {
            "stlfsi": stlfsi[-1][1] if stlfsi else None,
            "nfci": nfci[-1][1] if nfci else None,
            "series_stlfsi": trim(stlfsi, 60),
        },
    }
    return payload


if __name__ == "__main__":
    write_json("data/monetary.json", build())
