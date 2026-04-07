"""Finance Bot – investment analysis agent entry point.

Setup:
    1. Copy .env.example → .env and configure LLM_BASE_URL, LLM_MODEL, TAVILY_API_KEY.
    2. pip install -r requirements.txt
    3. python main.py

LOOP_INTERVAL=0  → run once and exit.
LOOP_INTERVAL=N  → repeat every N seconds (e.g. 86400 = daily).
"""
import logging
import os
import signal
import sys
import time

from colorama import Fore, Style, init

from config import ANALYSIS_LANGUAGE, LOOP_INTERVAL, TICKERS
from src.agent import InvestmentAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("FinanceBot")
init(autoreset=True)

# ANSI colours for recommendation labels
_REC_COLOR = {
    "STRONG BUY":  Fore.GREEN,
    "BUY":         Fore.GREEN,
    "ACCUMULATE":  Fore.CYAN,
    "HOLD":        Fore.YELLOW,
    "REDUCE":      Fore.RED,
    "AVOID":       Fore.RED,
}


def print_banner() -> None:
    print(Fore.CYAN + """
╔══════════════════════════════════════════════════════════╗
║        FINANCE BOT – Investment Analysis Agent           ║
║        LLM: local Docker  ·  Data: Yahoo Finance         ║
╚══════════════════════════════════════════════════════════╝""")
    print(f"  Tickers  : {Fore.WHITE}{', '.join(TICKERS)}{Style.RESET_ALL}")
    print(f"  Language : {Fore.WHITE}{ANALYSIS_LANGUAGE}{Style.RESET_ALL}")
    mode = "RUN ONCE" if LOOP_INTERVAL == 0 else f"every {LOOP_INTERVAL}s"
    print(f"  Schedule : {Fore.WHITE}{mode}{Style.RESET_ALL}\n")


def print_report(result: dict) -> None:
    print(Fore.CYAN + f"\n{'═'*62}")
    print(Fore.CYAN + f"  Analysis complete – {result['run_time'][:19]} UTC")
    print(Fore.CYAN + f"{'═'*62}" + Style.RESET_ALL)

    report = result.get("report", "")

    # Colour-code recommendation lines
    for line in report.splitlines():
        colored = False
        for label, color in _REC_COLOR.items():
            if label in line.upper():
                print(color + line + Style.RESET_ALL)
                colored = True
                break
        if not colored:
            print(line)

    print()


def main() -> None:
    print_banner()

    agent = InvestmentAgent()

    def _shutdown(sig, frame):  # noqa: ANN001
        logger.info("Shutdown signal received.")
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    run = 0
    while True:
        run += 1
        logger.info("Starting analysis run #%d for: %s", run, ", ".join(TICKERS))
        try:
            result = agent.run()
            print_report(result)
        except Exception as exc:
            logger.error("Run #%d failed: %s", run, exc, exc_info=True)

        if LOOP_INTERVAL == 0:
            break
        logger.info("Next analysis in %ds (%s)…", LOOP_INTERVAL,
                    f"{LOOP_INTERVAL // 3600}h" if LOOP_INTERVAL >= 3600 else f"{LOOP_INTERVAL}s")
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
