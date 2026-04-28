"""Run every section ETL, then assemble overview/health-score, then bundle
into a single macro.json that the frontend prefers (one round-trip).

Order matters: overview reads the per-section files.
"""
from __future__ import annotations

import json
import os
import sys
import traceback

from etl import (fetch_growth, fetch_inflation, fetch_labor, fetch_monetary,
                 fetch_external, fetch_markets, fetch_fiscal, fetch_risk,
                 fetch_news, fetch_overview)
from etl._common import utcnow_iso, write_json

SECTIONS = [
    ("growth",    fetch_growth.build),
    ("inflation", fetch_inflation.build),
    ("labor",     fetch_labor.build),
    ("monetary",  fetch_monetary.build),
    ("external",  fetch_external.build),
    ("markets",   fetch_markets.build),
    ("fiscal",    fetch_fiscal.build),
    ("risk",      fetch_risk.build),
    ("news",      fetch_news.build),
]


def main() -> int:
    failures: list[str] = []
    bundle: dict = {"asOf": utcnow_iso()}

    for name, build in SECTIONS:
        print(f"[etl] {name}...")
        try:
            payload = build()
            write_json(f"data/{name}.json", payload)
            bundle[name] = payload
        except Exception as e:
            print(f"  !! {name} FAILED: {e}")
            traceback.print_exc()
            failures.append(name)

    # Overview is built from per-section files, so do it last.
    print("[etl] overview...")
    try:
        ov = fetch_overview.build()
        write_json("data/overview.json", ov)
        bundle["overview"] = ov
    except Exception as e:
        print(f"  !! overview FAILED: {e}")
        failures.append("overview")

    write_json("data/macro.json", bundle)

    total = len(SECTIONS)
    succeeded = total - len(failures)
    if failures:
        print(f"\n[etl] {succeeded}/{total} sections succeeded; "
              f"failed: {failures}", file=sys.stderr)
    else:
        print(f"\n[etl] all {total} sections OK")

    # Only fail the run if more than half the sections crashed — single
    # endpoint quirks shouldn't block deploys.
    if len(failures) > total // 2:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
