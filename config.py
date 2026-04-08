"""Central configuration – loaded from .env."""
import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM (OpenAI API) ─────────────────────────────────────────────────────────
LLM_MODEL       = os.getenv("LLM_MODEL",       "gpt-4o")
LLM_API_KEY     = os.getenv("LLM_API_KEY",     "")
LLM_MAX_TOKENS  = int(os.getenv("LLM_MAX_TOKENS",    "4096"))

# ── Tavily ────────────────────────────────────────────────────────────────────
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ── Tickers ───────────────────────────────────────────────────────────────────
TICKERS: list[str] = [
    t.strip().upper()
    for t in os.getenv("TICKERS", "AAPL,MSFT,GOOGL,NVDA,TSLA").split(",")
    if t.strip()
]

# ── Analysis ──────────────────────────────────────────────────────────────────
HISTORY_PERIOD    = os.getenv("HISTORY_PERIOD",    "1y")
HISTORY_INTERVAL  = os.getenv("HISTORY_INTERVAL",  "1d")
ANALYSIS_LANGUAGE = os.getenv("ANALYSIS_LANGUAGE", "italian")


# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(__file__)
REPORT_DIR = os.path.join(BASE_DIR, os.getenv("REPORT_DIR", "reports"))
