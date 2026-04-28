"""News section — GDELT 2.0 headlines + FRED Releases economic calendar.

Output shape matches the SecNews React component contract:
  {
    asOf, headlines: [{time,date,region,impact,sentiment,tag,title,
                        summary,moved}, ...],
    calendar: [{date,time,region,event,prev,cons,impact}, ...],
    cbSpeak: [],          # not auto-fetchable; empty list rendered cleanly
    whatHappened: {narrative: {stance, text}, author}
  }
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

from etl._common import (utcnow_iso, write_json, safe, gdelt_articles,
                          fred_release_dates, google_news_rss)


# Topic queries (each maps to a section / region tag).
# GDELT 2.0 doc API: the language filter is `sourcelang:english` — the FULL
# language name, lowercase. The 3-letter code (eng) doesn't match. We append
# it after the topic clause, outside any parentheses, which is what GDELT's
# parser expects.
def _en(q: str) -> str:
    return f"({q}) sourcelang:english"

TOPIC_QUERIES = [
    ("FED",     "US", _en("federal reserve OR Powell OR FOMC OR \"interest rate\"")),
    ("ECB",     "EU", _en("ECB OR \"european central bank\" OR Lagarde")),
    ("DATA",    "US", _en("\"retail sales\" OR \"jobs report\" OR CPI OR PCE OR GDP")),
    ("PBoC",    "CN", _en("China OR PBoC OR \"Bank of China\"")),
    ("BoJ",     "JP", _en("\"Bank of Japan\" OR BOJ OR Ueda OR yen")),
    ("OIL",     "US", _en("\"oil prices\" OR brent OR OPEC")),
    ("CREDIT",  "US", _en("credit OR lending OR \"bond yields\" OR spreads")),
    ("FISCAL",  "US", _en("\"federal deficit\" OR \"national debt\" OR CBO")),
]

# Accept several forms GDELT may return: full English name, ISO 639-1 code,
# ISO 639-2 code, or empty/missing.
ENGLISH_LANG_CODES = {"english", "eng", "en", ""}


def _is_english(article: dict) -> bool:
    """Defensive post-filter — drop anything explicitly tagged non-English."""
    lang = (article.get("language") or "").strip().lower()
    return lang in ENGLISH_LANG_CODES

# Critical-impact tags get the red treatment in the UI
CRITICAL_TAGS = {"FED", "ECB", "PBoC", "BoJ"}
HIGH_TAGS = {"DATA", "OIL"}


def _classify_sentiment(tone: float | None) -> str:
    """GDELT tone roughly maps to:
       < -3   = pessimistic / hawkish (rates / inflation context)
       > +3   = optimistic / dovish
       else   = neutral
    """
    if tone is None:
        return "neutral"
    if tone <= -2.5:
        return "hawkish"
    if tone >= 2.5:
        return "dovish"
    return "neutral"


def _format_seen_at(seendate: str | None) -> tuple[str, str]:
    """GDELT seendate is YYYYMMDDHHMMSS UTC. Return (HH:MM ET, Mon DD)."""
    if not seendate or len(seendate) < 12:
        return ("", "")
    try:
        ts = dt.datetime.strptime(seendate[:12], "%Y%m%d%H%M")
        # Convert to US Eastern (very rough — UTC-4 / UTC-5; we use -4 for
        # daylight time. Display is illustrative, not exact.)
        et = ts - dt.timedelta(hours=4)
        return (et.strftime("%H:%M ET"), et.strftime("%b %d"))
    except Exception:
        return ("", "")


# Google News query per tag — used to add summary text to each GDELT title
GNEWS_QUERIES = {
    "FED":    "Federal Reserve interest rate",
    "ECB":    "European Central Bank rate",
    "DATA":   "US economic data",
    "PBoC":   "China economy",
    "BoJ":    "Bank of Japan",
    "OIL":    "oil prices",
    "CREDIT": "bond yields credit",
    "FISCAL": "US federal deficit",
}


def _fetch_summaries_per_tag() -> dict[str, list[dict]]:
    """Pull Google News RSS once per tag, return list of items with summary."""
    out: dict[str, list[dict]] = {}
    for tag, q in GNEWS_QUERIES.items():
        items = safe(google_news_rss, q, max_items=8, default=[]) or []
        out[tag] = items
    return out


def _match_summary(gnews_items: list[dict], gdelt_title: str) -> str:
    """Find the closest matching Google News summary for a GDELT title."""
    if not gnews_items or not gdelt_title:
        return ""
    # Score by word overlap
    target_words = set(gdelt_title.lower().split())
    best = ("", 0)
    for it in gnews_items:
        t = (it.get("title") or "").lower()
        s = (it.get("summary") or "")
        if not s:
            continue
        score = len(target_words & set(t.split()))
        if score > best[1]:
            best = (s, score)
    return best[0][:280] if best[1] >= 2 else ""


def _fetch_headlines() -> list[dict]:
    summaries_by_tag = _fetch_summaries_per_tag()
    items: list[dict] = []
    for tag, region, query in TOPIC_QUERIES:
        # First attempt: with sourcelang:english filter at GDELT
        articles = safe(gdelt_articles, query, max_records=6,
                         timespan="48h", default=[]) or []
        # If the filter killed everything for this topic (rate limit or
        # empty result), retry without sourcelang and post-filter on the
        # article-level language field so we still get some content.
        if not articles:
            # Strip the trailing " sourcelang:english" before retrying
            bare = query.replace(" sourcelang:english", "")
            articles = safe(gdelt_articles, bare, max_records=8,
                             timespan="48h", default=[]) or []
        # Defensive post-filter — drop articles GDELT explicitly tagged as
        # non-English. Articles with no language tag pass (we trust the
        # query-side filter or the source defaulting to English).
        articles = [a for a in articles if _is_english(a)]
        for a in articles:
            time_str, date_str = _format_seen_at(a.get("seendate"))
            tone = a.get("tone")
            try:
                tone = float(tone) if tone not in (None, "") else None
            except (TypeError, ValueError):
                tone = None
            title = (a.get("title") or "")[:160]
            # Try to attach a real summary from Google News RSS by topic
            summary = _match_summary(summaries_by_tag.get(tag, []), title)
            if not summary:
                summary = title[:280]   # fall back to title
            items.append({
                "time": time_str or "",
                "date": date_str or "",
                "region": region,
                "impact": "critical" if tag in CRITICAL_TAGS
                           else "high" if tag in HIGH_TAGS else "med",
                "sentiment": _classify_sentiment(tone),
                "tag": tag,
                "title": title,
                "summary": summary,
                "moved": [],   # left empty; we don't have desk attribution
                "_seendate": a.get("seendate") or "",
                "_url": a.get("url") or "",
            })
    # Sort newest first by raw seendate string (lexicographic == chronological)
    items.sort(key=lambda x: x["_seendate"], reverse=True)
    # De-dupe similar titles
    seen_titles: set[str] = set()
    deduped: list[dict] = []
    for it in items:
        key = re.sub(r"\W+", "", it["title"].lower())[:60]
        if key in seen_titles or not key:
            continue
        seen_titles.add(key)
        deduped.append(it)
    return deduped[:12]


# Map FRED release names → (event display name, impact)
RELEASE_IMPACT: dict[str, tuple[str, str]] = {
    "Employment Situation":           ("Nonfarm Payrolls",       "critical"),
    "Consumer Price Index":           ("CPI",                    "critical"),
    "Personal Income and Outlays":    ("Core PCE",               "critical"),
    "Gross Domestic Product":         ("GDP Advance",            "critical"),
    "FOMC":                            ("FOMC Rate Decision",    "critical"),
    "Producer Price Index":           ("PPI",                    "high"),
    "Retail Sales":                    ("Retail Sales",          "high"),
    "Industrial Production":           ("Industrial Production", "high"),
    "Job Openings and Labor Turnover": ("JOLTS Openings",        "high"),
    "ADP":                             ("ADP Private Payrolls",  "high"),
    "Housing Starts":                  ("Housing Starts",        "med"),
    "New Residential":                 ("New Home Sales",        "med"),
    "Existing Home Sales":             ("Existing Home Sales",   "med"),
    "Durable Goods":                   ("Durable Goods Orders",  "high"),
    "Consumer Sentiment":              ("UMich Consumer Sent.",  "med"),
    "Personal Saving":                 ("Personal Income",       "med"),
    "International Trade":             ("Trade Balance",         "med"),
    "S&P":                             ("Case-Shiller HPI",      "med"),
    "Construction Spending":           ("Construction Spending", "low"),
    "Wholesale Trade":                 ("Wholesale Inventories", "low"),
    "Beige Book":                      ("Fed Beige Book",        "med"),
    "Fed":                             ("Fed Release",           "med"),
}


def _format_calendar_date(d: str) -> str:
    """Convert YYYY-MM-DD to 'Mon DD (Day)'."""
    try:
        when = dt.date.fromisoformat(d)
        return when.strftime("%b %d (%a)")
    except Exception:
        return d


def _fetch_calendar() -> list[dict]:
    rows = fred_release_dates(days_ahead=14, days_back=1, limit=200) or []
    out: list[dict] = []
    for r in rows:
        rname = r.get("release_name", "") or ""
        # Match the first keyword in the rname against our impact map
        impact_event: tuple[str, str] | None = None
        for needle, mapping in RELEASE_IMPACT.items():
            if needle.lower() in rname.lower():
                impact_event = mapping
                break
        if impact_event is None:
            # Skip low-signal releases to keep the table compact
            continue
        event, impact = impact_event
        out.append({
            "date": _format_calendar_date(r.get("date", "")),
            "time": "08:30",   # most US releases drop at 08:30 ET
            "region": "US",
            "event": f"{event} · {rname[:40]}",
            "prev": "—",
            "cons": "—",
            "impact": impact,
        })
    # Truncate to ~17 to fit the table layout
    return out[:17]


def _auto_narrative(asof: str) -> dict:
    """Generate a short 'week in macro' paragraph from current data.

    Reads the per-section JSON files we just wrote in this same ETL run.
    """
    def load(name: str) -> dict:
        p = f"data/{name}.json"
        if not os.path.exists(p):
            return {}
        try:
            return json.load(open(p))
        except Exception:
            return {}

    g = load("growth"); i = load("inflation"); l = load("labor")
    m = load("monetary"); r = load("risk")

    cpi = ((i.get("cpi") or {}).get("headline"))
    core = ((i.get("cpi") or {}).get("core"))
    u3 = ((l.get("unemployment") or {}).get("u3"))
    real_yoy = ((g.get("gdp") or {}).get("real_yoy"))
    curve_bp = ((m.get("curve") or {}).get("current_2s10s_bp"))
    stlfsi = ((r.get("stress") or {}).get("stlfsi"))

    bits: list[str] = []
    stance = "neutral"
    if cpi is not None and core is not None:
        bits.append(f"<em>Headline CPI {cpi:.1f}% YoY</em>, core {core:.1f}%")
        if cpi > 3:
            stance = "hawkish"
        elif cpi < 2.2:
            stance = "dovish"
    if u3 is not None:
        bits.append(f"unemployment {u3:.1f}%")
    if real_yoy is not None:
        bits.append(f"real GDP {real_yoy:+.1f}% YoY")
    if curve_bp is not None:
        bits.append(f"<em>2s10s {curve_bp:+.0f}bp</em>")
    if stlfsi is not None:
        bits.append(f"STLFSI {stlfsi:+.2f}")

    if not bits:
        text = ("<em>The week in macro</em>: data refresh in progress; "
                "see individual section tabs for the latest numbers.")
    else:
        text = ("<em>The week in macro</em>: " + "; ".join(bits) +
                ". Auto-generated from live FRED data, refreshed every hour.")

    return {
        "narrative": {"stance": stance, "text": text},
        "author": f"Auto · {asof[:10]}",
    }


def build() -> dict:
    asof = utcnow_iso()
    headlines = _fetch_headlines()
    calendar = _fetch_calendar()

    return {
        "asOf": asof,
        "headlines": headlines or [{
            "time": dt.datetime.utcnow().strftime("%H:%M UTC"),
            "date": dt.datetime.utcnow().strftime("%b %d"),
            "region": "GL", "impact": "med", "sentiment": "neutral",
            "tag": "INFO",
            "title": "GDELT news feed unavailable this run — try again next refresh",
            "summary": "Headlines come from GDELT 2.0 (free). Refreshed hourly.",
            "moved": [],
        }],
        "calendar": calendar,
        "cbSpeak": [],
        "whatHappened": _auto_narrative(asof),
    }


if __name__ == "__main__":
    write_json("data/news.json", build())
