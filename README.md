# Finance Bot — VQM Screener

A quantitative stock screener for Italian (and international) equities built on the **Value / Quality / Momentum** framework.
Scoring is fully deterministic and reproducible; AI is an optional narrative layer that comments on results — it does not drive them.

---

## Features

- **Parallel fetch** — up to N workers downloading tickers simultaneously via `ThreadPoolExecutor`
- **VQM score 0–10** — completely deterministic, sector-calibrated thresholds, no analyst estimates
- **12 core metrics** across 3 pillars: Value, Quality, Momentum
- **7 GICS sector groups** with calibrated thresholds: Financial Services, Real Estate, Utilities, Energy, Technology, Healthcare, default
- **Multiple fallbacks** to maximise coverage on European tickers (EBITDA margin, D/E, EPS CAGR, EPS Rev)
- **Optional AI commentary** (`--ai`) — Tavily news search + LLM generates 2–3 sentences of context per ticker
- **Structured JSON output** — ranking, per-pillar sub-scores, extra metrics, missing-metrics map
- **External config via `thresholds.json`** — sector thresholds, pillar weights and default ticker list are all editable without touching the code
- **`finanalysis.bat`** — Windows launcher, no manual conda activation required
- **Save As dialog** — native file picker appears before the run to choose the output path

---

## Project structure

```
finance_bot/
├── screener.py          # Main entry point — VQM Screener
├── thresholds.json      # Sector thresholds, pillar weights, default tickers
├── finanalysis.bat      # Windows launcher (no conda activation needed)
├── config.py            # Runtime config from .env (API keys, language, workers)
├── create_sheet.py      # Generates the manual VQM Excel template
├── requirements.txt
└── .env                 # Environment variables (not committed)
```

---

## How it works

Every run follows three phases.

### Phase 1 — Parallel fetch
All tickers are fetched in parallel (configurable workers) from **Yahoo Finance via yfinance**:
- valuation, quality, margin and leverage metrics
- monthly price history for momentum calculation
- extra fields: target price, dividend yield, PEG, 52-week change, etc.

### Phase 2 — Deterministic VQM scoring
Each metric is normalised to a **0–10** scale by `_score_metric()`, then averaged within each pillar:

| Pillar | Default weight | Metrics |
|---|---|---|
| **Value** | 30 % | EV/EBITDA, P/FCF, P/E, P/Book |
| **Quality** | 50 % | ROE, EBITDA Margin, Gross Margin, D/E Ratio, EPS CAGR 5Y |
| **Momentum** | 20 % | Mom. 12M-1M, EPS Rev. proxy, Rel. Strength vs benchmark |

> Weights are read from `thresholds.json` → `"pesi"` and can be changed without editing the code.

Metrics marked N/A for a sector (e.g. EV/EBITDA for banks) are excluded without penalising the final score.

**Classification thresholds:**

| Score | Label |
|---|---|
| ≥ 7.5 | **BUY** |
| 5.0 – 7.4 | **HOLD** |
| < 5.0 | **SELL** |

### Phase 3 — AI commentary (optional, `--ai`)
For each valid ticker:
1. **Tavily** searches for recent news (earnings, analyst outlook, events)
2. **LLM** (configured in `.env`) writes 2–3 contextual sentences based on VQM data + news

The comment is stored in the JSON as `commento_ai`. The quantitative logic is unchanged — AI is support, not source.

---

## Metrics and fallbacks

All data comes from **yfinance** (`yf.Ticker.info`, `balance_sheet`, `income_stmt`). No third-party ratings, no analyst estimates.

### Core VQM metrics

#### Value
| Field | Source | Notes |
|---|---|---|
| `ev_ebitda` | `enterpriseToEbitda` | N/A for Financial Services |
| `p_fcf` | `marketCap / freeCashflow` | Computed; omitted if FCF ≤ 0; N/A for Financial Services |
| `pe` | `trailingPE` | N/A for Real Estate |
| `p_book` | `priceToBook` | N/A for Technology |

#### Quality
| Field | Source | Notes |
|---|---|---|
| `roe` | `returnOnEquity × 100` | |
| `ebitda_margin` | `ebitdaMargins × 100` → fallback `ebitda / totalRevenue` | N/A for Financial Services |
| `gross_margin` | `grossMargins × 100` | N/A for Financial Services, REIT, Utilities |
| `de_ratio` | `debtToEquity` → fallback from balance sheet | N/A for Financial Services; normalised if Yahoo returns ×100 |
| `eps_cagr_5y` | `income_stmt Net Income / shares` → proxy `earningsGrowth` | |

#### Momentum
| Field | Source | Notes |
|---|---|---|
| `mom_12m1m` | Monthly prices over 13M (excludes last month) | Standard cross-sectional momentum |
| `eps_rev` | `earningsQuarterlyGrowth` → `earningsGrowth` → `forwardEps / trailingEps` | Analyst revision proxy |
| `rel_strength` | 12M ticker return − 12M benchmark return | |

### Extra metrics (JSON only, not scored)
`operating_margin`, `profit_margin`, `rev_growth`, `roa`, `current_ratio`, `dividend_yield`, `peg`, `52w_change`, `target_price`

---

## thresholds.json

This file is the single configuration file for the screener model. It has three top-level sections:

### `"pesi"` — pillar weights
```json
"pesi": {
  "value":    0.30,
  "quality":  0.50,
  "momentum": 0.20
}
```
Must sum to 1.0. Calibrated for a **semi-annual review** strategy (quality-dominant, momentum still relevant).

### `"tickers"` — default ticker list
```json
"tickers": ["ISP.MI", "UCG.MI", "ENI.MI", ...]
```
Used when `screener.py` is run without explicit ticker arguments.

### Sector blocks — thresholds
One block per GICS sector group. Each metric entry has four fields:

| Field | Type | Description |
|---|---|---|
| `metrica` | string | Internal metric key (e.g. `"roe"`, `"pe"`) |
| `good` | number or null | Optimal threshold → score 10; `null` = N/A for this sector |
| `bad` | number or null | Worst threshold → score 0 |
| `lower_is_better` | bool | `true` for multiples (P/E, D/E), `false` for returns/margins |

To add a new sector, copy the `"_default"` block, rename the key to match the exact string returned by yfinance `info["sector"]`, and adjust the thresholds.

---

## JSON output

```json
{
  "metadata": {
    "generated_on": "2026-04-10",
    "benchmark": "FTSEMIB.MI",
    "n_tickers": 34,
    "ai_commentary": false,
    "pesi_score": {"value": 0.30, "quality": 0.50, "momentum": 0.20},
    "thresholds": {"BUY": ">=7.5", "HOLD": "5.0-7.4", "SELL": "<5.0"}
  },
  "ranking": [
    {
      "rank": 1,
      "ticker": "UCG.MI",
      "nome": "UniCredit S.p.A.",
      "settore": "Financial Services",
      "prezzo": 42.5,
      "value":    {"pe": 9.5, "p_book": 0.95, "score": 9.2},
      "quality":  {"roe": 15.2, "eps_cagr_5y": 12.1, "score": 8.8},
      "momentum": {"mom_12m1m": 45.2, "rel_strength": 22.1, "score": 9.1},
      "score_finale": 9.0,
      "classificazione": "BUY",
      "extra": {"dividend_yield": 6.77, "target_price": 52.0}
    }
  ],
  "riepilogo": {"BUY": ["UCG.MI", "ISP.MI"], "HOLD": ["..."], "SELL": ["..."]},
  "metriche_mancanti": {"ISP.MI": ["ev_ebitda", "p_fcf"]}
}
```

> `null` values are metrics unavailable for that ticker/sector. They do not penalise the score.

---

## Configuration (`.env`)

```env
# Required for --ai
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o

# Required for --ai (news search)
TAVILY_API_KEY=tvly-...

# Optional
ANALYSIS_LANGUAGE=english
TICKERS=ISP.MI,UCG.MI,ENI.MI      # overrides thresholds.json tickers
SCREENER_BENCHMARK=FTSEMIB.MI
SCREENER_WORKERS=6
```

Without `LLM_API_KEY` and `TAVILY_API_KEY` the screener works normally; `--ai` is skipped with a warning.

---

## Installation

```bash
conda create -p .conda python=3.12 -y
conda activate ./.conda
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys
```

---

## Usage

```bash
# All defaults (tickers from thresholds.json)
python screener.py
finanalysis.bat

# Explicit tickers
python screener.py ISP.MI UCG.MI ENI.MI
finanalysis.bat ISP.MI UCG.MI ENI.MI

# With AI commentary
python screener.py --ai
finanalysis.bat --ai

# Full options
python screener.py ISP.MI --ai --out results.json --benchmark SPY
```

A **Save As dialog** opens before the run to let you choose the output file path. Cancelling falls back to `--out` (default: `screener_vqm.json`).

---

## Dependencies

| Package | Purpose |
|---|---|
| `yfinance` | Fundamentals and price history |
| `pandas` | Data processing and balance sheet fallbacks |
| `langchain`, `langchain-openai` | LLM for AI commentary (`--ai`) |
| `tavily-python` | News search for AI commentary (`--ai`) |
| `python-dotenv` | `.env` loading |
| `colorama` | Coloured console output |

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

---

## License

Released under the [MIT License](LICENSE).
