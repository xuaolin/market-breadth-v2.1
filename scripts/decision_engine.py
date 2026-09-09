from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


MODEL_VERSION = "2.0.0-research"
CORE_WEIGHTS = {
    "term_score": 0.20 / 0.65,
    "breadth_score": 0.20 / 0.65,
    "vix_score": 0.15 / 0.65,
    "drawdown_score": 0.10 / 0.65,
}


@dataclass(frozen=True)
class StateMeta:
    label: str
    rank: int
    research_exposure: int


STATE_META = {
    "DEFENSIVE": StateMeta("Defensive", 0, 20),
    "REDUCE_WATCH": StateMeta("Reduce Watch", 1, 40),
    "ADD_WATCH": StateMeta("Add Watch", 2, 50),
    "HOLD_NO_CHASE": StateMeta("Hold / No Chase", 3, 70),
    "ADD_CONFIRMED": StateMeta("Add Confirmed", 4, 100),
}


def weighted_score(frame: pd.DataFrame, weights: dict[str, float]) -> tuple[pd.Series, pd.Series]:
    numerator = pd.Series(0.0, index=frame.index, dtype=float)
    denominator = pd.Series(0.0, index=frame.index, dtype=float)
    for column, weight in weights.items():
        if column not in frame.columns:
            continue
        valid = frame[column].notna()
        numerator = numerator.add(frame[column].fillna(0.0) * weight, fill_value=0.0)
        denominator = denominator.add(valid.astype(float) * weight, fill_value=0.0)
    return numerator.div(denominator.where(denominator > 0.0)), denominator


def add_asset_trend_columns(frame: pd.DataFrame, asset: str, market: str = "spy") -> None:
    price = pd.to_numeric(frame.get(asset), errors="coerce")
    prefix = asset.lower()
    ma20 = price.rolling(20, min_periods=20).mean()
    ma50 = price.rolling(50, min_periods=50).mean()
    ma200 = price.rolling(200, min_periods=120).mean()

    frame[f"{prefix}_ma20"] = ma20
    frame[f"{prefix}_ma50"] = ma50
    frame[f"{prefix}_ma200"] = ma200
    frame[f"{prefix}_ma20_slope"] = ma20.pct_change(5, fill_method=None)
    frame[f"{prefix}_ma50_slope"] = ma50.pct_change(20, fill_method=None)
    frame[f"{prefix}_ma200_slope"] = ma200.pct_change(20, fill_method=None)
    frame[f"{prefix}_vol20"] = price.pct_change(fill_method=None).rolling(20).std() * np.sqrt(252)

    market_price = pd.to_numeric(frame.get(market), errors="coerce")
    relative = price / market_price
    frame[f"{prefix}_relative_63d"] = relative.pct_change(63, fill_method=None)

    reclaim20 = (price > ma20) & (price.shift(1) <= ma20.shift(1))
    reclaim50 = (price > ma50) & (price.shift(1) <= ma50.shift(1))
    frame[f"{prefix}_reclaim20_5d"] = reclaim20.rolling(5, min_periods=1).max().astype(bool)
    frame[f"{prefix}_reclaim50_5d"] = reclaim50.rolling(5, min_periods=1).max().astype(bool)

    components = pd.DataFrame(index=frame.index)
    components["above20"] = (price > ma20).where(price.notna() & ma20.notna()) * 15.0
    components["above50"] = (price > ma50).where(price.notna() & ma50.notna()) * 20.0
    components["above200"] = (price > ma200).where(price.notna() & ma200.notna()) * 25.0
    components["ma20_above50"] = (ma20 > ma50).where(ma20.notna() & ma50.notna()) * 15.0
    components["ma50_above200"] = (ma50 > ma200).where(ma50.notna() & ma200.notna()) * 10.0
    components["ma200_rising"] = (frame[f"{prefix}_ma200_slope"] > 0).where(
        frame[f"{prefix}_ma200_slope"].notna()
    ) * 10.0
    components["relative_positive"] = (frame[f"{prefix}_relative_63d"] > 0).where(
        frame[f"{prefix}_relative_63d"].notna()
    ) * 5.0
    frame[f"{prefix}_trend_score"] = components.sum(axis=1, min_count=5)


def add_decision_columns(frame: pd.DataFrame) -> None:
    for asset in ("qqq", "soxx", "smh"):
        if asset in frame.columns:
            add_asset_trend_columns(frame, asset)

    semi_score = frame.get("soxx_trend_score")
    if semi_score is None or semi_score.notna().sum() == 0:
        semi_score = frame.get("smh_trend_score")
    qqq_score = frame.get("qqq_trend_score")
    if qqq_score is None:
        qqq_score = pd.Series(np.nan, index=frame.index)
    if semi_score is None:
        semi_score = pd.Series(np.nan, index=frame.index)

    trend_parts = pd.DataFrame({"qqq": qqq_score, "semi": semi_score}, index=frame.index)
    trend_weights = {"qqq": 0.35, "semi": 0.65}
    frame["decision_trend_score"], frame["decision_trend_quality"] = weighted_score(
        trend_parts, trend_weights
    )

    states: list[str] = []
    for _, row in frame.iterrows():
        trend = row.get("decision_trend_score")
        core = row.get("umsi_core")
        stress = row.get("stress")
        fragility = row.get("fragility")
        breadth = row.get("breadth_score")
        semi_price = row.get("soxx") if pd.notna(row.get("soxx")) else row.get("smh")
        semi_ma200 = row.get("soxx_ma200") if pd.notna(row.get("soxx_ma200")) else row.get("smh_ma200")
        semi_slope = (
            row.get("soxx_ma200_slope")
            if pd.notna(row.get("soxx_ma200_slope"))
            else row.get("smh_ma200_slope")
        )
        reclaim = bool(row.get("soxx_reclaim20_5d", False) or row.get("soxx_reclaim50_5d", False))
        if not reclaim:
            reclaim = bool(row.get("smh_reclaim20_5d", False) or row.get("smh_reclaim50_5d", False))

        below_falling_200 = (
            pd.notna(semi_price)
            and pd.notna(semi_ma200)
            and pd.notna(semi_slope)
            and semi_price < semi_ma200
            and semi_slope <= 0
        )
        if pd.notna(trend) and (trend < 30 or below_falling_200):
            state = "DEFENSIVE"
        elif (pd.notna(core) and core < 30) or (pd.notna(stress) and stress >= 55):
            state = "ADD_WATCH"
        elif (
            pd.notna(trend)
            and trend >= 70
            and pd.notna(breadth)
            and breadth >= 45
            and pd.notna(stress)
            and stress < 55
            and (reclaim or (pd.notna(core) and core < 65))
        ):
            state = "ADD_CONFIRMED"
        elif (
            (pd.notna(trend) and trend < 50)
            or (
                pd.notna(fragility)
                and fragility >= 65
                and (pd.isna(breadth) or breadth < 50 or (pd.notna(core) and core >= 80))
            )
        ):
            state = "REDUCE_WATCH"
        else:
            state = "HOLD_NO_CHASE"
        states.append(state)

    frame["decision_state"] = states
    frame["research_exposure"] = frame["decision_state"].map(
        {key: meta.research_exposure for key, meta in STATE_META.items()}
    )


def confirmed_zone_entries(values: pd.Series, minimum_days: int = 5) -> list[int]:
    """Return confirmation-day positions; no day-one look-ahead is allowed."""
    entries: list[int] = []
    i = 0
    while i < len(values):
        current = values.iloc[i]
        if pd.isna(current):
            i += 1
            continue
        j = i + 1
        while j < len(values) and values.iloc[j] == current:
            j += 1
        if j - i >= minimum_days:
            entries.append(i + minimum_days - 1)
        i = j
    return entries
