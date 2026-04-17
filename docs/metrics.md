# VQM Metrics — Definitions, Formulas & Fallback Chains

This document describes every metric computed by `screener.py`: what it measures, how it is calculated, and the full fallback chain used when primary data is unavailable.

---

## Data sources and conventions

**Primary source:** Yahoo Finance via `yfinance`.

For each ticker, the screener downloads:

| Statement | Variable | Used for |
|---|---|---|
| `quarterly_income_stmt` | `inc_q` | TTM revenue, EBITDA, margins, interest, taxes |
| `quarterly_cashflow` | `cf_q` | TTM OCF, CapEx, interest paid |
| `quarterly_balance_sheet` | `bs_q` | MRQ equity, debt, cash, assets |
| `income_stmt` (annual) | `inc_a` | EPS CAGR fallback; WACC interest fallback |
| `cashflow` (annual) | `cf_a` | FCF Growth fallback; WACC interest fallback |
| `balance_sheet` (annual) | `bs_a` | Fallback when quarterly BS empty |
| `t.info` | `info` | Metadata + last-resort pre-calculated ratios |

**Priority rule:** quarterly statements are preferred. Annual statements are used only when quarterly is `None` or empty. `info` dict values are the final fallback for any metric.

**TTM calculation:** sum of the 4 most recent quarterly periods (`iloc[:4].sum()` on the row, after `dropna()`). If only annual data is available, `n=1` (single period).

**MRQ:** the most recent value from a balance-sheet row (`iloc[0]` after `dropna()`).

**Sign conventions in yfinance:**
- Capital Expenditure is reported as **negative** in cashflow statements → `FCF = OCF + CapEx` (addition, not subtraction)
- Interest Expense is reported as **negative** in income statements → `abs()` is applied before use

---

## Metric categories

Metrics are grouped into four categories:

| Category | Pillar | Scored in VQM? |
|---|---|---|
| Value | VALUE | Yes |
| Quality | QUALITY | Yes |
| Momentum | MOMENTUM | Yes |
| Extra | — | No (display only) |

---

## VALUE metrics

### P/E — Price / Earnings

**Pillar:** VALUE  
**Direction:** lower is better

**Formula:**
$$P/E = \frac{\text{Market Cap}}{\text{TTM Net Income}}$$

**Fallback chain:**
1. `mktcap / ttm_ni` where `ttm_ni` = TTM sum of `"Net Income"` / `"Net Income Common Stockholders"` / `"Net Income From Continuing Operations"` from quarterly income stmt (or annual)
2. `info["trailingPE"]`

**Guard:** dropped if result ≤ 0 (loss-making company — negative P/E has no valuation meaning with `lower_is_better=true`)

---

### EV/EBITDA — Enterprise Value / EBITDA

**Pillar:** VALUE  
**Direction:** lower is better  
**N/A for:** Financial Services (EV concept undefined for banks with deposit funding)

**Formula:**
$$EV = \text{Market Cap} + \text{Total Debt}_{MRQ} - \text{Cash}_{MRQ}$$
$$EV/EBITDA = \frac{EV}{\text{TTM EBITDA}}$$

**EBITDA calculation:**
1. Direct: TTM sum of `"EBITDA"` / `"Normalized EBITDA"` from income stmt
2. Derived: `TTM Operating Income + |TTM D&A|` where D&A is from cashflow (`"Depreciation And Amortization"` / `"Depreciation Amortization Depletion"`) or income stmt fallback

**EV fallback:**
1. Computed from `mktcap + mrq_debt − mrq_cash`
2. `info["enterpriseValue"]`

**Fallback chain (final ratio):**
1. Computed EV / computed EBITDA
2. `info["enterpriseToEbitda"]`

**Guard:** dropped if result ≤ 0

---

### P/FCF — Price / Free Cash Flow

**Pillar:** VALUE  
**Direction:** lower is better

**Formula:**
$$\text{FCF}_{TTM} = \text{OCF}_{TTM} - |\text{CapEx}_{TTM}|$$
$$P/FCF = \frac{\text{Market Cap}}{\text{FCF}_{TTM}}$$

**OCF keys tried:** `"Operating Cash Flow"`, `"Cash Flows From Operations"`, `"Net Cash Provided By Operating Activities"`

**CapEx keys tried:** `"Capital Expenditure"`, `"Capital Expenditures"`, `"Purchase Of Property Plant And Equipment"`, `"Investments In Property Plant And Equipment"`, `"Capital Expenditure Reported"`, `"Net PPE Purchase And Sale"`

> The extended CapEx key list covers IFRS and US GAAP reporting variants (e.g. Snam uses `"Net PPE Purchase And Sale"`, Deutsche Bank uses `"Capital Expenditure Reported"`).

**Fallback chain:**
1. Computed from quarterly cashflow
2. `info["freeCashflow"]` as TTM estimate

**Guard:** only computed if `ttm_fcf > 0` (negative P/FCF has no valuation meaning). Dropped if ≤ 0.

---

### FCF Yield — Free Cash Flow Yield

**Pillar:** VALUE  
**Direction:** higher is better

**Formula:**
$$\text{FCF Yield} = \frac{\text{FCF}_{TTM}}{\text{Market Cap}} \times 100$$

**Note:** Unlike P/FCF, FCF Yield is stored and scored **even when negative**. This is intentional for capital-intensive sectors (e.g. utilities) where negative FCF during investment cycles is normal. The Utilities sector threshold applies `bad = −5%` to enable graduated scoring.

**Fallback chain:** same computation path as P/FCF (no additional fallback if FCF is `None`).

---

### P/Book — Price / Book Value

**Pillar:** VALUE for Financial Services; **Extra** for all other sectors  
**Direction:** lower is better

**Formula:**
$$P/Book = \frac{\text{Market Cap}}{\text{Equity}_{MRQ}}$$

**Equity keys tried:** `"Stockholders Equity"`, `"Common Stock Equity"`, `"Total Equity Gross Minority Interest"`

**Fallback chain:**
1. `mktcap / mrq_equity`
2. `info["priceToBook"]`

**Guard:** dropped if result ≤ 0 (negative book value)

---

## QUALITY metrics

### ROE — Return on Equity

**Pillar:** QUALITY  
**Direction:** higher is better

**Formula:**
$$ROE = \frac{\text{TTM Net Income}}{\text{Avg Equity}} \times 100$$

Where:
- Avg Equity = (MRQ Equity + Equity n periods ago) / 2
- "n periods ago" = 4 for quarterly source, 1 for annual source

**Fallback chain:**
1. Computed `ttm_ni / avg_equity`
2. `info["returnOnEquity"] × 100`

---

### EBITDA Margin

**Pillar:** QUALITY (all sectors except Financial Services)  
**Direction:** higher is better

**Formula:**
$$\text{EBITDA Margin} = \frac{\text{TTM EBITDA}}{\text{TTM Revenue}} \times 100$$

**Fallback chain:**
1. Computed from TTM statements
2. `info["ebitdaMargins"] × 100`

---

### ROA — Return on Assets

**Pillar:** QUALITY for Financial Services; **Extra** for all other sectors  
**Direction:** higher is better

**Formula:**
$$ROA = \frac{\text{TTM Net Income}}{\text{Total Assets}_{MRQ}} \times 100$$

**Assets keys tried:** `"Total Assets"`

**Fallback chain:**
1. Computed `ttm_ni / mrq_assets`
2. `info["returnOnAssets"] × 100`

**Rationale for Financial Services:** EBITDA Margin is structurally meaningless for banks (no EBITDA concept). ROA is the canonical bank efficiency metric (good ≥ 1.0%, bad ≤ 0.4%).

---

### ROIC — Return on Invested Capital

**Pillar:** QUALITY (N/A for Financial Services)  
**Direction:** higher is better

**Formula:**
$$NOPAT = \text{TTM Operating Income} \times (1 - T_{eff})$$
$$IC = \text{Equity}_{MRQ} + \text{Debt}_{MRQ} - \text{Cash}_{MRQ}$$
$$ROIC = \frac{NOPAT}{IC} \times 100$$

Where $T_{eff}$ is the effective tax rate (see below).

**No fallback to `info` dict.** If `ttm_oi`, `mrq_equity`, or IC ≤ 0, ROIC is not computed.

---

### D/E Ratio — Debt / Equity

**Pillar:** QUALITY  
**Direction:** lower is better (N/A for Financial Services — bank leverage is regulatory, not a credit quality signal)

**Formula:**
$$D/E = \frac{\text{Total Debt}_{MRQ}}{\text{Equity}_{MRQ}}$$

**Debt keys tried (in order):** `"Total Debt"`, `"Long Term Debt"`, `"Total Long Term Debt"`. If all fail, composite: `LTD + Current Debt` where LTD = `"Long Term Debt And Capital Lease Obligation"` and Current Debt = `"Current Debt And Capital Lease Obligation"` / `"Current Debt"` / `"Current Portion Of Long Term Debt"`.

**Fallback chain:**
1. Computed from balance sheet
2. `info["debtToEquity"]` — normalised: Yahoo sometimes returns this as a percentage (e.g. `82` instead of `0.82`); values > 20 are divided by 100

**Guard:** dropped if result < 0 (negative equity makes D/E undefined)

---

### EPS CAGR 4Y — EPS Compound Annual Growth Rate

**Pillar:** QUALITY  
**Direction:** higher is better

**Formula (Level 1 — from annual income statement):**
$$EPS_t = \frac{\text{Net Income}_t}{\text{Shares Outstanding}}$$
$$\text{EPS CAGR} = \left(\frac{EPS_{now}}{EPS_{old}}\right)^{1/n} - 1 \times 100$$

Where n = number of annual periods − 1 (up to 4 years as yfinance returns max 4 annual periods).

**Net Income keys tried:** `"Net Income"`, `"Net Income Common Stockholders"`, `"Net Income From Continuing Operations"`  
**Shares keys tried:** `info["sharesOutstanding"]`, `info["impliedSharesOutstanding"]`

**Guard:** only computed if both `eps_now > 0` and `eps_old > 0` (CAGR undefined for loss/turnaround cases).

**Fallback chain (Level 2 — from `info` dict):**
- `info["earningsGrowth"] × 100`
- `info["earningsQuarterlyGrowth"] × 100`
- `info["revenueGrowth"] × 100`

---

### Effective Tax Rate (internal, not scored)

Used for ROIC and WACC calculations.

**Formula:**
$$T_{eff} = \frac{\text{TTM Tax Provision}}{\text{TTM Pretax Income}}$$

**Keys tried:** Tax = `"Tax Provision"` / `"Income Tax Expense"`; Pretax = `"Pretax Income"` / `"Income Before Tax"`

**Clamp:** 10%–40% (neutralises DTA reversals, one-off tax benefits, and negative pretax periods)  
**Fallback:** 24% (IRES Italian corporate tax / generic proxy)

---

## MOMENTUM metrics

### Mom. 12M−1M — 12-Month Momentum excluding last month

**Pillar:** MOMENTUM  
**Direction:** higher is better

**Definition:** Standard cross-sectional price momentum — the past 12 months' return excluding the most recent month, to avoid short-term reversal.

**Formula:**
$$\text{Mom}_{12M-1M} = \frac{P_{t-1M}}{P_{t-13M}} - 1 \times 100$$

**Calculation:**
- Downloads 13 months of monthly adjusted close prices
- `p_start = hist["Close"].iloc[0]` (13 months ago)
- `p_end = hist["Close"].iloc[-2]` (1 month ago, excludes current month)

**Fallback:** if fewer than 13 months of data, uses full 12M return (no exclusion of last month).

---

### EPS Revision Proxy

**Pillar:** MOMENTUM  
**Direction:** higher is better

**Definition:** Proxy for analyst earnings revision sentiment. Yahoo Finance does not expose direct analyst revision data; the screener constructs a proxy with two levels.

**Level 1 — Forward vs Trailing EPS ratio:**
$$\text{EPS Rev} = \left(\frac{\text{forwardEps}}{\text{trailingEps}} - 1\right) \times 100$$

Used only when `trailingEps > 0`. When `trailingEps ≤ 0` (loss-making or turnaround), the formula would invert the sign and incorrectly penalise improving earnings — so the fallback is used instead.

**Level 2 — Growth rates from `info`:**
- `info["earningsQuarterlyGrowth"] × 100`
- `info["earningsGrowth"] × 100`

**Output clamp:** ±100% to suppress outliers from turnaround situations (e.g. BMPS: from large losses to profit → +300% raw). The VQM score is bounded 0–10 by the `good`/`bad` thresholds regardless.

---

### FCF Growth — Free Cash Flow Growth YoY

**Pillar:** MOMENTUM  
**Direction:** higher is better

**Formula:**
$$\text{FCF Growth} = \frac{\text{FCF}_{TTM}}{\text{FCF}_{prior-year TTM}} - 1 \times 100$$

**Path 1 — Quarterly (preferred):**  
Requires ≥ 8 quarterly columns in `cf_q`. Current TTM = `cf_q.iloc[:, 0:4]`; prior-year TTM = `cf_q.iloc[:, 4:8]`.

**Path 2 — Annual fallback:**  
Used when `cf_q` has fewer than 8 columns (e.g. European banks like DBK.DE which only report annual cashflow). Current year = `cf_a.iloc[:, 0]`; prior year = `cf_a.iloc[:, 1]` (via `_mrq_nth(..., n=1)`).

**Guard:** not computed if either current or prior-year FCF ≤ 0 (negative-to-negative or positive-to-negative growth is not meaningful as a momentum signal). Requires `ttm_fcf > 0`.

---

## EXTRA metrics (display only, not scored in VQM)

### Gross Margin

$$\text{Gross Margin} = \frac{\text{TTM Gross Profit}}{\text{TTM Revenue}} \times 100$$

**Fallback:** if `"Gross Profit"` row is missing: `(Revenue − COGS) / Revenue`. Final fallback: `info["grossMargins"] × 100`.

**COGS keys tried:** `"Cost Of Revenue"`, `"Cost Of Goods Sold"`, `"Reconciled Cost Of Revenue"`

---

### Operating Margin

$$\text{Operating Margin} = \frac{\text{TTM Operating Income}}{\text{TTM Revenue}} \times 100$$

**Operating Income keys tried:** `"Operating Income"`, `"Total Operating Income As Reported"`, `"EBIT"`

**Fallback:** `info["operatingMargins"] × 100`

---

### Profit Margin (Net Margin)

$$\text{Profit Margin} = \frac{\text{TTM Net Income}}{\text{TTM Revenue}} \times 100$$

**Fallback:** `info["profitMargins"] × 100`

---

### Revenue Growth

$$\text{Rev Growth} = \frac{\text{TTM Revenue}_{now}}{\text{TTM Revenue}_{prior}} - 1 \times 100$$

**Calculation:** requires ≥ 8 quarterly income stmt columns. Current TTM = cols 0–3; prior TTM = cols 4–7.  
**Fallback:** `info["revenueGrowth"] × 100`

---

### Dividend Yield

$$\text{Div Yield} = \text{info}[\text{"dividendYield"}]$$

**Note:** `yfinance` returns `dividendYield` already as a decimal (e.g. `0.0442` = 4.42%). No multiplier is applied. An earlier bug (`× 100` applied twice) was fixed in v2.2.0.

---

### PEG — Price / Earnings to Growth

**Formula (when available):**
$$PEG = P/E_{trailing} \div \text{EPS Growth (\%)}$$

**Fallback chain:**
1. `info["trailingPegRatio"]`
2. `info["pegRatio"]`
3. Manual computation: `info["trailingPE"] / (info["earningsGrowth"] × 100)` — used when Yahoo does not populate pegRatio (common for European tickers). Only computed if `earningsGrowth > 0`.

---

### 52W Change — 52-Week Price Change

$$\text{52W Change} = \text{info}[\text{"52WeekChange"}] \times 100$$

---

### Current Ratio

$$\text{Current Ratio} = \text{info}[\text{"currentRatio"}]$$

No statement-based calculation — uses `info` directly.

---

### Relative Strength vs Benchmark

$$\text{Rel Strength} = \text{Return}_{12M}^{ticker} - \text{Return}_{12M}^{benchmark}$$

**Calculation:** 12-month adjusted close price return for the ticker minus the same for the resolved benchmark index. Benchmark history is cached in `_bm_history_cache` to avoid re-downloading the same index for multiple tickers in the same run.

**Not scored in VQM.** Kept for historical analysis in the DB.

---

### WACC — Weighted Average Cost of Capital

$$\text{WACC} = \frac{E}{V} \cdot R_e + \frac{D}{V} \cdot R_d \cdot (1 - T_{eff})$$

**Cost of equity ($R_e$):**
$$R_e = R_f + \beta \times ERP$$
- $R_f$ = ECB AAA sovereign EUR 8Y spot rate, fetched live from ECB SDW API, cached 24 h, clamped 0.5%–12%, fallback to last known value
- $\beta$ = `info["beta"]`; if β ≤ 0 (gold/defensive assets): $R_e = R_f$; if β is `None`: WACC not computed
- ERP = 5.5% (Damodaran EUR equity risk premium)

**Cost of debt ($R_d$):**
$$R_d = \frac{|\text{Interest Expense}_{TTM}|}{\text{Total Debt}_{MRQ}}, \quad \text{clamped } 0.5\%–20\%$$

**Interest Expense fallback chain (5 levels):**
1. TTM from quarterly income stmt (`"Interest Expense"` / `"Interest Expense Non Operating"` / `"Net Interest Income"`)
2. `info["interestExpense"]`
3. `"Interest Paid Supplemental Data"` from quarterly cashflow (used by Salesforce and some US GAAP companies that omit the interest row from the I/S)
4. Annual income stmt (`_INT_EXP_KEYS`, n=1) — covers AAPL which does not report interest expense quarterly
5. Annual cashflow `"Interest Paid Supplemental Data"` (annual fallback)

**Total Debt fallback:**
1. Balance sheet MRQ (`"Total Debt"`, `"Long Term Debt"`, composite LTD + STD)
2. `info["totalDebt"]`

**Debt-free case:** if Total Debt = 0, WACC = $R_e$ (no leverage component).

**Effective tax rate:** same as the internal $T_{eff}$ used for ROIC (clamped 10%–40%, fallback 24%).

**Not scored in VQM.** Stored in DB and shown in the Extra section of the detail drawer.

---

## Scoring formula

Each VQM metric maps to a 0–10 score via linear interpolation:

**Higher is better (`lower_is_better = false`):**
$$\text{score} = \frac{v - \text{bad}}{\text{good} - \text{bad}} \times 10, \quad \text{clamped } [0, 10]$$

**Lower is better (`lower_is_better = true`):**
$$\text{score} = \frac{\text{bad} - v}{\text{bad} - \text{good}} \times 10, \quad \text{clamped } [0, 10]$$

If `good = null` or `bad = null` for a sector, the metric is excluded from that sector's pillar score without penalising the ticker.

**Pillar score** = simple average of all scored metrics in the pillar (metrics with no data for the specific run are also excluded).

**Final score:**
$$\text{Score} = \frac{w_V \cdot S_V + w_Q \cdot S_Q + w_M \cdot S_M}{w_V + w_Q + w_M}$$

Where weights are read from `thresholds.json → "pesi"` (default: V=0.25, Q=0.50, M=0.25). The denominator re-normalises if a pillar has no scored metrics.

---

## Sector threshold tables

> Values from `thresholds.json` — edit there to recalibrate without code changes.

### Financial Services

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | N/A | N/A | — |
| p_fcf | Value | N/A | N/A | — |
| pe | Value | 9 | 18 | lower |
| p_book | Value | 0.9 | 1.8 | lower |
| roe | Quality | 13% | 4% | higher |
| roa | Quality | 1.0% | 0.4% | higher |
| roic | Quality | N/A | N/A | — |
| de_ratio | Quality | N/A | N/A | — |
| eps_cagr_4y | Quality | 8% | −2% | higher |
| mom_12m1m | Momentum | 15% | −5% | higher |
| eps_rev | Momentum | 3% | −5% | higher |
| fcf_growth | Momentum | N/A | N/A | — |

### Real Estate

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | 14 | 22 | lower |
| p_fcf | Value | 16 | 28 | lower |
| pe | Value | N/A | N/A | — |
| fcf_yield | Value | 5% | 2% | higher |
| roe | Quality | 10% | 3% | higher |
| ebitda_margin | Quality | 55% | 30% | higher |
| roic | Quality | 7% | 2% | higher |
| de_ratio | Quality | 1.0 | 2.5 | lower |
| eps_cagr_4y | Quality | 5% | −2% | higher |
| mom_12m1m | Momentum | 12% | −8% | higher |
| eps_rev | Momentum | 2% | −5% | higher |
| fcf_growth | Momentum | 8% | −8% | higher |

### Utilities

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | 7 | 13 | lower |
| p_fcf | Value | 12 | 20 | lower |
| pe | Value | 13 | 22 | lower |
| fcf_yield | Value | 5% | **−5%** | higher |
| roe | Quality | 10% | 4% | higher |
| ebitda_margin | Quality | 35% | 20% | higher |
| roic | Quality | 8% | 3% | higher |
| de_ratio | Quality | 2.0 | 4.5 | lower |
| eps_cagr_4y | Quality | 6% | 0% | higher |
| mom_12m1m | Momentum | 10% | −8% | higher |
| eps_rev | Momentum | 2% | −5% | higher |
| fcf_growth | Momentum | 6% | −8% | higher |

> `fcf_yield bad = −5%` allows capital-intensive utilities with negative FCF to receive a graduated score (0–5) rather than a flat zero.

### Energy

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | 5 | 11 | lower |
| p_fcf | Value | 7 | 16 | lower |
| pe | Value | 9 | 16 | lower |
| fcf_yield | Value | 9% | 2% | higher |
| roe | Quality | 14% | 3% | higher |
| ebitda_margin | Quality | 30% | 12% | higher |
| roic | Quality | 12% | 4% | higher |
| de_ratio | Quality | 0.5 | 1.5 | lower |
| eps_cagr_4y | Quality | 7% | −8% | higher |
| mom_12m1m | Momentum | 18% | −4% | higher |
| eps_rev | Momentum | 3% | −5% | higher |
| fcf_growth | Momentum | 15% | −20% | higher |

### Technology

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | 18 | 35 | lower |
| p_fcf | Value | 22 | 45 | lower |
| pe | Value | 20 | 40 | lower |
| fcf_yield | Value | 3% | 0.5% | higher |
| roe | Quality | 25% | 10% | higher |
| ebitda_margin | Quality | 35% | 12% | higher |
| roic | Quality | 25% | 10% | higher |
| de_ratio | Quality | 0.2 | 1.0 | lower |
| eps_cagr_4y | Quality | 18% | 5% | higher |
| mom_12m1m | Momentum | 22% | 0% | higher |
| eps_rev | Momentum | 3% | −5% | higher |
| fcf_growth | Momentum | 20% | −5% | higher |

### Healthcare

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | 13 | 24 | lower |
| p_fcf | Value | 18 | 38 | lower |
| pe | Value | 18 | 35 | lower |
| fcf_yield | Value | 4% | 0.5% | higher |
| roe | Quality | 20% | 6% | higher |
| ebitda_margin | Quality | 30% | 10% | higher |
| roic | Quality | 15% | 5% | higher |
| de_ratio | Quality | 0.6 | 2.0 | lower |
| eps_cagr_4y | Quality | 10% | 2% | higher |
| mom_12m1m | Momentum | 15% | −2% | higher |
| eps_rev | Momentum | 3% | −5% | higher |
| fcf_growth | Momentum | 15% | −5% | higher |

### Consumer Cyclical

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | 10 | 18 | lower |
| p_fcf | Value | 14 | 25 | lower |
| pe | Value | 16 | 28 | lower |
| fcf_yield | Value | 5% | 1% | higher |
| roe | Quality | 20% | 6% | higher |
| ebitda_margin | Quality | 18% | 7% | higher |
| roic | Quality | 14% | 5% | higher |
| de_ratio | Quality | 0.8 | 2.0 | lower |
| eps_cagr_4y | Quality | 12% | 2% | higher |
| mom_12m1m | Momentum | 18% | −2% | higher |
| eps_rev | Momentum | 3% | −5% | higher |
| fcf_growth | Momentum | 15% | −8% | higher |

### Consumer Defensive

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | 14 | 22 | lower |
| p_fcf | Value | 18 | 30 | lower |
| pe | Value | 20 | 30 | lower |
| fcf_yield | Value | 4% | 2% | higher |
| roe | Quality | 25% | 8% | higher |
| ebitda_margin | Quality | 22% | 10% | higher |
| roic | Quality | 15% | 6% | higher |
| de_ratio | Quality | 1.0 | 2.5 | lower |
| eps_cagr_4y | Quality | 7% | 1% | higher |
| mom_12m1m | Momentum | 12% | −5% | higher |
| eps_rev | Momentum | 2% | −5% | higher |
| fcf_growth | Momentum | 8% | −5% | higher |

### Industrials

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | 10 | 17 | lower |
| p_fcf | Value | 16 | 26 | lower |
| pe | Value | 16 | 26 | lower |
| fcf_yield | Value | 5% | 1.5% | higher |
| roe | Quality | 16% | 5% | higher |
| ebitda_margin | Quality | 16% | 8% | higher |
| roic | Quality | 14% | 5% | higher |
| de_ratio | Quality | 0.7 | 2.0 | lower |
| eps_cagr_4y | Quality | 10% | 1% | higher |
| mom_12m1m | Momentum | 15% | −2% | higher |
| eps_rev | Momentum | 3% | −5% | higher |
| fcf_growth | Momentum | 12% | −5% | higher |

### Communication Services

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | 7 | 13 | lower |
| p_fcf | Value | 12 | 22 | lower |
| pe | Value | 14 | 24 | lower |
| fcf_yield | Value | 5% | 1.5% | higher |
| roe | Quality | 14% | 4% | higher |
| ebitda_margin | Quality | 30% | 12% | higher |
| roic | Quality | 10% | 3% | higher |
| de_ratio | Quality | 1.5 | 3.5 | lower |
| eps_cagr_4y | Quality | 6% | −2% | higher |
| mom_12m1m | Momentum | 12% | −5% | higher |
| eps_rev | Momentum | 3% | −5% | higher |
| fcf_growth | Momentum | 8% | −8% | higher |

### Basic Materials

| Metric | Pillar | Good | Bad | Direction |
|---|---|---|---|---|
| ev_ebitda | Value | 6 | 12 | lower |
| p_fcf | Value | 9 | 18 | lower |
| pe | Value | 10 | 18 | lower |
| fcf_yield | Value | 7% | 2% | higher |
| roe | Quality | 12% | 3% | higher |
| ebitda_margin | Quality | 30% | 12% | higher |
| roic | Quality | 10% | 3% | higher |
| de_ratio | Quality | 0.5 | 1.8 | lower |
| eps_cagr_4y | Quality | 8% | −5% | higher |
| mom_12m1m | Momentum | 18% | −5% | higher |
| eps_rev | Momentum | 3% | −5% | higher |
| fcf_growth | Momentum | 12% | −15% | higher |

---

## Known data quirks and mitigations

| Issue | Ticker example | Mitigation |
|---|---|---|
| No quarterly cashflow for EU banks | DBK.DE | Annual cashflow fallback for FCF Growth (Path 2) |
| No quarterly interest expense | AAPL | Annual income stmt fallback (WACC Fallback 3) |
| Interest Expense not on I/S, only in cashflow | CRM (Salesforce) | `"Interest Paid Supplemental Data"` from cashflow (WACC Fallback 2) |
| CapEx labelled differently under IFRS | SRG.MI (Snam) | Expanded `_CAPEX_KEYS` with IFRS variants |
| Yahoo `pegRatio = None` for EU tickers | IVG.MI | Manual PEG = P/E / (earningsGrowth × 100) |
| D/E returned as percentage by Yahoo | various | Divided by 100 when value > 20 |
| `dividendYield` raw value from Yahoo | various | Stored as-is from `info` (no multiplier); already represents the yield value |
| Negative FCF in infrastructure sector | SRG.MI | FCF Yield guard removed; `bad = −5%` threshold for Utilities |
| `trailingEps < 0` in turnaround | BMPS.MI | EPS Rev Level 1 skipped; falls back to earningsQuarterlyGrowth |
| `quoteType` != GICS sector | ETFs, indices | Uses `quoteType` only for known instrument types (ETF, INDEX, MUTUALFUND, CURRENCY, FUTURE); otherwise `"N/D"` |
