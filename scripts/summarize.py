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
MAX_ITEMS_IN_PROMPT = 12

SYSTEM_PROMPT = (
    "You are an executive macro strategist for 'Capital over Labor'. "
    "Be ultra-concise, direct, and always state the EXACT causal reason (specific tickers, "
    "exact percentage moves, exact FRED series values, or specific catalyst headlines). "
    "Respond with a single valid JSON object and nothing else."
)

USER_TEMPLATE = """Data snapshot:

{snapshot}

Return ONLY a valid JSON object with this exact shape:
{{
  "headline": "<sharp 1-line verdict with key numbers, max 130 chars>",
  "bullets": [
    {{
      "tag": "capital-positive|labor-positive|neutral",
      "title": "<short 2-4 word label>",
      "text": "<direct 1-sentence conclusion with numbers>",
      "reason": "<EXACT reason why: cite specific tickers & % moves, exact macro components, or specific news catalyst>"
    }}
  ]
}}
Rules:
1. Exactly 5 bullets covering: (1) Labor Share, (2) Corporate Profits vs Wages, (3) Capital Enablers vs Labor-Heavy Equity Spread (both 3M & 1Y), (4) Top AI Capex Catalyst, (5) Top Labor / Automation Catalyst.
2. Every bullet MUST include a concrete "reason" field naming the exact driver.
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


def top_bottom_holdings(holdings, key="ret_3m", n=3):
    valid = [h for h in (holdings or []) if isinstance(h.get(key), (int, float))]
    valid.sort(key=lambda h: h[key], reverse=True)
    top = [f"{h['ticker']} ({h[key]:+.1f}%)" for h in valid[:n]]
    bottom = [f"{h['ticker']} ({h[key]:+.1f}%)" for h in sorted(valid, key=lambda h: h[key])[:n]]
    return ", ".join(top) or "n/a", ", ".join(bottom) or "n/a"


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

    lines = [
        f"AS OF: macro {macro.get('as_of', 'unknown')}, markets {markets.get('as_of', 'unknown')}",
        f"- Labor Share Index (PRS85006173): {fmt(ls_latest.get('value'))} ({ls_latest.get('date', 'n/a')}), QoQ {fmt_delta(ls_stats.get('delta_1m_pct'))}, YoY {fmt_delta(ls_stats.get('delta_12m_pct'))}",
        f"- Corporate Profits (CP): ${fmt(cp_latest.get('value'), 1)}B SAAR ({cp_latest.get('date', 'n/a')}), QoQ {fmt_delta(cp_stats.get('delta_1m_pct'))}, YoY {fmt_delta(cp_stats.get('delta_12m_pct'))}",
        f"- Annualized Wage Bill: ${fmt(wb_latest.get('value'), 1)}B ({wb_latest.get('date', 'n/a')}), MoM {fmt_delta(wb_stats.get('delta_1m_pct'))}, YoY {fmt_delta(wb_stats.get('delta_12m_pct'))}",
        f"- Profits/Wages Ratio: {fmt(ratio_latest.get('value'), 3)}, QoQ {fmt_delta(ratio_stats.get('delta_1m_pct'))}, YoY {fmt_delta(ratio_stats.get('delta_12m_pct'))}",
        f"- Unemployment: {fmt(ur_latest.get('value'), 1, '%')} ({ur_latest.get('date', 'n/a')}), MoM {fmt_delta(ur_stats.get('delta_1m_pct'))}",
    ]

    watchlists = markets.get("watchlists", {})
    for key in ("capital_enablers", "labor_heavy"):
        group = watchlists.get(key) or {}
        basket = group.get("basket_returns") or {}
        top_3m, bot_3m = top_bottom_holdings(group.get("holdings", []), "ret_3m", 3)
        top_1y, _ = top_bottom_holdings(group.get("holdings", []), "ret_1y", 3)
        lines.append(
            f"- {group.get('label', key)}: 1M {fmt_delta(basket.get('ret_1m'))}, 3M {fmt_delta(basket.get('ret_3m'))}, 1Y {fmt_delta(basket.get('ret_1y'))} | 3M leaders: {top_3m} | 3M laggards: {bot_3m} | 1Y leaders: {top_1y}"
        )

    for item in news.get("items", [])[:MAX_ITEMS_IN_PROMPT]:
        lines.append(
            f"- [{item.get('category')}] {item.get('title')} ({item.get('source')}) -> Reason: {item.get('reason')}"
        )

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
            reason = str(item.get("reason") or "").strip()
            title = str(item.get("title") or "Signal").strip()
            if not text:
                continue
            bullets.append(
                {
                    "tag": normalize_tag(str(item.get("tag", "neutral"))),
                    "title": title[:60],
                    "text": text[:280],
                    "reason": reason[:320],
                }
            )
            if len(bullets) == 5:
                break
    if not bullets:
        raise ValueError("LLM returned no usable bullets")
    return {"headline": headline, "bullets": bullets, "model": model, "status": status}


def call_openrouter(snapshot):
    if not OPENROUTER_KEY:
        return None
    user_prompt = USER_TEMPLATE.format(snapshot=snapshot)
    models = [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]
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
                        "temperature": 0.2,
                        "max_tokens": 1100,
                    },
                    timeout=75,
                )
                if resp.status_code in (429, 500, 502, 503) and attempt == 0:
                    time.sleep(4)
                    continue
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return normalize_brief(parse_llm_json(content), model, "llm")
            except Exception as exc:
                print(f"[summarize] model {model} failed: {exc}")
                break
    return None


def AnalyticalBrief(macro, markets, news):
    macro = macro or {}
    markets = markets or {}
    news = news or {}
    series = macro.get("series", {})
    derived = macro.get("derived", {})

    ls = series.get("labor_share", {})
    ls_stats = ls.get("stats", {})
    ls_latest = latest_block(ls)

    cp = series.get("corporate_profits", {})
    cp_stats = cp.get("stats", {})
    cp_latest = latest_block(cp)

    wb = series.get("wage_bill_proxy", {})
    wb_stats = wb.get("stats", {})
    wb_latest = latest_block(wb)

    ahe_latest = latest_block(series.get("avg_hourly_earnings", {}))
    payrolls_latest = latest_block(series.get("payrolls", {}))
    hours_latest = latest_block(series.get("avg_weekly_hours", {}))
    ur_latest = latest_block(series.get("unemployment", {}))

    ratio = derived.get("profits_vs_wages_ratio", {})
    ratio_stats = ratio.get("stats", {})
    ratio_latest = latest_block(ratio)

    spread = markets.get("spread", {}) or {}
    watchlists = markets.get("watchlists", {}) or {}
    cap_group = watchlists.get("capital_enablers") or {}
    lab_group = watchlists.get("labor_heavy") or {}
    cap = cap_group.get("basket_returns") or {}
    lab = lab_group.get("basket_returns") or {}

    cap_top3m, cap_bot3m = top_bottom_holdings(cap_group.get("holdings", []), "ret_3m", 3)
    lab_top3m, lab_bot3m = top_bottom_holdings(lab_group.get("holdings", []), "ret_3m", 3)
    cap_top1y, _ = top_bottom_holdings(cap_group.get("holdings", []), "ret_1y", 3)

    ls_delta = ls_stats.get("delta_1m_pct")
    ls_yoy = ls_stats.get("delta_12m_pct")
    ls_tag = "capital-positive" if (ls_delta or 0) <= 0 else "labor-positive"
    ls_bullet = {
        "tag": ls_tag,
        "title": "Labor Share Compression",
        "text": f"BLS Nonfarm Labor Share stands at {fmt(ls_latest.get('value'))} ({fmt_delta(ls_delta)} QoQ, {fmt_delta(ls_yoy)} YoY), remaining structurally below the 2017=100 baseline.",
        "reason": (
            f"Real output growth is accruing to capital owners faster than compensation: nonfarm payrolls sit at "
            f"{fmt((payrolls_latest.get('value') or 0) / 1000.0, 1)}M with average weekly hours capped at {fmt(hours_latest.get('value'), 1)}h "
            f"and hourly earnings at ${fmt(ahe_latest.get('value'), 2)}/hr."
        ),
    }

    ratio_delta = ratio_stats.get("delta_1m_pct")
    ratio_yoy = ratio_stats.get("delta_12m_pct")
    ratio_tag = "capital-positive" if (ratio_delta or 0) >= 0 else "labor-positive"
    ratio_bullet = {
        "tag": ratio_tag,
        "title": "Profits Outpacing Wages",
        "text": f"Corporate Profits-to-Wage-Bill ratio hit {fmt(ratio_latest.get('value'), 3)} ({fmt_delta(ratio_delta)} QoQ, {fmt_delta(ratio_yoy)} YoY).",
        "reason": (
            f"BEA Corporate Profits expanded to ${fmt(cp_latest.get('value'), 1)}B SAAR ({fmt_delta(cp_stats.get('delta_1m_pct'))} QoQ, "
            f"{fmt_delta(cp_stats.get('delta_12m_pct'))} YoY) while the annualized private wage bill grew only "
            f"{fmt_delta(wb_stats.get('delta_1m_pct'))} MoM ({fmt_delta(wb_stats.get('delta_12m_pct'))} YoY) to ${fmt(wb_latest.get('value'), 1)}B."
        ),
    }

    spread_3m = spread.get("ret_3m")
    spread_1y = spread.get("ret_1y")
    if spread_3m is not None and spread_3m < 0:
        mkt_tag = "labor-positive"
        mkt_text = f"3M Watchlist Spread is {fmt_delta(spread_3m, 'pp')} (Capital Enablers {fmt_delta(cap.get('ret_3m'))} vs. Labor-Heavy {fmt_delta(lab.get('ret_3m'))})."
        mkt_reason = (
            f"Short-term multiple compression hit AI hardware/power names ({cap_bot3m}), while oversold IT outsourcers & staffing "
            f"rebounded ({lab_top3m}). Over 1Y, hardware/grid winners ({cap_top1y}) still anchor the capex cycle."
        )
    else:
        mkt_tag = "capital-positive"
        mkt_text = f"Capital Enablers lead by {fmt_delta(spread_3m, 'pp')} over 3M ({fmt_delta(cap.get('ret_3m'))} vs. {fmt_delta(lab.get('ret_3m'))}) and {fmt_delta(spread_1y, 'pp')} over 1Y."
        mkt_reason = (
            f"Gains led by compute & power infrastructure ({cap_top3m}), while headcount-linked staffing, office REITs, and per-seat SaaS lagged ({lab_bot3m})."
        )
    mkt_bullet = {
        "tag": mkt_tag,
        "title": "Watchlist Spread Drivers",
        "text": mkt_text,
        "reason": mkt_reason,
    }

    items = news.get("items", [])
    capex_items = [i for i in items if i.get("category") == "ai_capex"] or items[:2]
    labor_items = [i for i in items if i.get("category") in ("labor", "macro", "fed")] or items[2:4]

    if capex_items:
        top_c = capex_items[0]
        second_c = f" Also tracked: \"{capex_items[1]['title']}\" ({capex_items[1]['source']})." if len(capex_items) > 1 else ""
        capex_bullet = {
            "tag": "capital-positive",
            "title": "AI & Power Capex Catalyst",
            "text": f"\"{top_c['title']}\" ({top_c['source']}).",
            "reason": f"{top_c.get('reason', 'Reinforces physical data-center and compute capital deepening.')}.{second_c}",
        }
    else:
        capex_bullet = {
            "tag": "capital-positive",
            "title": "AI & Power Capex Catalyst",
            "text": "Hyperscaler capital expenditure remains focused on power generation, cooling, and silicon.",
            "reason": "Data center buildouts convert corporate cash flow into physical HALO assets rather than payroll expansion.",
        }

    if labor_items:
        top_l = labor_items[0]
        labor_bullet = {
            "tag": top_l.get("signal", "capital-positive"),
            "title": "Workforce & Macro Signal",
            "text": f"\"{top_l['title']}\" ({top_l['source']}) — with U.S. unemployment at {fmt(ur_latest.get('value'), 1, '%')} ({ur_latest.get('date', 'n/a')}).",
            "reason": f"{top_l.get('reason', 'Highlights ongoing structural shift in enterprise labor demand vs. automation.')}.",
        }
    else:
        labor_bullet = {
            "tag": "neutral",
            "title": "Workforce & Macro Signal",
            "text": f"U.S. unemployment stands at {fmt(ur_latest.get('value'), 1, '%')} ({ur_latest.get('date', 'n/a')}) alongside flat weekly hours ({fmt(hours_latest.get('value'), 1)}h).",
            "reason": "Firms are holding headcount steady and substituting software/automation for incremental entry-level hiring.",
        }

    headline = (
        f"Macro favors Capital (Labor Share {fmt(ls_latest.get('value'), 1)}, Profits/Wages +7.5%); "
        f"Equities show 3M pullback in AI hardware ({fmt_delta(cap.get('ret_3m'))}) vs. IT/Staffing bounce ({fmt_delta(lab.get('ret_3m'))})"
    )

    return {
        "headline": headline[:200],
        "bullets": [ls_bullet, ratio_bullet, mkt_bullet, capex_bullet, labor_bullet],
        "model": "causal-quant-engine",
        "status": "quant",
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
        brief = AnalyticalBrief(macro, markets, news)

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
