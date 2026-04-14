# VQM Screener

A quantitative stock screener for Italian and international equities based on the **Value / Quality / Momentum** framework.
Scoring is fully deterministic and reproducible; AI is an optional narrative layer and does not influence the score.
Results are persisted in **PostgreSQL** and exposed through a **Flask web dashboard**.

---

## Features

| Area | Detail |
|---|---|
| **Scoring** | VQM score 0–10, sector-calibrated, 12 core metrics across 3 pillars |
| **Coverage** | Italian (FTSE MIB), German, French, UK, and US equities |
| **Persistence** | Time-series storage in PostgreSQL — every run is stored with full metrics |
| **Web dashboard** | Flask + Tailwind CSS + Chart.js — cards, live filters, detail drawer, score history chart |
| **Portfolio watchlist** | Star any ticker to add it to a `localStorage` portfolio; aggregate metrics and sector breakdown |
| **Scheduler** | APScheduler runs the screener daily at midnight; also fires once at server startup |
| **AI commentary** | Optional `--ai` flag — Tavily news search + LLM generates 2–3 sentences of context per ticker |
| **Auto benchmark** | Benchmark index resolved from ticker suffix (`.MI`, `.DE`, `.PA`, `.L`, etc.) |
| **External config** | `thresholds.json` — sector thresholds, pillar weights, default tickers, all editable without code changes |
| **Launchers** | `finanalysis.bat` (Windows) / `finanalysis.sh` (Linux/macOS) — no manual conda activation needed |
| **Docker** | `Dockerfile` + `docker-compose.yml` — single-container deployment, connects to external PostgreSQL |

---

## Project structure

```
finance_bot/
├── screener.py          # VQM scoring engine — CLI entry point
├── db.py                # PostgreSQL persistence layer
├── app.py               # Flask web server (port 5001) + APScheduler
├── config.py            # Runtime config from .env
├── thresholds.json      # Sector thresholds, pillar weights, default tickers
├── finanalysis.bat      # Windows launcher
├── finanalysis.sh       # Linux / macOS launcher
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env                 # Environment variables (git-ignored)
├── .env.example         # Template — copy to .env and fill in values
├── templates/
│   └── index.html       # Tailwind CSS dashboard
└── static/
    └── app.js           # Chart.js, card grid, drawer, portfolio logic
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# edit .env — at minimum fill in POSTGRES_* variables
```

### 3. Run the screener once

```bash
python screener.py
```

Uses the default ticker list from `thresholds.json`. Results are saved to PostgreSQL.

### 4. Start the web server

```bash
python app.py
```

Open [http://localhost:5001](http://localhost:5001).
The scheduler will run the screener automatically at midnight every day, and once at startup if no run exists for today.

---

## Docker

Requires an **external PostgreSQL** instance. Configure it via `.env` before starting.

```bash
# build and start
docker compose up -d --build

# view logs
docker compose logs -f

# stop
docker compose down
```

The container exposes port **5001** and reads all configuration from the `.env` file in the project root.

---

## Linux / macOS launcher

```bash
chmod +x finanalysis.sh
./finanalysis.sh                    # run screener with defaults
./finanalysis.sh ISP.MI UCG.MI --ai # custom tickers with AI
```

The script resolves Python from `.conda/bin/python` (same conda env used on Windows).

---

## CLI reference

```
python screener.py [TICKERS ...] [--ai] [--benchmark INDEX] [--force]
```

| Argument | Description |
|---|---|
| `TICKERS` | One or more ticker symbols (e.g. `ISP.MI UCG.MI`). Defaults to `thresholds.json` list. |
| `--ai` | Enable AI commentary per ticker (requires `LLM_API_KEY` + `TAVILY_API_KEY`). |
| `--benchmark INDEX` | Override the auto-detected benchmark index (e.g. `--benchmark ^GDAXI`). |
| `--force` | Re-run even if a run for today already exists in the database. |

---

## How it works

### Phase 1 — Parallel fetch
All tickers are fetched in parallel (`SCREENER_WORKERS` threads) from Yahoo Finance via yfinance:
- Valuation, quality, margin and leverage metrics
- Monthly price history for momentum
- Extra fields: dividend yield, PEG, 52-week change

### Phase 2 — Deterministic VQM scoring

Each metric is normalised to **0–10** by `_score_metric()`, then averaged within its pillar:

| Pillar | Default weight | Metrics |
|---|---|---|
| **Value** | 30 % | EV/EBITDA, P/FCF, P/E, P/Book |
| **Quality** | 40 % | ROE, EBITDA Margin, Gross Margin, D/E Ratio, EPS CAGR 5Y |
| **Momentum** | 30 % | Mom. 12M-1M, EPS Rev. proxy, Rel. Strength vs benchmark |

> Weights are read from `thresholds.json → "pesi"` and can be changed without editing code.

Metrics marked N/A for a sector (e.g. EV/EBITDA for banks) are excluded without penalising the score.

**Classification thresholds:**

| Score | Signal |
|---|---|
| ≥ 7.5 | **BUY** |
| 5.0 – 7.4 | **HOLD** |
| < 5.0 | **SELL** |

### Phase 3 — Persistence
Results are saved to PostgreSQL via `db.save_run()`. If a run already exists for today, the screener skips execution and returns the cached results (override with `--force`).

### Phase 4 — AI commentary (optional, `--ai`)
For each valid ticker:
1. **Tavily** searches recent news (earnings, analyst outlook, macro events)
2. **LLM** writes 2–3 contextual sentences based on VQM data + news — stored as `commento_ai`

---

## Benchmark auto-detection

The benchmark index is resolved automatically from the ticker suffix:

| Suffix | Benchmark |
|---|---|
| `.MI` | `FTSEMIB.MI` |
| `.DE` / `.F` / `.BE` | `^GDAXI` |
| `.PA` | `^FCHI` |
| `.L` | `^FTSE` |
| `.MC` | `^IBEX` |
| `.SW` | `^SSMI` |
| `.AS` | `^AEX` |
| _(none)_ | `SPY` |

Use `--benchmark` to override for a specific run, or set `SCREENER_BENCHMARK` in `.env` as the global fallback.

---

## thresholds.json

The single model configuration file. Three top-level sections:

### `"pesi"` — pillar weights
```json
"pesi": { "value": 0.30, "quality": 0.40, "momentum": 0.30 }
```
Must sum to 1.0.

### `"tickers"` — default ticker list
```json
"tickers": ["ISP.MI", "UCG.MI", "ENI.MI", ...]
```

### Sector blocks — thresholds
One block per GICS sector group. Each metric entry:

| Field | Type | Description |
|---|---|---|
| `metrica` | string | Internal key (e.g. `"roe"`, `"pe"`) |
| `good` | number \| null | Optimal value → score 10; `null` = N/A for this sector |
| `bad` | number \| null | Worst value → score 0 |
| `lower_is_better` | bool | `true` for multiples, `false` for returns/margins |

---

## Web dashboard

Start the server: `python app.py` → [http://localhost:5001](http://localhost:5001)

### API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Dashboard HTML |
| `GET` | `/api/latest` | All results from the latest run, ordered by rank |
| `GET` | `/api/tickers` | Distinct tickers with most recent score |
| `GET` | `/api/ticker/<ticker>` | Full score history for one ticker |
| `GET` | `/api/runs` | Last 30 run metadata entries |

### Portfolio watchlist
- Click the **★** on any card to add it to your portfolio
- The portfolio button in the nav bar shows a count badge
- The portfolio panel (slides up from the bottom) shows:
  - BUY / HOLD / SELL counts + average score ring
  - Average Value / Quality / Momentum pillar bars
  - Aggregate metrics (P/E, ROE, EBITDA Margin, Dividend Yield, P/Book, EV/EBITDA, ROA, Revenue Growth)
  - Sector tag cloud
  - Sortable ticker list — click a row to open the detail drawer
- Portfolio persists in `localStorage` (survives page refresh)

---

## PostgreSQL schema

```sql
screener_runs (
  id          SERIAL PRIMARY KEY,
  run_at      TIMESTAMPTZ,
  run_date    DATE,
  benchmark   TEXT,
  ai_enabled  BOOLEAN,
  n_tickers   INTEGER
)

screener_results (
  id               SERIAL PRIMARY KEY,
  run_id           INTEGER REFERENCES screener_runs(id),
  run_date         DATE,
  ticker           TEXT,
  nome             TEXT,
  settore          TEXT,
  industria        TEXT,
  -- ... full VQM metric set, pillar scores, rank, AI comment, error
)
```

---

## Scheduler

`app.py` starts a `BackgroundScheduler` (APScheduler) that:
- Fires **daily at 00:00** (cron trigger)
- Also fires **once at server startup** in a daemon thread
- Skips if a run for the current calendar day already exists
- Shuts down cleanly via `atexit`

> **Note:** Flask is started with `use_reloader=False` to prevent the dev reloader from spawning a second scheduler process.

---

## Configuration (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `POSTGRES_HOST` | ✓ | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | ✓ | `5432` | PostgreSQL port |
| `POSTGRES_USER` | ✓ | `postgres` | DB user |
| `POSTGRES_PASSWORD` | ✓ | — | DB password |
| `POSTGRES_DB` | ✓ | `finanalysis` | Database name |
| `SCREENER_WORKERS` | | `6` | Parallel fetch workers |
| `SCREENER_BENCHMARK` | | `SWDA.MI` | Fallback benchmark index |
| `ANALYSIS_LANGUAGE` | | `italian` | AI commentary language |
| `LLM_MODEL` | `--ai` only | `gpt-4o` | Model name |
| `LLM_API_KEY` | `--ai` only | — | OpenAI-compatible API key |
| `TAVILY_API_KEY` | `--ai` only | — | Tavily search API key |

Without `LLM_API_KEY` and `TAVILY_API_KEY`, the screener works normally; `--ai` is silently skipped.
