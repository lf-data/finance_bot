"""Technical indicators for medium-to-long term investment analysis.

Input:  pandas DataFrame with OHLCV columns (from yfinance).
Output: dict of indicator values + a formatted text summary.
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Configurable periods ──────────────────────────────────────────────────────
_SMA_PERIODS    = (20, 50, 200)
_EMA_PERIODS    = (12, 26)
_BB_PERIOD      = 20
_BB_STD         = 2.0
_RSI_PERIOD     = 14
_MACD_FAST      = 12
_MACD_SLOW      = 26
_MACD_SIGNAL    = 9
_ATR_PERIOD     = 14
_VOL_PERIOD     = 20   # periods for average volume comparison


def compute(df: pd.DataFrame) -> Optional[dict]:
    """Compute all indicators.  Returns None if DataFrame is too short."""
    min_rows = max(_SMA_PERIODS) + 10
    if len(df) < min_rows:
        logger.warning("Not enough rows (%d < %d) for full indicator suite.", len(df), min_rows)
        return None

    close   = df["Close"].values.astype(float)
    high    = df["High"].values.astype(float)
    low     = df["Low"].values.astype(float)
    volume  = df["Volume"].values.astype(float) if "Volume" in df.columns else None
    current = float(close[-1])
    dates   = df.index

    out: dict = {"current_price": round(current, 4)}

    # ── SMAs ─────────────────────────────────────────────────────────────────
    for p in _SMA_PERIODS:
        val = float(np.mean(close[-p:]))
        out[f"sma{p}"] = round(val, 4)
        out[f"sma{p}_distance_pct"] = round((current - val) / val * 100, 2)

    # SMA trend labels
    if out["sma50"] > out["sma200"]:
        out["trend_50_200"] = "golden_cross (bullish)"
    elif out["sma50"] < out["sma200"]:
        out["trend_50_200"] = "death_cross (bearish)"
    else:
        out["trend_50_200"] = "neutral"

    # ── Bollinger Bands (BB_PERIOD) ───────────────────────────────────────────
    bb_slice = close[-_BB_PERIOD:]
    bb_mid   = float(np.mean(bb_slice))
    bb_std   = float(np.std(bb_slice, ddof=1))
    bb_upper = bb_mid + _BB_STD * bb_std
    bb_lower = bb_mid - _BB_STD * bb_std
    bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid != 0 else 0.0
    bb_pct_b = ((current - bb_lower) / (bb_upper - bb_lower)
                if (bb_upper != bb_lower) else 0.5)
    out.update(
        bb_upper=round(bb_upper, 4),
        bb_mid=round(bb_mid, 4),
        bb_lower=round(bb_lower, 4),
        bb_pct_b=round(bb_pct_b, 4),   # 0 = lower band, 1 = upper band
        bb_bandwidth=round(bb_width * 100, 2),  # as %
    )

    # ── RSI ───────────────────────────────────────────────────────────────────
    out["rsi14"] = round(_rsi(close, _RSI_PERIOD), 2)

    # ── MACD ─────────────────────────────────────────────────────────────────
    ema_fast      = _ema_series(close, _MACD_FAST)
    ema_slow      = _ema_series(close, _MACD_SLOW)
    macd_line     = ema_fast - ema_slow
    signal_line   = _ema_series(macd_line, _MACD_SIGNAL)
    macd_hist     = macd_line - signal_line
    out.update(
        macd=round(float(macd_line[-1]), 4),
        macd_signal=round(float(signal_line[-1]), 4),
        macd_hist=round(float(macd_hist[-1]), 4),
        macd_trend="bullish" if macd_hist[-1] > 0 else "bearish",
    )

    # ── ATR (Average True Range) ──────────────────────────────────────────────
    tr    = np.maximum(
        high[-_ATR_PERIOD:] - low[-_ATR_PERIOD:],
        np.maximum(
            np.abs(high[-_ATR_PERIOD:] - np.roll(close, 1)[-_ATR_PERIOD:]),
            np.abs(low[-_ATR_PERIOD:] - np.roll(close, 1)[-_ATR_PERIOD:]),
        ),
    )
    atr   = float(np.mean(tr[1:]))  # skip first (wrap-around artefact)
    out["atr14"]     = round(atr, 4)
    out["atr14_pct"] = round(atr / current * 100, 2)

    # ── Volume trend ──────────────────────────────────────────────────────────
    if volume is not None and len(volume) >= _VOL_PERIOD + 5:
        avg_vol   = float(np.mean(volume[-_VOL_PERIOD:]))
        recent_vol = float(np.mean(volume[-5:]))
        out["avg_volume_20d"]      = int(avg_vol)
        out["recent_volume_5d"]    = int(recent_vol)
        out["volume_vs_avg_pct"]   = round((recent_vol - avg_vol) / avg_vol * 100, 1)

    # ── 52-week stats ─────────────────────────────────────────────────────────
    year_slice = close[-252:] if len(close) >= 252 else close
    out["52w_high"]         = round(float(np.max(year_slice)), 4)
    out["52w_low"]          = round(float(np.min(year_slice)), 4)
    out["52w_high_dist_pct"] = round((current - out["52w_high"]) / out["52w_high"] * 100, 2)
    out["52w_low_dist_pct"]  = round((current - out["52w_low"])  / out["52w_low"]  * 100, 2)

    # ── YTD / 1-year performance ──────────────────────────────────────────────
    if len(close) >= 2:
        out["perf_1m_pct"]  = _perf(close, 21)
        out["perf_3m_pct"]  = _perf(close, 63)
        out["perf_6m_pct"]  = _perf(close, 126)
        out["perf_1y_pct"]  = _perf(close, 252)

    # ── Support / resistance (simple: recent swing highs/lows) ──────────────
    window = min(60, len(close))
    recent = close[-window:]
    out["support_60d"]    = round(float(np.min(recent)), 4)
    out["resistance_60d"] = round(float(np.max(recent)), 4)

    return out


def format_summary(ind: dict) -> str:
    """Return a compact multi-line text summary for the LLM."""
    lines = [
        f"Current Price : {ind['current_price']}",
        "",
        "── Moving Averages ──",
        f"  SMA20  : {ind.get('sma20','N/A')}  ({ind.get('sma20_distance_pct','?'):+}% vs price)",
        f"  SMA50  : {ind.get('sma50','N/A')}  ({ind.get('sma50_distance_pct','?'):+}% vs price)",
        f"  SMA200 : {ind.get('sma200','N/A')}  ({ind.get('sma200_distance_pct','?'):+}% vs price)",
        f"  Trend (SMA50/200): {ind.get('trend_50_200','N/A')}",
        "",
        "── Bollinger Bands (20,2) ──",
        f"  Upper={ind.get('bb_upper','?')}  Mid={ind.get('bb_mid','?')}  Lower={ind.get('bb_lower','?')}",
        f"  %B={ind.get('bb_pct_b','?')}  (0=at lower, 1=at upper)  Bandwidth={ind.get('bb_bandwidth','?')}%",
        "",
        "── Momentum ──",
        f"  RSI14 : {ind.get('rsi14','?')}",
        f"  MACD  : {ind.get('macd','?')}  Signal={ind.get('macd_signal','?')}  Hist={ind.get('macd_hist','?')}  [{ind.get('macd_trend','?')}]",
        "",
        "── Volatility ──",
        f"  ATR14 : {ind.get('atr14','?')}  ({ind.get('atr14_pct','?')}% of price)",
        "",
        "── 52-Week Range ──",
        f"  High  : {ind.get('52w_high','?')}  ({ind.get('52w_high_dist_pct','?'):+}% vs current)",
        f"  Low   : {ind.get('52w_low','?')}   ({ind.get('52w_low_dist_pct','?'):+}% vs current)",
        "",
        "── Performance ──",
        f"  1M={ind.get('perf_1m_pct','?'):+}%  3M={ind.get('perf_3m_pct','?'):+}%  6M={ind.get('perf_6m_pct','?'):+}%  1Y={ind.get('perf_1y_pct','?'):+}%",
        "",
        "── Support / Resistance (60d) ──",
        f"  Support={ind.get('support_60d','?')}  Resistance={ind.get('resistance_60d','?')}",
    ]
    if "avg_volume_20d" in ind:
        lines += [
            "",
            "── Volume ──",
            f"  Avg(20d)={ind['avg_volume_20d']:,}  Recent(5d)={ind['recent_volume_5d']:,}  vs avg={ind.get('volume_vs_avg_pct','?'):+}%",
        ]
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ema_series(data: np.ndarray, period: int) -> np.ndarray:
    k   = 2.0 / (period + 1)
    ema = np.empty_like(data)
    ema[:period] = np.mean(data[:period])
    for i in range(period, len(data)):
        ema[i] = data[i] * k + ema[i - 1] * (1 - k)
    return ema


def _rsi(close: np.ndarray, period: int) -> float:
    deltas = np.diff(close[-(period + 1):])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = float(np.mean(gains))
    avg_l  = float(np.mean(losses))
    if avg_l == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_g / avg_l)


def _perf(close: np.ndarray, periods: int) -> float:
    if len(close) <= periods:
        start = close[0]
    else:
        start = close[-periods - 1]
    if start == 0:
        return 0.0
    return round((close[-1] - start) / start * 100, 2)
