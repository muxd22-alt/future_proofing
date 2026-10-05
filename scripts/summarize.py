import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "LLM_MODEL_FALLBACKS",
        "meta-llama/llama-3.3-70b-instruct:free, qwen/qwen3-30b-a3b:free, google/gemma-3-27b-it:free",
    ).split(",")
    if m.strip()
]
MODEL = (os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODEL)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_ITEMS_IN_PROMPT = 15

SYSTEM_PROMPT = (
    "You are a disciplined macro research analyst writing for a public dashboard called "
    "'Capital over Labor', which tracks the structural shift from a labor-driven to a "
    "capital-driven economy (falling labor share, rising corporate profits, AI/automation capex). "
    "You write terse, numeric, non-partisan bullets. You never give personal investment advice. "
    "You always respond with a single valid JSON object and nothing else."
)

USER_TEMPLATE = """Here is today's machine-generated data snapshot:

{snapshot}

Write the daily brief for the dashboard.

Requirements:
1. Return ONLY a valid JSON object with this exact shape:
   {{"headline": "<max 140 chars>", "bullets": [{{"text": "<max 280 chars>", "tag": "<tag>"}}, ...]}}
2. Exactly 5 bullets.
3. Allowed tags per bullet:
   - "capital-positive": evidence that capital income, corporate profits, asset/capex or automation is gaining relative to labor.
   - "labor-positive": evidence that wages, employment or labor bargaining power are strengthening.
   - "neutral": context, uncertainty or methodology notes.
4. Ground every bullet in the numbers above and cite the actual figures (percentages, index levels, dates).
5. If a figure is unavailable, say so plainly instead of inventing it.
6. No markdown, no code fences, no commentary outside the JSON.
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(name):
    path = DATA_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[summarize] could not load {name}: {exc}")
        return None


def fmt(value, digits=2, suffix=""):
    if value is None:
        return "n/a"
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}{suffix}"


def fmt_delta(value, suffix="%"):
    if value is None:
        return "n/a"
    return f"{value:+.2f}{suffix}"


def latest_block(block):
    if not block:
        return {}
    stats = block.get("stats") or block
    return stats.get("latest") or {}


def build_snapshot(macro, markets, news):
    macro = macro or {}
    markets = markets or {}
    news = news or {}
    series = macro.get("series", {})
    derived = macro.get("derived", {})

    ls = series.get("labor_share", {})
    ls_latest = latest_block(ls)
    ls_stats = ls.get("stats", {})
    cp = series.get("corporate_profits", {})
    cp_latest = latest_block(cp)
    cp_stats = cp.get("stats", {})
    wb = series.get("wage_bill_proxy", {})
    wb_latest = latest_block(wb)
    wb_stats = wb.get("stats", {})
    ur = series.get("unemployment", {})
    ur_latest = latest_block(ur)
    ur_stats = ur.get("stats", {})
    ratio = derived.get("profits_vs_wages_ratio", {})
    ratio_latest = latest_block(ratio)
    ratio_stats = ratio.get("stats", {})

    lines = []
    lines.append(f"AS OF: {macro.get('as_of', 'unknown')} (macro), markets as of {markets.get('as_of', 'unknown')}")
    if macro.get("stale"):
        lines.append(f"WARNING: macro data is STALE ({macro.get('stale_reason', 'unavailable')})")
    lines.append("")
    lines.append("[LABOR SHARE]")
    lines.append(
        f"- Labor share index ({ls.get('id', 'n/a')}, {ls.get('frequency', 'n/a')}): "
        f"{fmt(ls_latest.get('value'))} on {ls_latest.get('date', 'n/a')}; "
        f"change vs prior observation {fmt_delta(ls_stats.get('delta_1m_pct'))}; "
        f"vs prior year {fmt_delta(ls_stats.get('delta_12m_pct'))}"
    )
    lines.append("")
    lines.append("[CAPITAL INCOME vs WAGE INCOME]")
    lines.append(
        f"- Corporate profits ({cp.get('id', 'n/a')}): {fmt(cp_latest.get('value'))} bn USD SAAR on "
        f"{cp_latest.get('date', 'n/a')}; prior obs {fmt_delta(cp_stats.get('delta_1m_pct'))}; YoY {fmt_delta(cp_stats.get('delta_12m_pct'))}"
    )
    lines.append(
        f"- Wage bill proxy: {fmt(wb_latest.get('value'))} bn USD/yr on {wb_latest.get('date', 'n/a')}; "
        f"prior month {fmt_delta(wb_stats.get('delta_1m_pct'))}; YoY {fmt_delta(wb_stats.get('delta_12m_pct'))}"
    )
    lines.append(
        f"- Profits-to-wage-bill ratio: {fmt(ratio_latest.get('value'), 3)} on {ratio_latest.get('date', 'n/a')}; "
        f"prior obs {fmt_delta(ratio_stats.get('delta_1m_pct'))}; YoY {fmt_delta(ratio_stats.get('delta_12m_pct'))}"
    )
    lines.append(
        f"- Unemployment rate: {fmt(ur_latest.get('value'), 1, '%')} on {ur_latest.get('date', 'n/a')}; "
        f"1 month change {fmt_delta(ur_stats.get('delta_1m_pct'))}"
    )
    lines.append("")
    lines.append("[WATCHLISTS]")

    watchlists = markets.get("watchlists", {})
    for key in ("capital_enablers", "labor_heavy"):
        group = watchlists.get(key) or {}
        basket = group.get("basket_returns") or {}
        lines.append(
            f"- {group.get('label', key)} ({group.get('count', 0)} tickers): basket returns "
            f"1m {fmt_delta(basket.get('ret_1m'))}, 3m {fmt_delta(basket.get('ret_3m'))}, "
            f"6m {fmt_delta(basket.get('ret_6m'))}, 1y {fmt_delta(basket.get('ret_1y'))}, "
            f"YTD {fmt_delta(basket.get('ret_ytd'))}"
        )
        holdings = sorted(
            [h for h in group.get("holdings", []) if h.get("ret_3m") is not None],
            key=lambda h: h["ret_3m"],
            reverse=True,
        )
        if holdings:
            top = holdings[:3]
            bottom = holdings[-3:]
            top_str = ", ".join(f"{h['ticker']} {h['ret_3m']:+.1f}%" for h in top)
            bottom_str = ", ".join(f"{h['ticker']} {h['ret_3m']:+.1f}%" for h in bottom)
            lines.append(f"  top 3m: {top_str}")
            lines.append(f"  bottom 3m: {bottom_str}")
    spread = markets.get("spread", {}) or {}
    lines.append(
        f"- Spread (enablers minus labor-heavy): 1m {fmt_delta(spread.get('ret_1m'))}, "
        f"3m {fmt_delta(spread.get('ret_3m'))}, 1y {fmt_delta(spread.get('ret_1y'))}"
    )
    lines.append("")
    lines.append("[NEWS]")
    counts = news.get("counts", {}) or {}
    lines.append(
        "- Item counts by category (last "
        f"{news.get('max_age_days', '?')} days): "
        + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none")
        + f"; total {news.get('total', 0)}"
    )
    for item in news.get("items", [])[:MAX_ITEMS_IN_PROMPT]:
        title = item.get("title", "")
        lines.append(f"  - [{item.get('category', 'general')}] {title} ({item.get('source', '')})")

    return "\n".join(lines)


def parse_llm_json(content):
    text = content.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in LLM output")
    return json.loads(text[start : end + 1])


def normalize_tag(tag):
    tag = (tag or "").strip().lower()
    if "capital" in tag:
        return "capital-positive"
    if "labor" in tag or "labour" in tag:
        return "labor-positive"
    return "neutral"


def normalize_brief(parsed, model, status):
    headline = str(parsed.get("headline") or "").strip()[:200]
    raw_bullets = parsed.get("bullets")
    bullets = []
    if isinstance(raw_bullets, list):
        for item in raw_bullets:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            bullets.append({"text": text[:320], "tag": normalize_tag(str(item.get("tag", "neutral")))})
            if len(bullets) == 5:
                break
    if not bullets:
        raise ValueError("LLM returned no usable bullets")
    return {"headline": headline, "bullets": bullets, "model": model, "status": status}


def call_openrouter(snapshot):
    if not OPENROUTER_KEY:
        print("[summarize] OPENROUTER_API_KEY not set; using deterministic fallback brief")
        return None
    user_prompt = USER_TEMPLATE.format(snapshot=snapshot)
    models = [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]
    errors = []
    for model in models:
        for attempt in range(2):
            try:
                resp = requests.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/capital-over-labor",
                        "X-Title": "Capital over Labor Tracker",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 1100,
                    },
                    timeout=75,
                )
                if resp.status_code in (429, 500, 502, 503) and attempt == 0:
                    time.sleep(4)
                    continue
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                brief = normalize_brief(parse_llm_json(content), model, "llm")
                print(f"[summarize] LLM brief generated with {model}")
                return brief
            except Exception as exc:
                errors.append(f"{model}: {exc}")
                print(f"[summarize] model {model} failed: {exc}")
                break
    print("[summarize] all models failed; using fallback")
    for err in errors:
        print(f"[summarize]   - {err}")
    return None


def fallback_brief(macro, markets, news):
    macro = macro or {}
    markets = markets or {}
    news = news or {}
    series = macro.get("series", {})
    derived = macro.get("derived", {})

    ls = series.get("labor_share", {})
    ls_stats = ls.get("stats", {})
    ls_latest = latest_block(ls)
    ratio = derived.get("profits_vs_wages_ratio", {})
    ratio_stats = ratio.get("stats", {})
    ratio_latest = latest_block(ratio)
    ur_latest = latest_block(series.get("unemployment", {}))

    spread = markets.get("spread", {}) or {}
    watchlists = markets.get("watchlists", {}) or {}
    cap = (watchlists.get("capital_enablers") or {}).get("basket_returns") or {}
    lab = (watchlists.get("labor_heavy") or {}).get("basket_returns") or {}

    ls_delta = ls_stats.get("delta_1m_pct")
    if ls_delta is None:
        ls_tag = "neutral"
        ls_text = f"Labor share index reads {fmt(ls_latest.get('value'))} ({ls_latest.get('date', 'n/a')}); no prior observation available for a change reading."
    elif ls_delta < 0:
        ls_tag = "capital-positive"
        ls_text = (
            f"Labor share index fell to {fmt(ls_latest.get('value'))} ({fmt_delta(ls_delta)} vs prior observation, "
            f"{fmt_delta(ls_stats.get('delta_12m_pct'))} YoY) - income continues to rotate toward capital."
        )
    else:
        ls_tag = "labor-positive"
        ls_text = (
            f"Labor share index rose to {fmt(ls_latest.get('value'))} ({fmt_delta(ls_delta)} vs prior observation, "
            f"{fmt_delta(ls_stats.get('delta_12m_pct'))} YoY) - labor is regaining income share."
        )

    ratio_delta = ratio_stats.get("delta_1m_pct")
    if ratio_delta is None:
        ratio_tag = "neutral"
        ratio_text = f"Profits-to-wage-bill ratio stands at {fmt(ratio_latest.get('value'), 3)} ({ratio_latest.get('date', 'n/a')})."
    elif ratio_delta > 0:
        ratio_tag = "capital-positive"
        ratio_text = (
            f"Corporate profits divided by the wage bill rose to {fmt(ratio_latest.get('value'), 3)} "
            f"({fmt_delta(ratio_delta)} vs prior observation) - capital income is outpacing wage income."
        )
    else:
        ratio_tag = "labor-positive"
        ratio_text = (
            f"Corporate profits divided by the wage bill eased to {fmt(ratio_latest.get('value'), 3)} "
            f"({fmt_delta(ratio_delta)} vs prior observation) - wage income is catching up."
        )

    spread_3m = spread.get("ret_3m")
    if spread_3m is None:
        markets_tag = "neutral"
        markets_text = "Watchlist performance data is unavailable for this run."
    elif spread_3m >= 0:
        markets_tag = "capital-positive"
        markets_text = (
            f"Capital enablers returned {fmt_delta(cap.get('ret_3m'))} over 3 months versus {fmt_delta(lab.get('ret_3m'))} "
            f"for labor-heavy names - a {fmt_delta(spread_3m)} spread in favor of capital."
        )
    else:
        markets_tag = "labor-positive"
        markets_text = (
            f"Labor-heavy names returned {fmt_delta(lab.get('ret_3m'))} over 3 months versus {fmt_delta(cap.get('ret_3m'))} "
            f"for capital enablers - a {fmt_delta(-spread_3m)} spread in favor of labor."
        )

    counts = news.get("counts", {}) or {}
    ai_capex = counts.get("ai_capex", 0)
    labor_items = counts.get("labor", 0)
    news_tag = "capital-positive" if ai_capex > labor_items else "labor-positive" if labor_items > ai_capex else "neutral"
    news_text = (
        f"News flow over the last {news.get('max_age_days', 7)} days: {ai_capex} AI-capex stories versus "
        f"{labor_items} labor-market stories across {news.get('total', 0)} tracked items."
    )

    context_text = (
        f"Unemployment at {fmt(ur_latest.get('value'), 1, '%')} ({ur_latest.get('date', 'n/a')}). "
        f"Macro data as of {macro.get('as_of', 'n/a')}; brief generated deterministically because no LLM API key was available."
    )

    headline = "Labor share "
    headline += "falling" if (ls_delta or 0) < 0 else "rising" if ls_delta is not None else "unavailable"
    if spread_3m is not None:
        headline += f"; capital enablers {'ahead' if spread_3m >= 0 else 'behind'} by {abs(spread_3m):.1f}pp (3m)"

    return {
        "headline": headline[:200],
        "bullets": [
            {"text": ls_text, "tag": ls_tag},
            {"text": ratio_text, "tag": ratio_tag},
            {"text": markets_text, "tag": markets_tag},
            {"text": news_text, "tag": news_tag},
            {"text": context_text, "tag": "neutral"},
        ],
        "model": "deterministic-fallback",
        "status": "fallback",
    }


def main():
    DATA_DIR.mkdir(exist_ok=True)
    macro = load_json("macro.json")
    markets = load_json("markets.json")
    news = load_json("news.json")

    snapshot = build_snapshot(macro, markets, news)
    brief = None
    if OPENROUTER_KEY:
        try:
            brief = call_openrouter(snapshot)
        except Exception:
            traceback.print_exc()

    if brief is None:
        brief = fallback_brief(macro, markets, news)

    if len(brief["bullets"]) < 5:
        filler = fallback_brief(macro, markets, news)
        for bullet in filler["bullets"]:
            if len(brief["bullets"]) >= 5:
                break
            if bullet not in brief["bullets"]:
                brief["bullets"].append(bullet)

    payload = {
        "generated_at": now_iso(),
        "headline": brief["headline"],
        "bullets": brief["bullets"][:5],
        "model": brief["model"],
        "status": brief["status"],
        "news_items_considered": (news or {}).get("total", 0),
        "macro_as_of": (macro or {}).get("as_of"),
        "markets_as_of": (markets or {}).get("as_of"),
        "snapshot_used": snapshot[:4000],
    }
    out_path = DATA_DIR / "brief.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[summarize] wrote {out_path} ({brief['status']}, {len(payload['bullets'])} bullets)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
