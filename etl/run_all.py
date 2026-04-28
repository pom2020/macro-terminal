"""Run every section ETL, then assemble overview/health-score, then bundle
into a single macro.json that the frontend prefers (one round-trip).

Order matters: overview reads the per-section files.

Architecture note (Apr 28, 2026): the React components in the prototype
read very specific shapes (e.g. monetary.curve = {labels, current, three_m_ago,
one_y_ago}, not just a list of [date, value] pairs). My fetch_<section>.py
modules currently produce a flatter "raw" shape that the components don't
consume directly. Until each section has a shape adapter, the bundle is
built by:
  1. Loading the prototype's seed shape as the base (everything renders).
  2. Overlaying only the live fields whose shapes already match: top-level
     ticker, healthScore, regions, asOf, plus anything individual fetchers
     return as a "patch" dict matching the seed.
This guarantees the page never breaks; live coverage expands per-section
as we add adapters.
"""
from __future__ import annotations

import json
import os
import sys
import traceback

from etl import (fetch_growth, fetch_inflation, fetch_labor, fetch_monetary,
                 fetch_external, fetch_markets, fetch_fiscal, fetch_risk,
                 fetch_news, fetch_overview)
from etl._adapters import adapt
from etl._common import utcnow_iso, write_json

SEED_PATH = os.path.join(os.path.dirname(__file__), "_seed_macro.json")


def load_seed() -> dict:
    with open(SEED_PATH) as f:
        return json.load(f)

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
    # Start from the prototype seed shape so every component renders even
    # before live shape-adapters are in place.
    bundle: dict = load_seed()
    bundle["asOf"] = utcnow_iso()

    # Run per-section ETL, write raw payloads to data/<name>.json for
    # inspection, then run the shape adapter to merge live values into the
    # seed contract that the React components actually read.
    for name, build in SECTIONS:
        print(f"[etl] {name}...")
        try:
            payload = build()
            write_json(f"data/{name}.json", payload)
            if name == "news":
                # SecNews component reads window.MACRO.news directly (after
                # our build_public_html patch).
                bundle["news"] = payload
            else:
                # Every other section gets reshaped to the seed contract so
                # the existing React components read live values.
                bundle[name] = adapt(name, payload, bundle.get(name, {}))
        except Exception as e:
            print(f"  !! {name} FAILED: {e}")
            traceback.print_exc()
            failures.append(name)

    # Overview is built from per-section files. Its outputs (ticker,
    # healthScore, regions) DO match the seed contract, so we overlay them.
    print("[etl] overview...")
    try:
        ov = fetch_overview.build()
        write_json("data/overview.json", ov)
        bundle["overview"] = ov
        if ov.get("regions"):     bundle["regions"]     = ov["regions"]
        if ov.get("healthScore"): bundle["healthScore"] = ov["healthScore"]
        if ov.get("ticker"):      bundle["ticker"]      = ov["ticker"]
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
