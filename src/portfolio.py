"""Portfolio snapshot — persist and reload the latest allocation."""
import json
import logging
import os
import re

from config import REPORT_DIR

logger = logging.getLogger(__name__)


def snapshot_path(tickers: list[str]) -> str:
    """Return the snapshot file path for a given ticker list."""
    key = "_".join(tickers) if len("_".join(tickers)) <= 80 else f"{len(tickers)}tickers"
    return os.path.join(REPORT_DIR, f"portfolio_{key}.json")


def load_snapshot(tickers: list[str]) -> dict | None:
    """Return the last saved snapshot for *tickers*, or None if it doesn't exist."""
    path = snapshot_path(tickers)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning("Could not load portfolio snapshot: %s", exc)
        return None


def save_snapshot(md_text: str, date_str: str, tickers: list[str]) -> bool:
    """Parse the allocation table from *md_text* and persist it as JSON.

    Returns True if at least one position was extracted and saved.
    """
    positions: list[dict] = []

    # Match data rows in the markdown allocation table.
    # Expected columns: Ticker | Rating | Weight | 12M Target | (optional: vs Previous)
    for m in re.finditer(
        r"^\|\s*([A-Z0-9.^=-]+)\s*\|\s*([^|]+?)\s*\|\s*(\d[\d.]*\s*%)\s*\|\s*([^|]+?)\s*\|",
        md_text,
        re.MULTILINE,
    ):
        ticker, rating, weight, target = (g.strip() for g in m.groups())
        if ticker.upper() in ("TICKER", "------", "---"):
            continue  # skip header / separator rows
        positions.append({
            "ticker": ticker,
            "rating": rating,
            "weight": weight,
            "target": target,
        })

    if not positions:
        logger.info("No allocation table found in report — snapshot not updated.")
        return False

    snapshot = {"date": date_str, "positions": positions}
    path = snapshot_path(tickers)
    os.makedirs(REPORT_DIR, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, ensure_ascii=False)
        return True
    except Exception as exc:
        logger.error("Could not save portfolio snapshot: %s", exc)
        return False


def format_snapshot_for_prompt(snapshot: dict) -> str:
    """Render a snapshot dict as a compact text block for the LLM prompt."""
    lines = [f"Previous portfolio as of {snapshot['date']}:"]
    for p in snapshot["positions"]:
        lines.append(
            f"  {p['ticker']:<12}  {p['rating']:<12}  {p['weight']:<8}  target {p['target']}"
        )
    return "\n".join(lines)
