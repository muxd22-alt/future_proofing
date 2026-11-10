import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_DIR = ROOT / "config"
FEEDS_PATH = CONFIG_DIR / "feeds.json"
USER_AGENT = "capital-over-labor-tracker/1.0 (+github actions)"
MAX_AGE_DAYS = int(os.environ.get("NEWS_MAX_AGE_DAYS", "7"))
PER_FEED = int(os.environ.get("NEWS_PER_FEED", "10"))
MAX_ITEMS = int(os.environ.get("NEWS_MAX_ITEMS", "24"))
MAX_SUMMARY = 220
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

NOISE_RE = re.compile(
    r"\b(black friday|cyber monday|price-match|shopping|coupon|gift guide|best buy|walmart|target|"
    r"sponsored|podcast|horoscope|recipe|box office|celebrity|riaa|cable theft|gantry|"
    r"midterm|midterms|voting-machine|plague|snap election|october review)\b",
    re.I,
)

REASON_RULES = [
    (
        re.compile(r"\b(layoff|layoffs|job cut|job cuts|headcount|downsiz|firing|workforce reduction|replaced by ai)\b", re.I),
        "capital-positive",
        "labor",
        "Direct headcount reduction / AI substitution compressing labor share",
    ),
    (
        re.compile(r"\b(entry-level|recent grad|graduates|hiring freeze|slow hiring|white-collar|junior)\b", re.I),
        "capital-positive",
        "labor",
        "Entry-level cognitive hiring compression as firms automate routine work",
    ),
    (
        re.compile(r"\b(megawatt|gigawatt|mw|gw|nuclear|power grid|liquid cooling|substation|utility|turbine|energy)\b", re.I),
        "capital-positive",
        "ai_capex",
        "Physical power & cooling bottleneck driving HALO infrastructure capex",
    ),
    (
        re.compile(r"\b(capex|capital expenditure|data center|hyperscaler|gpu|blackwell|wafer|tsmc|nvidia|broadcom|server)\b", re.I),
        "capital-positive",
        "ai_capex",
        "Compute & silicon capex expansion shifting income toward capital owners",
    ),
    (
        re.compile(r"\b(corporate profits|profit margin|productivity|operating margin|labor share)\b", re.I),
        "capital-positive",
        "macro",
        "Output & margin expansion decoupling from payroll growth",
    ),
    (
        re.compile(r"\b(wage growth|union|strike|pay raise|tight labor|job gains|payroll surge)\b", re.I),
        "labor-positive",
        "labor",
        "Wage or bargaining-power resilience supporting labor income share",
    ),
    (
        re.compile(r"\b(fomc|federal reserve|interest rate|fed|gdp|inflation|pce|bea)\b", re.I),
        "neutral",
        "fed",
        "Macro / cost-of-capital policy shift affecting capex vs. hiring",
    ),
]


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(text):
    if not text:
        return ""
    text = TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&#39;", "'").replace("&quot;", '"').replace("&#8217;", "'").replace("&#8220;", '"').replace("&#8221;", '"')
    text = WS_RE.sub(" ", text).strip()
    if len(text) > MAX_SUMMARY:
        text = text[: MAX_SUMMARY - 1].rstrip() + "…"
    return text


def split_google_title(title, default_source):
    if " - " in title and len(title.rsplit(" - ", 1)[1]) <= 40:
        clean_title, pub = title.rsplit(" - ", 1)
        if clean_title.strip() and pub.strip():
            return clean_title.strip(), pub.strip()
    return title, default_source


def classify_item(title, summary, default_category):
    combined = f"{title} {summary}"
    if NOISE_RE.search(combined):
        return None
    for pattern, signal, category, reason in REASON_RULES:
        if pattern.search(combined):
            return {
                "signal": signal,
                "category": category or default_category,
                "reason": reason,
            }
    if default_category in ("fed", "macro"):
        return {
            "signal": "neutral",
            "category": default_category,
            "reason": "Official macro / monetary release impacting capital & labor",
        }
    return None


def entry_datetime(entry):
    for attr in ("published_parsed", "updated_parsed"):
        parsed = entry.get(attr)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def fetch_feed(feed, cutoff):
    resp = requests.get(
        feed["url"],
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
        timeout=30,
        allow_redirects=True,
    )
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    items = []
    default_source = feed.get("name", parsed.feed.get("title", "unknown"))
    default_cat = feed.get("category", "general")

    for entry in parsed.entries[:40]:
        published = entry_datetime(entry)
        if published and published < cutoff:
            continue
        link = entry.get("link") or ""
        raw_title = clean_text(entry.get("title") or "")
        if not raw_title or not link:
            continue
        title, source = split_google_title(raw_title, default_source)
        summary = clean_text(entry.get("summary") or entry.get("description") or "")
        meta = classify_item(title, summary, default_cat)
        if not meta:
            continue
        items.append(
            {
                "title": title,
                "link": link,
                "source": source,
                "category": meta["category"],
                "signal": meta["signal"],
                "reason": meta["reason"],
                "published": published.isoformat().replace("+00:00", "Z") if published else None,
                "summary": summary,
            }
        )
        if len(items) >= PER_FEED:
            break
    return items, len(parsed.entries)


def dedupe(items):
    seen = set()
    unique = []
    for item in items:
        norm_title = re.sub(r"[^a-z0-9]+", " ", item["title"].lower()).strip()
        key = norm_title[:70]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def sort_key(item):
    published = item.get("published")
    if not published:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def main():
    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / "news.json"
    previous = None
    try:
        previous = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception:
        previous = None

    feeds = json.loads(FEEDS_PATH.read_text(encoding="utf-8"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    feed_blocks = []
    all_items = []
    failures = 0

    for feed in feeds:
        block = {"name": feed.get("name"), "category": feed.get("category"), "url": feed.get("url")}
        try:
            items, total = fetch_feed(feed, cutoff)
            block.update({"status": "ok", "items_returned": len(items), "entries_seen": total})
            all_items.extend(items)
            print(f"[fetch_news] {feed.get('name')}: {len(items)} high-signal items ({total} seen)")
        except Exception as exc:
            failures += 1
            block.update({"status": "error", "error": str(exc)[:200]})
            print(f"[fetch_news] {feed.get('name')}: FAILED ({exc})")
        feed_blocks.append(block)

    items = dedupe(all_items)
    items.sort(key=sort_key, reverse=True)
    items = items[:MAX_ITEMS]
    carried_over = False
    if not items and previous and previous.get("items"):
        items = previous.get("items", [])[:MAX_ITEMS]
        carried_over = True
        print("[fetch_news] no fresh items; carrying over previous items")

    counts = {}
    for item in items:
        counts[item["category"]] = counts.get(item["category"], 0) + 1

    payload = {
        "generated_at": now_iso(),
        "max_age_days": MAX_AGE_DAYS,
        "feeds": feed_blocks,
        "feed_failures": failures,
        "carried_over": carried_over,
        "counts": counts,
        "total": len(items),
        "items": items,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[fetch_news] wrote {out_path} ({len(items)} items, {failures} feed failures)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
