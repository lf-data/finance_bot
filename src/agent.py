"""Investment analysis agent – analysis-only."""
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Generator

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from config import (
    ANALYSIS_LANGUAGE,
    LLM_API_KEY,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    TAVILY_API_KEY,
    TICKERS,
)
from src.data_fetcher import (
    fetch_analyst_recommendations,
    fetch_earnings_history,
    fetch_fundamentals,
    fetch_history,
)
from src.indicators import compute, format_summary
from src.portfolio import (
    format_snapshot_for_prompt,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)
from src.tools import build_tools

logger = logging.getLogger(__name__)


# StreamEvent type: (event_name, content)
# event_name: "fetch_done" | "fetch_error" | "tool_start"
#           | "analysis_start" | "status" | "llm_stats" | "report_md" | "done"
StreamEvent = tuple[str, object]

# ── Key fundamental fields — set for O(1) lookup ──────────────────────────────
_FUND_KEYS: frozenset[str] = frozenset([
    "longName", "sector",
    "trailingPE", "forwardPE", "pegRatio",
    "trailingEps", "forwardEps",
    "revenueGrowth", "earningsGrowth",
    "profitMargins", "returnOnEquity", "debtToEquity",
    "beta", "recommendationKey", "targetMeanPrice",
])

# ── Macro-news prompt (called once; search_news tool available) ────────────────
_NEWS_SYSTEM = """You are a macro-economic research analyst. Today's date is {date}.

Call search_news ONCE using a broad query that covers:
- Current macroeconomic conditions (inflation, interest rates, GDP growth, central bank policy)
- Sector-specific news and trends relevant to the tickers under analysis
- Geopolitical risks and their market implications
- Earnings season trends and analyst sentiment shifts
- Any recent black-swan events or major regulatory changes

Return EXACTLY 6-8 bullet points structured as follows:
• [MACRO] <key macro theme and its market implication>
• [SECTOR] <sector-specific trend or news>
• [RISK] <main risk factor to monitor>
... and so on.

Each bullet must be substantive (1-2 sentences), cite the specific data or event, and explain the market implication.
No preamble, no conclusion, no other text outside the bullet points."""


# ── Per-ticker direct news fetch (Tavily, no LLM intermediary) ────────────────
def _search_ticker_news(ticker: str) -> str:
    """Call Tavily directly to get recent news for a single ticker."""
    if not TAVILY_API_KEY:
        return ""
    try:
        from tavily import TavilyClient  # type: ignore
        client = TavilyClient(api_key=TAVILY_API_KEY)
        result = client.search(
            topic="finance",
            query=f"{ticker} stock latest news earnings analyst outlook",
            search_depth="basic",
            max_results=5,
            include_answer=True,
        )
        answer    = result.get("answer", "")
        headlines = [r.get("title", "") for r in result.get("results", []) if r.get("title")]
        parts: list[str] = []
        if answer:
            parts.append(answer)
        if headlines:
            parts.append("Headlines: " + " | ".join(headlines[:5]))
        return " ".join(parts)
    except Exception as exc:
        logger.error("Ticker news fetch error for %s: %s", ticker, exc)
        return ""


# ── Unified analysis prompt (single LLM call, no tools) ────────────────────────
_ANALYSIS_SYSTEM = """You are a senior equity analyst and portfolio manager with 20+ years of experience.
Respond entirely in {language}. Today's date is {date}.
All market data and macro context have been pre-fetched and are provided in the user message.
Your investment strategy: medium-to-long term horizon (6 months - 3 years),
combining fundamental quality, technical momentum, and macroeconomic context.

## YOUR TASK

Produce a comprehensive investment report structured EXACTLY as follows:

---

# INVESTMENT REPORT — {date}

## 1. MACRO & SECTOR CONTEXT
Summarise the key macro themes from the provided context (3-5 sentences).
Explain how the current macro environment (rates, inflation, growth cycle, geopolitics)
affects the asset classes and sectors represented in the ticker list.

## 2. INDIVIDUAL TICKER ANALYSIS
For EACH ticker in the list, write a dedicated sub-section:

### [TICKER] — [Company Name] ([Sector])
**Technical Outlook:** Describe the price trend, key support/resistance levels, momentum
indicators (RSI, MACD, moving averages). State whether the technical picture is
bullish, bearish, or neutral and why.

**Fundamental Outlook:** Analyse the valuation (P/E, forward P/E, PEG, EPS growth),
profitability (margins, ROE), and balance sheet health (debt/equity). Compare to
sector averages where possible. Highlight any red flags or strengths.

**Analyst Consensus:** Report the consensus rating and mean price target. Note any
recent upgrades or downgrades and what they signal.

**Catalysts & Risks:** Using the `news:` field provided in the context for this ticker,
identify 2-3 specific positive catalysts and 2-3 specific risks for the next 6-18 months.
Reference the actual news headlines or events cited in the data.

**Verdict:** Assign one of: STRONG BUY / BUY / ACCUMULATE / HOLD / REDUCE / AVOID.
Provide 2-3 sentences explaining the verdict, referencing the data above.

---

## 3. PORTFOLIO ALLOCATION
Include ONLY tickers rated STRONG BUY, BUY, or ACCUMULATE.
Select AT MOST 5 tickers — choose the highest-conviction ones if more qualify.
If none qualify, state: **NO BUY OPPORTUNITIES AT THIS TIME** and explain why.

**Sector exclusion rule:** Before selecting positions, evaluate whether the macro context
or the individual ticker news justifies excluding an entire sector from the portfolio.
If a sector faces unfavourable macro tailwinds (e.g. rising rates hitting real estate,
recession risk hitting consumer discretionary, regulatory crackdown on a specific industry,
geopolitical disruption to a supply chain), exclude ALL tickers from that sector regardless
of their individual rating. Explicitly state in the rationale which sectors were excluded
and why.

{previous_portfolio_instructions}

For qualifying tickers, present the allocation table:

| Ticker | Rating | Weight | 12M Target | vs Previous |
|--------|--------|--------|------------|-------------|

Weights must sum to 100%. Order by conviction (highest first).
In the "vs Previous" column use: NEW (not in previous portfolio), = (weight unchanged),
↑ (weight increased), ↓ (weight decreased), EXIT (was in previous, now excluded).
If there is no previous portfolio, leave "vs Previous" as N/A for all rows.

## 4. PORTFOLIO RATIONALE
Write 3-5 paragraphs explaining:
- The overall portfolio construction logic (why these weights, diversification)
- The primary thesis driving the allocation
- Key risk factors for the entire portfolio and how they are mitigated
- Conditions that would cause you to revise the allocation (triggers)

## 5. MONITORING CHECKLIST
List 4-6 specific indicators or events to monitor in the next 3-6 months
that could validate or invalidate the investment thesis.

---

IMPORTANT RULES:
- Every claim must be supported by specific data from the provided context.
- Use exact numbers (e.g. P/E of 24.3x, RSI at 62, target $185).
- Do not hedge with generic disclaimers — be direct and precise.
- Do not invent data not present in the provided context.
- Never truncate the report. Complete all sections fully.

{previous_portfolio_section}"""


class InvestmentAgent:
    """Investment analysis agent: parallel data fetch + macro news + LLM report."""

    def __init__(self) -> None:
        self._tools        = build_tools()
        self._llm          = self._make_llm()           # news graph – capped tokens
        self._analysis_llm = self._make_llm(cap=False)  # analysis – no cap
        self._news_graph   = self._build_news_graph()   # macro: search_news once

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze_stream(
        self, tickers: list[str] | None = None
    ) -> Generator[StreamEvent, None, None]:
        """Parallel fetch → single news search → single LLM call → allocation table."""
        tickers = tickers or TICKERS
        n       = len(tickers)

        # ── Phase 1: parallel data fetch ──────────────────────────────────
        done     = 0
        all_data: dict = {}
        yield ("status", f"Recupero dati  0/{n}")
        t_fetch = time.perf_counter()
        for ev_type, ev_val in self._prefetch_stream(tickers):
            if ev_type == "_done_":
                all_data = ev_val
            else:
                done += 1
                yield ("status", f"Dati ricevuti  {done}/{n}")
                yield (ev_type, ev_val)
        elapsed_fetch = time.perf_counter() - t_fetch
        yield ("status", f"Dati pronti  ({elapsed_fetch:.1f}s)")

        # ── Phase 2: search_news ONCE for macro context ────────────────────
        yield ("status", "Ricerca notizie macro…")
        yield ("tool_start", "search_news")
        t_news = time.perf_counter()
        macro  = self._fetch_macro(tickers)
        elapsed_news = time.perf_counter() - t_news
        yield ("status", f"Notizie pronte  ({elapsed_news:.1f}s)")

        # ── Phase 3: single LLM call for allocation ─────────────────────────
        yield ("analysis_start", None)
        date     = datetime.now(timezone.utc).strftime("%B %d, %Y")
        snapshot = load_snapshot(tickers)
        prev_instructions = (
            "A previous portfolio snapshot is provided at the end of this prompt.\n"
            "For each position compare the new allocation against the previous one."
            if snapshot else
            "No previous portfolio snapshot is available. This is a fresh allocation."
        )
        prev_section = (
            f"\n## PREVIOUS PORTFOLIO SNAPSHOT\n\n{format_snapshot_for_prompt(snapshot)}\n"
            if snapshot else ""
        )
        system  = _ANALYSIS_SYSTEM.format(
            language=ANALYSIS_LANGUAGE,
            date=date,
            previous_portfolio_instructions=prev_instructions,
            previous_portfolio_section=prev_section,
        )
        context = self._build_data_context(all_data, tickers)
        msgs    = [
            SystemMessage(content=system),
            HumanMessage(content=f"Macro context:\n{macro}\n\n{context}"),
        ]
        yield ("status", "Sto analizzando il portafoglio\u2026")
        t0 = time.perf_counter()
        try:
            response = self._analysis_llm.invoke(msgs)
        except Exception as exc:
            logger.error("Analysis LLM error: %s", exc)
            yield ("report_md", (f"_Analysis error: {exc}_", tickers, date))
            yield ("done", None)
            return
        elapsed = time.perf_counter() - t0
        um      = getattr(response, "usage_metadata", None) or {}
        in_tok  = um.get("input_tokens",  0)
        out_tok = um.get("output_tokens", 0)
        raw     = getattr(response, "content", "")
        # Strip any <think>...</think> reasoning blocks some models emit
        md_text = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()
        yield ("llm_stats", {"elapsed": elapsed, "in_tokens": in_tok, "out_tokens": out_tok})
        yield ("report_md", (md_text, tickers, date))
        saved = save_snapshot(md_text, date, tickers)
        yield ("snapshot_saved", snapshot_path(tickers) if saved else None)
        yield ("done", None)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _prefetch_stream(
        self, tickers: list[str]
    ) -> Generator[tuple[str, object], None, None]:
        """Yield ("fetch_done"|"fetch_error", label) per ticker as each completes,
        then ("_done_", all_data_dict) as the final sentinel."""
        def _fetch_one(ticker: str) -> tuple[str, dict, float]:
            t0   = time.perf_counter()
            df   = fetch_history(ticker)
            ind  = compute(df) if df is not None else None
            tech = format_summary(ind) if ind else "No technical data."
            fund = fetch_fundamentals(ticker)
            recs = fetch_analyst_recommendations(ticker)
            earn = fetch_earnings_history(ticker)
            news = _search_ticker_news(ticker)
            return ticker, {"tech": tech, "fund": fund, "recs": recs, "earn": earn, "news": news}, \
                   time.perf_counter() - t0

        results: dict = {}
        with ThreadPoolExecutor(max_workers=min(len(tickers), 6)) as exe:
            futures = {exe.submit(_fetch_one, t): t for t in tickers}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    _, data, elapsed = future.result()
                    results[ticker] = data
                    yield ("fetch_done", f"{ticker}  ({elapsed:.1f}s)")
                except Exception as exc:
                    logger.error("Prefetch error for %s: %s", ticker, exc)
                    results[ticker] = {"tech": "Error.", "fund": {}, "recs": [], "earn": [], "news": ""}
                    yield ("fetch_error", ticker)
        yield ("_done_", results)

    def _build_data_context(self, all_data: dict, tickers: list[str]) -> str:
        """Serialise pre-fetched data into a minimal token-efficient block."""
        parts: list[str] = []
        for ticker in tickers:
            d    = all_data.get(ticker, {})
            rows = [f"[{ticker}]"]

            # Technical – already 3-4 compact lines
            rows.append("tech: " + d.get("tech", "N/A").replace("\n", " | "))

            # Fundamentals – only selected keys, one line
            fund = d.get("fund", {})
            if fund:
                items = [f"{k}={v}" for k, v in fund.items() if k in _FUND_KEYS and v is not None]
                rows.append("fund: " + "  ".join(items))

            # Analyst ratings – last 3 only, compressed
            recs = d.get("recs", [])
            if recs:
                rec_parts = []
                for r in recs[-3:]:
                    firm  = r.get("Firm", r.get("firm", ""))
                    grade = r.get("To Grade", r.get("toGrade", r.get("action", "")))
                    if firm or grade:
                        rec_parts.append(f"{firm}:{grade}" if firm else grade)
                if rec_parts:
                    rows.append("ratings: " + "  ".join(rec_parts))

            # Earnings – last 2 quarters, key fields only
            earn = d.get("earn", [])
            if earn:
                eq = []
                for e in earn[-2:]:
                    act  = e.get("epsActual",   e.get("Reported EPS", ""))
                    est  = e.get("epsEstimate",  e.get("EPS Estimate", ""))
                    date = e.get("quarter",      e.get("Date", ""))
                    if act or est:
                        eq.append(f"{date} act={act} est={est}")
                if eq:
                    rows.append("eps: " + "  ".join(eq))

            # Per-ticker news (direct Tavily fetch, truncated for token efficiency)
            news = d.get("news", "")
            if news:
                rows.append("news: " + news[:600])

            parts.append("\n".join(rows))
        return "\n\n".join(parts)

    def _fetch_macro(self, tickers: list[str]) -> str:
        """Run search_news ONCE and return a short macro-context string."""
        query = (
            f"macro outlook 2025-2026, sector news for: {', '.join(tickers)}, "
            "Fed rates, earnings season, geopolitical risks"
        )
        msgs   = [HumanMessage(content=query)]
        parts: list[str] = []
        try:
            for chunk, _ in self._news_graph.stream(
                {"messages": msgs},
                stream_mode="messages",
                config={"recursion_limit": 10},
            ):
                if isinstance(chunk, ToolMessage):
                    continue
                content = getattr(chunk, "content", "")
                if isinstance(content, str) and content:
                    parts.append(content)
        except Exception as exc:
            logger.error("Macro fetch error: %s", exc)
        return "".join(parts)

    def _make_llm(self, *, cap: bool = True) -> ChatOpenAI:
        kwargs: dict = dict(
            api_key=LLM_API_KEY,
            model=LLM_MODEL,
            store=False,
        )
        if cap:
            kwargs["max_tokens"] = LLM_MAX_TOKENS
        return ChatOpenAI(**kwargs)

    def _build_news_graph(self):
        """Macro news graph: only search_news tool."""
        news_tool = [t for t in self._tools if t.name == "search_news"]
        return create_agent(
            model=self._llm,
            tools=news_tool,
            system_prompt=_NEWS_SYSTEM.format(
                date=datetime.now(timezone.utc).strftime("%B %d, %Y"),
            ),
        )
