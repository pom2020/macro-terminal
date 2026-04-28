"""Labor section — U-3, U-6, LFPR, NFP, JOLTS, AHE wages, Beveridge curve."""
from __future__ import annotations

from etl._common import (fred, fred_latest, yoy_pct, trim, utcnow_iso,
                          write_json, safe)


def build() -> dict:
    u3 = fred("UNRATE") or []
    u6 = fred("U6RATE") or []
    lfpr = fred("CIVPART") or []
    nfp = fred("PAYEMS") or []
    jolts = fred("JTSJOL") or []      # Job Openings
    hires = fred("JTSHIL") or []      # Hires (NEW)
    quits = fred("JTSQUL") or []      # Quits (NEW)
    eci = fred("ECIALLCIV") or []     # Employment Cost Index (NEW)
    ahe = fred("CES0500000003") or []
    ahe_yoy = yoy_pct(ahe)
    ahe_mom = []
    for i in range(1, len(ahe)):
        prev = ahe[i-1][1]
        if prev:
            ahe_mom.append((ahe[i][0], round((ahe[i][1] / prev - 1) * 100, 2)))
    eci_yoy = yoy_pct(eci)

    # Beveridge curve: pair (UNRATE, openings_rate) by month, last 36 months
    open_rate = safe(fred, "JTSJOR", default=[]) or []  # JOLTS opening rate
    by_month: dict[str, dict[str, float]] = {}
    for d, v in u3[-60:]:
        by_month.setdefault(d, {})["u"] = v
    for d, v in open_rate[-60:]:
        by_month.setdefault(d, {})["o"] = v
    beveridge = [
        {"date": d, "u3": pt["u"], "openings_rate": pt["o"]}
        for d, pt in sorted(by_month.items())
        if "u" in pt and "o" in pt
    ][-36:]

    # Month-over-month NFP change
    nfp_mom = []
    for i in range(1, len(nfp)):
        d, v = nfp[i]
        _, p = nfp[i - 1]
        nfp_mom.append((d, round(v - p, 1)))  # in thousands of jobs

    payload = {
        "asOf": utcnow_iso(),
        "narrative": {
            "stance": "neutral",
            "text": "Unemployment {:.1f}%, NFP {:+.0f}k m/m, AHE {:+.1f}% YoY.".format(
                u3[-1][1] if u3 else 0,
                nfp_mom[-1][1] if nfp_mom else 0,
                ahe_yoy[-1][1] if ahe_yoy else 0,
            ),
        },
        "unemployment": {
            "u3": u3[-1][1] if u3 else None,
            "u6": u6[-1][1] if u6 else None,
            "series_u3": trim(u3, 60),
            "series_u6": trim(u6, 60),
        },
        "lfpr": {"val": lfpr[-1][1] if lfpr else None,
                 "series": trim(lfpr, 60)},
        "nfp": {
            "current": nfp_mom[-1][1] if nfp_mom else None,
            "three_m_ago": nfp_mom[-3][1] if len(nfp_mom) >= 3 else None,
            "one_y_ago": nfp_mom[-12][1] if len(nfp_mom) >= 12 else None,
            "series": trim(nfp_mom, 36),
        },
        "jolts": {
            "openings": jolts[-1][1] if jolts else None,
            "hires":    hires[-1][1] if hires else None,
            "quits":    quits[-1][1] if quits else None,
            "ratio":    (jolts[-1][1] / u3[-1][1] / 1000)
                          if jolts and u3 and u3[-1][1] else None,
            "openings_series": trim(jolts, 36),
        },
        "wages": {
            "ahe_yoy":  ahe_yoy[-1][1] if ahe_yoy else None,
            "ahe_mom":  ahe_mom[-1][1] if ahe_mom else None,
            "eci_yoy":  eci_yoy[-1][1] if eci_yoy else None,
            "nominal_yoy": ahe_yoy[-1][1] if ahe_yoy else None,
            "series": trim(ahe_yoy, 60),
        },
        "beveridge": beveridge,
    }
    return payload


if __name__ == "__main__":
    write_json("data/labor.json", build())
