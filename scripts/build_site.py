import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "site"
INDEX_PATH = SITE_DIR / "index.html"
MARKER_START = "<!-- CAPITAL_DATA_START -->"
MARKER_END = "<!-- CAPITAL_DATA_END -->"
DATA_FILES = {
    "macro": "macro.json",
    "markets": "markets.json",
    "news": "news.json",
    "brief": "brief.json",
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(name):
    path = DATA_DIR / name
    if not path.exists():
        print(f"[build_site] missing {path}, using null")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[build_site] failed to parse {path}: {exc}")
        return None


def main():
    if not INDEX_PATH.exists():
        print(f"[build_site] template not found: {INDEX_PATH}")
        sys.exit(1)

    payload = {key: load_json(fname) for key, fname in DATA_FILES.items()}
    payload["built_at"] = now_iso()
    payload["schema_version"] = 1

    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    blob = blob.replace("</", "<\\/")

    html = INDEX_PATH.read_text(encoding="utf-8")
    start = html.find(MARKER_START)
    end = html.find(MARKER_END)
    if start == -1 or end == -1 or end < start:
        print(f"[build_site] markers missing in {INDEX_PATH}")
        sys.exit(1)

    block = (
        f"{MARKER_START}\n"
        f"<script>window.CAPITAL_DATA = {blob};</script>\n"
        f"{MARKER_END}"
    )
    rebuilt = html[:start] + block + html[end + len(MARKER_END):]
    INDEX_PATH.write_text(rebuilt, encoding="utf-8", newline="\n")

    sizes = {k: (len(v) if isinstance(v, (dict, list)) else 0) for k, v in payload.items() if k not in ("built_at", "schema_version")}
    print(f"[build_site] injected data into {INDEX_PATH} ({len(rebuilt)} bytes)")
    print(f"[build_site] payload keys: {json.dumps(sizes)}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
