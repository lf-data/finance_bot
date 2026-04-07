"""LangChain tools for the investment analysis agent.

Tools (read-only – no orders):
  1. get_technical_analysis(ticker)   – yfinance OHLCV + computed indicators
  2. get_fundamental_data(ticker)     – yfinance fundamentals + analyst targets
  3. get_analyst_recommendations(ticker) – recent analyst rating history
  4. search_news(query)               – Tavily web search
"""
import json
import logging

from langchain_core.tools import tool

from config import TAVILY_API_KEY
from src.data_fetcher import (
    fetch_analyst_recommendations,
    fetch_earnings_history,
    fetch_fundamentals,
    fetch_history,
)
from src.indicators import compute, format_summary

logger = logging.getLogger(__name__)


def build_tools() -> list:
    """Return all LangChain tools for the agent."""

    # ── 1. Technical analysis ─────────────────────────────────────────────────

    @tool
    def get_technical_analysis(ticker: str) -> str:
        """Fetch 1-year daily OHLCV history from Yahoo Finance and compute
        technical indicators: SMA(20/50/200), Bollinger Bands, RSI(14),
        MACD(12/26/9), ATR(14), volume, 52-week range, and performance.

        Use this to assess the price trend and momentum of a stock before
        giving an investment recommendation.
        Input: ticker symbol (e.g. AAPL, MSFT, EURUSD=X).
        """
        ticker = ticker.upper().strip()
        df = fetch_history(ticker)
        if df is None:
            return f"No price data available for {ticker} on Yahoo Finance."
        ind = compute(df)
        if ind is None:
            return f"Not enough price history for {ticker} to compute full indicators."
        return f"Technical Analysis for {ticker}:\n{format_summary(ind)}"

    # ── 2. Fundamentals ────────────────────────────────────────────────────────

    @tool
    def get_fundamental_data(ticker: str) -> str:
        """Fetch fundamental financial data from Yahoo Finance for a stock.

        Returns: company name, sector, P/E ratio (trailing and forward),
        EPS, revenue growth, earnings growth, margins (gross/operating/net),
        ROE, debt/equity, dividend yield, beta, analyst price targets, and
        consensus recommendation.

        Use this to assess the valuation and financial health of a company.
        Input: ticker symbol (e.g. AAPL, NVDA).
        """
        ticker = ticker.upper().strip()
        data = fetch_fundamentals(ticker)
        if not data:
            return f"No fundamental data available for {ticker}."

        def fmt(v: object) -> str:
            if isinstance(v, float):
                return f"{v:.4f}" if abs(v) < 1000 else f"{v:,.0f}"
            return str(v)

        lines = [f"Fundamentals for {ticker}:"]
        for k, v in data.items():
            lines.append(f"  {k}: {fmt(v)}")
        return "\n".join(lines)

    # ── 3. Analyst recommendations ────────────────────────────────────────────

    @tool
    def get_analyst_recommendations(ticker: str) -> str:
        """Fetch the most recent analyst buy/sell/hold ratings from Yahoo Finance.

        Also includes recent quarterly earnings results (actual vs estimated EPS).
        Use this to understand what professional analysts think about the stock.
        Input: ticker symbol (e.g. TSLA, GOOGL).
        """
        ticker = ticker.upper().strip()
        recs   = fetch_analyst_recommendations(ticker)
        earn   = fetch_earnings_history(ticker)

        lines = [f"Analyst Recommendations for {ticker}:"]
        if recs:
            for r in recs[-8:]:
                period = r.get("period", r.get("Date", ""))
                firm   = r.get("Firm", r.get("firm", ""))
                grade  = r.get("To Grade", r.get("toGrade", r.get("action", "")))
                lines.append(f"  {period}  {firm}: {grade}")
        else:
            lines.append("  No analyst ratings available.")

        lines.append("\nRecent Earnings (EPS Actual vs Estimate):")
        if earn:
            for e in earn[-4:]:
                lines.append(f"  {e}")
        else:
            lines.append("  No earnings data available.")

        return "\n".join(lines)

    # ── 4. News search ────────────────────────────────────────────────────────

    @tool
    def search_news(query: str) -> str:
        """Search the web for the latest news and analysis about a stock or topic
        using Tavily Search. Returns an AI-generated answer plus recent headlines.

        Use this to understand recent catalysts, earnings news, macro events,
        or any fundamental developments affecting a ticker.
        Input: a natural language query (e.g. 'NVDA earnings outlook 2025',
               'Apple AI strategy news', 'Fed interest rate impact on tech stocks').
        """
        if not TAVILY_API_KEY:
            return "Tavily API key not configured (TAVILY_API_KEY in .env). Cannot search news."
        try:
            from tavily import TavilyClient  # type: ignore
            client = TavilyClient(api_key=TAVILY_API_KEY)
            result = client.search(
                query=query,
                search_depth="advanced",
                max_results=6,
                include_answer=True,
            )
            answer    = result.get("answer", "")
            headlines = [
                f"- [{r.get('title','')}]  {r.get('url','')}"
                for r in result.get("results", [])
            ]
            body = f"Summary:\n{answer}\n\nSources:\n" + "\n".join(headlines)
            return body if answer else "Sources:\n" + "\n".join(headlines)
        except Exception as exc:
            logger.error("Tavily search error: %s", exc)
            return f"News search failed: {exc}"

    return [get_technical_analysis, get_fundamental_data,
            get_analyst_recommendations, search_news]
