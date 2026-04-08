"""Finance Bot – investment analysis runner.

Usage
-----
# Use default tickers from .env / config.py
python main.py

# Override tickers
python main.py AAPL MSFT NVDA
python main.py ISP.MI ENI.MI ENEL.MI
"""

import argparse
import logging
import sys

import colorama
from colorama import Fore, Style

from config import TICKERS

# Suppress noisy library loggers
logging.disable(logging.CRITICAL)

colorama.init(autoreset=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_status(msg: str) -> None:
    print(f"\r{Fore.YELLOW}  ⟳  {Style.DIM}{msg}{Style.RESET_ALL}", end="", flush=True)


def _clear_line() -> None:
    print("\r" + " " * 72 + "\r", end="", flush=True)


# ── Stream consumer ───────────────────────────────────────────────────────────

def _ask_save_path(tickers: list[str], date_str: str) -> str | None:
    """Open a native Save-As dialog and return the chosen path, or None to use default."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        safe_date = date_str.replace(",", "").replace(" ", "_")
        joined = "_".join(tickers)
        safe_tickers = f"{len(tickers)}tickers" if len(joined) > 80 else joined
        default_name = f"{safe_date}_{safe_tickers}.pdf"

        root = tk.Tk()
        root.withdraw()          # hide the empty root window
        root.attributes("-topmost", True)
        path = filedialog.asksaveasfilename(
            parent=root,
            title="Salva report PDF",
            initialfile=default_name,
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        root.destroy()
        return path if path else None   # empty string = user cancelled → use default
    except Exception:
        return None  # tkinter not available → fall back to default path


def _run(tickers: list[str]) -> None:
    from src.agent import InvestmentAgent

    agent = InvestmentAgent()

    tickers_fmt = "  ".join(tickers)
    print()
    print(f"{Fore.CYAN}{Style.BRIGHT}◆  Finance Bot — Investment Analysis{Style.RESET_ALL}")
    print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}")
    print(f"{Style.DIM}Tickers:{Style.RESET_ALL}  {Fore.WHITE}{tickers_fmt}{Style.RESET_ALL}")
    print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}\n")

    pending_tool = ""

    for event_type, content in agent.analyze_stream(tickers):

        # ── Status (overwrite same line) ──────────────────────────────────
        if event_type == "status":
            _print_status(content)

        # ── Parallel fetch lines ──────────────────────────────────────────
        elif event_type == "fetch_done":
            _clear_line()
            print(f"  {Fore.CYAN}●{Style.RESET_ALL}  {Style.DIM}{content}{Style.RESET_ALL}")

        elif event_type == "fetch_error":
            _clear_line()
            print(f"  {Fore.RED}✗{Style.RESET_ALL}  {Fore.RED}{content}{Style.RESET_ALL}")

        # ── Analysis phase started ────────────────────────────────────────
        elif event_type == "analysis_start":
            if pending_tool:
                _clear_line()
                print(f"  {Fore.GREEN}✓{Style.RESET_ALL}  {Style.DIM}{pending_tool}{Style.RESET_ALL}")
                pending_tool = ""
            print()
            _print_status("Generazione report in corso…")

        # ── Tool calls ────────────────────────────────────────────────────
        elif event_type == "tool_start":
            label = "Ricerca notizie macro" if content == "search_news" else content
            if pending_tool:
                _clear_line()
                print(f"  {Fore.GREEN}\u2713{Style.RESET_ALL}  {Style.DIM}{pending_tool}{Style.RESET_ALL}")
            pending_tool = label
            _print_status(label)

        # ── LLM stats ─────────────────────────────────────────────────────
        elif event_type == "llm_stats":
            d       = content
            elapsed = d.get("elapsed", 0.0)
            in_tok  = d.get("in_tokens",  0)
            out_tok = d.get("out_tokens", 0)
            parts   = [f"⏱ {elapsed:.1f}s"]
            if in_tok or out_tok:
                parts += [f"↑{in_tok} in", f"↓{out_tok} out", f"= {in_tok + out_tok} tok"]
            _clear_line()
            print(f"{Style.DIM}  {'  '.join(parts)}{Style.RESET_ALL}")

        # ── PDF report ────────────────────────────────────────────────────
        elif event_type == "report_md":
            md_text, tkrs, date_str = content
            _print_status("Generazione PDF…")
            try:
                from src.report import save_pdf
                output_path = _ask_save_path(tkrs, date_str)
                path = save_pdf(md_text, tkrs, date_str, output_path)
                _clear_line()
                print(f"  {Fore.GREEN}✓  Report PDF salvato:{Style.RESET_ALL} {path}")
            except Exception as exc:
                _clear_line()
                print(f"  {Fore.RED}✗  PDF non generato: {exc}{Style.RESET_ALL}")

        # ── Portfolio snapshot ────────────────────────────────────────────
        elif event_type == "snapshot_saved":
            _clear_line()
            if content:
                print(f"  {Fore.CYAN}✓  Snapshot portafoglio aggiornato{Style.RESET_ALL}")
            # else: table not found in report, snapshot unchanged — silent

        # ── Done ──────────────────────────────────────────────────────────
        elif event_type == "done":
            _clear_line()
            print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}")
            print(f"{Fore.CYAN}{Style.BRIGHT}  Analisi completata.{Style.RESET_ALL}\n")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a full investment analysis and generate a PDF report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py\n"
            "  python main.py AAPL MSFT NVDA\n"
            "  python main.py ISP.MI ENI.MI ENEL.MI\n"
        ),
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        metavar="TICKER",
        help="Ticker symbols to analyse (overrides .env defaults when provided).",
    )
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers] if args.tickers else list(TICKERS)

    try:
        _run(tickers)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}  Interrotto.{Style.RESET_ALL}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
