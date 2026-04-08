# Finance Bot — Investment Analysis Agent

An AI-powered investment analysis tool that fetches real-time market data, computes screener-style fundamental metrics with a composite score, performs technical analysis, searches macro and per-ticker news, and generates a comprehensive **investment report in PDF format** — without any human interaction.

---

## Features

- **Parallel data fetch** — price history, fundamental metrics, and news for all tickers simultaneously
- **Screener-style scoring** — every ticker receives a composite score 0–100 computed from raw yfinance data (no analyst estimates, no third-party ratings)
- **Sector-aware scoring** — separate scoring model for Financial Services (banks/insurers) vs all other sectors
- **Per-ticker news search** — direct Tavily web search for each stock (no LLM intermediary)
- **Macro context search** — a single LLM-guided Tavily search covering rates, inflation, geopolitics, and sector trends
- **Single LLM call** for the full report — no interactive chat, no multi-turn overhead
- **Structured PDF report** — styled A4 document generated from the LLM's Markdown output
- **Portfolio snapshot** — previous allocation is persisted and injected into the next run's prompt, so the model can track changes (`NEW`, `↑`, `↓`, `EXIT`)
- **Sector exclusion** — the model evaluates macro/news context and automatically excludes entire sectors when conditions are unfavourable
- **`<think>` block stripping** — reasoning tokens emitted by OpenAI `o`-series models are filtered before the report is written

---

## Project Structure

```
finance_bot/
├── main.py               # Entry point — CLI runner
├── finanalysis.bat        # Windows launcher (no conda activation needed)
├── config.py             # All configuration loaded from .env
├── requirements.txt
├── .env                  # Environment variables (not committed)
├── reports/              # Generated PDFs and portfolio snapshot
│   ├── *.pdf
│   └── portfolio_snapshot.json
└── src/
    ├── agent.py          # Core analysis agent
    ├── data_fetcher.py   # yfinance fetch + metric computation + composite score
    ├── indicators.py     # Technical indicator computation
    ├── tools.py          # LangChain tools (technical, fundamental, news)
    ├── portfolio.py      # Snapshot save/load and prompt formatting
    └── report.py         # Markdown → HTML → PDF conversion
```

---

## How It Works

Each run executes three sequential phases:

### Phase 1 — Parallel Data Fetch
For every ticker, the following is fetched concurrently (up to 6 workers):

| Data | Source | Details |
|---|---|---|
| Price history | yfinance | 1 year daily OHLCV |
| Technical indicators | Computed locally | SMA 20/50/200, Bollinger Bands, RSI(14), MACD(12/26/9), ATR(14), volume ratio, 52-week range |
| Fundamental metrics + score | yfinance | See [Fundamental Data & Scoring](#fundamental-data--scoring) below |
| Ticker news | Tavily API | Direct search: latest news, earnings, analyst outlook for each symbol |

### Phase 2 — Macro Context Search
A single LangChain agent with access to `search_news` performs one broad Tavily query covering:
- Central bank policy and interest rates
- Inflation and GDP cycle
- Geopolitical risks
- Earnings season sentiment
- Sector-specific regulatory or structural events

The result is 6–8 structured bullet points (`[MACRO]`, `[SECTOR]`, `[RISK]`, etc.) that are passed verbatim to the analysis prompt.

### Phase 3 — LLM Analysis & Report Generation
All pre-fetched data is serialised into a compact context block and sent to the LLM in a single `invoke()` call. The model produces a full Markdown report with five sections:

1. **Macro & Sector Context** — narrative interpretation of current macro conditions
2. **Individual Ticker Analysis** — for each ticker:
   - Technical Outlook (trend, key levels, momentum)
   - Fundamental Outlook (valuation, profitability, balance sheet — metrics appropriate to the sector)
   - Catalysts & Risks (from the ticker's news feed)
   - **Verdict**: `STRONG BUY` / `BUY` / `ACCUMULATE` / `HOLD` / `REDUCE` / `AVOID`
3. **Portfolio Allocation** — max 5 highest-conviction tickers rated BUY or better, with sector exclusion if macro/news conditions warrant it. Includes a `vs Previous` column tracking changes from the prior run.
4. **Portfolio Rationale** — construction logic, primary thesis, risk mitigation, revision triggers
5. **Monitoring Checklist** — 4–6 specific events/indicators to track over the next 3–6 months

The Markdown is then rendered to a **styled PDF** and saved to `reports/`.

---

## Fundamental Data & Scoring

All fundamental data is sourced exclusively from **yfinance** (`yf.Ticker.info`, `balance_sheet`, `income_stmt`). No analyst estimates, broker ratings, or third-party consensus data are used.

### Metrics collected

#### Basic info
| Field | Source | Description |
|---|---|---|
| `nome` | `shortName` | Company name |
| `settore` | `sector` | Yahoo Finance sector classification |
| `mktcap` | `marketCap` | Market capitalisation |

#### Valuation
| Field | Source | Description |
|---|---|---|
| `pe_trailing` | `trailingPE` | Price / trailing 12-month EPS |
| `pe_forward` | `forwardPE` | Price / next 12-month EPS estimate |
| `ev_ebitda` | `enterpriseToEbitda` | Enterprise Value / EBITDA |
| `ev_sales` | `enterpriseToRevenue` | Enterprise Value / Revenue |
| `p_book` | `priceToBook` | Price / Book Value |
| `p_fcf` | computed | Market Cap / Free Cash Flow (omitted if FCF ≤ 0) |

#### Capital efficiency
| Field | Source | Description |
|---|---|---|
| `roe` | `returnOnEquity` × 100 | Return on Equity % |
| `roa` | `returnOnAssets` × 100 | Return on Assets % |
| `roic` | computed | Net Income / (Total Assets − Current Liabilities) × 100 |

#### Earnings quality
| Field | Source | Description |
|---|---|---|
| `fcf_conversion` | computed | Free Cash Flow / Net Income × 100 |
| `fcf_margin` | computed | Free Cash Flow / Revenue × 100 |
| `gross_margin` | `grossMargins` × 100 | Gross Margin % |
| `operating_margin` | `operatingMargins` × 100 | Operating Margin % |
| `profit_margin` | `profitMargins` × 100 | Net Profit Margin % |

#### Financial solidity
| Field | Source | Description |
|---|---|---|
| `debt_ebitda` | computed | (Total Debt − Cash) / EBITDA |
| `interest_coverage` | computed | EBIT / \|Interest Expense\| (from `income_stmt`) |
| `current_ratio` | `currentRatio` | Current Assets / Current Liabilities |
| `quick_ratio` | `quickRatio` | (Current Assets − Inventory) / Current Liabilities |
| `debt_equity` | `debtToEquity` | Total Debt / Equity |

#### Growth
| Field | Source | Description |
|---|---|---|
| `rev_growth_yoy` | `revenueGrowth` × 100 | Revenue growth year-over-year % |

#### Momentum (computed from price history)
| Field | Method | Description |
|---|---|---|
| `momentum_6m` | `(P_last / P_first − 1) × 100` | 6-month price return % |
| `momentum_12m` | `(P_last / P_first − 1) × 100` | 12-month price return % |

> **Missing values:** if a field cannot be fetched (e.g. banks have no EBITDA or FCF), it is simply omitted. The scoring functions skip `None` values and normalise the final score over the weights that were actually present — so a ticker with fewer available metrics is not penalised.

---

### Composite score (0–100)

The score is computed differently depending on the sector, because standard industrial metrics are structurally unavailable for banks and insurers.

#### Standard score — all sectors except Financial Services

Six equally-weighted pillars (theoretical total = 100 pts):

| Pillar | Metrics | Weight |
|---|---|---|
| **Capital efficiency** | ROIC (15), ROE (10) | 25 |
| **Earnings quality** | FCF conversion (12), FCF margin (8) | 20 |
| **Financial solidity** | Net Debt/EBITDA ↓ (10), Interest coverage (10) | 20 |
| **Valuation** | EV/EBITDA ↓ (8), P/FCF ↓ (7) | 15 |
| **Growth** | Revenue growth (5), Gross margin (5) | 10 |
| **Momentum** | 6m return (5), 12m return (5) | 10 |

Each metric is min-max normalised to [0, 1] within calibrated ranges. Lower-is-better metrics (`↓`) are inverted. The final score is:

$$\text{score} = \frac{\sum_{i} \text{norm}_i \times w_i}{\sum_{i} w_i} \times 100$$

where the sum runs only over metrics that are not `None`.

#### Bank score — Financial Services sector

Standard EBITDA/FCF/current ratio metrics do not apply to banks and insurers. A dedicated four-pillar model is used instead:

| Pillar | Metrics | Weight |
|---|---|---|
| **Valuation** | P/E trailing ↓ (20), P/Book ↓ (15) | 35 |
| **Profitability** | ROE (20), Net profit margin (15) | 35 |
| **Growth** | Revenue growth (15) | 15 |
| **Momentum** | 6m return (8), 12m return (7) | 15 |

---

## Portfolio Snapshot

After each run, the allocation table is parsed and **appended** to `reports/portfolio_<tickers>.json` as a new history entry:

```json
{
  "history": [
    {
      "date": "April 08, 2026",
      "positions": [
        { "ticker": "UCG.MI", "rating": "BUY", "weight": "25%", "target": "82.42" },
        ...
      ]
    },
    {
      "date": "April 15, 2026",
      "positions": [ ... ]
    }
  ]
}
```

Each run adds a new entry — nothing is ever overwritten. The full history is yours to review; the **LLM only ever sees the most recent entry** (the last element of `history`) to avoid inflating the context window.

Existing files in the old flat format (`{"date": ..., "positions": [...]}`) are automatically migrated to the new format on the next run.

| Symbol | Meaning |
|---|---|
| `NEW` | Not in previous portfolio |
| `=` | Weight unchanged |
| `↑` | Weight increased |
| `↓` | Weight decreased |
| `EXIT` | Was in previous portfolio, now excluded |
| `N/A` | No previous snapshot exists |

---

## Installation

### 1. Clone and create a conda environment

```bash
git clone <repo-url>
cd finance_bot
conda create -p .conda python=3.12 -y
conda activate ./.conda
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure `.env`

Create a `.env` file in the project root:

```env
# LLM — OpenAI API (https://platform.openai.com/api-keys)
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-your-openai-api-key-here
LLM_MAX_TOKENS=4096

# Tavily web search (https://tavily.com)
TAVILY_API_KEY=tvly-...

# Default tickers (comma-separated, overridable via CLI)
TICKERS=AAPL,MSFT,GOOGL,NVDA,TSLA

# Report output directory (relative to project root)
REPORT_DIR=reports

# Language for the report
ANALYSIS_LANGUAGE=italian

# Price history settings
HISTORY_PERIOD=1y
HISTORY_INTERVAL=1d
```

> **Tavily:** Free tier available at [tavily.com](https://tavily.com). Without a key, news search is silently skipped and the report is generated from quantitative data only.

---

## Usage

```bash
# Analyse the default tickers from .env
python main.py

# Override tickers for this run
python main.py AAPL MSFT NVDA
python main.py ISP.MI ENI.MI ENEL.MI UCG.MI
```

When the PDF is ready, a native **Save As** dialog opens so you can choose where to save it.
If you close the dialog without choosing a path, the file is saved automatically in the `reports/` folder with an auto-generated name.

### Windows — `finanalysis.bat`

Double-click `finanalysis.bat` or call it from CMD/PowerShell without activating the conda environment manually:

```bat
# Default tickers
finanalysis

# Override tickers
finanalysis AAPL MSFT NVDA
finanalysis ISP.MI ENI.MI ENEL.MI UCG.MI
```

The script resolves the Python interpreter from the local `.conda` folder automatically (`%SCRIPT_DIR%.conda\python.exe`).

#### Aggiungere alle variabili d'ambiente di Windows (PATH)

Per poter eseguire `finanalysis` da qualsiasi cartella:

1. Apri **Impostazioni di sistema avanzate** → scheda **Avanzate** → **Variabili d'ambiente**
2. In **Variabili utente**, seleziona `Path` e clicca **Modifica**
3. Aggiungi il percorso della cartella del progetto
4. Clicca OK e apri un nuovo CMD

Dopo di che sarà disponibile globalmente:

```bat
finanalysis AAPL MSFT NVDA
```

### Console output

```
◆  Finance Bot — Investment Analysis
──────────────────────────────────────────────────────────────
Tickers:  ISP.MI  ENI.MI  ENEL.MI

  ●  ISP.MI  (1.3s)
  ●  ENI.MI  (1.5s)
  ●  ENEL.MI (1.2s)
  ✓  Ricerca notizie macro
  ⟳  Sto analizzando il portafoglio…
  ⏱ 48.2s  ↑4120 in  ↓9340 out  = 13460 tok
  ✓  Report PDF salvato: C:\...\reports\April_08_2026_3tickers.pdf
  ✓  Snapshot portafoglio aggiornato
──────────────────────────────────────────────────────────────
  Analisi completata.
```

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `LLM_MODEL` | `gpt-4o` | OpenAI model identifier |
| `LLM_API_KEY` | *(required)* | OpenAI API key (`sk-...`) |
| `LLM_MAX_TOKENS` | `4096` | Token cap for the macro news graph LLM |
| `TAVILY_API_KEY` | *(empty)* | Tavily search API key |
| `TICKERS` | `AAPL,MSFT,GOOGL,NVDA,TSLA` | Default ticker list |
| `ANALYSIS_LANGUAGE` | `italian` | Language for the report text |
| `HISTORY_PERIOD` | `1y` | yfinance history period |
| `HISTORY_INTERVAL` | `1d` | yfinance history interval |
| `REPORT_DIR` | `reports` | Output directory for PDFs and snapshot |

---

## Technical Indicators Computed

| Indicator | Parameters |
|---|---|
| Simple Moving Average | 20, 50, 200 periods |
| Bollinger Bands | 20 periods, 2σ |
| RSI | 14 periods |
| MACD | 12/26/9 |
| ATR | 14 periods |
| Volume ratio | Current vs 20-period average |
| 52-week high/low | — |
| Price vs SMA distance | % deviation from SMA20/50/200 |
| Golden/Death cross | SMA50 vs SMA200 |

---

## Dependencies

| Package | Purpose |
|---|---|
| `yfinance` | Price history, fundamentals, analyst data |
| `pandas`, `numpy` | Data processing and indicator computation |
| `langchain`, `langchain-openai` | LLM agent framework |
| `tavily-python` | Web search for news |
| `python-dotenv` | `.env` file loading |
| `colorama` | Coloured console output |
| `markdown` | Markdown → HTML conversion |
| `xhtml2pdf` | HTML → PDF rendering (pure Python, no system deps) |

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

---

## License

This project is released under the [MIT License](LICENSE).
