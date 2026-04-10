"""Central configuration – loaded from .env."""
import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM (OpenAI API) ─────────────────────────────────────────────────────────
LLM_MODEL   = os.getenv("LLM_MODEL",   "gpt-4o")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# ── Tavily ────────────────────────────────────────────────────────────────────
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ── Screener ──────────────────────────────────────────────────────────────────
ANALYSIS_LANGUAGE  = os.getenv("ANALYSIS_LANGUAGE",  "italian")
SCREENER_BENCHMARK = os.getenv("SCREENER_BENCHMARK", "SWDA.MI")
SCREENER_WORKERS   = int(os.getenv("SCREENER_WORKERS", "6"))
