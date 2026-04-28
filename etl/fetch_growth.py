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
        # PMI and LEI tiles are intentionally omitted (paywalled).
        "pmi": None,
        "lei": None,
    }
    return payload


if __name__ == "__main__":
    write_json("data/growth.json", build())
