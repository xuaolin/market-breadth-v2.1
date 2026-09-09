from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_DIR, is_stale, now_iso, read_json, write_json
from decision_engine import MODEL_VERSION, STATE_META, confirmed_zone_entries

WEIGHTS = {
    "term": 0.20,
    "credit": 0.20,
    "breadth": 0.20,
    "vix": 0.15,
    "put_call": 0.10,
    "drawdown": 0.10,
    "aaii": 0.05,
}

EVENTS = [
    ("2009-03-09", "2009 GFC Bottom"),
    ("2018-02-05", "2018 Volmageddon"),
    ("2018-12-24", "2018 Q4 Selloff"),
    ("2020-03-23", "2020 COVID Bottom"),
    ("2022-10-12", "2022 Bear Market Low"),
    ("2025-04-07", "2025 Tariff Shock"),
]

BINS = [(0, 15), (15, 30), (30, 45), (45, 65), (65, 80), (80, 90), (90, 101)]


def n(v, digits=2):
    if v is None:
        return None
    try:
        f = float(v)
        return round(f, digits) if np.isfinite(f) else None
    except Exception:
        return None


def clean_date(v) -> str | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v)
    if not s or s.lower() in {"nan", "nat", "none"}:
        return None
    try:
        return pd.Timestamp(s).date().isoformat()
    except Exception:
        return None


def umsi_regime(v: float | None, fragility: float | None) -> str:
    if v is None:
        return "AWAITING DATA"
    if v < 15:
        return "EXTREME FEAR"
    if v < 30:
        return "FEAR"
    if v < 45:
        return "CAUTIOUS"
    if v < 65:
        return "NEUTRAL"
    if v < 80:
        return "RISK-ON"
    if v >= 80 and fragility is not None and fragility >= 65:
        return "RISK-ON / COMPLACENT"
    if v < 90:
        return "STRONG RISK-ON"
    return "EXTREME COMPLACENCY"


def band(v: float | None, kind: str) -> str:
    if v is None:
        return "N/A"
    if kind == "stress":
        return "CRISIS" if v >= 75 else "HIGH" if v >= 55 else "ELEVATED" if v >= 35 else "LOW"
    if kind == "fragility":
        return "HIGH" if v >= 75 else "ELEVATED" if v >= 55 else "MODERATE" if v >= 35 else "LOW"
    return "N/A"


def score_status(score: float | None) -> str:
    if score is None:
        return "Unavailable"
    if score < 15:
        return "Extreme Fear"
    if score < 30:
        return "Fear"
    if score < 45:
        return "Cautious"
    if score < 65:
        return "Neutral"
    if score < 80:
        return "Risk-On"
    if score < 90:
        return "Strong Risk-On"
    return "Complacent"


def sp_status(dd: float | None) -> str:
    if dd is None:
        return "N/A"
    if dd >= -0.03:
        return "Near 52W High"
    if dd >= -0.08:
        return "Normal Pullback"
    if dd >= -0.15:
        return "Correction"
    if dd >= -0.25:
        return "Bear Market"
    return "Deep Drawdown"


def asset_snapshot(row: pd.Series, asset: str) -> dict:
    prefix = asset.lower()
    price = n(row.get(asset), 2)
    ma20 = n(row.get(f"{prefix}_ma20"), 2)
    ma50 = n(row.get(f"{prefix}_ma50"), 2)
    ma200 = n(row.get(f"{prefix}_ma200"), 2)
    slope = row.get(f"{prefix}_ma200_slope")
    relative = row.get(f"{prefix}_relative_63d")
    volatility = row.get(f"{prefix}_vol20")
    return {
        "symbol": asset.upper(),
        "price": price,
        "source_date": clean_date(row.get(f"{prefix}_source_date")),
        "stale": is_stale(clean_date(row.get(f"{prefix}_source_date")), 4),
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "above_ma20": price is not None and ma20 is not None and price > ma20,
        "above_ma50": price is not None and ma50 is not None and price > ma50,
        "above_ma200": price is not None and ma200 is not None and price > ma200,
        "ma200_slope_20d": n(slope * 100 if pd.notna(slope) else None, 2),
        "relative_strength_63d": n(relative * 100 if pd.notna(relative) else None, 2),
        "realized_volatility_20d": n(volatility * 100 if pd.notna(volatility) else None, 1),
        "trend_score": n(row.get(f"{prefix}_trend_score"), 1),
        "recent_reclaim": bool(
            row.get(f"{prefix}_reclaim20_5d", False)
            or row.get(f"{prefix}_reclaim50_5d", False)
        ),
    }


def decision_reasons(row: pd.Series) -> list[str]:
    state = str(row.get("decision_state", "HOLD_NO_CHASE"))
    trend = n(row.get("decision_trend_score"), 1)
    core = n(row.get("umsi_core"), 1)
    stress = n(row.get("stress"), 1)
    fragility = n(row.get("fragility"), 1)
    breadth = n(row.get("breadth_score"), 1)
    reasons = [f"Combined QQQ/semiconductor trend score is {trend:.1f}" if trend is not None else "Trend score unavailable"]
    if core is not None:
        reasons.append(f"UMSI Core is {core:.1f}")
    if stress is not None:
        reasons.append(f"Stress is {stress:.1f}")
    if fragility is not None and fragility >= 55:
        reasons.append(f"Fragility is elevated at {fragility:.1f}")
    if breadth is not None and breadth < 50:
        reasons.append(f"Breadth is soft at {breadth:.1f}")
    if state == "ADD_WATCH":
        reasons.append("Fear/stress is an opportunity watch, not a bottom confirmation")
    elif state == "ADD_CONFIRMED":
        reasons.append("Trend, breadth and stress filters confirm the recovery setup")
    elif state == "REDUCE_WATCH":
        reasons.append("Risk is deteriorating; require confirmation before changing exposure")
    elif state == "DEFENSIVE":
        reasons.append("The unleveraged semiconductor benchmark has a weak long-term trend")
    else:
        reasons.append("Trend remains constructive, but no fresh confirmation supports chasing")
    return reasons


def simulate_research_model(frame: pd.DataFrame, start: str | None = None, cost_bps: float = 10.0) -> dict:
    sample = frame.copy()
    if start:
        sample = sample.loc[sample.index >= pd.Timestamp(start)].copy()
    benchmark_col = "soxx" if sample.get("soxx", pd.Series(dtype=float)).notna().any() else "smh"
    sample = sample.dropna(subset=[benchmark_col, "research_exposure"])
    if len(sample) < 252:
        return {"status": "insufficient_data", "observations": int(len(sample))}
    returns = sample[benchmark_col].pct_change(fill_method=None).fillna(0.0)
    exposure = (sample["research_exposure"] / 100.0).shift(1).fillna(0.0)
    turnover = exposure.diff().abs().fillna(exposure.abs())
    net = exposure * returns - turnover * (cost_bps / 10000.0)

    def metrics(series: pd.Series) -> dict:
        wealth = (1.0 + series).cumprod()
        years = max(len(series) / 252.0, 1.0 / 252.0)
        cagr = wealth.iloc[-1] ** (1.0 / years) - 1.0
        volatility = series.std(ddof=1) * np.sqrt(252)
        drawdown = wealth / wealth.cummax() - 1.0
        return {
            "cagr": n(cagr * 100, 2),
            "annualized_volatility": n(volatility * 100, 2),
            "max_drawdown": n(drawdown.min() * 100, 2),
            "total_return": n((wealth.iloc[-1] - 1.0) * 100, 2),
        }

    return {
        "status": "ok",
        "benchmark": benchmark_col.upper(),
        "observations": int(len(sample)),
        "cost_assumption_bps_per_full_turn": cost_bps,
        "signal_applied_next_session": True,
        "annual_turnover": n(turnover.sum() / (len(sample) / 252.0), 2),
        "model": metrics(net),
        "buy_and_hold": metrics(returns),
    }


def nearest_event_row(df: pd.DataFrame, date: str) -> pd.Series | None:
    target = pd.Timestamp(date)
    idx = df.index.searchsorted(target)
    if idx >= len(df):
        return None
    row = df.iloc[idx]
    if (row.name - target).days > 5:
        return None
    return row


def forward_return_from_index(df: pd.DataFrame, pos: int, days: int) -> float | None:
    if pos + days >= len(df):
        return None
    a = df.iloc[pos]["sp500"]
    b = df.iloc[pos + days]["sp500"]
    if pd.isna(a) or pd.isna(b) or a == 0:
        return None
    return (b / a - 1) * 100


def main() -> None:
    csv_path = DATA_DIR / "history.csv"
    if not csv_path.exists():
        raise RuntimeError("data/history.csv not found. Run build_history.py first.")

    df = pd.read_csv(csv_path, parse_dates=["date"]).set_index("date").sort_index()
    valid = df[df["umsi"].notna()]
    if valid.empty:
        raise RuntimeError("No valid UMSI rows were produced")

    last = valid.iloc[-1]
    last_date = valid.index[-1].date().isoformat()
    previous = valid.iloc[-2] if len(valid) > 1 else last

    indicator_defs = [
        ("term", "VIX / VIX3M Term Structure", "term_ratio", "term_pct", "term_score",
         WEIGHTS["term"], "term_source_date", 4),
        ("credit", "High Yield Credit Spread / OAS", "hy_oas", "credit_pct", "credit_score",
         WEIGHTS["credit"], "hy_oas_source_date", 4),
        ("breadth", "Market Breadth (RSP/SPY Proxy)", "breadth_raw", "breadth_pct", "breadth_score",
         WEIGHTS["breadth"], None, 4),
        ("vix", "VIX Level", "vix", "vix_pct", "vix_score",
         WEIGHTS["vix"], "vix_source_date", 4),
        ("put_call", "Equity Put/Call Ratio", "put_call", "put_call_pct", "put_call_score",
         WEIGHTS["put_call"], "put_call_source_date", 5),
        ("drawdown", "S&P 500 52-Week Drawdown", "drawdown_52w", "drawdown_pct", "drawdown_score",
         WEIGHTS["drawdown"], None, 4),
        ("aaii", "AAII Bull-Bear Spread", "aaii_spread", "aaii_pct", "aaii_score",
         WEIGHTS["aaii"], "aaii_source_date", 10),
    ]

    indicators = {}
    available_target_weight = sum(
        weight
        for _, _, _, _, score_col, weight, _, _ in indicator_defs
        if n(last.get(score_col), 6) is not None
    )

    for key, label, raw_col, pct_col, score_col, weight, source_col, stale_days in indicator_defs:
        raw = n(last.get(raw_col), 4)
        pct = n(last.get(pct_col), 1)
        score = n(last.get(score_col), 1)
        source_date = clean_date(last.get(source_col)) if source_col else last_date
        effective_weight = (
            weight / available_target_weight
            if score is not None and available_target_weight > 0
            else None
        )
        contribution = n(score * effective_weight, 2) if score is not None and effective_weight else None

        indicators[key] = {
            "label": label,
            "raw_value": raw,
            "percentile": pct,
            "score": score,
            "weight": weight,
            "effective_weight": n(effective_weight, 4),
            "contribution": contribution,
            "status": score_status(score),
            "source_date": source_date,
            "updated_at": now_iso(),
            "stale": is_stale(source_date, stale_days),
        }

    umsi = n(last["umsi_core"], 1)
    umsi_live = n(last.get("umsi_live"), 1)
    stress = n(last["stress"], 1)
    stress_live = n(last.get("stress_live"), 1)
    fragility = n(last["fragility"], 1)
    fragility_live = n(last.get("fragility_live"), 1)
    spx = n(last["sp500"], 2)
    prev_spx = n(previous["sp500"], 2)
    chg = ((spx / prev_spx) - 1) * 100 if spx is not None and prev_spx else None
    dd = n(last["drawdown_52w"] * 100, 2)
    state_key = str(last.get("decision_state", "HOLD_NO_CHASE"))
    state_meta = STATE_META.get(state_key, STATE_META["HOLD_NO_CHASE"])
    trend_assets = {
        asset: asset_snapshot(last, asset)
        for asset in ("qqq", "soxx", "smh")
        if n(last.get(asset), 2) is not None
    }

    daily = {
        "generated_at": now_iso(),
        "status": "ok",
        "model_version": MODEL_VERSION,
        "umsi": {
            "value": umsi,
            "core_value": umsi,
            "live_value": umsi_live,
            "regime": umsi_regime(umsi, fragility),
            "core_quality": n(last.get("core_quality"), 2),
            "live_quality": n(last["calculation_quality"], 2),
            "calculation_quality": n(last.get("core_quality"), 2),
        },
        "stress": {
            "value": stress,
            "core_value": stress,
            "live_value": stress_live,
            "status": band(stress, "stress"),
            "quality": n(last.get("stress_core_quality"), 2),
            "live_quality": n(last["stress_quality"], 2),
        },
        "fragility": {
            "value": fragility,
            "core_value": fragility,
            "live_value": fragility_live,
            "status": band(fragility, "fragility"),
            "quality": n(last.get("fragility_core_quality"), 2),
            "live_quality": n(last["fragility_quality"], 2),
        },
        "market": {
            "sp500": {
                "value": spx,
                "change_pct": n(chg, 2),
                "drawdown_52w": dd,
                "status": sp_status((dd / 100) if dd is not None else None),
                "source_date": last_date,
                "updated_at": now_iso(),
                "stale": is_stale(last_date, 4),
            },
            "trend_assets": trend_assets,
        },
        "decision": {
            "state": state_key,
            "label": state_meta.label,
            "trend_score": n(last.get("decision_trend_score"), 1),
            "trend_quality": n(last.get("decision_trend_quality"), 2),
            "research_exposure_score": state_meta.research_exposure,
            "reasons": decision_reasons(last),
            "confirmation_rule": "Signals are calculated at the close and apply no earlier than the next session.",
            "notice": "Research and simulation only; not an instruction to trade.",
        },
        "indicators": indicators,
        "sources": [
            {
                "name": "FRED",
                "url": "https://fred.stlouisfed.org/",
                "use": "VIX, VIX3M, ICE BofA High Yield OAS",
            },
            {
                "name": "Cboe",
                "url": "https://www.cboe.com/us/options/market_statistics/daily/",
                "use": "Equity Put/Call Ratio; official 2006–2019 reference archive + recent daily bootstrap",
            },
            {
                "name": "AAII",
                "url": "https://www.aaii.com/sentimentsurvey/sent_results",
                "use": "Weekly Bull-Bear Sentiment",
            },
            {
                "name": "Stooq",
                "url": "https://stooq.com/",
                "use": "Fallback long-run S&P 500 / SPY / RSP price history",
            },
            {
                "name": "Yahoo Finance",
                "url": "https://finance.yahoo.com/",
                "use": "Primary price history, QQQ/SOXX/SMH trend inputs and intraday market snapshot",
            },
        ],
        "methodology_note": (
            "Dense daily indicators use 5-year rolling percentiles. Sparse Cboe put/call and "
            "AAII series use percentiles over the last N actual observations, not forward-filled "
            "calendar rows. Delayed FRED observations may be carried forward for up to three market "
            "rows while retaining the true source_date. calculation_quality reports the target model "
            "weight available before re-normalization. UMSI Core uses fixed long-history factors for "
            "comparability; UMSI Live adds AAII and put/call when available. Decision states use "
            "unleveraged benchmarks and apply at the next session only."
        ),
    }
    write_json(DATA_DIR / "daily.json", daily)

    series = []
    for date, row in valid.iterrows():
        series.append({
            "date": date.date().isoformat(),
            "umsi": n(row["umsi"], 1),
            "umsi_core": n(row.get("umsi_core"), 1),
            "umsi_live": n(row.get("umsi_live"), 1),
            "stress": n(row["stress"], 1),
            "stress_live": n(row.get("stress_live"), 1),
            "fragility": n(row["fragility"], 1),
            "fragility_live": n(row.get("fragility_live"), 1),
            "sp500": n(row["sp500"], 2),
            "quality": n(row.get("core_quality"), 2),
            "live_quality": n(row["calculation_quality"], 2),
            "decision_state": row.get("decision_state"),
            "decision_trend_score": n(row.get("decision_trend_score"), 1),
            "research_exposure": n(row.get("research_exposure"), 0),
            "scores": {
                "term": n(row.get("term_score"), 1),
                "credit": n(row.get("credit_score"), 1),
                "breadth": n(row.get("breadth_score"), 1),
                "vix": n(row.get("vix_score"), 1),
                "put_call": n(row.get("put_call_score"), 1),
                "drawdown": n(row.get("drawdown_score"), 1),
                "aaii": n(row.get("aaii_score"), 1),
            },
        })

    events = []
    for event_date, name in EVENTS:
        row = nearest_event_row(valid, event_date)
        if row is None:
            continue
        pos = valid.index.get_loc(row.name)
        events.append({
            "date": row.name.date().isoformat(),
            "event": name,
            "umsi": n(row["umsi"], 1),
            "stress": n(row["stress"], 1),
            "fragility": n(row["fragility"], 1),
            "return_1m": n(forward_return_from_index(valid, pos, 21), 1),
            "return_3m": n(forward_return_from_index(valid, pos, 63), 1),
            "return_6m": n(forward_return_from_index(valid, pos, 126), 1),
        })

    tmp = valid.copy()
    for label, days in [("1m", 21), ("3m", 63), ("6m", 126), ("12m", 252)]:
        tmp[f"fwd_{label}"] = (tmp["sp500"].shift(-days) / tmp["sp500"] - 1) * 100

    def bin_id(v):
        if pd.isna(v):
            return None
        for i, (low, high) in enumerate(BINS):
            if low <= v < high:
                return i
        return None

    tmp["bin"] = tmp["umsi"].map(bin_id)
    tmp["confirmed_entry"] = False
    confirmed_positions = confirmed_zone_entries(tmp["bin"], minimum_days=5)
    if confirmed_positions:
        tmp.iloc[confirmed_positions, tmp.columns.get_loc("confirmed_entry")] = True

    def zone_stats(sample, col):
        s = sample[col].dropna()
        if s.empty:
            return {"mean": None, "median": None, "std": None, "hit": None, "worst": None}
        return {
            "mean": n(s.mean(), 1),
            "median": n(s.median(), 1),
            "std": n(s.std(ddof=1), 1) if len(s) > 1 else None,
            "hit": n((s > 0).mean() * 100, 0),
            "worst": n(s.min(), 1),
        }

    forward = []
    for i, (low, high) in enumerate(BINS):
        sample = tmp.loc[tmp["confirmed_entry"] & (tmp["bin"] == i)]
        stats_by_horizon = {
            label: zone_stats(sample, f"fwd_{label}")
            for label in ("1m", "3m", "6m", "12m")
        }
        forward.append({
            "range": f"{low}–{100 if high == 101 else high}",
            "observations": int(len(sample)),
            "sampling": "confirmation_day_after_5_consecutive_sessions",
            **{
                f"{metric}_{label}" if metric != "mean" else f"return_{label}": value
                for label, values in stats_by_horizon.items()
                for metric, value in values.items()
            },
        })

    def period_forward(start: str | None, end: str | None) -> list[dict]:
        period = tmp
        if start:
            period = period.loc[period.index >= pd.Timestamp(start)]
        if end:
            period = period.loc[period.index <= pd.Timestamp(end)]
        rows = []
        for i, (low, high) in enumerate(BINS):
            sample = period.loc[period["confirmed_entry"] & (period["bin"] == i)]
            values = zone_stats(sample, "fwd_3m")
            rows.append({
                "range": f"{low}–{100 if high == 101 else high}",
                "observations": int(sample["fwd_3m"].notna().sum()),
                "return_3m": values["mean"],
                "median_3m": values["median"],
                "hit_3m": values["hit"],
                "worst_3m": values["worst"],
            })
        return rows

    validation = {
        "method": "fixed thresholds; confirmation-day sampling; no threshold fitting on test data",
        "in_sample": {"through": "2017-12-31", "forward_3m": period_forward(None, "2017-12-31")},
        "out_of_sample": {"from": "2018-01-01", "forward_3m": period_forward("2018-01-01", None)},
        "full_model_simulation": simulate_research_model(tmp),
        "out_of_sample_simulation": simulate_research_model(tmp, start="2018-01-01"),
    }

    history = {
        "generated_at": now_iso(),
        "series": series,
        "events": events,
        "forward_returns": forward,
        "validation": validation,
        "model_version": MODEL_VERSION,
    }
    write_json(DATA_DIR / "history.json", history)
    print(f"daily.json and history.json generated from {last_date}")


if __name__ == "__main__":
    main()
