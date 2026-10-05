import io
import json
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_DIR = ROOT / "config"
FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
FRED_START = os.environ.get("FRED_START", "1947-01-01")
USER_AGENT = "capital-over-labor-tracker/1.0 (+github actions)"

SERIES_SPECS = {
    "labor_share": {
        "id": "PRS85006173",
        "title": "Labor Share Index, Nonfarm Business",
        "source": "BLS via FRED",
        "unit": "index (2017=100)",
        "frequency": "quarterly",
        "chart": True,
    },
    "corporate_profits": {
        "id": "CP",
        "title": "Corporate Profits (BEA)",
        "source": "BEA via FRED",
        "unit": "billions USD, SAAR",
        "frequency": "quarterly",
        "chart": True,
    },
    "avg_hourly_earnings": {
        "id": "CES0500000003",
        "title": "Average Hourly Earnings, Total Private",
        "source": "BLS via FRED",
        "unit": "USD per hour",
        "frequency": "monthly",
        "chart": False,
    },
    "payrolls": {
        "id": "PAYEMS",
        "title": "All Employees, Nonfarm Payrolls",
        "source": "BLS via FRED",
        "unit": "thousands of jobs",
        "frequency": "monthly",
        "chart": False,
    },
    "avg_weekly_hours": {
        "id": "AWHAETP",
        "title": "Average Weekly Hours, Total Private",
        "source": "BLS via FRED",
        "unit": "hours",
        "frequency": "monthly",
        "chart": False,
    },
    "eci_wages": {
        "id": "ECIWAG",
        "title": "Employment Cost Index, Wages and Salaries",
        "source": "BLS via FRED",
        "unit": "index (Dec 2005=100)",
        "frequency": "quarterly",
        "chart": False,
    },
    "unemployment": {
        "id": "UNRATE",
        "title": "Unemployment Rate",
        "source": "BLS via FRED",
        "unit": "percent",
        "frequency": "monthly",
        "chart": False,
    },
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def r(value, digits=4):
    if value is None:
        return None
    return round(float(value), digits)


def pct_change(current, base):
    if current is None or base in (None, 0):
        return None
    return round((float(current) / float(base) - 1.0) * 100.0, 3)


def fetch_fred_api(series_id, start):
    params = {
        "series_id": series_id,
        "api_key": FRED_KEY,
        "file_type": "json",
        "observation_start": start,
    }
    resp = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=45,
    )
    resp.raise_for_status()
    points = []
    for obs in resp.json().get("observations", []):
        value = obs.get("value")
        if value in (None, ".", ""):
            continue
        points.append((obs["date"], float(value)))
    return points


def fetch_fred_csv(series_id):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    if df.shape[1] < 2:
        raise ValueError(f"unexpected CSV shape for {series_id}")
    df = df.rename(columns={df.columns[0]: "date", df.columns[1]: "value"})
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])
    points = [(str(d), float(v)) for d, v in zip(df["date"], df["value"])]
    points.sort(key=lambda p: p[0])
    return [p for p in points if p[0] >= "1940-01-01"]


def fetch_fred_series(series_id):
    if FRED_KEY:
        try:
            points = fetch_fred_api(series_id, FRED_START)
            if points:
                return points, "api"
        except Exception as exc:
            print(f"[fetch_data] FRED api failed for {series_id}: {exc}; falling back to CSV")
    return fetch_fred_csv(series_id), "csv"


def value_at_or_before(points, target):
    chosen = None
    for day, value in points:
        if date.fromisoformat(day) <= target:
            chosen = (day, value)
        else:
            break
    return chosen


def series_stats(points, frequency=None):
    if not points:
        return {}
    last_day, last_value = points[-1]
    last_date = date.fromisoformat(last_day)
    prev_month = value_at_or_before(points, last_date - timedelta(days=28))
    prev_year = value_at_or_before(points, last_date - timedelta(days=365))
    delta_1m = None
    if prev_month and prev_month[0] != last_day:
        delta_1m = pct_change(last_value, prev_month[1])
    delta_12m = None
    if prev_year and prev_year[0] != last_day:
        delta_12m = pct_change(last_value, prev_year[1])
    return {
        "latest": {"date": last_day, "value": r(last_value)},
        "prev_month": {"date": prev_month[0], "value": r(prev_month[1])} if prev_month else None,
        "prev_year": {"date": prev_year[0], "value": r(prev_year[1])} if prev_year else None,
        "delta_1m_pct": delta_1m,
        "delta_12m_pct": delta_12m,
        "observations": len(points),
        "frequency": frequency,
    }


def to_series(points):
    series = pd.Series({pd.Timestamp(d): float(v) for d, v in points}, dtype="float64")
    return series[~series.index.duplicated(keep="last")].sort_index()


def build_wage_bill(obs):
    payroll = to_series(obs["payrolls"])
    hourly = to_series(obs["avg_hourly_earnings"])
    hours = to_series(obs["avg_weekly_hours"])
    frame = pd.DataFrame({"payroll": payroll, "hourly": hourly, "hours": hours}).sort_index().ffill()
    frame = frame.dropna()
    frame = frame[frame.index >= "1990-01-01"]
    bill = frame["payroll"] * 1000.0 * frame["hourly"] * frame["hours"] * 52.0 / 1e9
    bill = bill.round(3)
    points = [(d.date().isoformat(), float(v)) for d, v in bill.items()]
    return points, frame


def build_profits_vs_wages(profits_points, wage_bill_points):
    profits = to_series(profits_points)
    wage_bill = to_series(wage_bill_points)
    profits = profits[profits.index >= "1990-01-01"]
    aligned_wages = wage_bill.reindex(profits.index.union(wage_bill.index)).ffill().reindex(profits.index)
    ratio = (profits / aligned_wages).dropna()
    ratio_points = [(d.date().isoformat(), float(v)) for d, v in ratio.items()]
    window_start = max(profits.index.min(), aligned_wages.index.min(), pd.Timestamp(date.today() - timedelta(days=3650)))
    cp_window = profits[profits.index >= window_start]
    wage_window = aligned_wages[aligned_wages.index >= window_start]
    if len(cp_window) < 2 or len(wage_window) < 2:
        cp_window = profits.tail(20)
        wage_window = aligned_wages.tail(20)
    cp_base = cp_window.iloc[0]
    wage_base = wage_window.iloc[0]
    rebased = {
        "dates": [d.date().isoformat() for d in cp_window.index],
        "corporate_profits": [round(float(v / cp_base * 100.0), 2) for v in cp_window],
        "wage_bill": [round(float(v / wage_base * 100.0), 2) for v in wage_window],
    }
    return ratio_points, rebased


def load_previous(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def preserve_previous(path, label, error):
    previous = load_previous(path)
    if previous:
        previous["stale"] = True
        previous["stale_reason"] = str(error)[:300]
        previous["stale_at"] = now_iso()
        path.write_text(json.dumps(previous, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[fetch_data] {label}: keeping previous data (stale): {error}")
        return
    print(f"[fetch_data] {label}: no previous data to keep: {error}")


def build_macro():
    observations = {}
    series_blocks = {}
    errors = {}
    for key, spec in SERIES_SPECS.items():
        try:
            points, via = fetch_fred_series(spec["id"])
            observations[key] = points
            block = dict(spec)
            block["via"] = via
            block["stats"] = series_stats(points, spec["frequency"])
            block["points"] = [{"date": d, "value": r(v)} for d, v in points]
            series_blocks[key] = block
            latest = block["stats"].get("latest")
            print(f"[fetch_data] {key}: {len(points)} pts, latest={latest}")
        except Exception as exc:
            errors[key] = str(exc)
            print(f"[fetch_data] {key}: FAILED ({exc})")

    if "labor_share" not in series_blocks:
        raise RuntimeError(f"labor_share series unavailable: {errors}")

    derived = {}
    try:
        if "corporate_profits" in observations and "payrolls" in observations:
            wage_points, _ = build_wage_bill(observations)
            wage_stats = series_stats(wage_points, "monthly")
            series_blocks["wage_bill_proxy"] = {
                "id": "DERIVED:PAYEMS*CES0500000003*AWHAETP*52",
                "title": "Wage Bill Proxy (payrolls x avg hourly earnings x weekly hours)",
                "source": "BLS via FRED (derived)",
                "unit": "billions USD per year",
                "frequency": "monthly",
                "chart": False,
                "stats": wage_stats,
                "points": [{"date": d, "value": r(v)} for d, v in wage_points],
                "via": "derived",
            }
            ratio_points, rebased = build_profits_vs_wages(
                observations["corporate_profits"], wage_points
            )
            ratio_stats = series_stats(ratio_points, "quarterly")
            derived["profits_vs_wages_ratio"] = {
                "title": "Corporate Profits / Wage Bill",
                "stats": ratio_stats,
                "points": [{"date": d, "value": r(v)} for d, v in ratio_points],
            }
            derived["rebased"] = rebased
            print(f"[fetch_data] derived wage bill + ratio OK, ratio={ratio_stats.get('latest')}")
    except Exception as exc:
        traceback.print_exc()
        errors["derived"] = str(exc)

    labor_stats = series_blocks["labor_share"]["stats"]
    payload = {
        "as_of": labor_stats.get("latest", {}).get("date") or date.today().isoformat(),
        "generated_at": now_iso(),
        "stale": False,
        "source": "FRED (fredgraph CSV / FRED API)",
        "fred_api_used": bool(FRED_KEY),
        "series": series_blocks,
        "derived": derived,
        "errors": errors,
        "headline": {
            "labor_share_latest": labor_stats.get("latest"),
            "labor_share_change_1m_pct": labor_stats.get("delta_1m_pct"),
            "labor_share_change_12m_pct": labor_stats.get("delta_12m_pct"),
            "profits_vs_wages_ratio_latest": derived.get("profits_vs_wages_ratio", {}).get("stats", {}).get("latest"),
            "profits_vs_wages_ratio_change_1m_pct": derived.get("profits_vs_wages_ratio", {}).get("stats", {}).get("delta_1m_pct"),
        },
    }
    return payload


def trailing_return(series, days):
    series = series.dropna()
    if len(series) < 2:
        return None
    target = series.index[-1] - timedelta(days=days)
    past = series[series.index <= target]
    if past.empty:
        past = series.iloc[:1]
    return pct_change(series.iloc[-1], past.iloc[-1])


def returns_block(series, ytd_start):
    series = series.dropna()
    if series.empty:
        return {}
    last = float(series.iloc[-1])
    return {
        "price": round(last, 2),
        "as_of": series.index[-1].date().isoformat(),
        "ret_1m": trailing_return(series, 30),
        "ret_3m": trailing_return(series, 90),
        "ret_6m": trailing_return(series, 180),
        "ret_1y": trailing_return(series, 365),
        "ret_ytd": pct_change(last, series[series.index >= ytd_start].iloc[0])
        if (series.index >= ytd_start).any()
        else None,
    }


def basket_stats(tickers_stats, horizons):
    out = {}
    for horizon in horizons:
        values = [t.get(horizon) for t in tickers_stats if t.get(horizon) is not None]
        out[horizon] = round(sum(values) / len(values), 3) if values else None
    return out


def normalized_basket(close, days=365):
    start = pd.Timestamp(date.today() - timedelta(days=days))
    window = close[close.index >= start]
    window = window.dropna(how="all").ffill()
    if window.empty or len(window) < 5:
        return {"dates": [], "values": []}
    normalized = window.apply(lambda col: col / col.dropna().iloc[0] * 100.0 if col.notna().any() else col)
    basket = normalized.mean(axis=1, skipna=True).round(2)
    return {
        "dates": [d.date().isoformat() for d in basket.index],
        "values": [None if pd.isna(v) else float(v) for v in basket],
    }


def download_close(tickers):
    raw = yf.download(
        tickers,
        period="2y",
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
        threads=True,
    )
    if raw is None or raw.empty:
        raise RuntimeError("yfinance returned no data")
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            close = raw["Close"]
        else:
            close = raw.xs("Close", axis=1, level=1)
    else:
        close = raw[["Close"]].rename(columns={"Close": tickers[0]})
    close.index = pd.DatetimeIndex(close.index)
    if close.index.tz is not None:
        close.index = close.index.tz_localize(None)
    return close


def build_markets():
    watchlist = json.loads((CONFIG_DIR / "watchlist.json").read_text(encoding="utf-8"))
    groups = {}
    all_tickers = []
    for key, block in watchlist.items():
        tickers = block.get("tickers", [])
        all_tickers.extend(tickers)
        groups[key] = {"label": block.get("label", key), "rationale": block.get("rationale", ""), "tickers": tickers}

    close = download_close(all_tickers)
    ytd_start = pd.Timestamp(date(date.today().year, 1, 1))
    horizons = ["ret_1m", "ret_3m", "ret_6m", "ret_1y", "ret_ytd"]

    out_groups = {}
    for key, block in groups.items():
        stats = []
        for ticker in block["tickers"]:
            if ticker not in close.columns:
                print(f"[fetch_data] {ticker}: missing from download")
                continue
            entry = {"ticker": ticker}
            entry.update(returns_block(close[ticker], ytd_start))
            stats.append(entry)
        out_groups[key] = {
            "label": block["label"],
            "rationale": block["rationale"],
            "count": len(stats),
            "basket_returns": basket_stats(stats, horizons),
            "holdings": stats,
        }
        print(f"[fetch_data] {key}: {len(stats)} holdings, basket 3m={out_groups[key]['basket_returns'].get('ret_3m')}%")

    spread = {}
    capital_returns = out_groups.get("capital_enablers", {}).get("basket_returns", {})
    labor_returns = out_groups.get("labor_heavy", {}).get("basket_returns", {})
    for horizon in horizons:
        c_val = capital_returns.get(horizon)
        l_val = labor_returns.get(horizon)
        spread[horizon] = round(c_val - l_val, 3) if c_val is not None and l_val is not None else None

    normalized = {"window_days": 365, "dates": []}
    series_out = {}
    for key in out_groups:
        cols = [t for t in groups[key]["tickers"] if t in close.columns]
        series_out[key] = normalized_basket(close[cols]) if cols else {"dates": [], "values": []}
    common_dates = series_out.get("capital_enablers", {}).get("dates") or next(
        (v["dates"] for v in series_out.values() if v.get("dates")), []
    )
    normalized["dates"] = common_dates
    for key, block in series_out.items():
        if block.get("dates") == common_dates:
            normalized[key] = block["values"]
        else:
            lookup = dict(zip(block.get("dates", []), block.get("values", [])))
            normalized[key] = [lookup.get(d) for d in common_dates]

    return {
        "as_of": close.index[-1].date().isoformat() if len(close.index) else date.today().isoformat(),
        "generated_at": now_iso(),
        "stale": False,
        "source": "yfinance",
        "watchlists": out_groups,
        "spread": spread,
        "normalized": {"window_days": 365, **normalized},
    }


def main():
    DATA_DIR.mkdir(exist_ok=True)
    macro_path = DATA_DIR / "macro.json"
    markets_path = DATA_DIR / "markets.json"

    macro_ok = False
    try:
        macro = build_macro()
        macro_path.write_text(json.dumps(macro, indent=2, ensure_ascii=False), encoding="utf-8")
        macro_ok = True
        print(f"[fetch_data] wrote {macro_path}")
    except Exception as exc:
        traceback.print_exc()
        preserve_previous(macro_path, "macro", exc)

    try:
        markets = build_markets()
        markets_path.write_text(json.dumps(markets, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[fetch_data] wrote {markets_path}")
    except Exception as exc:
        traceback.print_exc()
        preserve_previous(markets_path, "markets", exc)

    if not macro_ok and not macro_path.exists():
        print("[fetch_data] no macro data available at all")
        sys.exit(1)
    print("[fetch_data] done")


if __name__ == "__main__":
    main()
