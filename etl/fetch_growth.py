"""Growth section — GDP, IP, capacity utilization, contributions.

Note: ISM PMI Manufacturing & Services and Conference Board LEI are
intentionally NOT fetched — they are paywalled. The UI greys those tiles.
"""
from __future__ import annotations

from etl._common import (fred, fred_latest, yoy_pct, trim, utcnow_iso,
                          write_json, safe)


def build() -> dict:
    gdp_real = fred("GDPC1") or []
    gdp_nom = fred("GDP") or []
    ip = fred("INDPRO") or []
    tcu = fred("TCU") or []
    pot = fred("GDPPOT") or []
    # Phila Fed Leading Index — used as Conference Board LEI proxy
    lei = fred("USSLIND") or []
    # Phila Fed Manufacturing Business Outlook — used as ISM Mfg PMI proxy.
    # Index is centered on 0 (above 0 = expanding); we shift to ~50 to match
    # the diffusion-index display convention used by the React component.
    phil_mfg = fred("MANEMP") or []           # placeholder — not perfect
    phil_business = fred("GACDISA066MSFRBPHI") or []  # Phila Fed Business Activity
    # Empire State (NY) — another regional Fed mfg proxy
    nymfg = fred("GACDISA066MSFRBNY") or []

    real_yoy_series = yoy_pct(gdp_real)
    nom_yoy_series = yoy_pct(gdp_nom)
    ip_yoy_series = yoy_pct(ip)

    payload = {
        "asOf": utcnow_iso(),
        "narrative": {
            "stance": "neutral",
            "text": ("US real GDP {:.1f}% YoY, capacity utilization {:.1f}%. "
                     "PMI/LEI tiles intentionally omitted (paywalled).").format(
                real_yoy_series[-1][1] if real_yoy_series else 0.0,
                tcu[-1][1] if tcu else 0.0,
            ),
        },
        "gdp": {
            "real_qoq_saar": safe(fred_latest, "A191RL1Q225SBEA",
                                   default=("", 0.0))[1],
            "real_yoy": real_yoy_series[-1][1] if real_yoy_series else None,
            "nominal_yoy": nom_yoy_series[-1][1] if nom_yoy_series else None,
            "potential": pot[-1][1] if pot else None,
            "series": trim(gdp_real, 40),
            "nominal_series": trim(gdp_nom, 40),     # NEW: nominal GDP series
            "labels": [d for d, _ in trim(gdp_real, 40)],
            "contrib": {
                # Contributions to %change in real GDP (annualised)
                "pce": safe(fred_latest, "DPCERY2Q224SBEA", default=("", 0.0))[1],
                "gpdi": safe(fred_latest, "A006RY2Q224SBEA", default=("", 0.0))[1],
                "gov": safe(fred_latest, "A822RY2Q224SBEA", default=("", 0.0))[1],
                "net_exp": safe(fred_latest, "A019RY2Q224SBEA", default=("", 0.0))[1],
            },
        },
        "ip": {
            "yoy": ip_yoy_series[-1][1] if ip_yoy_series else None,
            "series": trim(ip, 60),
            "manuf_series": trim(safe(fred, "IPMAN", default=[]), 60),
        },
        "caputil": {"val": tcu[-1][1] if tcu else None,
                    "series": trim(tcu, 60)},
        # PMI proxy from Phila Fed (Business Activity) and NY Empire State —
        # both are diffusion indices that map roughly to ISM PMI movements.
        # Range: -50..+50 → shift +50 to display in 0..100 PMI convention.
        "pmi": {
            "phil_business":  (phil_business[-1][1] + 50) if phil_business else None,
            "ny_empire":      (nymfg[-1][1] + 50) if nymfg else None,
            "phil_series":    [(d, v + 50) for d, v in trim(phil_business, 24)],
            "ny_series":      [(d, v + 50) for d, v in trim(nymfg, 24)],
        },
        "lei": {
            "val": lei[-1][1] if lei else None,
            "series": trim(lei, 24),
            "consec_neg_months": sum(1 for _, v in lei[-12:] if v < 0) if lei else 0,
        },
    }
    return payload


if __name__ == "__main__":
    write_json("data/growth.json", build())
