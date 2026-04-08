# Finance Bot — Investment Analysis Agent

An AI-powered investment analysis tool that fetches real-time market data, performs technical and fundamental analysis on a list of stocks, searches macro and per-ticker news, and generates a comprehensive **investment report in PDF format** — without any human interaction.

---

## Features

- **Parallel data fetch** — price history, fundamentals, analyst ratings, and earnings for all tickers simultaneously
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
    ├── data_fetcher.py   # yfinance wrappers
    ├── indicators.py     # Technical indicator computation
    ├── tools.py          # LangChain tools (technical, fundamental, ratings, news)
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
| Fundamentals | yfinance | P/E, forward P/E, PEG, EPS, revenue/earnings growth, margins, ROE, D/E, beta, analyst targets |
| Analyst ratings | yfinance | 10 most recent broker ratings |
| Earnings history | yfinance | Last 8 quarters (EPS actual vs estimate) |
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
   - Fundamental Outlook (valuation, profitability, balance sheet)
   - Analyst Consensus (rating, price target, recent changes)
   - Catalysts & Risks (from the ticker's news feed)
   - **Verdict**: `STRONG BUY` / `BUY` / `ACCUMULATE` / `HOLD` / `REDUCE` / `AVOID`
3. **Portfolio Allocation** — max 5 highest-conviction tickers rated BUY or better, with sector exclusion if macro/news conditions warrant it. Includes a `vs Previous` column tracking changes from the prior run.
4. **Portfolio Rationale** — construction logic, primary thesis, risk mitigation, revision triggers
5. **Monitoring Checklist** — 4–6 specific events/indicators to track over the next 3–6 months

The Markdown is then rendered to a **styled PDF** and saved to `reports/`.

---

## Portfolio Snapshot

After each run, the allocation table is parsed and saved to `reports/portfolio_snapshot.json`:

```json
{
  "date": "April 08, 2026",
  "positions": [
    { "ticker": "AAPL", "rating": "BUY", "weight": "25%", "target": "$210" },
    ...
  ]
}
```

On the **next run**, this snapshot is loaded and appended to the system prompt. The LLM then fills the `vs Previous` column in the allocation table:

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
