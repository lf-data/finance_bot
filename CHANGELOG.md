# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

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

