"""Portfolio snapshot — persist and reload the allocation history.

File format (JSON):
{
  "history": [
    {
      "date": "April 08, 2026",
      "positions": [
        {"ticker": "UCG.MI", "rating": "BUY", "weight": "25%", "target": "82.42"},
        ...
      ]
    },
    ...  // older runs appended at the end
  ]
}

The LLM only receives the most recent entry; the full history is stored for
the user's own tracking and review.
"""
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
    """Return the most recent saved snapshot entry for *tickers*, or None."""
    path = snapshot_path(tickers)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        # Support both the new {"history": [...]} format and the legacy flat format
        if "history" in data:
            history = data["history"]
            return history[-1] if history else None
        # Legacy format: {"date": ..., "positions": [...]}
        return data
    except Exception as exc:
        logger.warning("Could not load portfolio snapshot: %s", exc)
        return None


def save_snapshot(md_text: str, date_str: str, tickers: list[str]) -> bool:
    """Parse the allocation table from *md_text* and append it to the history.

    Returns True if at least one position was extracted and saved.
    """
    positions: list[dict] = []

    for m in re.finditer(
        r"^\|\s*([A-Z0-9.^=-]+)\s*\|\s*([^|]+?)\s*\|\s*(\d[\d.]*\s*%)\s*\|\s*([^|]+?)\s*\|",
        md_text,
        re.MULTILINE,
    ):
        ticker, rating, weight, target = (g.strip() for g in m.groups())
        if ticker.upper() in ("TICKER", "------", "---"):
            continue
        positions.append({
            "ticker": ticker,
            "rating": rating,
            "weight": weight,
            "target": target,
        })

    if not positions:
        logger.info("No allocation table found in report — snapshot not updated.")
        return False

    new_entry = {"date": date_str, "positions": positions}
    path = snapshot_path(tickers)
    os.makedirs(REPORT_DIR, exist_ok=True)

    # Load existing history (handle both formats)
    history: list[dict] = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                existing = json.load(fh)
            if "history" in existing:
                history = existing["history"]
            else:
                # Migrate legacy flat entry into history list
                history = [existing]
        except Exception as exc:
            logger.warning("Could not read existing snapshot for migration: %s", exc)

    history.append(new_entry)

    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"history": history}, fh, indent=2, ensure_ascii=False)
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

