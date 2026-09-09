from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
UA = "Mozilla/5.0 (compatible; UMSI-Dashboard/2.0; +https://github.com/)"

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&d1={start}&d2={end}&i=d"
YAHOO_CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
)
AAII_URL = "https://www.aaii.com/sentimentsurvey/sent_results"
CBOE_DAILY_URL = "https://www.cboe.com/us/options/market_statistics/daily/"
CBOE_EQUITY_PC_HISTORY_URL = (
    "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypc.csv"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


def clean_number(value: Any) -> float | None:
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _request_yahoo(symbol: str, params: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    errors: list[str] = []
    encoded = quote(symbol, safe="")
    for template in YAHOO_CHART_URLS:
        url = template.format(symbol=encoded)
        try:
            r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=timeout)
            r.raise_for_status()
            payload = r.json()
            result = payload.get("chart", {}).get("result")
            if result:
                return result[0]
            errors.append(f"{url}: no result ({payload.get('chart', {}).get('error')})")
        except Exception as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError(f"Yahoo failed for {symbol}: " + " | ".join(errors))


def _epoch_seconds(ts: pd.Timestamp) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return int(t.timestamp())


def fetch_fred_series(series_id: str) -> pd.Series:
    url = FRED_URL.format(series_id=series_id)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    date_col = df.columns[0]
    value_col = df.columns[-1]
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    return (
        df.dropna(subset=[date_col])
        .set_index(date_col)[value_col]
        .sort_index()
        .rename(series_id)
    )


def fetch_stooq(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    url = STOOQ_URL.format(
        symbol=quote(symbol.lower(), safe=".^"),
        start=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"),
    )
    r = requests.get(url, headers={"User-Agent": UA}, timeout=45)
    r.raise_for_status()
    if not r.text.strip() or "No data" in r.text:
        return pd.DataFrame()

    df = pd.read_csv(StringIO(r.text))
    if "Date" not in df.columns:
        return pd.DataFrame()

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.dropna(subset=["Date"]).set_index("Date").sort_index()


def fetch_yahoo_history(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    params = {
        "period1": _epoch_seconds(pd.Timestamp(start).normalize()),
        "period2": _epoch_seconds(pd.Timestamp(end).normalize() + pd.Timedelta(days=1)),
        "interval": "1d",
        "includePrePost": "false",
        "events": "div,splits",
    }
    item = _request_yahoo(symbol, params, timeout=45)

    timestamps = item.get("timestamp") or []
    indicators = item.get("indicators") or {}
    quotes = (indicators.get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    adj_blocks = indicators.get("adjclose") or []
    adjusted = adj_blocks[0].get("adjclose") if adj_blocks else None

    rows: list[tuple[pd.Timestamp, float]] = []
    for i, ts in enumerate(timestamps):
        value = None
        if adjusted and i < len(adjusted):
            value = clean_number(adjusted[i])
        if value is None and i < len(closes):
            value = clean_number(closes[i])
        if value is None:
            continue
        date = pd.to_datetime(ts, unit="s", utc=True).tz_convert(None).normalize()
        rows.append((date, value))

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["Date", "Close"]).drop_duplicates("Date", keep="last")
    return df.set_index("Date").sort_index()


def fetch_yahoo_chart(symbol: str, interval: str = "5m", range_: str = "1d") -> dict[str, Any]:
    params = {
        "interval": interval,
        "range": range_,
        "includePrePost": "false",
        "events": "div,splits",
    }
    item = _request_yahoo(symbol, params, timeout=20)
    meta = item.get("meta", {})
    timestamps = item.get("timestamp") or []
    closes = (((item.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
    pairs = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
    if not pairs:
        raise RuntimeError(f"Yahoo returned no prices for {symbol}")

    ts, last = pairs[-1]
    prev = clean_number(meta.get("chartPreviousClose") or meta.get("previousClose"))
    change_pct = ((float(last) / prev) - 1.0) * 100 if prev else None

    return {
        "value": float(last),
        "change_pct": change_pct,
        "source_date": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
        "updated_at": datetime.fromtimestamp(ts, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "stale": False,
        "source": "Yahoo Finance chart endpoint",
    }


def _parse_month_day(text: str) -> pd.Timestamp | None:
    current = pd.Timestamp.utcnow().tz_localize(None)
    clean = re.sub(r"\s+", " ", text).strip()

    direct = pd.to_datetime(clean, errors="coerce")
    if pd.notna(direct) and re.search(r"\b20\d{2}\b|\b19\d{2}\b", clean):
        return pd.Timestamp(direct).normalize()

    try:
        dt = pd.to_datetime(f"{clean} {current.year}", errors="raise")
        if dt > current + pd.Timedelta(days=30):
            dt = dt.replace(year=current.year - 1)
        return pd.Timestamp(dt).normalize()
    except Exception:
        return None


def _aaii_rows_from_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []

    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < 4:
            continue

        dt = _parse_month_day(cells[0])
        if dt is None:
            continue

        def pct(v: Any) -> float | None:
            return clean_number(str(v).replace("%", "").replace(",", "").strip())

        bull = pct(cells[1])
        bear = pct(cells[3])
        if bull is None or bear is None:
            continue

        out.append(
            {
                "date": dt.date().isoformat(),
                "bullish": round(bull, 3),
                "bearish": round(bear, 3),
                "spread": round(bull - bear, 3),
                "updated_at": now_iso(),
                "source": AAII_URL,
            }
        )

    return out


def fetch_aaii_recent() -> list[dict[str, Any]]:
    r = requests.get(AAII_URL, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()

    out: list[dict[str, Any]] = []
    try:
        tables = pd.read_html(StringIO(r.text))
    except Exception:
        tables = []

    for table in tables:
        if isinstance(table.columns, pd.MultiIndex):
            cols = [
                " ".join(str(x) for x in c if str(x).lower() != "nan").strip()
                for c in table.columns
            ]
        else:
            cols = [str(c).strip() for c in table.columns]

        lowered = [c.lower() for c in cols]
        if not (
            any("bullish" in c for c in lowered)
            and any("bearish" in c for c in lowered)
        ):
            continue

        target = table.copy()
        target.columns = cols

        try:
            date_col = next(c for c in target.columns if "date" in c.lower())
            bull_col = next(c for c in target.columns if "bullish" in c.lower())
            bear_col = next(c for c in target.columns if "bearish" in c.lower())
        except StopIteration:
            continue

        for _, row in target.iterrows():
            dt = _parse_month_day(str(row[date_col]).strip())
            if dt is None:
                continue

            def pct(v: Any) -> float | None:
                return clean_number(str(v).replace("%", "").replace(",", "").strip())

            bull = pct(row[bull_col])
            bear = pct(row[bear_col])
            if bull is None or bear is None:
                continue

            out.append(
                {
                    "date": dt.date().isoformat(),
                    "bullish": round(bull, 3),
                    "bearish": round(bear, 3),
                    "spread": round(bull - bear, 3),
                    "updated_at": now_iso(),
                    "source": AAII_URL,
                }
            )
        if out:
            break

    if not out:
        out = _aaii_rows_from_html(r.text)

    if not out:
        raise RuntimeError("AAII sentiment rows could not be parsed")

    return out


def _extract_cboe_equity_ratio(text: str) -> float | None:
    match = re.search(
        r"EQUITY\s+PUT/CALL\s+RATIO.{0,500}?([0-9]+\.[0-9]+)",
        text,
        flags=re.I | re.S,
    )
    value = clean_number(match.group(1)) if match else None
    if value is not None:
        return value

    try:
        tables = pd.read_html(StringIO(text))
    except Exception:
        tables = []

    for table in tables:
        blob = " ".join(map(str, table.astype(str).values.flatten()))
        if "EQUITY PUT/CALL RATIO" not in blob.upper():
            continue
        for _, row in table.iterrows():
            row_text = " ".join(map(str, row.values))
            if "EQUITY PUT/CALL RATIO" not in row_text.upper():
                continue
            nums = re.findall(r"\b\d+\.\d+\b", row_text)
            if nums:
                return float(nums[-1])
    return None


def _extract_cboe_report_date(text: str, fallback: str | None = None) -> str | None:
    """Parse a report date from Cboe daily market statistics HTML/text."""
    patterns = [
        r"Market Statistics for\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        r"as of\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        r"as of\s+(\d{1,2}/\d{1,2}/\d{4})",
        r"as of\s+(\d{4}-\d{2}-\d{2})",
        r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b([A-Za-z]+\s+\d{1,2},\s+\d{4})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        dt = pd.to_datetime(match.group(1), errors="coerce")
        if pd.notna(dt):
            return pd.Timestamp(dt).date().isoformat()
    return fallback


def fetch_cboe_equity_put_call(date: str | None = None) -> dict[str, Any]:
    params = {"dt": date} if date else None
    r = requests.get(
        CBOE_DAILY_URL,
        params=params,
        headers={"User-Agent": UA},
        timeout=30,
    )
    r.raise_for_status()
    value = _extract_cboe_equity_ratio(r.text)
    if value is None:
        raise RuntimeError(f"Cboe equity put/call ratio not found for {date or 'latest'}")

    source_date = date or _extract_cboe_report_date(r.text)
    if not source_date:
        raise RuntimeError("Cboe equity put/call ratio found but report date could not be parsed")
    return {
        "date": source_date,
        "value": round(value, 4),
        "updated_at": now_iso(),
        "source": CBOE_DAILY_URL,
    }


def fetch_cboe_equity_put_call_history() -> list[dict[str, Any]]:
    """Official Cboe Equity put/call reference history (2006-2019 archive)."""
    r = requests.get(
        CBOE_EQUITY_PC_HISTORY_URL,
        headers={"User-Agent": UA},
        timeout=45,
    )
    r.raise_for_status()

    # CDN CSV starts with a disclaimer line, then a PRODUCT metadata row,
    # then the real header: DATE,CALL,PUT,TOTAL,P/C Ratio
    lines = r.text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("DATE,"):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError("Cboe equity put/call history missing DATE header row")

    csv_text = "\n".join(lines[header_idx:])
    df = pd.read_csv(StringIO(csv_text))
    if df.empty:
        raise RuntimeError("Cboe equity put/call history is empty")

    cols = [str(c).strip() for c in df.columns]
    lower = {c: c.lower() for c in cols}
    df.columns = cols

    date_candidates = [c for c in cols if lower[c] == "date"]
    if not date_candidates:
        raise RuntimeError(f"Cboe archive missing required date column; columns={cols}")
    date_col = date_candidates[0]

    ratio_candidates = [
        c for c in cols
        if (
            "p/c" in lower[c]
            or "put/call" in lower[c]
            or ("put" in lower[c] and "call" in lower[c] and "ratio" in lower[c])
            or lower[c] in {"p/c ratio", "pc ratio", "put call ratio"}
        )
    ]
    if not ratio_candidates:
        raise RuntimeError(f"Cboe archive missing required P/C ratio column; columns={cols}")
    ratio_col = ratio_candidates[-1]

    dates = pd.to_datetime(df[date_col], errors="coerce")
    ratios = pd.to_numeric(df[ratio_col], errors="coerce")

    out: list[dict[str, Any]] = []
    for dt, value in zip(dates, ratios):
        if pd.isna(dt) or pd.isna(value):
            continue
        if not (0.05 <= float(value) <= 5.0):
            continue
        out.append(
            {
                "date": pd.Timestamp(dt).date().isoformat(),
                "value": round(float(value), 4),
                "updated_at": now_iso(),
                "source": CBOE_EQUITY_PC_HISTORY_URL,
                "reference_history": True,
            }
        )

    if len(out) < 100:
        raise RuntimeError(
            f"Cboe archive parser found too few observations ({len(out)}); columns={cols}"
        )
    return out


def merge_records(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
    key: str = "date",
) -> list[dict[str, Any]]:
    merged = {str(x.get(key)): x for x in existing if x.get(key)}
    for item in new:
        if item.get(key):
            merged[str(item[key])] = item
    return [merged[k] for k in sorted(merged)]


def is_stale(date_str: str | None, max_days: int) -> bool:
    if not date_str:
        return True
    try:
        d = pd.Timestamp(date_str).tz_localize(None).normalize()
        now = pd.Timestamp.utcnow().tz_localize(None).normalize()
        return (now - d).days > max_days
    except Exception:
        return True
