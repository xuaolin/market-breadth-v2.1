from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_DIR, fetch_fred_series, fetch_yahoo_chart, now_iso, read_json, write_json


def fallback_fred(series_id: str, old: dict) -> dict:
    try:
        s = fetch_fred_series(series_id).dropna()
        if s.empty:
            raise RuntimeError("empty FRED series")
        date = s.index[-1]
        value = float(s.iloc[-1])
        prev = float(s.iloc[-2]) if len(s) > 1 else value
        return {
            "value": round(value, 3),
            "change_pct": round((value / prev - 1) * 100, 2) if prev else None,
            "source_date": date.date().isoformat(),
            "updated_at": now_iso(),
            "stale": True,
            "source": f"FRED {series_id} daily fallback",
        }
    except Exception:
        old = dict(old or {})
        old["stale"] = True
        return old


def main() -> None:
    path = DATA_DIR / "intraday.json"
    old = read_json(path, {})
    result = {"generated_at": now_iso(), "status": "ok"}

    mapping = {
        "sp500": ("^GSPC", None),
        "vix": ("^VIX", "VIXCLS"),
        "vix3m": ("^VIX3M", "VXVCLS"),
    }
    for key, (symbol, fred) in mapping.items():
        try:
            item = fetch_yahoo_chart(symbol, interval="5m", range_="1d")
            item["value"] = round(item["value"], 3)
            item["change_pct"] = round(item["change_pct"], 2) if item.get("change_pct") is not None else None
            result[key] = item
        except Exception as e:
            print(f"WARN intraday {symbol} failed: {e}")
            if fred:
                result[key] = fallback_fred(fred, old.get(key, {}))
            else:
                item = dict(old.get(key, {}))
                item["stale"] = True
                result[key] = item

    write_json(path, result)
    print("intraday.json updated")


if __name__ == "__main__":
    main()
