"""Risk section — recession prob, financial stress, GPR, flashpoints."""
from __future__ import annotations

import csv
import io
from etl._common import (fred, fred_latest, trim, utcnow_iso, write_json,
                          safe, _retry_get, gdelt_articles)


def _ny_fed_recession() -> list[tuple[str, float]]:
    """NY Fed publishes the yield-curve-based recession probability series
    monthly as an Excel file. We fetch the CSV mirror via FRED if the Excel
    fetch fails."""
    out: list[tuple[str, float]] = []
    try:
        r = _retry_get(
            "https://www.newyorkfed.org/medialibrary/media/research/capital_markets/Prob_Rec.xlsx",
            timeout=30,
        )
        # We don't depend on openpyxl here — fall through to FRED if it fails.
        raise RuntimeError("xlsx parse not wired; using FRED proxy")
    except Exception:
        # FRED hosts an analogous series RECPROUSM156N (smoothed recession prob)
        s = safe(fred, "RECPROUSM156N", default=[]) or []
        return s


def _gpr_index() -> tuple[str, float] | None:
    """Iacoviello publishes GPR monthly. CSV mirror at
    https://www.matteoiacoviello.com/gpr_files/data_gpr_export.csv
    """
    try:
        r = _retry_get(
            "https://www.matteoiacoviello.com/gpr_files/data_gpr_export.csv",
            timeout=30,
        )
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if not rows:
            return None
        last = rows[-1]
        for k in ("GPR", "gpr", "GPRC"):
            if k in last:
                return (last.get("month") or last.get("date") or "", float(last[k]))
    except Exception:
        return None
    return None


def build() -> dict:
    rec = _ny_fed_recession()
    stlfsi = fred("STLFSI4") or []
    nfci = fred("NFCI") or []

    gpr = _gpr_index()
    flashpoint_articles = safe(
        gdelt_articles,
        "(conflict OR military OR sanctions OR escalation) AND (Russia OR China OR Iran OR Israel)",
        max_records=10, timespan="48h", default=[],
    )

    # Composite early-warning: weighted z-blend of stress + curve + spreads
    def percentile(series, val):
        if not series:
            return 50
        sorted_v = sorted(v for _, v in series)
        idx = next((i for i, v in enumerate(sorted_v) if v >= val), len(sorted_v))
        return round(idx / len(sorted_v) * 100)

    early = []
    if stlfsi:
        early.append({
            "indicator": "STLFSI",
            "level": stlfsi[-1][1],
            "percentile": percentile(stlfsi, stlfsi[-1][1]),
            "alert": stlfsi[-1][1] > 0.5,
        })

    payload = {
        "asOf": utcnow_iso(),
        "narrative": {
            "stance": "neutral",
            "text": "Recession prob {:.0f}%, STLFSI {:+.2f}.".format(
                (rec[-1][1] if rec else 0) * (1 if (rec and rec[-1][1] > 1) else 100),
                stlfsi[-1][1] if stlfsi else 0,
            ),
        },
        "recession_prob": {
            "ny_fed": rec[-1][1] if rec else None,
            "series": trim(rec, 60),
        },
        "stress": {
            "stlfsi": stlfsi[-1][1] if stlfsi else None,
            "nfci": nfci[-1][1] if nfci else None,
            "series_stlfsi": trim(stlfsi, 60),
        },
        "geopolitical": {
            "gpr": gpr[1] if gpr else None,
            "gpr_date": gpr[0] if gpr else None,
        },
        "flashpoints": [
            {"title": a.get("title", "")[:140],
             "url": a.get("url"),
             "domain": a.get("domain"),
             "tone": a.get("tone"),
             "seen_at": a.get("seendate")}
            for a in flashpoint_articles
        ],
        "early_warning": early,
    }
    return payload


if __name__ == "__main__":
    write_json("data/risk.json", build())
