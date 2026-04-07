"""Central configuration – loaded from .env."""
import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM ───────────────────────────────────────────────────────────────────────
LLM_BASE_URL    = os.getenv("LLM_BASE_URL",    "http://localhost:8000/v1")
LLM_MODEL       = os.getenv("LLM_MODEL",       "local-model")
LLM_API_KEY     = os.getenv("LLM_API_KEY",     "not-required")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
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

# ── Runtime ───────────────────────────────────────────────────────────────────
LOOP_INTERVAL = int(os.getenv("LOOP_INTERVAL", "86400"))  # 0 = run once and exit

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(__file__)
REPORT_DIR = os.path.join(BASE_DIR, os.getenv("REPORT_DIR", "reports"))
