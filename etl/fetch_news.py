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
                          fred_release_dates, google_news_rss,
                          investing_commodities_rss, oilprice_rss)


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
    ("BoJ",     "JP", _en("\"Bank of Japan\" OR BOJ OR Ueda OR yen")),
    # Energy beat — phrase-anchored query to avoid grabbing political pieces
    # that just happen to mention "oil" once. We further filter by title in
    # _is_energy_relevant() below so only genuinely energy stories survive.
    ("ENERGY",  "GL", _en("\"oil price\" OR \"oil prices\" OR \"crude oil\" "
                            "OR OPEC OR \"natural gas\" OR LNG "
                            "OR brent OR WTI OR \"refining margin\" OR \"gasoline price\"")),
    ("CREDIT",  "US", _en("credit OR lending OR \"bond yields\" OR spreads")),
    ("FISCAL",  "US", _en("\"federal deficit\" OR \"national debt\" OR CBO")),
    # PBoC/China dropped — most articles came back filtered (non-English
    # sources or off-topic). Coverage is preserved indirectly via DATA / ENERGY
    # which often reference China data when material.
]

# Accept several forms GDELT may return: full English name, ISO 639-1 code,
# ISO 639-2 code, or empty/missing.
ENGLISH_LANG_CODES = {"english", "eng", "en", ""}


def _is_english(article: dict) -> bool:
    """Defensive post-filter — drop anything explicitly tagged non-English."""
    lang = (article.get("language") or "").strip().lower()
    return lang in ENGLISH_LANG_CODES


# Title must contain at least one of these to qualify as a genuine energy /
# commodities story. Prevents political pieces that mention "oil" once in
# passing from leaking into the Energy & Commodities card.
ENERGY_TITLE_KEYWORDS = (
    "oil", "opec", "crude", "brent", "wti", "gasoline", "petrol",
    "natural gas", "lng", "refinery", "refining", "barrel",
    "energy", "petroleum", "diesel", "fuel",
)


def _rss_to_news_item(rss_item: dict, *, tag: str = "ENERGY",
                       region: str = "GL", impact: str = "high") -> dict:
    """Convert an RSS item dict to our news-item shape."""
    pub = rss_item.get("pub") or ""
    seendate, time_str, date_str = "", "", ""
    # RFC-822 date "Mon, 28 Apr 2026 14:32:00 GMT"
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S",
                 "%a, %d %b %Y %H:%M %Z", "%Y-%m-%dT%H:%M:%S"):
        try:
            ts = dt.datetime.strptime(pub[:25].strip(), fmt[:25] if len(fmt) > 25 else fmt)
            et = ts - dt.timedelta(hours=4)   # rough UTC -> ET
            time_str = et.strftime("%H:%M ET")
            date_str = et.strftime("%b %d")
            seendate = ts.strftime("%Y%m%d%H%M%S")
            break
        except Exception:
            continue
    title = (rss_item.get("title") or "")[:160]
    raw_summary = rss_item.get("summary") or ""
    # Don't duplicate the title; don't show URL garbage
    if not raw_summary or raw_summary.strip().lower() == title.strip().lower():
        summary = ""
    else:
        summary = raw_summary[:280]
    return {
        "time":      time_str,
        "date":      date_str,
        "region":    region,
        "impact":    impact,
        "sentiment": "neutral",   # RSS feeds don't carry tone scores
        "tag":       tag,
        "title":     title,
        "summary":   summary,
        "moved":     [],
        "url":       rss_item.get("link") or "",
        "_seendate": seendate,
    }


def _supplement_energy_from_rss(existing: list[dict], target: int) -> list[dict]:
    """If GDELT under-delivered ENERGY headlines, pull more from
    Investing.com Commodities News RSS, then OilPrice.com — both free,
    both genuinely energy-focused, both filtered through _is_energy_relevant
    so non-energy items get dropped."""
    if len(existing) >= target:
        return existing

    seen_titles = {it.get("title", "").lower() for it in existing}
    out = list(existing)

    sources = (
        ("Investing.com", investing_commodities_rss),
        ("OilPrice.com",  oilprice_rss),
    )
    for source_name, fetch_fn in sources:
        if len(out) >= target:
            break
        feed = safe(fetch_fn, default=[]) or []
        added = 0
        for ri in feed:
            if len(out) >= target:
                break
            title = ri.get("title", "")
            tlow = title.lower().strip()
            if not tlow or tlow in seen_titles:
                continue
            # Both Investing.com Commodities + OilPrice are energy-themed,
            # but they cover metals/grains too. Apply our title filter.
            if not _is_energy_relevant(title):
                continue
            seen_titles.add(tlow)
            out.append(_rss_to_news_item(ri, tag="ENERGY",
                                          region="GL", impact="high"))
            added += 1
        if added:
            print(f"  + supplemented {added} headlines from {source_name}")
    return out


def _is_energy_relevant(title: str) -> bool:
    """Return True if the title clearly indicates an energy/commodities
    story. Used as a post-fetch safety filter for the ENERGY topic."""
    if not title:
        return False
    low = title.lower()
    # Reject obvious political / personality pieces that GDELT might match
    # because the body mentions "oil" once. Keep this list minimal and only
    # for terms that almost never appear in genuine energy headlines.
    political_traps = ("trump", "biden", "harris", "vance", "election")
    has_energy_kw = any(kw in low for kw in ENERGY_TITLE_KEYWORDS)
    if not has_energy_kw:
        return False
    # If a political name dominates the title and there's no clear price
    # angle, drop it. Allow it through if both a politician AND a strong
    # price/trade keyword appear (e.g. "Biden Iran sanctions oil exports").
    is_political = any(p in low for p in political_traps)
    if is_political:
        strong_terms = ("price", "barrel", "opec", "brent", "wti",
                         "lng", "refinery", "production", "export", "import",
                         "sanctions", "embargo", "supply", "demand")
        if not any(s in low for s in strong_terms):
            return False
    return True

# Critical-impact tags get the red treatment in the UI
CRITICAL_TAGS = {"FED", "ECB", "BoJ"}
HIGH_TAGS = {"DATA", "ENERGY"}


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
    "BoJ":    "Bank of Japan",
    "ENERGY": "oil prices natural gas OPEC",
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
    """Find the closest matching Google News summary for a GDELT title.
    The RSS parser already strips link-redirect garbage, but we re-validate
    here as a safety net so the UI never sees `<a href=...` literals."""
    from etl._common import _is_rss_garbage
    if not gnews_items or not gdelt_title:
        return ""
    target_words = set(gdelt_title.lower().split())
    best = ("", 0)
    for it in gnews_items:
        t = (it.get("title") or "").lower()
        s = (it.get("summary") or "").strip()
        if not s or _is_rss_garbage(s):
            continue
        # Skip "summary" that is just the headline repeated
        if s.lower() == gdelt_title.lower():
            continue
        score = len(target_words & set(t.split()))
        if score > best[1]:
            best = (s, score)
    return best[0][:280] if best[1] >= 2 else ""


ENERGY_TAGS = {"ENERGY", "OIL"}
ECONOMIC_CAP = 10      # max articles in the Economic Headlines card
ENERGY_CAP = 10        # max articles in the Energy & Commodities card


def _fetch_headlines() -> list[dict]:
    summaries_by_tag = _fetch_summaries_per_tag()
    # Per-topic article fetch + filter + bucket
    items_by_tag: dict[str, list[dict]] = {}
    for tag, region, query in TOPIC_QUERIES:
        # ENERGY needs more candidates because the title filter is strict
        # (drops political pieces). Use a longer window too so we have
        # enough genuine energy stories.
        max_rec = 20 if tag in ENERGY_TAGS else 8
        span = "72h" if tag in ENERGY_TAGS else "48h"

        articles = safe(gdelt_articles, query, max_records=max_rec,
                         timespan=span, default=[]) or []
        if not articles:
            bare = query.replace(" sourcelang:english", "")
            articles = safe(gdelt_articles, bare, max_records=max_rec * 2,
                             timespan=span, default=[]) or []
        articles = [a for a in articles if _is_english(a)]

        if tag in ENERGY_TAGS:
            # Title-level filter — keep only genuine energy/commodities pieces
            articles = [a for a in articles
                        if _is_energy_relevant(a.get("title") or "")]

        articles.sort(key=lambda a: a.get("seendate") or "", reverse=True)

        topic_items: list[dict] = []
        for a in articles:
            time_str, date_str = _format_seen_at(a.get("seendate"))
            tone = a.get("tone")
            try:
                tone = float(tone) if tone not in (None, "") else None
            except (TypeError, ValueError):
                tone = None
            title = (a.get("title") or "")[:160]
            summary = _match_summary(summaries_by_tag.get(tag, []), title)
            # If no real summary is available, leave empty — never repeat
            # the title verbatim (looks like duplicate text in the UI).
            if not summary or summary.strip().lower() == title.strip().lower():
                summary = ""
            topic_items.append({
                "time": time_str or "",
                "date": date_str or "",
                "region": region,
                "impact": "critical" if tag in CRITICAL_TAGS
                           else "high" if tag in HIGH_TAGS else "med",
                "sentiment": _classify_sentiment(tone),
                "tag": tag,
                "title": title,
                "summary": summary,
                "moved": [],
                "url": a.get("url") or "",
                "_seendate": a.get("seendate") or "",
            })

        # ENERGY-only: supplement from Investing.com / OilPrice RSS if GDELT
        # under-delivered. Target ENERGY_CAP so the section is always full.
        if tag in ENERGY_TAGS and len(topic_items) < ENERGY_CAP:
            print(f"  + GDELT returned {len(topic_items)} ENERGY items; "
                  f"supplementing from RSS to reach {ENERGY_CAP}...")
            topic_items = _supplement_energy_from_rss(topic_items, ENERGY_CAP)
            # Re-sort merged list by recency
            topic_items.sort(key=lambda x: x.get("_seendate") or "", reverse=True)

        items_by_tag[tag] = topic_items

    def _round_robin(tags: list[str], cap: int,
                      max_per_topic: int) -> list[dict]:
        """Round-robin pick across topics, dedupe by title, stop at cap."""
        seen: set[str] = set()
        out: list[dict] = []
        for round_num in range(max_per_topic):
            for tag in tags:
                if len(out) >= cap:
                    return out
                bucket = items_by_tag.get(tag, [])
                if round_num >= len(bucket):
                    continue
                it = bucket[round_num]
                key = re.sub(r"\W+", "", it["title"].lower())[:60]
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(it)
        return out

    economic_tags = [t for t, _, _ in TOPIC_QUERIES if t not in ENERGY_TAGS]
    energy_tags   = [t for t, _, _ in TOPIC_QUERIES if t in ENERGY_TAGS]

    economic = _round_robin(economic_tags, cap=ECONOMIC_CAP, max_per_topic=3)
    energy   = _round_robin(energy_tags,   cap=ENERGY_CAP,   max_per_topic=ENERGY_CAP)

    return economic + energy


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
