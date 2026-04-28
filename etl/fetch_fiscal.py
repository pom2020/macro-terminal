"""Fiscal section — debt, deficit, interest costs, spending & revenue."""
from __future__ import annotations

from etl._common import (fred, fred_latest, yoy_pct, trim, utcnow_iso,
                          write_json, safe, _retry_get, wb)


def _treasury_recent_outlays() -> list[dict]:
    """Treasury Fiscal Data — monthly receipts vs outlays.

    Endpoint: /v1/accounting/mts/mts_table_5  (free, no key)
    """
    try:
        r = _retry_get(
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
            "v1/accounting/mts/mts_table_5",
            params={"fields": "record_date,classification_desc,current_month_net_outly_amt",
                    "page[size]": 50, "sort": "-record_date"},
        )
        return r.json().get("data", [])
    except Exception:
        return []


def build() -> dict:
    debt = fred("GFDEGDQ188S") or []
    deficit = fred("FYFSGDA188S") or []
    interest = fred("A091RC1Q027SBEA") or []

    # Global debt-to-GDP comparison via World Bank (last available year)
    countries = [("USA", "United States"), ("DEU", "Germany"),
                 ("JPN", "Japan"), ("CHN", "China"), ("GBR", "UK")]
    global_compare = []
    for code, name in countries:
        s = safe(wb, code, "GC.DOD.TOTL.GD.ZS", default=[])
        if s:
            global_compare.append({"country": name, "debt_pct_gdp": s[-1][1],
                                   "year": s[-1][0]})

    outlays = _treasury_recent_outlays()

    payload = {
        "asOf": utcnow_iso(),
        "narrative": {
            "stance": "neutral",
            "text": "Federal debt {:.1f}% of GDP, deficit {:.1f}% of GDP.".format(
                debt[-1][1] if debt else 0,
                deficit[-1][1] if deficit else 0,
            ),
        },
        "us": {
            "debt_pct_gdp": debt[-1][1] if debt else None,
            "deficit_pct_gdp": deficit[-1][1] if deficit else None,
            "primary_deficit": None,  # CBO baseline; can add v2
            "interest_costs": interest[-1][1] if interest else None,
            "debt_series": trim(debt, 40),
            "deficit_series": trim(deficit, 40),
        },
        "global": global_compare,
        "spending": outlays[:25],
        "revenue": [],
    }
    return payload


if __name__ == "__main__":
    write_json("data/fiscal.json", build())
