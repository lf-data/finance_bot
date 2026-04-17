# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [3.3.0] — 2026-04-17

### Added
- **WACC** — Weighted Average Cost of Capital calculated per ticker and stored in DB + API + UI
  - `Re = Rf + β × ERP` (CAPM; ERP = 5.5% Damodaran EUR)
  - `Rd = abs(TTM Interest Expense) / Total Debt`, clamped 0.5%–20%
  - `WACC = (E/V)×Re + (D/V)×Rd×(1−T)`; debt-free case: WACC = Re
  - `_INT_EXP_KEYS` tuple: `"Interest Expense"`, `"Interest Expense Non Operating"`, `"Net Interest Income"`
  - 5-level interest expense fallback chain (see below)
- **ECB risk-free rate** — `_fetch_rf_rate()` fetches the ECB AAA sovereign EUR 8Y spot rate from the ECB SDW API (`data-api.ecb.europa.eu`); result cached 24 h in `_rf_cache`; clamped 0.5%–12%; fallback to last known value on error
- **WACC interest expense fallback chain**:
  1. Quarterly income stmt TTM (`_INT_EXP_KEYS`)
  2. `info["interestExpense"]` from Yahoo info dict
  3. `"Interest Paid Supplemental Data"` from quarterly cashflow (Salesforce / US GAAP companies that omit the I/S row)
  4. Annual income stmt (`_INT_EXP_KEYS`, n=1) — covers AAPL which does not report quarterly
  5. Annual cashflow `"Interest Paid Supplemental Data"` (annual fallback)
- **PEG manual fallback** — when Yahoo's `pegRatio` / `trailingPegRatio` are both `None`, computes `PEG = trailingPE / (earningsGrowth × 100)` (e.g. European tickers like IVG.MI)
- **ROIC** — `NOPAT / Invested Capital × 100`; `NOPAT = TTM EBIT × (1 − effective_tax_rate)`, `IC = Equity + Debt − Cash`
- **Effective tax rate** — derived from TTM (`Tax Provision / Pretax Income`), clamped 10%–40%; fallback to 24% (IRES proxy) when data unavailable
- **ROA** in Financial Services quality pillar — replaces `ebitda_margin` (structurally undefined for banks); thresholds: good ≥ 1.0%, bad ≤ 0.4%
- **`wacc` column** in `screener_results` DB schema (DDL + `ADD COLUMN IF NOT EXISTS` migration)
- **`wacc`** added to both `/api/latest` and `/api/ticker/<ticker>` SELECTs in `app.py`
- **WACC display** in Extra section of drawer in `app.js`
- **ROA / EBITDA Margin conditional** in Quality drawer: Financial Services → ROA; all others → EBITDA Margin

### Changed
- **FCF Yield** — removed `ttm_fcf > 0` guard; FCF Yield is now stored and displayed even when negative (e.g. Snam −4.1%)
- **P/FCF** — guard `ttm_fcf > 0` retained (negative P/FCF multiple has no valuation meaning)
- **FCF Growth** — added annual cashflow fallback (Path 2) for companies without quarterly cashflow (e.g. DBK.DE); uses `_mrq_nth` at index 1 to retrieve the prior-year annual figure
- **`_CAPEX_KEYS`** — expanded with `"Investments In Property Plant And Equipment"`, `"Capital Expenditure Reported"`, `"Net PPE Purchase And Sale"` to cover EU and IFRS reporting variants
- **Utilities `fcf_yield` threshold** — `bad` changed from `1.5` → `−5` to allow graduated scoring for companies with structurally negative FCF (capital-intensive infrastructure)
- **`_VQM_WEIGHTS`** — Value 30 % → 25 %, Quality 40 % → 50 %, Momentum 30 % → 25 % (calibrated for semi-annual review strategy)

---

## [3.2.0] — 2026-04-14

### Added
- **New sectors in `thresholds.json`**: Consumer Cyclical, Consumer Defensive, Industrials, Communication Services, Basic Materials — each with fully calibrated value / quality / momentum thresholds
- **`fcf_yield`** as Value metric for all non-Financial-Services sectors (replaces `p_book` which moved to Extra)
- **`roic`** in Quality pillar across all sectors
- **`fcf_growth`** in Momentum pillar across all sectors
- **Quick-metric chips** in card header — `.qm-strip` / `.qm-lbl` / `.qm-val` CSS classes added to `index.html`

### Changed
- Default ticker list in `thresholds.json` expanded to ~110 tickers covering FTSE MIB, DAX, CAC 40, IBEX, SMI, AEX, Euronext Lisbon + main US large-caps

---

## [3.1.0] — 2026-04-14

### Added
- **Web dashboard redesign** — full UI overhaul with Inter font (Google Fonts), deep `#09090f` background, layered surface palette, `#00d084` green accent with glow effects
- **Score ring glow** — SVG `drop-shadow` filter on both card rings and drawer hero ring, colour-matched to classification
- **Card entrance animation** — staggered `fadeUp` with per-card delay (capped at 300ms)
- **Nav stats strip** — live BUY / HOLD / SELL counters updated after every fetch
- **`.m-tile` / `.s-head` design system** — pure-CSS metric tiles and section headings used across drawer and portfolio panel
- **Portfolio panel redesign** — average-score ring in stats bar, coloured dot per ticker, inline `onmouseenter/leave` hover, no Tailwind class flipping
- **History chart gradient fill** — linear gradient from `rgba(0,208,132,.18)` to transparent under the Score Finale line

### Changed
- `filter-btn` active classes — moved to `f-active-{ALL|BUY|HOLD|SELL}` classes; background colour matches the signal (green/yellow/red)
- `pillarBar()` / `miniPillar()` — bar height reduced to 3–4 px for cleaner appearance; value label coloured to match pillar
- Drawer — separate `#drawer-subtitle` element (company name); score ring enlarged to 72 px with `/10` sub-label
- Portfolio panel — handle bar at top; "Svuota" button replaced with rounded pill, close button is circular icon
- `clsColor()` / `pillClass()` — extracted as module-level constants for reuse across all templates
- Chart tooltip — rounded corners (`cornerRadius:10`), explicit border; legend padding increased

---

## [3.0.0] — 2026-04-14

### Added
- **Flask web interface** (`app.py`) — serves a Tailwind CSS + Chart.js dashboard on port 5001
  - `GET /` — renders `index.html`
  - `GET /api/latest` — all results from the latest run, ordered by rank
  - `GET /api/tickers` — distinct tickers with most recent score
  - `GET /api/ticker/<ticker>` — full score history for one ticker
  - `GET /api/runs` — last 30 run metadata entries
- **`templates/index.html`** — responsive card grid, detail drawer (slide-in right panel), filter/sort toolbar
- **`static/app.js`** — `loadLatest()`, `renderCards()`, `openDrawer()`, `buildDrawerBody()`, `renderHistoryChart()` (Chart.js line chart with 4 datasets)
- **Portfolio feature** — `localStorage`-backed watchlist; star/bookmark toggle on every card; bottom-sheet portfolio panel with signal distribution, avg pillar scores, aggregate metrics, sector breakdown, sortable ticker list
- **APScheduler integration** — `BackgroundScheduler` runs screener daily at midnight; also fires once at server startup in a daemon thread; `atexit` hook ensures clean shutdown; `use_reloader=False` prevents duplicate scheduler on Flask dev reloader
- **Today-run cache** — `db.load_today_run()` returns the existing run for the current calendar day; `screener.py` skips re-fetching if a run is already present (override with `--force`)
- **`_normalize()` helper** in `app.py` — converts `decimal.Decimal` → `float` and `date`/`datetime` → ISO string to avoid Flask JSON serialization errors

### Changed
- `screener.py` — removed JSON file export (`export_json`, `_ask_save_path`, `--out` flag); results are now persisted exclusively to PostgreSQL
- `app.py` — `use_reloader=False` enforced when run directly to avoid duplicate scheduler instances

### Fixed
- `Decimal` serialization bug — psycopg2 returns `NUMERIC` columns as Python `Decimal`; `_normalize()` converts them before `jsonify`

---

## [2.9.0] — 2026-04-13

### Added
- **PostgreSQL persistence** — `db.py` module with `ensure_db()`, `ensure_schema()`, `save_run()`, `load_today_run()`
  - `screener_runs` table: `id, run_at, run_date, benchmark, ai_enabled, n_tickers`
  - `screener_results` table: full VQM metric set + pillar scores + rank + AI comment + error field
  - Indexes on `ticker`, `run_date`, `run_id`
- **`POSTGRES_*` variables** added to `config.py` and `.env.example`

### Changed
- `save_run()` — uses `_clean()` to normalise `Decimal` / `NaN` / `inf` before insert; returns `run_id`

### Removed
- `target_price` metric removed from both `screener.py` fetch and `screener_results` DB schema
- JSON file export path removed entirely (replaced by PostgreSQL)

### Fixed
- `psycopg2.extensions.AsIs` `TypeError` in `ensure_db()` — replaced with plain f-string

---

## [2.8.0] — 2026-04-12

### Added
- **Auto benchmark detection** — `_benchmark_for_ticker(ticker, override)` maps ticker suffix to market index:
  `.MI` → `FTSEMIB.MI`, `.DE/.F/.BE` → `^GDAXI`, `.PA` → `^FCHI`, `.L` → `^FTSE`, `.MC` → `^IBEX`, `.SW` → `^SSMI`, `.AS` → `^AEX`, no suffix → `SPY`
- `_SUFFIX_BENCHMARK` dict — lookup table for suffix → benchmark string
- `benchmark_override: str | None = None` parameter on `run_screener()` — replaces the previous fixed benchmark string

### Changed
- `run_screener()` — benchmark is now resolved per-ticker via `_benchmark_for_ticker()`; header shows "auto (per nazione)" when no override is set
- `--benchmark` CLI flag — defaults to `None` instead of a fixed string; `None` activates auto-detection
- CLI output header — shows resolved benchmark or "auto (per nazione)"

---

## [2.2.0] — 2026-04-10

### Added
- **`thresholds.json`** — external configuration file for the entire VQM model:
  - `"pesi"` block: pillar weights (value / quality / momentum), editable without touching the code
  - `"tickers"` block: default ticker list, replaces the hardcoded `DEFAULT_TICKERS` list in `screener.py`
  - Sector blocks: all sector thresholds, same data as before but now human-editable JSON
- **Save As dialog** — native OS file picker (tkinter) opens before the run to let the user choose the JSON output path; cancelling falls back to `--out`
- **`SCREENER_BENCHMARK`** and **`SCREENER_WORKERS`** added to `config.py` as `.env`-configurable settings

### Changed
- `screener.py` — `_load_vqm_config()` replaces the hardcoded `_THRESHOLDS` dict and `DEFAULT_TICKERS` list; both are now loaded from `thresholds.json` at startup
- `calc_vqm_score()` — weights are now read from `_VQM_WEIGHTS` (loaded from JSON) instead of hardcoded default parameters
- `_get_thresholds()` — simplified: redundant `_SECTOR_MAP` dict removed, direct lookup into `_THRESHOLDS`
- `config.py` — removed dead variables: `LLM_MAX_TOKENS`, `HISTORY_PERIOD`, `HISTORY_INTERVAL`, `BASE_DIR`, `REPORT_DIR`; added `SCREENER_BENCHMARK`, `SCREENER_WORKERS`
- `import math` moved to top-level (was re-imported inside `_clean()` on every call)
- Default pillar weights updated to **Value 30% / Quality 50% / Momentum 20%** — calibrated for a semi-annual portfolio review strategy

### Fixed
- **`dividend_yield` multiplier bug** — Yahoo Finance already returns this field as a percentage (e.g. `4.42` = 4.42%); the erroneous `× 100` has been removed

### Removed
- `analyst_buy` (`numberOfAnalystOpinions`) and `recommendation` (`recommendationKey`) removed from both `fetch_metrics()` and `_EXTRA_KEYS`

---

## [2.1.0] — 2026-04-10

### Added
- **`screener.py`** as sole entry point replacing `main.py`
- **Parallel fetch** — `ThreadPoolExecutor` (6 workers by default)
- **Colorama CLI** — header `◆`, `●` per completed ticker, `✗` error, `⟳` live status line
- **Positional tickers** — `python screener.py ISP.MI UCG.MI` (replaces `--tickers` comma-separated)
- **`--ai` flag** — optional AI commentary per ticker: Tavily search + LLM (2–3 sentences), stored as `commento_ai` in JSON
- **Structured JSON output** — ranking, per-pillar sub-scores, extra metrics, missing-metrics map, run metadata
- **Footer with timing** — `⏱ Xs  Screener completed.`
- **TOP 10 coloured summary** — BUY green, HOLD yellow, SELL red

### Changed
- `finanalysis.bat` — updated to launch `screener.py`; passes all arguments through
- `requirements.txt` — removed `markdown` and `xhtml2pdf`
- `README.md` — fully rewritten to reflect new architecture

### Removed
- `main.py` — replaced by `screener.py`
- `src/` — entire directory removed: `agent.py`, `report.py`, `portfolio.py`, `tools.py`, `data_fetcher.py`, `indicators.py`
- `reports/` — output directory removed (replaced by single JSON file)
- PDF dependencies: `markdown>=3.5.0`, `xhtml2pdf>=0.2.11`

---

## [1.1.0] — 2026-04-08

### Added
- **`screener.py`** (standalone) — VQM screener with yfinance fetch, 0–10 per-pillar scoring, structured JSON output
- **12 VQM core metrics** — EV/EBITDA, P/FCF, P/E, P/Book (Value); ROE, EBITDA Margin, Gross Margin, D/E Ratio, EPS CAGR 5Y (Quality); Mom. 12M-1M, EPS Rev. proxy, Rel. Strength (Momentum)
- **7 GICS sector threshold groups** — Financial Services, Real Estate, Utilities, Energy, Technology, Healthcare, `_default`
- **Multiple fallbacks** — EBITDA Margin (`ebitda/totalRevenue`), D/E from balance sheet, D/E ×100 normalisation, EPS Rev proxy chain
- **Extra metrics in JSON** — operating_margin, profit_margin, rev_growth, roa, current_ratio, dividend_yield, peg, 52w_change, target_price
- **`metriche_mancanti`** map in JSON — ticker → list of missing VQM metrics
- **Excel template (`create_sheet.py`)** — generates `StockPicking_VQM_Framework.xlsx` with 10 GICS sectors, sector dropdown, dynamic thresholds via `INDIRECT`

### Changed
- `create_sheet.py` — metrics updated: EV/FCF→EV/EBITDA, ROA→EBITDA Margin, FCF Margin→Gross Margin, Net Debt/Eq.→D/E Ratio; fixed `SECTOR_SHEET_ROWS` offsets; fixed `row += 2` in `add_numeric_thresholds`

---

## [1.0.0] — 2026-03-01

### Added
- Initial release: parallel yfinance + Tavily fetch, LangChain agent with technical/fundamental/news tools, single LLM call for full report, PDF generation via xhtml2pdf, portfolio snapshot, `finanalysis.bat` launcher

