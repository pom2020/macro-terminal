"""Fiscal section — debt, deficit, interest costs, spending & revenue."""
from __future__ import annotations

from etl._common import (fred, fred_latest, yoy_pct, trim, utcnow_iso,
                          write_json, safe, _retry_get, wb)


def _treasury_recent_outlays() -> list[dict]:
    """Treasury Fiscal Data — monthly outlays.
    Endpoint: /v1/accounting/mts/mts_table_5  (free, no key)
    """
    try:
        r = _retry_get(
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
            "v1/accounting/mts/mts_table_5",
            params={"fields": "record_date,classification_desc,current_month_net_outly_amt",
                    "page[size]": 200, "sort": "-record_date"},
        )
        return r.json().get("data", [])
    except Exception:
        return []


def _treasury_recent_receipts() -> list[dict]:
    """Treasury Fiscal Data — monthly receipts (revenue) by source.
    Endpoint: /v1/accounting/mts/mts_table_4  (free, no key)
    """
    try:
        r = _retry_get(
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
            "v1/accounting/mts/mts_table_4",
            params={"fields": "record_date,classification_desc,current_month_rcpt_amt",
                    "page[size]": 200, "sort": "-record_date"},
        )
        return r.json().get("data", [])
    except Exception:
        return []


# Map Treasury function names → seed `cat` labels (for spending) and an
# ordering hint. Treasury uses long descriptive names; we condense.
SPENDING_MAP = [
    ("Social Security",          "Social Security"),
    ("National Defense",         "Defense"),
    ("Medicare",                 "Medicare"),
    ("Health",                   "Health (Medicaid)"),
    ("Income Security",          "Income Security"),
    ("Net Interest",             "Net Interest"),
    ("Veterans",                 "Veterans Benefits"),
    ("Education",                "Education"),
    ("Transportation",           "Transportation"),
]

REVENUE_MAP = [
    ("Individual Income",        "Individual Income"),
    ("Social Insurance",         "Payroll (FICA)"),
    ("Corporation Income",       "Corporate Income"),
    ("Excise",                   "Excise"),
    ("Customs",                  "Customs"),
    ("Estate and Gift",          "Estate & Gift"),
    ("Miscellaneous",            "Other"),
]


def _aggregate_by_function(rows: list[dict], amount_field: str,
                            mapping: list[tuple[str, str]]) -> list[dict]:
    """Take recent monthly rows and group by mapped function. Sum the
    last 12 months for each. Return [{cat, val, pct}, ...]."""
    if not rows:
        return []

    # Find the most recent record_date and take 12 months back
    dates = sorted({r.get("record_date") for r in rows if r.get("record_date")},
                    reverse=True)
    if not dates:
        return []
    keep_dates = set(dates[:12])

    totals: dict[str, float] = {label: 0.0 for _, label in mapping}
    for row in rows:
        if row.get("record_date") not in keep_dates:
            continue
        desc = row.get("classification_desc", "") or ""
        amt = row.get(amount_field, "0") or "0"
        try:
            val = float(amt)
        except (TypeError, ValueError):
            continue
        for needle, label in mapping:
            if needle.lower() in desc.lower():
                totals[label] += val
                break

    grand = sum(totals.values()) or 1.0
    out = []
    for _, label in mapping:
        v = totals[label]
        if v <= 0:
            continue
        out.append({
            "cat": label,
            "val": int(round(v / 1_000_000)),    # raw is dollars; show $bn
            "pct": round(v / grand * 100, 1),
        })
    out.sort(key=lambda x: -x["pct"])
    return out


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

    outlays_raw = _treasury_recent_outlays()
    receipts_raw = _treasury_recent_receipts()
    spending_agg = _aggregate_by_function(
        outlays_raw, "current_month_net_outly_amt", SPENDING_MAP)
    revenue_agg = _aggregate_by_function(
        receipts_raw, "current_month_rcpt_amt", REVENUE_MAP)

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
        "spending": spending_agg,
        "revenue": revenue_agg,
    }
    return payload


if __name__ == "__main__":
    write_json("data/fiscal.json", build())
