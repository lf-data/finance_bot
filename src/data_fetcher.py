"""yfinance data fetcher – price history + screener-style fundamental metrics."""
import logging
from typing import Optional

import pandas as pd
import yfinance as yf

from config import HISTORY_INTERVAL, HISTORY_PERIOD

logger = logging.getLogger(__name__)


def fetch_history(
    ticker: str,
    period: str = HISTORY_PERIOD,
    interval: str = HISTORY_INTERVAL,
) -> Optional[pd.DataFrame]:
    """Return a DataFrame with OHLCV columns (index = date).

    Returns None if the ticker is invalid or data is unavailable.
    """
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        if df.empty:
            logger.warning("yfinance returned empty history for %s", ticker)
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception as exc:
        logger.error("History fetch error for %s: %s", ticker, exc)
        return None


def fetch_fundamentals(ticker: str) -> dict:
    """Fetch and compute screener-style fundamental metrics + composite score.

    Mirrors the logic of european_stock_screener.py fetch_metrics().
    Returns a dict with only non-None values.
    """
    result: dict = {}
    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}

        # --- Basic info ---
        result["nome"]    = info.get("shortName", ticker)
        result["settore"] = info.get("sector", "N/D")
        result["mktcap"]  = info.get("marketCap")

        # --- Valuation ---
        result["pe_trailing"] = info.get("trailingPE")
        result["pe_forward"]  = info.get("forwardPE")
        result["ev_ebitda"]   = info.get("enterpriseToEbitda")
        result["ev_sales"]    = info.get("enterpriseToRevenue")
        result["peg"]         = info.get("trailingPegRatio") or info.get("pegRatio")
        result["p_book"]      = info.get("priceToBook")

        fcf    = info.get("freeCashflow")
        mktcap = info.get("marketCap")
        result["p_fcf"] = round(mktcap / fcf, 2) if fcf and mktcap and fcf > 0 else None

        # --- Capital efficiency ---
        result["roe"]  = _pct(info.get("returnOnEquity"))
        result["roa"]  = _pct(info.get("returnOnAssets"))
        result["roic"] = _calc_roic(t, info)

        # --- Earnings quality ---
        net_income = info.get("netIncomeToCommon")
        result["fcf_conversion"] = (
            _pct(fcf / net_income) if fcf and net_income and net_income > 0 else None
        )
        total_rev = info.get("totalRevenue")
        result["fcf_margin"]       = _pct(fcf / total_rev) if fcf and total_rev else None
        result["gross_margin"]     = _pct(info.get("grossMargins"))
        result["operating_margin"] = _pct(info.get("operatingMargins"))
        result["profit_margin"]    = _pct(info.get("profitMargins"))

        # --- Financial solidity ---
        result["debt_ebitda"]       = _calc_debt_ebitda(info)
        result["interest_coverage"] = _calc_interest_coverage(t, info)
        result["current_ratio"]     = info.get("currentRatio")
        result["quick_ratio"]       = info.get("quickRatio")
        result["debt_equity"]       = info.get("debtToEquity")

        # --- Growth ---
        result["rev_growth_yoy"] = _pct(info.get("revenueGrowth"))

        # --- Momentum ---
        result["momentum_6m"]  = _calc_momentum(t, months=6)
        result["momentum_12m"] = _calc_momentum(t, months=12)

        # --- Composite score (0-100) ---
        sector = result.get("settore", "")
        if sector == "Financial Services":
            result["score"] = _calc_score_bank(result)
        else:
            result["score"] = _calc_score(result)

    except Exception as exc:
        logger.error("Fundamentals fetch error for %s: %s", ticker, exc)

    return {k: v for k, v in result.items() if v is not None}


# ── Helpers (mirrors european_stock_screener.py logic) ───────────────────────

def _pct(val) -> Optional[float]:
    if val is None:
        return None
    return round(val * 100, 2)


def _get_row(df: pd.DataFrame, keys: list):
    for key in keys:
        if key in df.index:
            val = df.loc[key].iloc[0]
            if pd.notna(val):
                return float(val)
    return None


def _calc_roic(t: yf.Ticker, info: dict) -> Optional[float]:
    try:
        bs = t.balance_sheet
        if bs is None or bs.empty:
            return None
        total_assets = _get_row(bs, ["Total Assets"])
        current_liab = _get_row(bs, ["Current Liabilities", "Total Current Liabilities"])
        if total_assets is None or current_liab is None:
            return None
        invested_capital = total_assets - current_liab
        if invested_capital <= 0:
            return None
        net_income = info.get("netIncomeToCommon")
        if not net_income:
            return None
        return round((net_income / invested_capital) * 100, 2)
    except Exception:
        return None


def _calc_debt_ebitda(info: dict) -> Optional[float]:
    try:
        total_debt = info.get("totalDebt", 0) or 0
        cash       = info.get("totalCash",  0) or 0
        ebitda     = info.get("ebitda")
        if not ebitda or ebitda <= 0:
            return None
        return round((total_debt - cash) / ebitda, 2)
    except Exception:
        return None


def _calc_interest_coverage(t: yf.Ticker, info: dict) -> Optional[float]:
    try:
        inc = t.income_stmt
        if inc is None or inc.empty:
            return None
        ebit     = _get_row(inc, ["EBIT", "Operating Income"])
        interest = _get_row(inc, ["Interest Expense"])
        if ebit is None or interest is None or interest == 0:
            return None
        return round(ebit / abs(interest), 2)
    except Exception:
        return None


def _calc_momentum(t: yf.Ticker, months: int) -> Optional[float]:
    try:
        hist = t.history(period=f"{months}mo")
        if hist.empty or len(hist) < 2:
            return None
        ret = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
        return round(ret, 2)
    except Exception:
        return None


def _calc_score_bank(r: dict) -> float:
    """Composite score 0-100 for Financial Services / banks.
    Standard metrics (EBITDA, FCF, current ratio) don't apply to banks.
    Pillars: Valuation(35) + Profitability(35) + Growth(15) + Momentum(15)
    """
    score = 0.0
    weight_tot = 0.0

    def add(val, lo, hi, weight, inverse=False):
        nonlocal score, weight_tot
        if val is None:
            return
        norm = max(0.0, min(1.0, (val - lo) / (hi - lo)))
        if inverse:
            norm = 1 - norm
        score += norm * weight
        weight_tot += weight

    # Valuation (35) — P/E and P/Book are the standard bank multiples
    add(r.get("pe_trailing"),  5,  20, 20, inverse=True)
    add(r.get("p_book"),       0.3, 3, 15, inverse=True)

    # Profitability (35) — ROE and net profit margin
    add(r.get("roe"),           5,  25, 20)
    add(r.get("profit_margin"), 10, 50, 15)

    # Growth (15)
    add(r.get("rev_growth_yoy"), -20, 20, 15)

    # Momentum (15)
    add(r.get("momentum_6m"),  -30,  60, 8)
    add(r.get("momentum_12m"), -40, 100, 7)

    if weight_tot == 0:
        return 0.0
    return round((score / weight_tot) * 100, 1)


def _calc_score(r: dict) -> float:
    """Composite score 0-100 (6 pillars).
    Weights: Capital efficiency(25) + Earnings quality(20) + Solidity(20)
             + Valuation(15) + Growth(10) + Momentum(10)
    """
    score = 0.0
    weight_tot = 0.0

    def add(val, lo, hi, weight, inverse=False):
        nonlocal score, weight_tot
        if val is None:
            return
        norm = max(0.0, min(1.0, (val - lo) / (hi - lo)))
        if inverse:
            norm = 1 - norm
        score += norm * weight
        weight_tot += weight

    # Capital efficiency (25)
    add(r.get("roic"),  0,  30, 15)
    add(r.get("roe"),   0,  40, 10)

    # Earnings quality (20)
    add(r.get("fcf_conversion"),  0, 150, 12)
    add(r.get("fcf_margin"),      0,  30,  8)

    # Financial solidity (20)
    add(r.get("debt_ebitda"),       -1,  5, 10, inverse=True)
    add(r.get("interest_coverage"),  0, 20, 10)

    # Valuation (15)
    add(r.get("ev_ebitda"),  4,  25,  8, inverse=True)
    add(r.get("p_fcf"),      5,  40,  7, inverse=True)

    # Growth (10)
    add(r.get("rev_growth_yoy"), -10, 30, 5)
    add(r.get("gross_margin"),     0, 80, 5)

    # Momentum (10)
    add(r.get("momentum_6m"),  -30,  60, 5)
    add(r.get("momentum_12m"), -40, 100, 5)

    if weight_tot == 0:
        return 0.0
    return round((score / weight_tot) * 100, 1)
