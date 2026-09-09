from __future__ import annotations

import sys
import time
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    DATA_DIR,
    fetch_aaii_recent,
    fetch_cboe_equity_put_call,
    fetch_cboe_equity_put_call_history,
    fetch_fred_series,
    fetch_stooq,
    fetch_yahoo_history,
    merge_records,
    now_iso,
    read_json,
    write_json,
)
from decision_engine import CORE_WEIGHTS, add_decision_columns

BASE_WEIGHTS = {
    "term_score": 0.20,
    "credit_score": 0.20,
    "breadth_score": 0.20,
    "vix_score": 0.15,
    "put_call_score": 0.10,
    "drawdown_score": 0.10,
    "aaii_score": 0.05,
}

REQUIRED_COLUMNS = [
    "vix",
    "vix3m",
    "hy_oas",
    "spy",
    "rsp",
    "sp500",
    "put_call",
    "aaii_spread",
    "qqq",
    "soxx",
    "smh",
]


def rolling_percentile(
    s: pd.Series,
    window: int = 1260,
    min_periods: int = 252,
) -> pd.Series:
    """
    Rolling percentile of the CURRENT observation.

    Important: if today's raw observation is missing, the score is also missing.
    This prevents a prior day's score from silently appearing as today's score.
    """
    def pct(a: np.ndarray) -> float:
        if len(a) == 0 or not np.isfinite(a[-1]):
            return np.nan
        current = a[-1]
        valid = a[np.isfinite(a)]
        if len(valid) < min_periods:
            return np.nan
        return float(np.mean(valid <= current) * 100.0)

    return s.rolling(window=window, min_periods=1).apply(pct, raw=True)


def observation_percentile(
    s: pd.Series,
    window_observations: int = 1260,
    min_periods: int = 20,
) -> pd.Series:
    """
    Percentile based on the last N *actual observations*, not calendar rows.

    This is used for sparse series such as AAII and Cboe put/call, so gaps do not
    incorrectly discard older valid observations from the reference distribution.
    """
    out = pd.Series(np.nan, index=s.index, dtype=float)
    history: deque[float] = deque(maxlen=window_observations)

    for idx, value in s.items():
        if pd.isna(value) or not np.isfinite(float(value)):
            continue
        v = float(value)
        history.append(v)
        if len(history) >= min_periods:
            arr = np.asarray(history, dtype=float)
            out.loc[idx] = float(np.mean(arr <= v) * 100.0)
    return out


def weighted_composite(
    df: pd.DataFrame,
    weights: dict[str, float],
) -> tuple[pd.Series, pd.Series]:
    numerator = pd.Series(0.0, index=df.index, dtype=float)
    denominator = pd.Series(0.0, index=df.index, dtype=float)

    for c, w in weights.items():
        if c not in df.columns:
            continue
        mask = df[c].notna()
        numerator = numerator.add(df[c].fillna(0) * w, fill_value=0)
        denominator = denominator.add(mask.astype(float) * w, fill_value=0)

    score = numerator.div(denominator.where(denominator > 0))
    return score, denominator


def ensure_columns(df: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    for name in names:
        if name not in df.columns:
            df[name] = np.nan
    return df


def carry_forward_with_source(
    df: pd.DataFrame,
    col: str,
    limit: int,
) -> None:
    """
    Carry a delayed observation forward a small number of market rows while
    preserving the date of the actual source observation.
    """
    raw = pd.to_numeric(df[col], errors="coerce")
    source = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    mask = raw.notna()
    source.loc[mask] = df.index[mask]

    df[col] = raw.ffill(limit=limit)
    source = source.ffill(limit=limit)
    df[f"{col}_source_date"] = source.dt.strftime("%Y-%m-%d")


def update_reference_sources() -> dict:
    path = DATA_DIR / "source_history.json"
    src = read_json(
        path,
        {
            "put_call": [],
            "aaii": [],
            "put_call_reference_bootstrapped": False,
            "put_call_recent_bootstrapped": False,
        },
    )
    src.setdefault("put_call", [])
    src.setdefault("aaii", [])
    src.setdefault("put_call_reference_bootstrapped", False)
    src.setdefault("put_call_recent_bootstrapped", False)

    if not src["put_call_reference_bootstrapped"]:
        reference = fetch_cboe_equity_put_call_history()
        src["put_call"] = merge_records(src["put_call"], reference)
        src["put_call_reference_bootstrapped"] = True
        print(f"Cboe historical put/call reference loaded: {len(reference):,} rows")

    try:
        recent = fetch_aaii_recent()
        src["aaii"] = merge_records(src["aaii"], recent)
        print(f"AAII updated: {len(recent)} recent rows")
    except Exception as e:
        print(f"WARN AAII fetch failed; keeping old values: {e}")

    src["updated_at"] = now_iso()
    write_json(path, src)
    return src


def add_price_series(
    series: dict[str, pd.Series],
    name: str,
    yahoo_symbol: str,
    stooq_symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> None:
    try:
        y = fetch_yahoo_history(yahoo_symbol, start, end)
        if not y.empty and "Close" in y:
            series[name] = y["Close"].rename(name)
            print(f"Yahoo {yahoo_symbol}: {len(y):,} rows")
            return
        print(f"WARN Yahoo {yahoo_symbol} returned no daily rows")
    except Exception as e:
        print(f"WARN Yahoo {yahoo_symbol} failed: {e}")

    try:
        s = fetch_stooq(stooq_symbol, start, end)
        if not s.empty and "Close" in s:
            series[name] = s["Close"].rename(name)
            print(f"Stooq {stooq_symbol}: {len(s):,} rows")
            return
        print(f"WARN Stooq {stooq_symbol} returned no daily rows")
    except Exception as e:
        print(f"WARN Stooq {stooq_symbol} failed: {e}")


def bootstrap_recent_put_call(src: dict, trading_dates: pd.DatetimeIndex) -> None:
    """
    Pull a small recent sample from Cboe on the first V2 run.
    This gives the current put/call score a modern comparison set immediately
    without making thousands of historical HTTP requests.
    """
    if src.get("put_call_recent_bootstrapped"):
        return

    dates = list(trading_dates[-30:])
    successes = 0
    for dt in dates:
        date_str = pd.Timestamp(dt).date().isoformat()
        try:
            rec = fetch_cboe_equity_put_call(date_str)
            src["put_call"] = merge_records(src["put_call"], [rec])
            successes += 1
        except Exception as e:
            print(f"WARN Cboe daily bootstrap {date_str}: {e}")
        time.sleep(0.08)

    if successes >= 20:
        src["put_call_recent_bootstrapped"] = True
        print(f"Cboe recent put/call bootstrap: {successes} rows")
    else:
        print(f"WARN Cboe recent bootstrap only obtained {successes} rows; will retry next run")


def add_latest_put_call(src: dict, latest_market_date: str) -> None:
    try:
        rec = fetch_cboe_equity_put_call(latest_market_date)
        src["put_call"] = merge_records(src["put_call"], [rec])
        print(f"Cboe put/call {latest_market_date}: {rec['value']}")
    except Exception as e:
        print(f"WARN Cboe put/call {latest_market_date} failed; keeping old values: {e}")


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    src_path = DATA_DIR / "source_history.json"
    src = update_reference_sources()

    csv_path = DATA_DIR / "history.csv"
    if csv_path.exists():
        old = (
            pd.read_csv(csv_path, parse_dates=["date"])
            .set_index("date")
            .sort_index()
        )
        start = old.index.max() - pd.Timedelta(days=90)
    else:
        old = pd.DataFrame()
        start = pd.Timestamp("2007-12-04")

    end = pd.Timestamp.utcnow().tz_localize(None).normalize()
    series: dict[str, pd.Series] = {}

    for fred_id, name in [
        ("VIXCLS", "vix"),
        ("VXVCLS", "vix3m"),
        ("BAMLH0A0HYM2", "hy_oas"),
    ]:
        try:
            s = fetch_fred_series(fred_id)
            s = s.loc[(s.index >= start) & (s.index <= end)]
            if not s.empty:
                series[name] = s.rename(name)
                print(f"FRED {fred_id}: {len(s):,} rows")
            else:
                print(f"WARN FRED {fred_id} returned no rows in requested window")
        except Exception as e:
            print(f"WARN FRED {fred_id} failed: {e}")

    # Repair an older incremental history that may have started a FRED column
    # only recently. A stable UMSI Core must not silently omit credit history.
    fred_full_start = pd.Timestamp("2007-12-04")
    for fred_id, name in [
        ("VIXCLS", "vix"),
        ("VXVCLS", "vix3m"),
        ("BAMLH0A0HYM2", "hy_oas"),
    ]:
        fresh_count = int(series.get(name, pd.Series(dtype=float)).notna().sum())
        old_count = int(old[name].notna().sum()) if (not old.empty and name in old.columns) else 0
        if fresh_count + old_count < 700:
            try:
                full = fetch_fred_series(fred_id).loc[fred_full_start:end]
                if not full.empty:
                    series[name] = full.rename(name)
                    print(f"FRED {fred_id} FULL repair: {len(full):,} rows")
            except Exception as e:
                print(f"WARN FRED {fred_id} full repair failed: {e}")

    add_price_series(series, "sp500", "^GSPC", "^spx", start, end)
    add_price_series(series, "spy", "SPY", "spy.us", start, end)
    add_price_series(series, "rsp", "RSP", "rsp.us", start, end)
    add_price_series(series, "qqq", "QQQ", "qqq.us", start, end)
    add_price_series(series, "soxx", "SOXX", "soxx.us", start, end)
    add_price_series(series, "smh", "SMH", "smh.us", start, end)

    # Breadth needs ~252 trading days of RSP/SPY relative history. A 90-day
    # incremental window never accumulates enough points when prior history
    # lacked those series — force a full backfill from 2007-12-04.
    full_start = pd.Timestamp("2007-12-04")
    min_breadth_points = 400
    for name, yahoo_symbol, stooq_symbol in [
        ("spy", "SPY", "spy.us"),
        ("rsp", "RSP", "rsp.us"),
    ]:
        if name in series and (not old.empty) and name in old.columns:
            estimated = int(series[name].combine_first(old[name]).notna().sum())
        elif name in series:
            estimated = int(series[name].notna().sum())
        elif (not old.empty) and name in old.columns:
            estimated = int(old[name].notna().sum())
        else:
            estimated = 0

        if estimated < min_breadth_points:
            print(
                f"WARN {name} has only ~{estimated} non-null points after windowed fetch; "
                f"forcing FULL backfill from {full_start.date()}"
            )
            add_price_series(series, name, yahoo_symbol, stooq_symbol, full_start, end)

    # Decision trends require at least a full 200-session moving-average base.
    for name, yahoo_symbol, stooq_symbol in [
        ("qqq", "QQQ", "qqq.us"),
        ("soxx", "SOXX", "soxx.us"),
        ("smh", "SMH", "smh.us"),
    ]:
        if name in series and (not old.empty) and name in old.columns:
            estimated = int(series[name].combine_first(old[name]).notna().sum())
        elif name in series:
            estimated = int(series[name].notna().sum())
        elif (not old.empty) and name in old.columns:
            estimated = int(old[name].notna().sum())
        else:
            estimated = 0
        if estimated < 300:
            print(
                f"WARN {name} has only ~{estimated} non-null points; "
                f"forcing FULL trend backfill from {full_start.date()}"
            )
            add_price_series(series, name, yahoo_symbol, stooq_symbol, full_start, end)

    if not series and old.empty:
        raise RuntimeError("No historical market series could be downloaded and no prior history exists")

    if series:
        fresh = pd.concat(series.values(), axis=1)
        fresh.columns = list(series.keys())
    else:
        fresh = pd.DataFrame(index=old.index.copy())

    combined = fresh.sort_index() if old.empty else fresh.combine_first(old).sort_index()
    combined = ensure_columns(combined, REQUIRED_COLUMNS)

    if combined["sp500"].notna().sum() == 0 and combined["spy"].notna().sum() > 0:
        combined["sp500"] = combined["spy"]
        print("WARN ^GSPC unavailable; using SPY as S&P 500 price-path proxy")

    if combined["spy"].notna().sum() == 0 and combined["sp500"].notna().sum() > 0:
        combined["spy"] = combined["sp500"]
        print("WARN SPY unavailable; using S&P 500 index as temporary SPY proxy")

    if combined["sp500"].notna().sum() == 0:
        raise RuntimeError(
            "S&P 500 history is unavailable from Yahoo, Stooq, and prior saved history"
        )

    market_rows = combined.index[combined["sp500"].notna()]
    latest_market_date = pd.Timestamp(market_rows[-1]).date().isoformat()

    bootstrap_recent_put_call(src, market_rows)
    add_latest_put_call(src, latest_market_date)
    src["updated_at"] = now_iso()
    write_json(src_path, src)

    # FRED series can publish with a slight delay. Carry them forward at most
    # three market rows and preserve the real source observation date.
    for col in ["vix", "vix3m", "hy_oas"]:
        carry_forward_with_source(combined, col, limit=3)

    # Price providers can lag the S&P index row by one market session. Preserve
    # the true observation date and permit a short carry so the decision card
    # degrades transparently instead of disappearing.
    for col in ["qqq", "soxx", "smh"]:
        carry_forward_with_source(combined, col, limit=2)

    # Term structure source date is the older of its two source observations.
    vix_dates = pd.to_datetime(combined["vix_source_date"], errors="coerce")
    vix3_dates = pd.to_datetime(combined["vix3m_source_date"], errors="coerce")
    term_dates = pd.concat([vix_dates, vix3_dates], axis=1).min(axis=1)
    combined["term_source_date"] = term_dates.dt.strftime("%Y-%m-%d")

    # Cboe daily put/call: score on actual observations, then carry only the
    # latest observation/score forward across a few market rows.
    pc_obs = pd.Series(np.nan, index=combined.index, dtype=float)
    pc_source = pd.Series(pd.NaT, index=combined.index, dtype="datetime64[ns]")
    if src.get("put_call"):
        pc_df = pd.DataFrame(src["put_call"])
        pc_df["date"] = pd.to_datetime(pc_df["date"], errors="coerce")
        pc_df["value"] = pd.to_numeric(pc_df["value"], errors="coerce")
        # Keep archive rows in source_history.json, but do not mix the
        # 2006-2019 Cboe reference file into the live percentile window.
        if "reference_history" in pc_df.columns:
            ref = pc_df["reference_history"].fillna(False).astype(bool)
            pc_df = pc_df.loc[~ref].copy()
        cutoff = pd.Timestamp("2020-01-01")
        pc_df = pc_df.loc[pc_df["date"] >= cutoff].copy()
        pc = (
            pc_df.dropna(subset=["date", "value"])
            .drop_duplicates("date", keep="last")
            .set_index("date")["value"]
            .sort_index()
        )
        common_idx = pc.index.intersection(combined.index)
        pc_obs.loc[common_idx] = pc.loc[common_idx]
        pc_source.loc[common_idx] = common_idx
        print(f"Put/call score sample (post-2019, non-archive): {int(pc_obs.notna().sum())} obs")

    combined["put_call_observed"] = pc_obs
    combined["put_call"] = pc_obs.ffill(limit=5)
    combined["put_call_source_date"] = pc_source.ffill(limit=5).dt.strftime("%Y-%m-%d")

    pc_pct_obs = observation_percentile(pc_obs, window_observations=1260, min_periods=20)
    combined["put_call_pct"] = pc_pct_obs.ffill(limit=5)
    combined["put_call_score"] = (100.0 - pc_pct_obs).ffill(limit=5)

    # AAII is weekly. Percentile is based on weekly observations, not repeated
    # daily forward-filled values.
    aa_obs = pd.Series(np.nan, index=combined.index, dtype=float)
    aa_source = pd.Series(pd.NaT, index=combined.index, dtype="datetime64[ns]")
    if src.get("aaii"):
        aa_df = pd.DataFrame(src["aaii"])
        aa_df["date"] = pd.to_datetime(aa_df["date"], errors="coerce")
        aa_df["spread"] = pd.to_numeric(aa_df["spread"], errors="coerce")
        aa = (
            aa_df.dropna(subset=["date", "spread"])
            .drop_duplicates("date", keep="last")
            .set_index("date")["spread"]
            .sort_index()
        )
        common_idx = aa.index.intersection(combined.index)
        aa_obs.loc[common_idx] = aa.loc[common_idx]
        aa_source.loc[common_idx] = common_idx

    combined["aaii_observed"] = aa_obs
    combined["aaii_spread"] = aa_obs.ffill(limit=10)
    combined["aaii_source_date"] = aa_source.ffill(limit=10).dt.strftime("%Y-%m-%d")

    aa_pct_obs = observation_percentile(aa_obs, window_observations=260, min_periods=20)
    combined["aaii_pct"] = aa_pct_obs.ffill(limit=10)
    combined["aaii_score"] = aa_pct_obs.ffill(limit=10)

    combined["term_ratio"] = combined["vix"] / combined["vix3m"]

    if combined["rsp"].notna().any() and combined["spy"].notna().any():
        rel = combined["rsp"] / combined["spy"]
        combined["breadth_raw"] = (
            0.60 * rel.pct_change(63, fill_method=None)
            + 0.40 * rel.pct_change(252, fill_method=None)
        )
        combined["concentration_raw"] = -combined["breadth_raw"]
    else:
        combined["breadth_raw"] = np.nan
        combined["concentration_raw"] = np.nan

    combined["drawdown_52w"] = (
        combined["sp500"]
        / combined["sp500"].rolling(252, min_periods=60).max()
        - 1.0
    )
    combined["stretch_200d"] = (
        combined["sp500"]
        / combined["sp500"].rolling(200, min_periods=120).mean()
        - 1.0
    )

    combined["term_pct"] = rolling_percentile(combined["term_ratio"])
    combined["term_score"] = 100.0 - combined["term_pct"]

    combined["credit_pct"] = rolling_percentile(combined["hy_oas"])
    combined["credit_score"] = 100.0 - combined["credit_pct"]

    combined["breadth_pct"] = rolling_percentile(combined["breadth_raw"])
    combined["breadth_score"] = combined["breadth_pct"]

    combined["vix_pct"] = rolling_percentile(combined["vix"])
    combined["vix_score"] = 100.0 - combined["vix_pct"]

    combined["drawdown_pct"] = rolling_percentile(combined["drawdown_52w"])
    combined["drawdown_score"] = combined["drawdown_pct"]

    combined["umsi_live"], combined["calculation_quality"] = weighted_composite(
        combined,
        BASE_WEIGHTS,
    )
    combined["umsi_core"], combined["core_quality"] = weighted_composite(
        combined,
        CORE_WEIGHTS,
    )
    # The chart and historical evaluation use the stable long-history core.
    # Live-only factors remain visible as UMSI Live, but are not mixed into a
    # historical series that silently changes composition over time.
    combined["umsi"] = combined["umsi_core"]

    stress_components = pd.DataFrame(index=combined.index)
    stress_components["term_stress"] = 100.0 - combined["term_score"]
    stress_components["credit_stress"] = 100.0 - combined["credit_score"]
    stress_components["vix_stress"] = 100.0 - combined["vix_score"]
    stress_components["drawdown_stress"] = 100.0 - combined["drawdown_score"]

    combined["stress_live"], combined["stress_quality"] = weighted_composite(
        stress_components,
        {
            "term_stress": 0.25,
            "credit_stress": 0.25,
            "vix_stress": 0.30,
            "drawdown_stress": 0.20,
        },
    )
    combined["stress_core"], combined["stress_core_quality"] = weighted_composite(
        stress_components,
        {
            "term_stress": 0.25 / 0.75,
            "vix_stress": 0.30 / 0.75,
            "drawdown_stress": 0.20 / 0.75,
        },
    )
    combined["stress"] = combined["stress_core"]

    concentration_risk = rolling_percentile(combined["concentration_raw"])
    stretch_risk = rolling_percentile(combined["stretch_200d"])

    frag = pd.DataFrame(index=combined.index)
    frag["low_vol_complacency"] = combined["vix_score"]
    frag["curve_complacency"] = combined["term_score"]
    frag["narrow_breadth"] = 100.0 - combined["breadth_score"]
    frag["aaii_optimism"] = combined["aaii_score"]
    frag["low_put_call"] = combined["put_call_score"]
    frag["concentration"] = concentration_risk
    frag["price_stretch"] = stretch_risk

    combined["fragility_live"], combined["fragility_quality"] = weighted_composite(
        frag,
        {
            "low_vol_complacency": 0.15,
            "curve_complacency": 0.10,
            "narrow_breadth": 0.25,
            "aaii_optimism": 0.10,
            "low_put_call": 0.10,
            "concentration": 0.15,
            "price_stretch": 0.15,
        },
    )
    combined["fragility_core"], combined["fragility_core_quality"] = weighted_composite(
        frag,
        {
            "low_vol_complacency": 0.15 / 0.80,
            "curve_complacency": 0.10 / 0.80,
            "narrow_breadth": 0.25 / 0.80,
            "concentration": 0.15 / 0.80,
            "price_stretch": 0.15 / 0.80,
        },
    )
    combined["fragility"] = combined["fragility_core"]

    add_decision_columns(combined)

    combined = combined.loc[combined["sp500"].notna()].copy()
    if combined.empty:
        raise RuntimeError("History contains no valid S&P 500 observations")
    if combined["umsi"].notna().sum() == 0:
        raise RuntimeError(
            "Market history was downloaded, but no UMSI values could be calculated."
        )

    last_umsi = combined.loc[combined["umsi"].notna()].iloc[-1]
    if pd.isna(last_umsi.get("breadth_score")):
        spy_n = int(combined["spy"].notna().sum())
        rsp_n = int(combined["rsp"].notna().sum())
        raise RuntimeError(
            "Latest UMSI row has null breadth_score; "
            f"spy non-null={spy_n}, rsp non-null={rsp_n}. "
            "RSP/SPY history is insufficient for the 252-day breadth proxy."
        )

    combined.index.name = "date"
    combined.reset_index().to_csv(csv_path, index=False)

    last_valid = combined.loc[combined["umsi"].notna()].iloc[-1]
    print(
        f"History saved: {len(combined):,} rows, "
        f"{combined.index.min().date()} -> {combined.index.max().date()}, "
        f"latest calculation quality={last_valid['calculation_quality']:.0%}"
    )


if __name__ == "__main__":
    main()
