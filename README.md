# Capital over Labor

**A zero-cost, self-updating tracker of the macroeconomic shift from a labor-driven to a capital-driven economy.**

> Output is decoupling from headcount. This repository measures that decoupling every day — automatically, publicly, and for free.

---

## The Manifesto

**The fundamental transformation is the decoupling of economic output from human headcount.** As AI and automation move into cognitive, analytical and professional tasks, codifiable knowledge work is substituted by machine intelligence at a fraction of the cost. When a firm replaces a \$75,000 salary with a \$15,000 software subscription, selling hours of routine cognitive labor becomes a structurally losing position.

The numbers already show it: labor's share of national income peaked at **58.4% in 1970** and had fallen to **51.9% by 2024**, with the BLS labor share index (FRED `PRS85006173`) sitting below 100 — near multi-decade lows — while corporate profits and hyperscaler capital expenditure (\$660B+ planned in 2026) set records.

**To position yourself on the winning side of this transfer, move from an *executed laborer* to an *orchestrator of digital capital*.** That does not require pre-existing wealth:

1. **Build intangible assets at near-zero marginal cost.** Software, automated workflows and AI-driven content scale infinitely without production cost. Owning the system pays; being a unit inside it does not.
2. **Capture the capex boom with fractional capital.** Zero-commission brokers and DRIPs let small amounts compound into the "picks and shovels" of the AI buildout — semiconductors, power, grid, cooling, automation (the **HALO** stack: Heavy Asset, Low Obsolescence).
3. **Orchestrate, don't execute.** Deploy agentic systems end-to-end for others, with proper containment and monitoring. The value accrues to whoever owns the workflow, not to whoever performs a task inside it.

This repository is the **free automated monitor** for that thesis: FRED macro data, watchlist momentum, RSS signal flow and a daily LLM brief, rebuilt by GitHub Actions and published to GitHub Pages.

---

## What Would Change My Mind

The thesis is only useful if it is falsifiable. These are the metrics that would break it — each one is tracked by this pipeline:

| # | Metric | Source (tracked daily) | Thesis prediction | What would falsify it |
|---|---|---|---|---|
| 1 | **Labor share index** | FRED `PRS85006173` (`data/macro.json`) | Stays depressed / keeps shrinking relative to profits | Labor share rises **> 1% for 3 consecutive quarterly observations** and reclaims its pre-2020 range — labor is regaining income share |
| 2 | **Corporate profits ÷ wage bill ratio** | FRED `CP` + derived wage bill (`data/macro.json`) | Rises over time (capital income outpaces wages) | Ratio falls **> 2% quarter-over-quarter for two consecutive quarters** |
| 3 | **Capital-enablers vs labor-heavy spread** | yfinance watchlists (`data/markets.json`) | Enablers (NVDA, AVGO, TSM, VRT, ETN, PWR…) outperform staffing/office/per-seat SaaS names | Labor-heavy basket **beats the enabler basket by > 5pp on a rolling 12-month basis** |
| 4 | **Hyperscaler AI capex** | RSS / daily brief (`data/news.json`, `data/brief.json`) | Capex guidance keeps rising | Top hyperscalers **cut capex guidance > 10% YoY for two consecutive quarters** — the buildout is over |
| 5 | **Unemployment vs profits** | FRED `UNRATE` (`data/macro.json`) | Weak entry-level labor demand coexists with strong profits | Unemployment rises **> 0.5pp while profits fall** — that is weak aggregate demand, not labor-to-capital substitution, and it invalidates the "substitution" mechanism |

**Automated alerts:** the workflow opens a GitHub Issue (label `macro-alert`) when these hard thresholds trip:

- labor share down **> 1.0%** vs the prior observation, or
- profits-to-wages ratio up **> 2.0%** vs the prior observation

Thresholds live as env vars in `.github/workflows/pipeline.yml` (`LABOR_SHARE_DROP_THRESHOLD_PCT`, `PROFITS_WAGES_RATIO_SPIKE_PCT`). Note the labor share series is published **quarterly**, so a "month-over-month" comparison is effectively quarter-over-quarter.

---

## Dashboard

Published at `https://<owner>.github.io/<repo>/` (GitHub Pages):

- **Labor Share Index** — full FRED history
- **Corporate Profits vs Wages** — both rebased to 100 over 10 years
- **Capital Enablers vs Labor-Heavy** — equal-weight baskets, 1-year normalized
- **Watchlist Momentum** — 3-month returns per ticker
- **Profits-to-Wages Ratio** and **Unemployment** context charts
- **Daily AI Brief** — 5 bullets, each tagged `CAPITAL` / `LABOR` / `CONTEXT`
- **Signal Stream** — last 7 days of Fed / tech / AI-capex / labor RSS items

## Architecture

```
FRED (labor share, profits, wages, unemployment)   yfinance watchlists
              \                                        /
               \           RSS: Fed, tech, AI capex   /
                \              /                      /
                 v             v                     v
              +------------------------------------------+
              |  GitHub Actions — daily cron 06:00 UTC   |
              |  1. scripts/fetch_data.py  -> data/*.json|
              |  2. scripts/fetch_news.py  -> news.json  |
              |  3. scripts/summarize.py   -> brief.json |  (OpenRouter LLM,
              |  4. scripts/build_site.py  -> index.html |   rule-based fallback)
              |  5. threshold check -> gh issue create   |
              |  6. commit data/ + site/, deploy Pages   |
              +------------------------------------------+
                                  |
                                  v
                     GitHub Pages (static Chart.js dashboard)
```

**Zero cost:** GitHub Actions (2,000 free min/mo), GitHub Pages, FRED (free key), yfinance (free), public RSS, OpenRouter free-tier models. No servers, no databases.

## Repository Structure

```
.
├── README.md
├── requirements.txt
├── .github/workflows/pipeline.yml   # daily cron + dispatch + issue alerts + Pages deploy
├── config/
│   ├── watchlist.json               # capital-enablers vs labor-heavy tickers
│   └── feeds.json                   # RSS sources (Fed, tech, AI capex, labor)
├── scripts/
│   ├── fetch_data.py                # FRED + yfinance -> data/macro.json, data/markets.json
│   ├── fetch_news.py                # RSS -> data/news.json
│   ├── summarize.py                 # LLM brief -> data/brief.json
│   └── build_site.py                # inject JSON -> site/index.html
├── site/
│   └── index.html                   # Chart.js dashboard (data injected between markers)
└── data/                            # generated daily, committed by the workflow
    ├── macro.json
    ├── markets.json
    ├── news.json
    └── brief.json
```

---

## Setup

### 1. Create the repository and push

```bash
cd FUTURE
git init
git add .
git commit -m "feat: capital-over-labor tracker (FRED + yfinance + RSS + LLM brief + Pages)"
git branch -M main
git remote add origin https://github.com/<OWNER>/<REPO>.git
git push -u origin main
```

### 2. Configure GitHub Secrets (repo → Settings → Secrets and variables → Actions)

| Secret | Required | Value |
|---|---|---|
| `FRED_API_KEY` | Optional | Free key from https://fred.stlouisfed.org/docs/api/api_key.html (without it, the pipeline falls back to keyless FRED CSV) |
| `OPENROUTER_API_KEY` | Optional | Free key from https://openrouter.ai/keys (without it, a deterministic rule-based brief is generated) |

| Variable (Settings → Variables) | Optional | Default |
|---|---|---|
| `LLM_MODEL` | Yes | `meta-llama/llama-3.3-70b-instruct:free` |

### 3. Enable GitHub Pages

Settings → Pages → **Source: GitHub Actions**.

### 4. Run it once manually

Actions → **Capital-over-Labor Daily Pipeline** → **Run workflow**.

---

## Local Development

```bash
# one-time: virtualenv + dependencies (Python 3.12+)
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

# export keys (or use $env: on PowerShell)
export FRED_API_KEY="your_fred_key"          # optional
export OPENROUTER_API_KEY="your_or_key"      # optional

# test the pipeline step by step
python scripts/fetch_data.py                 # -> data/macro.json, data/markets.json
python scripts/fetch_news.py                 # -> data/news.json
python scripts/summarize.py                  # -> data/brief.json
python scripts/build_site.py                 # -> site/index.html (data injected)

# preview the dashboard locally
python -m http.server 8000 --directory site
# open http://localhost:8000
```

Re-run `build_site.py` as often as you like — it is idempotent (data is injected between `CAPITAL_DATA` markers).

---

## How the Daily Pipeline Works

`.github/workflows/pipeline.yml` runs at **06:00 UTC daily** (and on `workflow_dispatch`):

1. `fetch_data.py` — FRED series (API if `FRED_API_KEY` set, else keyless CSV) + `yfinance` watchlist download; computes deltas, wage-bill proxy, profits-to-wages ratio, basket returns and spreads. On transient failure, previous data is preserved and flagged `stale`.
2. `fetch_news.py` — pulls the RSS feeds in `config/feeds.json`, filters to the last 7 days, dedupes, caps at 60 items.
3. `summarize.py` — sends a compact snapshot to an OpenRouter model and parses a strict 5-bullet JSON brief; falls back to deterministic rule-based bullets if the API is unavailable or returns garbage.
4. `build_site.py` — injects all four JSON files into `site/index.html` between `CAPITAL_DATA` markers.
5. Threshold check — evaluates the falsifiable rules above; if tripped, `gh issue create` opens a deduplicated `macro-alert` issue.
6. Commits `data/` and `site/`, uploads the Pages artifact, and deploys.

Permissions used: `contents: write`, `pages: write`, `issues: write`, `id-token: write`.

---

## Disclaimer

This project is for **educational and research purposes only**. It is **not investment advice**, not a solicitation, and not a recommendation to buy or sell any security or asset. Nothing here constitutes financial, tax or legal advice. Data may be delayed, revised, restated or unavailable; always verify against primary sources (FRED, BLS, BEA). LLM-generated briefs can be wrong. Past performance does not predict future results. Do your own research. The author assumes no liability for actions taken based on this repository.
