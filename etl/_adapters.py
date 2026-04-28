"""Shape adapters — convert raw ETL output into the seed contract that the
React components expect.

Each adapter takes:
  raw   : the dict produced by fetch_<section>.build()
  seed  : the section as it appears in etl/_seed_macro.json
          (used as a fallback for any field we cannot compute live)

Returns: a dict in the seed shape, ready to be merged into the bundle.

For every adapter the rule is: prefer the live value if we have it; else
keep the seed value verbatim. That guarantees the page never breaks even
if a single fetcher fails.
"""
from __future__ import annotations

import datetime as dt
from typing import Any


# ----------------------------------------------------------------- helpers --
def _fmt_month_label(date_str: str) -> str:
    try:
        d = dt.date.fromisoformat(date_str[:10])
        return d.strftime("%b %y")
    except Exception:
        return date_str[:7]


def _fmt_quarter_label(date_str: str) -> str:
    try:
        d = dt.date.fromisoformat(date_str[:10])
        return f"{(d.month - 1) // 3 + 1}Q{str(d.year)[-2:]}"
    except Exception:
        return date_str[:7]


def _fmt_year_label(date_str: str) -> str:
    return date_str[:4]


def _last(series: list[tuple[str, float]], default: float | None = None
          ) -> float | None:
    """Last numeric value of a [(date, value), ...] series."""
    if not series:
        return default
    return series[-1][1]


def _yoy(series: list[tuple[str, float]]) -> float | None:
    """Year-over-year % change of last vs. ~12 entries earlier."""
    if not series or len(series) < 13:
        return None
    last = series[-1][1]
    prev = series[-13][1]
    if not prev:
        return None
    return round((last / prev - 1) * 100, 2)


def _delta(curr: float | None, prev: float | None) -> float:
    """Curr - prev, rounded; 0 if either is None."""
    if curr is None or prev is None:
        return 0
    return round(curr - prev, 2)


def _split_series(series: list[tuple[str, float]], n: int,
                  label_fmt=_fmt_month_label) -> tuple[list[str], list[float]]:
    """Take the last N points of a [(date, value)] series and split into
    parallel labels[] and values[] arrays."""
    s = series[-n:] if len(series) > n else series
    return ([label_fmt(d) for d, _ in s], [v for _, v in s])


def _safe(d: dict, *keys, default=None):
    """Nested dict accessor that returns default at the first None."""
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d if d is not None else default


def _stance(criteria: dict[str, bool]) -> str:
    """Heuristic stance picker — returns 'hawk', 'dove', or 'neutral'."""
    if criteria.get("hawk"):
        return "hawk"
    if criteria.get("dove"):
        return "dove"
    return "neutral"


# ----------------------------------------------------------------- growth --
def shape_growth(raw: dict, seed: dict) -> dict:
    out = dict(seed)  # start from seed, override what we have

    gdp_raw = raw.get("gdp", {}) or {}
    real_yoy = gdp_raw.get("real_yoy")
    real_qoq = gdp_raw.get("real_qoq_saar")
    nominal_yoy = gdp_raw.get("nominal_yoy")
    potential = gdp_raw.get("potential")

    # gdp series → labels (quarterly) + real + nominal arrays
    series_raw = gdp_raw.get("series") or []
    labels, real_vals = _split_series(series_raw, 12, _fmt_quarter_label)

    out["gdp"] = {
        "real_qoq_saar": {
            "val": real_qoq if real_qoq is not None
                    else _safe(seed, "gdp", "real_qoq_saar", "val"),
            "delta": _safe(seed, "gdp", "real_qoq_saar", "delta", default=0),
            "label": _safe(seed, "gdp", "real_qoq_saar", "label",
                            default="Real GDP"),
            "unit": "% SAAR",
            "thresh": _safe(seed, "gdp", "real_qoq_saar", "thresh",
                             default={"expansion": 1.8}),
        },
        "real_yoy": {
            "val": real_yoy if real_yoy is not None
                    else _safe(seed, "gdp", "real_yoy", "val"),
            "delta": _safe(seed, "gdp", "real_yoy", "delta", default=0),
        },
        "nominal_yoy": {
            "val": nominal_yoy if nominal_yoy is not None
                    else _safe(seed, "gdp", "nominal_yoy", "val"),
            "delta": _safe(seed, "gdp", "nominal_yoy", "delta", default=0),
        },
        "potential": {
            "val": potential if potential is not None
                    else _safe(seed, "gdp", "potential", "val", default=1.8),
            "delta": 0,
        },
        "series": {
            "labels":  labels  if labels  else _safe(seed, "gdp", "series", "labels"),
            "real":    real_vals if real_vals else _safe(seed, "gdp", "series", "real"),
            "nominal": _safe(seed, "gdp", "series", "nominal"),  # nominal not separately available
        },
    }

    # IP — last value + 24-month series
    ip_raw = raw.get("ip", {}) or {}
    ip_series = ip_raw.get("series") or []
    if ip_series:
        out["ip"] = dict(seed.get("ip", {}))
        out["ip"]["val"] = round(ip_series[-1][1], 1)
        out["ip"]["yoy"] = ip_raw.get("yoy") or out["ip"].get("yoy")
        out["ip"]["series"] = [round(v, 1) for _, v in ip_series[-24:]]

    # Capacity utilization
    cu_raw = raw.get("caputil", {}) or {}
    if cu_raw.get("val") is not None:
        out["caputil"] = dict(seed.get("caputil", {}))
        out["caputil"]["val"] = cu_raw["val"]
        cu_series = cu_raw.get("series") or []
        if cu_series:
            out["caputil"]["series"] = [round(v, 1) for _, v in cu_series[-24:]]

    # Narrative
    out["narrative"] = {
        "stance": _stance({"hawk": (real_yoy or 0) > 2.5,
                            "dove": (real_yoy or 0) < 1.0}),
        "text": (
            f"<em>Real GDP {real_yoy:.1f}% YoY</em>" if real_yoy is not None else "Real GDP"
        ) + (
            f", capacity utilization {cu_raw.get('val', 0):.1f}%"
            if cu_raw.get("val") is not None else ""
        ) + ". <em>Auto-generated from FRED.</em>",
    }
    return out


# -------------------------------------------------------------- inflation --
def shape_inflation(raw: dict, seed: dict) -> dict:
    out = dict(seed)
    cpi_r = raw.get("cpi", {}) or {}
    ppi_r = raw.get("ppi", {}) or {}

    # cpi series — needs labels + headline + core arrays
    # raw doesn't carry both arrays directly, but we have a year-over-year
    # series in cpi.series. Use it for "headline" array and keep seed for core.
    cpi_series_raw = cpi_r.get("series") or []
    labels, headline_arr = _split_series(cpi_series_raw, 36, _fmt_month_label)

    out["cpi"] = {
        "headline": {
            "yoy":      cpi_r.get("headline") if cpi_r.get("headline") is not None
                          else _safe(seed, "cpi", "headline", "yoy"),
            "mom":      _safe(seed, "cpi", "headline", "mom"),
            "core":     cpi_r.get("core") if cpi_r.get("core") is not None
                          else _safe(seed, "cpi", "headline", "core"),
            "core_mom": _safe(seed, "cpi", "headline", "core_mom"),
        },
        "supercore_3m": cpi_r.get("supercore_3m") or _safe(seed, "cpi", "supercore_3m"),
        "sticky":       cpi_r.get("sticky") or _safe(seed, "cpi", "sticky"),
        "flexible":     _safe(raw, "sticky_vs_flex", "flexible") or _safe(seed, "cpi", "flexible"),
        "series": {
            "labels":   labels   if labels   else _safe(seed, "cpi", "series", "labels"),
            "headline": headline_arr if headline_arr else _safe(seed, "cpi", "series", "headline"),
            "core":     _safe(seed, "cpi", "series", "core"),  # not in raw
        },
    }

    # PPI
    if ppi_r.get("yoy") is not None:
        out["ppi"] = dict(seed.get("ppi", {}))
        out["ppi"]["yoy"] = ppi_r["yoy"]
        ppi_series = ppi_r.get("series") or []
        if ppi_series:
            out["ppi"]["series"] = [round(v, 2) for _, v in ppi_series[-24:]]

    # Deflator
    if _safe(raw, "deflator", "yoy") is not None:
        out["deflator"] = dict(seed.get("deflator", {}))
        out["deflator"]["yoy"] = raw["deflator"]["yoy"]

    # Expectations — replace any field we have
    exp_raw = raw.get("expectations", {}) or {}
    out["expectations"] = dict(seed.get("expectations", {}))
    for k in ("breakeven_5y", "breakeven_10y", "breakeven_5y5y",
              "umich_1y", "umich_5y"):
        if exp_raw.get(k) is not None:
            out["expectations"][k] = exp_raw[k]

    # Sticky vs flex
    svf = raw.get("sticky_vs_flex", {}) or {}
    s_series = svf.get("series_sticky") or []
    f_series = svf.get("series_flex") or []
    if s_series and f_series:
        labels, sticky_arr = _split_series(s_series, 24)
        _,      flex_arr   = _split_series(f_series, 24)
        out["sticky_vs_flex"] = {"labels": labels, "sticky": sticky_arr,
                                  "flexible": flex_arr}

    # Commodities — overlay live prices where we have them
    live_comms = {c.get("name"): c for c in raw.get("commodities", []) or []
                   if c.get("price") is not None}
    seed_comms = seed.get("commodities", []) or []
    new_comms = []
    for sc in seed_comms:
        live = live_comms.get(sc.get("name"))
        if live and live.get("price") is not None:
            new_comms.append({**sc, "val": round(live["price"], 2)})
        else:
            new_comms.append(sc)
    out["commodities"] = new_comms

    # Narrative
    cpi_v = cpi_r.get("headline")
    core_v = cpi_r.get("core")
    out["narrative"] = {
        "stance": _stance({"hawk": (cpi_v or 0) > 3.0,
                            "dove": (cpi_v or 0) < 2.2}),
        "text": (
            (f"<em>Headline CPI {cpi_v:.1f}% YoY</em>" if cpi_v is not None else "")
            + (f", core {core_v:.1f}%" if core_v is not None else "")
            + (f", sticky {cpi_r.get('sticky'):.1f}%" if cpi_r.get("sticky") is not None else "")
            + ". <em>Auto-generated from FRED.</em>"
        ),
    }
    return out


# ----------------------------------------------------------------- labor --
def shape_labor(raw: dict, seed: dict) -> dict:
    out = dict(seed)
    un = raw.get("unemployment", {}) or {}
    nfp = raw.get("nfp", {}) or {}
    jolts = raw.get("jolts", {}) or {}
    wages = raw.get("wages", {}) or {}

    u3 = un.get("u3"); u6 = un.get("u6")
    u3_series = un.get("series_u3") or []
    u6_series = un.get("series_u6") or []
    labels, u3_arr = _split_series(u3_series, 36)
    _,      u6_arr = _split_series(u6_series, 36)
    out["unemployment"] = {
        "u3": u3 if u3 is not None else _safe(seed, "unemployment", "u3"),
        "u3_delta": _delta(u3, _last(u3_series[:-1])) if u3 else 0,
        "u3_cycle_low": min((v for _, v in u3_series), default=
                              _safe(seed, "unemployment", "u3_cycle_low", default=3.4)),
        "u6": u6 if u6 is not None else _safe(seed, "unemployment", "u6"),
        "u6_delta": _delta(u6, _last(u6_series[:-1])) if u6 else 0,
        "series": {
            "labels": labels if labels else _safe(seed, "unemployment", "series", "labels"),
            "u3":     u3_arr if u3_arr else _safe(seed, "unemployment", "series", "u3"),
            "u6":     u6_arr if u6_arr else _safe(seed, "unemployment", "series", "u6"),
        },
    }

    # LFPR
    lfpr_v = _safe(raw, "lfpr", "val")
    if lfpr_v is not None:
        out["lfpr"] = dict(seed.get("lfpr", {}))
        out["lfpr"]["val"] = lfpr_v

    # NFP
    nfp_series = nfp.get("series") or []
    nfp_labels, nfp_arr = _split_series(nfp_series, 36)
    if nfp_arr:
        out["nfp"] = dict(seed.get("nfp", {}))
        out["nfp"]["current"] = int(nfp.get("current") or nfp_arr[-1])
        out["nfp"]["prev"] = int(nfp_arr[-2]) if len(nfp_arr) > 1 else out["nfp"]["current"]
        out["nfp"]["avg_3m"] = int(sum(nfp_arr[-3:]) / 3) if len(nfp_arr) >= 3 else out["nfp"]["current"]
        out["nfp"]["avg_12m"] = int(sum(nfp_arr[-12:]) / 12) if len(nfp_arr) >= 12 else out["nfp"]["current"]
        out["nfp"]["series"] = [int(v) for v in nfp_arr]
        out["nfp"]["labels"] = nfp_labels

    # JOLTS
    if jolts.get("openings") is not None:
        out["jolts"] = dict(seed.get("jolts", {}))
        # FRED JTSJOL is in thousands; the seed shows millions (e.g. 7.42)
        out["jolts"]["openings"] = round((jolts["openings"] or 0) / 1000, 2)
        op_series = jolts.get("openings_series") or []
        if op_series:
            out["jolts"]["openings_series"] = [round(v / 1000, 2) for _, v in op_series[-24:]]

    # Wages
    ahe = wages.get("ahe_yoy")
    wage_series = wages.get("series") or []
    if wage_series:
        labels_w, wage_arr = _split_series(wage_series, 36)
        out["wages"] = dict(seed.get("wages", {}))
        out["wages"]["ahe_yoy"] = ahe if ahe is not None else out["wages"].get("ahe_yoy")
        out["wages"]["series"] = wage_arr
        out["wages"]["labels"] = labels_w

    # Beveridge — take whatever points we have
    bev_raw = raw.get("beveridge", []) or []
    if bev_raw:
        out["beveridge"] = [
            {"u": pt.get("u3"), "v": pt.get("openings_rate"),
             "era": "live", "year": pt.get("date", "")[:4]}
            for pt in bev_raw[-7:] if pt.get("u3") and pt.get("openings_rate")
        ] or seed.get("beveridge", [])

    # Narrative
    nfp_curr = out["nfp"]["current"] if "current" in (out.get("nfp") or {}) else None
    out["narrative"] = {
        "stance": _stance({"hawk": (u3 or 5) < 3.8,
                            "dove": (u3 or 4) > 4.5}),
        "text": (
            (f"Unemployment {u3:.1f}%" if u3 is not None else "")
            + (f", NFP {nfp_curr:+,}k m/m" if nfp_curr is not None else "")
            + (f", AHE {ahe:+.1f}% YoY" if ahe is not None else "")
            + ". <em>Auto-generated from FRED.</em>"
        ),
    }
    return out


# -------------------------------------------------------------- monetary --
def shape_monetary(raw: dict, seed: dict) -> dict:
    out = dict(seed)
    pol = raw.get("policy_rates", []) or []
    curve = raw.get("curve", {}) or {}
    spreads = raw.get("spreads", {}) or {}
    money = raw.get("money", {}) or {}
    fci = raw.get("fci", {}) or {}

    # Policy rates — overlay rates onto seed entries (preserve cb labels)
    seed_pol = seed.get("policy_rates", []) or []
    region_to_rate = {}
    for p in pol:
        region_to_rate[p.get("region", "").upper()] = p.get("rate")
    new_pol = []
    for entry in seed_pol:
        cb = entry.get("cb", "")
        # Map cb label like "Fed (FFR)" → region "US"
        region = ("US" if "fed" in cb.lower() else
                   "EA" if "ecb" in cb.lower() else
                   "UK" if "boe" in cb.lower() else
                   "JP" if "boj" in cb.lower() else "")
        live_rate = region_to_rate.get(region)
        new_pol.append({**entry,
                         "rate": live_rate if live_rate is not None else entry.get("rate")})
    out["policy_rates"] = new_pol

    # 2s10s series + spreads
    sp_2s10s = spreads.get("series_2s10s") or curve.get("series_2s10s") or []
    if sp_2s10s:
        labels, vals = _split_series(sp_2s10s, 24)
        # FRED returns the 2s10s in pp (e.g. -0.26 = -26bp). Convert to bp.
        out["spreads"] = dict(seed.get("spreads", {}))
        out["spreads"]["series_2s10s"] = [int(round(v * 100)) for v in vals]
        out["spreads"]["labels"] = labels
        out["spreads"]["2s10s"] = int(round(vals[-1] * 100))

    # Curve — current 10y as last value of series_10y; seed labels stay
    s10 = curve.get("series_10y") or []
    if s10:
        # build a 10-tenor curve from the data we have
        tenors_seed = seed.get("curve", {}).get("labels", [])
        # Replace the 10Y entry only, keep rest from seed
        cur = list(seed.get("curve", {}).get("current", []))
        if "10Y" in tenors_seed:
            idx = tenors_seed.index("10Y")
            if idx < len(cur):
                cur[idx] = round(s10[-1][1], 2)
        out["curve"] = dict(seed.get("curve", {}))
        out["curve"]["current"] = cur

    # FCI
    if fci.get("stlfsi") is not None:
        out["fci"] = dict(seed.get("fci", {}))
        out["fci"]["val"] = fci["stlfsi"]
        s_stlfsi = fci.get("series_stlfsi") or []
        if s_stlfsi:
            labels_f, vals_f = _split_series(s_stlfsi, 24)
            out["fci"]["series"] = [round(v, 2) for v in vals_f]
            out["fci"]["labels"] = labels_f

    # Money — keep seed; m1/m2 yoy where available
    if money.get("m1_yoy") is not None or money.get("m2_yoy") is not None:
        out["money"] = dict(seed.get("money", {}))
        if money.get("m1_yoy") is not None: out["money"]["m1_yoy"] = money["m1_yoy"]
        if money.get("m2_yoy") is not None: out["money"]["m2_yoy"] = money["m2_yoy"]

    # Narrative
    s2s10 = out.get("spreads", {}).get("2s10s") or 0
    out["narrative"] = {
        "stance": _stance({"hawk": (fci.get("stlfsi") or 0) > 0,
                            "dove": (fci.get("stlfsi") or 0) < -0.5}),
        "text": (
            f"<em>10y Treasury</em> "
            + (f"{_safe(out, 'curve', 'current', default=[None]*10)[5] or '—'}%" if isinstance(_safe(out, 'curve', 'current'), list) else "—")
            + f", 2s10s {s2s10:+d}bp"
            + (f", STLFSI {fci.get('stlfsi'):+.2f}" if fci.get("stlfsi") is not None else "")
            + ". <em>Auto-generated from FRED.</em>"
        ),
    }
    return out


# -------------------------------------------------------------- external --
def shape_external(raw: dict, seed: dict) -> dict:
    out = dict(seed)
    trade = raw.get("trade", {}) or {}
    fx = raw.get("fx", {}) or {}
    ca = raw.get("current_account", {}) or {}

    # Trade balance — FRED is in millions; seed uses billions (negative ~78.2)
    if trade.get("balance") is not None:
        out["trade"] = dict(seed.get("trade", {}))
        out["trade"]["balance"] = round((trade["balance"] or 0) / 1000, 1)
        out["trade"]["exports"] = round((trade.get("exports") or 0) / 1000, 1)
        out["trade"]["imports"] = round((trade.get("imports") or 0) / 1000, 1)
        bs = trade.get("balance_series") or []
        if bs:
            labels, vals = _split_series(bs, 24)
            out["trade"]["series"] = {
                "labels": labels,
                "balance": [int(v / 1000) for v in vals],
            }

    # Current account — FRED returns in millions
    if ca.get("val") is not None:
        out["current_account"] = dict(seed.get("current_account", {}))
        out["current_account"]["pct_gdp"] = round(ca["val"] / 1000 / 280, 2)  # rough %GDP

    # FX
    if fx.get("dxy") is not None:
        out["fx"] = dict(seed.get("fx", {}))
        out["fx"]["dxy"] = round(fx["dxy"], 2)
        # Cross pairs — overlay where shape matches
        live_pairs = {p.get("pair"): p.get("rate") for p in fx.get("pairs", []) or []}
        seed_pairs = seed.get("fx", {}).get("pairs", [])
        out["fx"]["pairs"] = [
            {**sp, "val": round(live_pairs.get(sp.get("pair"), sp.get("val", 0)), 4)}
            for sp in seed_pairs
        ]
        # dxy_series
        dxy_series = fx.get("dxy_series") or []
        if dxy_series:
            labels, vals = _split_series(dxy_series, 24)
            out["fx"]["dxy_series"] = [round(v, 1) for v in vals]
            out["fx"]["labels"] = labels

    # Narrative
    bal = out.get("trade", {}).get("balance")
    out["narrative"] = {
        "stance": "neutral",
        "text": (
            (f"Trade balance {bal:+,.1f}B" if bal is not None else "")
            + (f", DXY {fx.get('dxy'):.2f}" if fx.get("dxy") is not None else "")
            + ". <em>Auto-generated from FRED + Frankfurter.</em>"
        ),
    }
    return out


# --------------------------------------------------------------- markets --
def shape_markets(raw: dict, seed: dict) -> dict:
    out = dict(seed)
    eq_live = {e.get("label"): e for e in raw.get("equity", []) or []}
    seed_eq = seed.get("equity", []) or []

    new_eq = []
    for sec in seed_eq:
        idx = sec.get("idx", "")
        live = eq_live.get(idx)
        if live and live.get("price") is not None:
            new_eq.append({**sec,
                            "val": round(live["price"], 2),
                            # YTD/YoY would need a year-ago anchor; keep seed
                            })
        else:
            new_eq.append(sec)
    out["equity"] = new_eq

    # SPX series — last 24 monthly closes from FRED SP500
    spx_series = raw.get("spx_series") or []
    if isinstance(spx_series, list) and spx_series:
        # spx_series is [(date, close)]; produce labels + price + ma50 + ma200
        labels, prices = _split_series(spx_series, 24)
        out["spx_series"] = dict(seed.get("spx_series", {}))
        out["spx_series"]["labels"] = labels
        out["spx_series"]["price"] = [int(round(p)) for p in prices]
        # ma50 / ma200 — keep seed (would need full daily history to compute)

    # Credit OAS
    cr = raw.get("credit", {}) or {}
    if cr.get("ig_oas") is not None:
        out["credit"] = dict(seed.get("credit", {}))
        # FRED returns OAS in pp; seed shows bp (108)
        out["credit"]["ig_oas"] = int(round((cr.get("ig_oas") or 0) * 100))
        out["credit"]["hy_oas"] = int(round((cr.get("hy_oas") or 0) * 100))
        ig_series = cr.get("series_ig") or []
        hy_series = cr.get("series_hy") or []
        if ig_series and hy_series:
            labels, ig_arr = _split_series(ig_series, 24)
            _,      hy_arr = _split_series(hy_series, 24)
            out["credit"]["series"] = {
                "labels": labels,
                "ig": [int(round(v * 100)) for v in ig_arr],
                "hy": [int(round(v * 100)) for v in hy_arr],
            }

    # Vol — VIX live, others stay seed (paywalled or only via Worker)
    vol = raw.get("vol", {}) or {}
    if vol.get("vix") is not None:
        out["vol"] = dict(seed.get("vol", {}))
        out["vol"]["vix"] = round(vol["vix"], 2)

    # Housing
    h = raw.get("housing", {}) or {}
    out["housing"] = dict(seed.get("housing", {}))
    if h.get("case_shiller_yoy") is not None:
        out["housing"]["case_shiller_yoy"] = round(h["case_shiller_yoy"], 1)
    if h.get("mortgage_30y") is not None:
        out["housing"]["mortgage_30y"] = round(h["mortgage_30y"], 2)
    starts_series = h.get("starts_series") or []
    if starts_series:
        labels, vals = _split_series(starts_series, 24)
        out["housing"]["starts_series"] = [int(round(v)) for v in vals]
        out["housing"]["labels"] = labels
        out["housing"]["starts"] = int(round(vals[-1]))
    mort_series = h.get("series_mortgage") or []
    if mort_series:
        out["housing"]["series_mortgage"] = [round(v, 2) for _, v in mort_series[-24:]]

    # Technicals — Fear & Greed + put/call live, others stay seed
    tech = raw.get("technicals", {}) or {}
    out["technicals"] = dict(seed.get("technicals", {}))
    if tech.get("fear_greed") is not None:
        out["technicals"]["fear_greed"] = int(tech["fear_greed"])
    if tech.get("put_call") is not None:
        out["technicals"]["put_call"] = round(tech["put_call"], 2)

    # Narrative
    spx = next((e.get("val") for e in out["equity"] if e.get("idx") == "S&P 500"), None)
    vix = out.get("vol", {}).get("vix")
    out["narrative"] = {
        "stance": _stance({"hawk": (vix or 0) > 25, "dove": (vix or 99) < 14}),
        "text": (
            (f"<em>S&P {spx:,.0f}</em>" if spx is not None else "")
            + (f", VIX {vix:.1f}" if vix is not None else "")
            + (f", IG OAS {out['credit'].get('ig_oas')}bp" if out.get('credit', {}).get('ig_oas') else "")
            + ". <em>Auto-generated from Stooq + FRED.</em>"
        ),
    }
    return out


# ---------------------------------------------------------------- fiscal --
def shape_fiscal(raw: dict, seed: dict) -> dict:
    out = dict(seed)
    us = raw.get("us", {}) or {}
    out["us"] = dict(seed.get("us", {}))
    if us.get("debt_pct_gdp") is not None:
        out["us"]["debt_pct_gdp"] = round(us["debt_pct_gdp"], 1)
    if us.get("deficit_pct_gdp") is not None:
        out["us"]["deficit_pct_gdp"] = round(us["deficit_pct_gdp"], 1)
    if us.get("interest_costs") is not None:
        out["us"]["interest_costs"] = int(round(us["interest_costs"] / 1000))  # to billions

    # Series
    debt_series = us.get("debt_series") or []
    deficit_series = us.get("deficit_series") or []
    if debt_series and deficit_series:
        labels, debt_arr = _split_series(debt_series, 12, _fmt_year_label)
        _,      def_arr  = _split_series(deficit_series, 12, _fmt_year_label)
        out["us"]["series"] = {
            "labels": labels,
            "deficit": [round(v, 2) for v in def_arr],
            "debt":    [round(v, 2) for v in debt_arr],
        }

    # Global comparison
    glob = raw.get("global", []) or []
    if glob:
        # Map known countries onto seed entries
        live_by_country = {g.get("country"): g for g in glob}
        seed_glob = seed.get("global", []) or []
        new_glob = []
        for entry in seed_glob:
            country = entry.get("country", "")
            live = next((g for n, g in live_by_country.items() if country.lower() in (n or "").lower()), None)
            if live:
                new_glob.append({**entry, "debt": round(live.get("debt_pct_gdp", entry["debt"]), 1)})
            else:
                new_glob.append(entry)
        out["global"] = new_glob

    out["narrative"] = {
        "stance": _stance({"hawk": (us.get("debt_pct_gdp") or 0) > 100,
                            "dove": False}),
        "text": (
            (f"<em>Federal debt {us.get('debt_pct_gdp'):.1f}%</em> of GDP"
              if us.get("debt_pct_gdp") is not None else "")
            + (f", deficit {us.get('deficit_pct_gdp'):+.1f}%" if us.get("deficit_pct_gdp") is not None else "")
            + ". <em>Auto-generated from FRED + Treasury.</em>"
        ),
    }
    return out


# ----------------------------------------------------------------- risk --
def shape_risk(raw: dict, seed: dict) -> dict:
    out = dict(seed)
    rec = raw.get("recession_prob", {}) or {}
    stress = raw.get("stress", {}) or {}

    # Recession prob — series goes 0..100 in our raw output
    if rec.get("ny_fed") is not None:
        out["recession_prob"] = dict(seed.get("recession_prob", {}))
        nyf = rec["ny_fed"]
        # FRED RECPROUSM156N is 0..1; convert to %
        if isinstance(nyf, (int, float)) and nyf <= 1:
            nyf = round(nyf * 100)
        out["recession_prob"]["ny_fed"] = int(nyf)
        rec_series = rec.get("series") or []
        if rec_series:
            labels, vals = _split_series(rec_series, 24)
            out["recession_prob"]["series"] = [
                int(round((v * 100) if isinstance(v, (int, float)) and v <= 1 else v))
                for v in vals]
            out["recession_prob"]["labels"] = labels

    # Stress
    if stress.get("stlfsi") is not None:
        out["stress"] = dict(seed.get("stress", {}))
        out["stress"]["stlfsi"] = round(stress["stlfsi"], 2)
        if stress.get("nfci") is not None:
            out["stress"]["chicago_nfci"] = round(stress["nfci"], 2)
        s_series = stress.get("series_stlfsi") or []
        if s_series:
            labels, vals = _split_series(s_series, 24)
            out["stress"]["series"] = [round(v, 2) for v in vals]
            out["stress"]["labels"] = labels

    # GPR — live value if available
    geo = raw.get("geopolitical", {}) or {}
    if geo.get("gpr") is not None:
        out["geopolitical"] = dict(seed.get("geopolitical", {}))
        out["geopolitical"]["gpr"] = int(round(geo["gpr"]))

    # Flashpoints — overlay GDELT articles into geopolitical.flashpoints
    fp = raw.get("flashpoints") or []
    if fp:
        # Reduce to top 6, group by region heuristics
        regions = {"Middle East": ["Israel", "Iran", "Gaza", "Yemen", "Syria"],
                   "Europe":      ["Russia", "Ukraine", "Putin"],
                   "Asia":        ["Taiwan", "China", "North Korea"],
                   "Africa":      ["Sudan", "Ethiopia"]}
        scored = {r: 0 for r in regions}
        for item in fp:
            title = (item.get("title") or "").lower()
            for r, kws in regions.items():
                if any(k.lower() in title for k in kws):
                    scored[r] += 1
        max_score = max(scored.values()) or 1
        out["geopolitical"]["flashpoints"] = [
            {"region": r,
             "score": int(round(scored[r] / max_score * 100)),
             "trend": "up" if scored[r] >= max_score / 2 else "flat"}
            for r in sorted(scored, key=lambda k: -scored[k]) if scored[r] > 0
        ] or seed.get("geopolitical", {}).get("flashpoints", [])

    out["narrative"] = {
        "stance": _stance({"hawk": (stress.get("stlfsi") or 0) > 0.5,
                            "dove": False}),
        "text": (
            (f"<em>Recession prob {out['recession_prob'].get('ny_fed')}%</em>"
              if out.get("recession_prob", {}).get("ny_fed") is not None else "")
            + (f", STLFSI {out.get('stress', {}).get('stlfsi'):+.2f}" if out.get("stress", {}).get("stlfsi") is not None else "")
            + (f", GPR {out.get('geopolitical', {}).get('gpr')}" if out.get("geopolitical", {}).get("gpr") else "")
            + ". <em>Auto-generated from FRED + GDELT.</em>"
        ),
    }
    return out


# -------------------------------------------------------- master dispatch --
ADAPTERS = {
    "growth":    shape_growth,
    "inflation": shape_inflation,
    "labor":     shape_labor,
    "monetary":  shape_monetary,
    "external":  shape_external,
    "markets":   shape_markets,
    "fiscal":    shape_fiscal,
    "risk":      shape_risk,
}


def adapt(name: str, raw: dict, seed_section: dict) -> dict:
    """Run the adapter for `name`. If anything goes wrong, return the seed
    untouched — page must never break."""
    fn = ADAPTERS.get(name)
    if not fn or not raw:
        return seed_section
    try:
        return fn(raw, seed_section)
    except Exception as e:
        print(f"  !! adapter[{name}] failed: {e}")
        return seed_section
