# VQM Screener

A quantitative stock screener for Italian and international equities based on the **Value / Quality / Momentum** framework.
Scoring is fully deterministic and reproducible; AI is an optional narrative layer and does not influence the score.
Results are persisted in **PostgreSQL** and exposed through a **Flask web dashboard**.

---

## Features

| Area | Detail |
|---|---|
| **Scoring** | VQM score 0–10, sector-calibrated across 11 GICS sector groups, 12 VQM metrics |
| **Coverage** | FTSE MIB, DAX, CAC 40, IBEX, SMI, AEX + US large-caps (~110 default tickers) |
| **Persistence** | Full time-series in PostgreSQL — every run stored with all metrics and pillar scores |
| **Web dashboard** | Flask + Chart.js — card grid, detail drawer, score history, filter/sort toolbar |
| **Portfolio watchlist** | `localStorage`-backed watchlist with signal distribution, pillar averages, sector breakdown |
| **Scheduler** | APScheduler fires daily at midnight + once at startup; skips if today's run already exists |
| **WACC** | Per-ticker WACC with dynamic risk-free rate from the ECB AAA sovereign 8Y curve (cached 24 h) |
| **AI commentary** | Optional `--ai` flag — Tavily news search + LLM generates 2–3 sentences of context per ticker |
| **Auto benchmark** | Benchmark index resolved from ticker suffix (`.MI`, `.DE`, `.PA`, `.L`, `.AS`, etc.) |
| **External config** | `thresholds.json` — sector thresholds, pillar weights, default tickers, all editable without code changes |
| **Launchers** | `finanalysis.bat` (Windows) / `finanalysis.sh` (Linux/macOS) — no manual conda activation needed |
| **Docker** | `Dockerfile` + `docker-compose.yml` — single-container deployment with external PostgreSQL |

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
├── docs/
│   └── metrics.md       # Detailed metric definitions, formulas, and fallback chains
├── templates/
│   └── index.html       # Dashboard (Chart.js + custom CSS)
└── static/
    ├── app.js           # Card grid, drawer, portfolio panel, Chart.js logic
    ├── sw.js            # Service Worker (offline cache)
    └── manifest.json    # PWA manifest
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

### 3. Run the screener

```bash
python screener.py                         # default tickers from thresholds.json
python screener.py ISP.MI UCG.MI ENI.MI    # explicit tickers
python screener.py --force                 # re-run even if today's run already exists
python screener.py --ai                    # + AI commentary per ticker
```

### 4. Start the web server

```bash
python app.py
```

Open [http://localhost:5001](http://localhost:5001).
The scheduler runs the screener automatically at midnight every day, and once at startup if no run exists for today.

---

## Docker

Requires an **external PostgreSQL** instance. Configure it via `.env` before starting.

```bash
docker compose up -d --build   # build and start
docker compose logs -f         # view logs
docker compose down            # stop
```

The container exposes port **5001** and reads all configuration from the `.env` file in the project root.

---

## CLI reference

```
python screener.py [TICKERS ...] [--ai] [--benchmark INDEX] [--force]
```

| Argument | Description |
|---|---|
| `TICKERS` | One or more ticker symbols (e.g. `ISP.MI UCG.MI`). Defaults to `thresholds.json → "tickers"`. |
| `--ai` | Enable AI commentary per ticker (requires `LLM_API_KEY` + `TAVILY_API_KEY`). |
| `--benchmark INDEX` | Override the auto-detected benchmark index (e.g. `--benchmark ^GDAXI`). |
| `--force` | Re-run even if a run for today already exists in the database. |

---

## How it works

### Phase 1 — Data fetch
Each ticker is fetched sequentially from Yahoo Finance via `yfinance`. The screener uses per-thread `curl_cffi` sessions (Chrome impersonation) for reliable HTTPS transport.

For every ticker, the following are downloaded:
- `quarterly_income_stmt`, `quarterly_balance_sheet`, `quarterly_cashflow` — preferred for TTM calculations
- `income_stmt`, `balance_sheet`, `cashflow` — annual fallback when quarterly data is missing or incomplete
- `t.info` — metadata (sector, market cap, currency) and pre-calculated ratios as last-resort fallback

### Phase 2 — Metric calculation
TTM (Trailing Twelve Months) metrics sum the four most recent quarterly periods.
Balance-sheet metrics use the most recent quarter (MRQ).
Each metric has a structured fallback chain: quarterly statements → annual statements → `info` dict.

→ See [docs/metrics.md](docs/metrics.md) for complete formulas and fallback chains.

### Phase 3 — Deterministic VQM scoring

Each metric is normalised to **0–10** by a linear interpolation between `bad` (→ 0) and `good` (→ 10) thresholds, then averaged within its pillar.

| Pillar | Default weight | Core metrics |
|---|---|---|
| **Value** | 25 % | EV/EBITDA, P/FCF, P/E, FCF Yield |
| **Quality** | 50 % | ROE, EBITDA Margin (or ROA for banks), ROIC, D/E Ratio, EPS CAGR 4Y |
| **Momentum** | 25 % | Mom. 12M−1M, EPS Revision, FCF Growth |

> Weights are read from `thresholds.json → "pesi"` and can be changed without editing code.
> Metrics with `null` thresholds for a sector are excluded without penalising the score.

**Classification thresholds:**

| Score | Signal |
|---|---|
| ≥ 7.5 | **BUY** |
| 5.0 – 7.4 | **HOLD** |
| < 5.0 | **SELL** |

### Phase 4 — Persistence
Results are saved to PostgreSQL via `db.save_run()`. If a run already exists for today, the screener skips execution and returns the cached data (override with `--force`).

### Phase 5 — AI commentary (optional, `--ai`)
For each valid ticker:
1. **Tavily** searches recent news (earnings, analyst outlook, macro events)
2. **LLM** writes 2–3 contextual sentences based on VQM data + news — stored as `commento_ai`

---

## Sector calibration

Thresholds are tuned per GICS sector group. Sectors covered:

| Sector | Notes |
|---|---|
| Financial Services | EV/EBITDA N/A; P/Book in Value; ROA replaces EBITDA Margin in Quality; D/E N/A |
| Real Estate | FCF Yield in Value instead of P/E; EBITDA Margin > 55% is optimal |
| Utilities | FCF Yield threshold bad = −5% (accepts capital-intensive negative FCF) |
| Energy | Tighter multiples; FCF Yield good = 9% |
| Technology | Wider multiples; higher growth bar for EPS CAGR |
| Healthcare | Mid-range multiples; ROIC and EBITDA Margin weighted highly |
| Consumer Cyclical | Moderate thresholds; cyclical-adjusted momentum |
| Consumer Defensive | Stricter D/E; ROE good ≥ 25% |
| Industrials | Balanced thresholds; FCF Yield good = 5% |
| Communication Services | EBITDA Margin good = 30%; tolerant EV/EBITDA = 7 |
| Basic Materials | Lowest PE/EV multiples; cyclical D/E tolerance |
| `_default` | Applied when sector is not recognised |

---

## WACC

The screener computes WACC for every ticker using:

$$\text{WACC} = \frac{E}{V} \cdot R_e + \frac{D}{V} \cdot R_d \cdot (1 - T)$$

Where:
- $R_e = R_f + \beta \times \text{ERP}$, ERP = 5.5% (Damodaran EUR), $R_f$ = ECB AAA sovereign EUR 8Y (live, cached 24 h)
- $R_d = |\text{TTM Interest Expense}| / \text{Total Debt}$, clamped 0.5%–20%
- $T$ = effective tax rate from TTM Tax Provision / Pretax Income, clamped 10%–40% (fallback: 24%)
- Debt-free companies: WACC = $R_e$

Interest Expense follows a 5-level fallback chain; see [docs/metrics.md](docs/metrics.md).

---

## Benchmark auto-detection

| Suffix | Benchmark |
|---|---|
| `.MI` | `FTSEMIB.MI` |
| `.DE` / `.F` / `.BE` | `^GDAXI` |
| `.PA` | `^FCHI` |
| `.L` | `^FTSE` |
| `.MC` | `^IBEX` |
| `.SW` | `^SSMI` |
| `.AS` | `^AEX` |
| `.LS` | `^PSI20` |
| `.T` | `^N225` |
| `.HK` | `^HSI` |
| `.TO` / `.V` | `^GSPTSE` |
| `.AX` | `^AXJO` |
| `.SA` | `^BVSP` |
| _(none)_ | `SPY` |

Use `--benchmark` to override for a specific run, or set `SCREENER_BENCHMARK` in `.env` as the global fallback.

---

## thresholds.json

The single model configuration file. Three top-level sections:

### `"pesi"` — pillar weights
```json
"pesi": { "value": 0.25, "quality": 0.50, "momentum": 0.25 }
```
Must sum to 1.0.

### `"tickers"` — default ticker list
```json
"tickers": ["ISP.MI", "UCG.MI", "ENI.MI", ...]
```
~110 tickers across Italian, German, French, Spanish, Swiss, Dutch, and US markets.

### Sector blocks — thresholds
One block per GICS sector. Each metric entry:

| Field | Type | Description |
|---|---|---|
| `metrica` | string | Internal key (e.g. `"roe"`, `"pe"`, `"fcf_yield"`) |
| `good` | number \| null | Optimal value → score 10; `null` = metric N/A for this sector |
| `bad` | number \| null | Worst value → score 0; `null` = metric N/A |
| `lower_is_better` | bool | `true` for multiples (PE, EV/EBITDA); `false` for returns/yields |

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

### Detail drawer

Each card opens a detail drawer with three sections:

- **Value** — EV/EBITDA, P/FCF, P/E, FCF Yield (+ P/Book for Financial Services)
- **Quality** — ROE, EBITDA Margin or ROA (Financial Services), ROIC, D/E, EPS CAGR 4Y
- **Momentum** — Mom. 12M−1M, EPS Revision, FCF Growth
- **Extra** — Gross Margin, Operating Margin, Profit Margin, Revenue Growth, ROA, Current Ratio, Dividend Yield, PEG, 52W Change, WACC, P/Book, Relative Strength
- **Score history** — Chart.js line chart with 4 datasets (Score Finale, Value, Quality, Momentum)

### Portfolio watchlist
- Click **★** on any card to add it to your portfolio
- Portfolio panel (bottom sheet) shows: BUY/HOLD/SELL counts, average score ring, pillar bar averages, aggregate metrics, sector breakdown, sortable ticker list
- Persists in `localStorage` (survives page refresh)

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
  valuta           TEXT,
  benchmark        TEXT,
  prezzo           NUMERIC(14,4),
  mktcap           BIGINT,
  -- Value
  ev_ebitda        NUMERIC(10,2),
  p_fcf            NUMERIC(10,2),
  pe               NUMERIC(10,2),
  p_book           NUMERIC(10,2),
  fcf_yield        NUMERIC(10,2),
  score_value      NUMERIC(5,2),
  -- Quality
  roe              NUMERIC(10,2),
  ebitda_margin    NUMERIC(10,2),
  gross_margin     NUMERIC(10,2),
  de_ratio         NUMERIC(10,2),
  eps_cagr_4y      NUMERIC(10,2),
  roic             NUMERIC(10,2),
  score_quality    NUMERIC(5,2),
  -- Momentum
  mom_12m1m        NUMERIC(10,2),
  eps_rev          NUMERIC(10,2),
  rel_strength     NUMERIC(10,2),
  fcf_growth       NUMERIC(10,2),
  score_momentum   NUMERIC(5,2),
  -- Final
  score_finale     NUMERIC(5,2),
  classificazione  TEXT,
  rank             INTEGER,
  -- Extra
  operating_margin NUMERIC(10,2),
  profit_margin    NUMERIC(10,2),
  rev_growth       NUMERIC(10,2),
  roa              NUMERIC(10,2),
  current_ratio    NUMERIC(10,2),
  dividend_yield   NUMERIC(10,4),
  peg              NUMERIC(10,2),
  week52_change    NUMERIC(10,2),
  wacc             NUMERIC(10,2),
  -- AI & errors
  commento_ai      TEXT,
  errore           TEXT
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
| `SCREENER_WORKERS` | | `6` | Unused — main fetch is sequential; reserved for future use |
| `SCREENER_BENCHMARK` | | `SWDA.MI` | Global fallback benchmark index |
| `ANALYSIS_LANGUAGE` | | `italian` | Language for AI commentary |
| `LLM_MODEL` | `--ai` only | `gpt-4o` | LLM model name |
| `LLM_API_KEY` | `--ai` only | — | OpenAI-compatible API key |
| `TAVILY_API_KEY` | `--ai` only | — | Tavily search API key |

Without `LLM_API_KEY` and `TAVILY_API_KEY`, the screener runs normally; `--ai` is silently skipped.

---

## Further reading

- [docs/metrics.md](docs/metrics.md) — complete metric definitions, formulas, fallback chains, and sector thresholds
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
