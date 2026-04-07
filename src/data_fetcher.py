"""yfinance data fetcher – price history + fundamentals + analyst data."""
import logging
from typing import Optional

import pandas as pd
import yfinance as yf

from config import HISTORY_INTERVAL, HISTORY_PERIOD

logger = logging.getLogger(__name__)

# Fundamental fields to retrieve from yf.Ticker.info
_FUNDAMENTAL_KEYS = [
    "longName", "sector", "industry", "country",
    "marketCap", "enterpriseValue",
    "trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months",
    "pegRatio",
    "trailingEps", "forwardEps",
    "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth",
    "grossMargins", "operatingMargins", "profitMargins",
    "returnOnEquity", "returnOnAssets",
    "debtToEquity", "currentRatio", "quickRatio",
    "totalCash", "totalDebt", "freeCashflow",
    "dividendYield", "payoutRatio",
    "beta",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "fiftyDayAverage", "twoHundredDayAverage",
    "shortRatio", "shortPercentOfFloat",
    "targetMeanPrice", "targetHighPrice", "targetLowPrice",
    "numberOfAnalystOpinions", "recommendationMean", "recommendationKey",
    "currency", "exchange",
]


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
    """Return a dict of fundamental metrics from yfinance.

    Missing values are omitted rather than returned as None.
    """
    try:
        info = yf.Ticker(ticker).info
        return {k: info[k] for k in _FUNDAMENTAL_KEYS if info.get(k) is not None}
    except Exception as exc:
        logger.error("Fundamentals fetch error for %s: %s", ticker, exc)
        return {}


def fetch_analyst_recommendations(ticker: str) -> list[dict]:
    """Return the 10 most recent analyst recommendation rows."""
    try:
        t   = yf.Ticker(ticker)
        rec = t.recommendations
        if rec is None or rec.empty:
            return []
        return rec.tail(10).reset_index().to_dict("records")
    except Exception as exc:
        logger.error("Analyst recommendations error for %s: %s", ticker, exc)
        return []


def fetch_earnings_history(ticker: str) -> list[dict]:
    """Return recent quarterly earnings (EPS actual vs estimate)."""
    try:
        t      = yf.Ticker(ticker)
        cal    = getattr(t, "earnings_history", None) or getattr(t, "quarterly_earnings", None)
        if cal is None or (hasattr(cal, "empty") and cal.empty):
            return []
        if isinstance(cal, pd.DataFrame):
            return cal.tail(8).reset_index().to_dict("records")
        return []
    except Exception as exc:
        logger.error("Earnings history error for %s: %s", ticker, exc)
        return []
