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
PER_FEED = int(os.environ.get("NEWS_PER_FEED", "8"))
MAX_ITEMS = int(os.environ.get("NEWS_MAX_ITEMS", "60"))
MAX_SUMMARY = 260
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


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
    for entry in parsed.entries[:40]:
        published = entry_datetime(entry)
        if published and published < cutoff:
            continue
        link = entry.get("link") or ""
        title = clean_text(entry.get("title") or "")
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "link": link,
                "source": feed.get("name", parsed.feed.get("title", "unknown")),
                "category": feed.get("category", "general"),
                "published": published.isoformat().replace("+00:00", "Z") if published else None,
                "summary": clean_text(entry.get("summary") or entry.get("description") or ""),
            }
        )
        if len(items) >= PER_FEED:
            break
    return items, len(parsed.entries)


def dedupe(items):
    seen = set()
    unique = []
    for item in items:
        key = (item["link"].split("?")[0].rstrip("/").lower(), item["title"].lower())
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
            print(f"[fetch_news] {feed.get('name')}: {len(items)} items ({total} entries)")
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

    if not items:
        print("[fetch_news] warning: no items available")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
