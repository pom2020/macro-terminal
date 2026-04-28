"""News section — GDELT 2.0 headlines + tone-based sentiment."""
from __future__ import annotations

from etl._common import (utcnow_iso, write_json, safe, gdelt_articles,
                          gdelt_tone)

# Reduced from 8 to 4 topics + we no longer call gdelt_tone separately
# (the artlist response already includes per-article tone; we average that).
# This keeps the news fetch under ~30s end-to-end even when GDELT is slow.
QUERIES = {
    "macro":    "(GDP OR inflation OR \"federal reserve\" OR unemployment)",
    "markets":  "(\"stock market\" OR \"S&P 500\" OR VIX OR \"bond yields\")",
    "fiscal":   "(\"federal deficit\" OR \"national debt\" OR \"government spending\")",
    "risk":     "(geopolitics OR sanctions OR conflict OR \"financial stress\")",
}


def build() -> dict:
    headlines: dict[str, list] = {}
    tones: dict[str, float | None] = {}
    for topic, q in QUERIES.items():
        articles = safe(gdelt_articles, q, max_records=6,
                         timespan="24h", default=[]) or []
        headlines[topic] = [
            {
                "title": a.get("title", "")[:160],
                "url": a.get("url"),
                "domain": a.get("domain"),
                "seen_at": a.get("seendate"),
                "language": a.get("language"),
                "tone": a.get("tone"),
            }
            for a in articles
        ]
        # Compute average tone from the articles we already have, no second
        # call needed.
        tone_vals = []
        for a in articles:
            t = a.get("tone")
            if t in (None, ""):
                continue
            try:
                tone_vals.append(float(t))
            except (TypeError, ValueError):
                continue
        tones[topic] = (round(sum(tone_vals) / len(tone_vals), 2)
                        if tone_vals else None)

    return {
        "asOf": utcnow_iso(),
        "headlines": headlines,
        "sentiment": tones,  # tone -10..+10 (negative = pessimistic)
    }


if __name__ == "__main__":
    write_json("data/news.json", build())
