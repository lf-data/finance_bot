"""Investment analysis agent.

Uses a locally hosted LLM (Docker container, OpenAI-compatible endpoint)
via LangChain's ChatOpenAI.  The agent autonomously calls the available
tools and produces a structured investment recommendation report.
"""
import logging
import os
from datetime import datetime, timezone

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from config import (
    ANALYSIS_LANGUAGE,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
    REPORT_DIR,
    TICKERS,
)
from src.tools import build_tools

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are an expert financial analyst specialising in medium-to-long term stock \
investment (horizons of 6 months to 3 years).

Your task is to analyse each requested ticker using the available tools and \
produce a comprehensive investment report in {language}.

FOR EACH TICKER you MUST:
1. Call `get_technical_analysis` to get price trends and momentum.
2. Call `get_fundamental_data` to assess valuation and financial health.
3. Call `get_analyst_recommendations` to see professional analyst consensus.
4. Call `search_news` with a specific query about the company's recent news, \
   earnings, or strategic outlook.
5. Synthesise all findings into a recommendation.

RECOMMENDATION LEVELS:
  STRONG BUY   – excellent fundamentals, strong uptrend, positive catalysts
  BUY          – good risk/reward, solid fundamentals, positive trend
  ACCUMULATE   – generally positive but wait for a better entry point
  HOLD         – neutral; reasonable to keep if already invested
  REDUCE       – deteriorating outlook; consider trimming exposure
  AVOID        – negative fundamentals or trend; not suitable for investment

OUTPUT FORMAT (repeat for each ticker):

---
## [TICKER] – [Company Name]  ·  [RECOMMENDATION]

**Investment Horizon:** [e.g. 12-18 months]
**Target Price Range:** [e.g. $180 – $210]  (or "N/A – insufficient data")
**Risk Level:** Low / Medium / High

### Technical Summary
[key technical observations: trend, momentum, support/resistance]

### Fundamental Summary
[key financial metrics: valuation, growth, margins, balance sheet]

### Analyst Consensus
[consensus rating, price target range, recent upgrades/downgrades]

### Recent News & Catalysts
[key recent events, earnings beats/misses, product launches, macro exposure]

### Thesis
[2-4 sentences summarising why you give this recommendation]

### Key Risks
[bullet list of the main risks to the thesis]

---

After all tickers, add a brief **Portfolio Allocation Suggestion** section \
prioritising the tickers by conviction level.

Today's date: {date}
"""


class InvestmentAgent:
    """Orchestrates the LLM agent for investment analysis."""

    def __init__(self) -> None:
        self._tools    = build_tools()
        self._executor = self._build_executor()
        os.makedirs(REPORT_DIR, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, tickers: list[str] | None = None) -> dict:
        """Analyse *tickers* (defaults to TICKERS from config).

        Returns:
            {
                "run_time":     ISO timestamp,
                "tickers":      list of tickers analysed,
                "report":       full markdown report string,
            }
        """
        tickers = tickers or TICKERS
        now     = datetime.now(timezone.utc)

        question = (
            f"Analyse the following tickers for medium-to-long term investment "
            f"and write a detailed report in {ANALYSIS_LANGUAGE}:\n\n"
            + ", ".join(tickers)
            + "\n\nFor each ticker use all four available tools before writing the recommendation."
        )

        logger.info("Starting analysis for: %s", ", ".join(tickers))
        try:
            result = self._executor.invoke({"input": question})
            report = result.get("output", "")
        except Exception as exc:
            logger.error("Agent error: %s", exc, exc_info=True)
            report = f"Agent error: {exc}"

        self._save_report(report, now)

        return {
            "run_time": now.isoformat(),
            "tickers":  tickers,
            "report":   report,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_executor(self) -> AgentExecutor:
        llm = ChatOpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            model=LLM_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                _SYSTEM.format(
                    language=ANALYSIS_LANGUAGE,
                    date=datetime.now(timezone.utc).strftime("%B %d, %Y"),
                ),
            ),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])

        agent = create_openai_tools_agent(llm, self._tools, prompt)
        return AgentExecutor(
            agent=agent,
            tools=self._tools,
            verbose=True,
            max_iterations=len(TICKERS) * 6 + 4,  # 4 tools/ticker + margin
            handle_parsing_errors=True,
            return_intermediate_steps=False,
        )

    def _save_report(self, report: str, timestamp: datetime) -> None:
        """Persist the report to a timestamped text file in REPORT_DIR."""
        filename = f"report_{timestamp.strftime('%Y%m%d_%H%M%S')}.md"
        path     = os.path.join(REPORT_DIR, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# Investment Analysis Report\n")
                f.write(f"Generated: {timestamp.strftime('%Y-%m-%d %H:%M UTC')}\n\n")
                f.write(report)
            logger.info("Report saved to %s", path)
        except Exception as exc:
            logger.warning("Could not save report: %s", exc)
