# Changelog

All notable changes to this project are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

---

## [1.1.0] — 2026-04-08

### Added
- **Screener-style fundamental metrics** — replaced all previous fundamental keys with the exact 22-metric set from the European stock screener: `pe_trailing`, `pe_forward`, `ev_ebitda`, `ev_sales`, `p_book`, `p_fcf`, `roe`, `roa`, `roic`, `fcf_conversion`, `fcf_margin`, `gross_margin`, `operating_margin`, `profit_margin`, `debt_ebitda`, `interest_coverage`, `current_ratio`, `quick_ratio`, `debt_equity`, `rev_growth_yoy`, `momentum_6m`, `momentum_12m`
- **Composite score (0–100)** — computed locally from raw yfinance data; no analyst estimates or third-party ratings used
- **Sector-aware scoring** — dedicated `_calc_score_bank()` model for `Financial Services` (banks/insurers); standard `_calc_score()` for all other sectors
- **Portfolio history** — `portfolio_<tickers>.json` now accumulates a full history under `{"history": [...]}` instead of overwriting the previous snapshot; existing flat-format files are auto-migrated
- **PDF Save As dialog** — a native `tkinter` Save As popup opens when the report is ready, letting the user choose the output path; closes gracefully and falls back to auto-generated filename if cancelled

### Changed
- `src/data_fetcher.py` — complete rewrite; removed `fetch_analyst_recommendations()` and `fetch_earnings_history()`; added `fetch_fundamentals()`, `_calc_score()`, `_calc_score_bank()`, and helpers (`_pct`, `_get_row`, `_calc_roic`, `_calc_debt_ebitda`, `_calc_interest_coverage`, `_calc_momentum`)
- `src/tools.py` — removed `get_analyst_recommendations` LangChain tool
- `src/agent.py` — updated `_FUND_KEYS`, removed analyst data from `_prefetch_stream` and `_build_data_context`; updated analysis prompt with bank vs non-financial scoring guidance
- `src/portfolio.py` — complete rewrite to support history accumulation and legacy flat-file migration
- `main.py` — removed `--output` CLI argument; added `_ask_save_path()` tkinter helper; fixed formatter-induced syntax error (three statements collapsed onto one line)
- `src/report.py` — `save_pdf()` now accepts optional `output_path` parameter
- `README.md` — major rewrite: new Fundamental Data & Scoring section with full metric tables, composite score formula, bank score table, portfolio history JSON example

### Removed
- `screener.py` — standalone screener script deleted (logic merged into `data_fetcher.py`)
- Optional fields `eps_growth_yoy`, `short_interest`, `peg` — never available for European tickers; silently omitted

---

## [1.0.0] — 2026-03-01

### Added
- Initial release: parallel yfinance + Tavily data fetch, LangChain agent with technical/fundamental/news tools, single-LLM-call report, PDF generation via xhtml2pdf, portfolio snapshot, `finanalysis.bat` Windows launcher
