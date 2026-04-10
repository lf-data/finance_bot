"""Finance Bot — VQM Screener.

Flusso: scoring quantitativo VQM (deterministico, stabile) + commento AI opzionale
per ticker (Tavily search + LLM). Il modello numerico è la fonte di verità;
l'IA fornisce contesto e narrative a supporto della logica stabile.

Usage
-----
    python screener.py                        # tickers da config.py / lista italiana
    python screener.py ISP.MI UCG.MI ENI.MI   # tickers espliciti
    python screener.py --ai                   # + commento AI per ogni ticker
    python screener.py --ai --out report.json
    python screener.py --benchmark SPY
"""

import argparse
import datetime
import json
import logging
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import yfinance as yf
import colorama
from colorama import Fore, Style

from config import (
    ANALYSIS_LANGUAGE,
    LLM_API_KEY,
    LLM_MODEL,
    SCREENER_BENCHMARK,
    SCREENER_WORKERS,
    TAVILY_API_KEY,
)

logging.disable(logging.CRITICAL)
colorama.init(autoreset=True)


# ── CLI HELPERS ───────────────────────────────────────────────────────────────

def _print_status(msg: str) -> None:
    print(f"\r{Fore.YELLOW}  ⟳  {Style.DIM}{msg}{Style.RESET_ALL}", end="", flush=True)


def _clear_line() -> None:
    print("\r" + " " * 72 + "\r", end="", flush=True)

# ── VQM CONFIG (caricata da thresholds.json) ────────────────────────────────

_THRESHOLDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thresholds.json")
_RESERVED_KEYS   = {"pesi", "tickers"}


def _load_vqm_config() -> tuple[dict, dict, list]:
    """Carica thresholds.json → (soglie, pesi, tickers)."""
    with open(_THRESHOLDS_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    pesi    = raw.get("pesi", {"value": 0.30, "quality": 0.40, "momentum": 0.30})
    tickers = raw.get("tickers") or []
    soglie  = {
        sector: {
            pillar: [(e["metrica"], e["good"], e["bad"], e["lower_is_better"]) for e in entries]
            for pillar, entries in pillars.items()
        }
        for sector, pillars in raw.items()
        if sector not in _RESERVED_KEYS
    }
    return soglie, pesi, tickers


_THRESHOLDS, _VQM_WEIGHTS, DEFAULT_TICKERS = _load_vqm_config()

# ── FETCH METRICHE DA YFINANCE ───────────────────────────────────────────────

def _safe(val, multiplier=1.0, decimals=2) -> float | None:
    """Normalizza valori restituiti da yfinance (alcuni sono frazioni, altri già %)."""
    if val is None:
        return None
    try:
        v = float(val) * multiplier
        return round(v, decimals)
    except (ValueError, TypeError):
        return None

def _momentum_12m_1m(ticker_obj: yf.Ticker) -> float | None:
    """Rendimento 12M escludendo l'ultimo mese (cross-sectional momentum standard)."""
    try:
        hist = ticker_obj.history(period="13mo", interval="1mo", auto_adjust=True)
        if hist is None or len(hist) < 13:
            # fallback: usa 12M completo
            hist = ticker_obj.history(period="12mo", interval="1mo", auto_adjust=True)
            if hist is None or len(hist) < 2:
                return None
            ret = (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
            return round(ret, 2)
        # prezzi: 13 mesi fa → 1 mese fa (esclude ultimo mese)
        p_start = hist["Close"].iloc[0]
        p_end   = hist["Close"].iloc[-2]   # -2 = esclude mese corrente
        if p_start and p_start > 0:
            return round((p_end / p_start - 1) * 100, 2)
    except Exception:
        pass
    return None

def _rel_strength(ticker_obj: yf.Ticker, benchmark: str = "FTSEMIB.MI") -> float | None:
    """Rendimento 12M titolo - rendimento 12M benchmark."""
    try:
        hist_t = ticker_obj.history(period="12mo", auto_adjust=True)
        hist_b = yf.Ticker(benchmark).history(period="12mo", auto_adjust=True)
        if hist_t.empty or hist_b.empty:
            return None
        ret_t = (hist_t["Close"].iloc[-1] / hist_t["Close"].iloc[0] - 1) * 100
        ret_b = (hist_b["Close"].iloc[-1] / hist_b["Close"].iloc[0] - 1) * 100
        return round(ret_t - ret_b, 2)
    except Exception:
        return None

def _eps_cagr_5y(ticker_obj: yf.Ticker, info: dict) -> float | None:
    """
    CAGR EPS a 5 anni — 2 livelli di fallback:
    1. Net Income / shares da income_stmt (fino a 4 anni → CAGR)
    2. Proxy: earningsGrowth / earningsQuarterlyGrowth / revenueGrowth da info
    """
    # Livello 1: Net Income / shares da income_stmt
    try:
        inc = ticker_obj.income_stmt
        if inc is not None and not inc.empty:
            shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
            ni_keys = ["Net Income", "Net Income Common Stockholders",
                       "Net Income From Continuing Operations"]
            net_inc = None
            for k in ni_keys:
                if k in inc.index:
                    net_inc = inc.loc[k].dropna()
                    break
            if net_inc is not None and shares and shares > 0 and len(net_inc) >= 2:
                eps_now = float(net_inc.iloc[0])  / shares
                eps_old = float(net_inc.iloc[-1]) / shares
                n = len(net_inc) - 1
                if eps_old > 0 and eps_now > 0:
                    return round(((eps_now / eps_old) ** (1 / n) - 1) * 100, 2)
    except Exception:
        pass

    # Livello 3: proxy da info
    for key in ("earningsGrowth", "earningsQuarterlyGrowth", "revenueGrowth"):
        v = info.get(key)
        if v is not None:
            return _safe(v, multiplier=100)
    return None

def fetch_metrics(ticker: str, benchmark: str = "FTSEMIB.MI") -> dict:
    """
    Scarica tutte le metriche VQM da Yahoo Finance per un singolo ticker.
    Restituisce un dict con tutte le metriche disponibili.
    """
    result: dict = {"ticker": ticker}
    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}

        # ── Info base ──────────────────────────────────────────────────────
        result["nome"]    = info.get("shortName") or info.get("longName") or ticker
        result["settore"] = info.get("sector")    or info.get("quoteType") or "N/D"
        result["industria"] = info.get("industry") or "N/D"
        result["mktcap"]  = info.get("marketCap")
        result["valuta"]  = info.get("currency") or "EUR"

        # ── VALUE — disponibili direttamente da info ───────────────────────
        # EV/EBITDA: Yahoo Finance Statistics → Valuation Measures
        result["ev_ebitda"] = _safe(info.get("enterpriseToEbitda"))

        # P/FCF: calcolato da marketCap / freeCashflow (Yahoo Statistics)
        fcf    = info.get("freeCashflow")
        mktcap = info.get("marketCap")
        if fcf and mktcap and fcf > 0:
            result["p_fcf"] = round(mktcap / fcf, 2)

        # P/E Trailing: Yahoo Finance Statistics → Trailing P/E
        result["pe"] = _safe(info.get("trailingPE"))

        # P/Book: Yahoo Finance Statistics → Price/Book (mrq)
        result["p_book"] = _safe(info.get("priceToBook"))

        # ── QUALITY — disponibili direttamente da info ─────────────────────
        # ROE: Yahoo Statistics → Return on Equity (ttm) — yfinance dà valore in frazione
        result["roe"] = _safe(info.get("returnOnEquity"), multiplier=100)

        # EBITDA Margin: Yahoo Statistics → EBITDA Margin (ttm)
        result["ebitda_margin"] = _safe(info.get("ebitdaMargins"), multiplier=100)

        # Gross Margin: Yahoo Statistics → Gross Profit Margin (ttm)
        result["gross_margin"] = _safe(info.get("grossMargins"), multiplier=100)

        # D/E Ratio: Yahoo Statistics → Total Debt/Equity (mrq)
        result["de_ratio"] = _safe(info.get("debtToEquity"))

        # EPS CAGR 5Y: calcolato da income statement o proxy
        result["eps_cagr_5y"] = _eps_cagr_5y(t, info)

        # ── MOMENTUM ───────────────────────────────────────────────────────
        # Momentum 12M-1M: calcolato da prezzi storici mensili
        result["mom_12m1m"] = _momentum_12m_1m(t)

        # EBITDA Margin: fallback calcolo diretto da ebitda/totalRevenue
        if result.get("ebitda_margin") is None:
            ebitda = info.get("ebitda")
            rev    = info.get("totalRevenue")
            if ebitda and rev and rev > 0:
                result["ebitda_margin"] = round(ebitda / rev * 100, 2)

        # D/E Ratio: fallback da balance sheet se debtToEquity mancante
        if result.get("de_ratio") is None:
            try:
                bs = t.balance_sheet
                if bs is not None and not bs.empty:
                    debt_keys   = ["Total Debt", "Long Term Debt",
                                   "Total Long Term Debt"]
                    equity_keys = ["Stockholders Equity", "Common Stock Equity",
                                   "Total Equity Gross Minority Interest"]
                    debt   = next((float(bs.loc[k].iloc[0]) for k in debt_keys   if k in bs.index and pd.notna(bs.loc[k].iloc[0])), None)
                    equity = next((float(bs.loc[k].iloc[0]) for k in equity_keys if k in bs.index and pd.notna(bs.loc[k].iloc[0])), None)
                    if debt is not None and equity and equity > 0:
                        result["de_ratio"] = round(debt / equity, 2)
            except Exception:
                pass
        # Normalizza: Yahoo a volte restituisce D/E come percentuale (82 = 0.82x)
        if result.get("de_ratio") and result["de_ratio"] > 20:
            result["de_ratio"] = round(result["de_ratio"] / 100, 2)

        # EPS Revision 3M:
        # yfinance non ha revisioni a 3M; usiamo la migliore approssimazione disponibile:
        # earningsQuarterlyGrowth (YoY trimestrale) → earningsGrowth → forwardEPS vs trailingEPS
        eps_rev = None
        for key in ("earningsQuarterlyGrowth", "earningsGrowth"):
            v = info.get(key)
            if v is not None:
                eps_rev = _safe(v, multiplier=100)
                break
        if eps_rev is None:
            fwd = info.get("forwardEps")
            trl = info.get("trailingEps")
            if fwd and trl and trl != 0:
                eps_rev = round((fwd / trl - 1) * 100, 2)
        result["eps_rev"] = eps_rev

        # Relative Strength vs benchmark
        result["rel_strength"] = _rel_strength(t, benchmark)

        # ── Metriche supplementari utili ──────────────────────────────────
        result["operating_margin"] = _safe(info.get("operatingMargins"), multiplier=100)
        result["profit_margin"]    = _safe(info.get("profitMargins"),    multiplier=100)
        result["rev_growth"]       = _safe(info.get("revenueGrowth"),    multiplier=100)
        result["roa"]              = _safe(info.get("returnOnAssets"),   multiplier=100)
        result["current_ratio"]    = _safe(info.get("currentRatio"))
        result["dividend_yield"]   = _safe(info.get("dividendYield"))
        result["peg"]              = _safe(info.get("trailingPegRatio") or info.get("pegRatio"))
        result["52w_change"]       = _safe(info.get("52WeekChange"),     multiplier=100)
        result["prezzo"]           = _safe(info.get("currentPrice") or info.get("regularMarketPrice"))
        result["target_price"]     = _safe(info.get("targetMeanPrice"))

    except Exception as exc:
        result["_errore"] = str(exc)

    # Rimuovi None per pulizia
    return {k: v for k, v in result.items() if v is not None}


# ── SCORING VQM ──────────────────────────────────────────────────────────────
# Score 0-10 per ogni metrica, poi score per pillar (VALUE, QUALITY, MOMENTUM).
# Score finale = 30% VALUE + 40% QUALITY + 30% MOMENTUM (pesi default).

def _score_metric(val: float | None, good: float, bad: float,
                  lower_is_better: bool = False) -> float | None:
    """
    Normalizza una metrica su scala 0-10.
    good = soglia ottimale, bad = soglia negativa.
    Gestisce sia 'lower is better' (multipli di valutazione) che 'higher is better'.
    """
    if val is None:
        return None
    if lower_is_better:
        # val <= good → 10, val >= bad → 0
        if bad <= good:
            return None
        norm = (bad - val) / (bad - good)
    else:
        # val >= good → 10, val <= bad → 0
        if good <= bad:
            return None
        norm = (val - bad) / (good - bad)
    return round(max(0.0, min(10.0, norm * 10)), 2)


def _get_thresholds(sector: str) -> dict:
    return _THRESHOLDS.get(sector, _THRESHOLDS["_default"])


def calc_vqm_score(metrics: dict) -> dict:
    """
    Calcola score VALUE (0-10), QUALITY (0-10), MOMENTUM (0-10) e SCORE FINALE.
    Restituisce un dict con i punteggi e i singoli score di metrica.
    """
    w_value    = _VQM_WEIGHTS["value"]
    w_quality  = _VQM_WEIGHTS["quality"]
    w_momentum = _VQM_WEIGHTS["momentum"]
    sector = metrics.get("settore", "_default")
    thr    = _get_thresholds(sector)
    scores = {}

    def pillar_score(defs: list) -> float | None:
        vals = []
        for metric, good, bad, lib in defs:
            if good is None:   # metrica N/A per questo settore
                continue
            v = metrics.get(metric)
            s = _score_metric(v, good, bad, lib)
            if s is not None:
                vals.append(s)
                scores[f"score_{metric}"] = s
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    score_v = pillar_score(thr["value"])
    score_q = pillar_score(thr["quality"])
    score_m = pillar_score(thr["momentum"])

    # Score finale pesato (escludi pillar mancanti per non penalizzare)
    parts = []
    weights = []
    if score_v is not None:
        parts.append(score_v * w_value)
        weights.append(w_value)
    if score_q is not None:
        parts.append(score_q * w_quality)
        weights.append(w_quality)
    if score_m is not None:
        parts.append(score_m * w_momentum)
        weights.append(w_momentum)

    score_finale = round(sum(parts) / sum(weights), 2) if weights else None

    return {
        "score_value":    score_v,
        "score_quality":  score_q,
        "score_momentum": score_m,
        "score_finale":   score_finale,
        **scores,
    }


def _classify(score: float | None) -> str:
    if score is None:
        return "N/D"
    if score >= 7.5:
        return "BUY"
    if score >= 5.0:
        return "HOLD"
    return "SELL"


# ── AI COMMENTARY ─────────────────────────────────────────────────────────────

_COMMENT_SYSTEM = (
    "You are a senior equity analyst. Respond entirely in {language}. "
    "Given the quantitative VQM score and the latest news, write exactly 2-3 concise "
    "sentences on the investment outlook for {ticker}. "
    "Reference specific numbers from the data. "
    "No preamble, no conclusion, no generic disclaimers."
)


def _search_ticker_news(ticker: str, nome: str) -> str:
    """Ricerca Tavily per un singolo ticker. Restituisce stringa breve o ''."""
    if not TAVILY_API_KEY:
        return ""
    try:
        from tavily import TavilyClient  # type: ignore
        client = TavilyClient(api_key=TAVILY_API_KEY)
        result = client.search(
            topic="finance",
            query=f"{ticker} {nome} stock news analyst outlook earnings",
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
            parts.append("Headlines: " + " | ".join(headlines[:4]))
        return " ".join(parts)[:800]
    except Exception:
        return ""


def _ai_comment(row: dict) -> str:
    """
    Genera un commento AI (2-3 frasi) per un ticker:
      1. Cerca news recenti su Tavily
      2. Passa dati VQM + news all'LLM per il commento
    Restituisce stringa vuota se API keys mancanti o in caso di errore.
    """
    if not LLM_API_KEY:
        return ""

    ticker = row.get("ticker", "?")
    nome   = row.get("nome", ticker)
    news   = _search_ticker_news(ticker, nome)

    metric_parts: list[str] = []
    for k, lbl in [
        ("pe",            "P/E"),
        ("ev_ebitda",     "EV/EBITDA"),
        ("roe",           "ROE%"),
        ("ebitda_margin", "EBITDA M%"),
        ("de_ratio",      "D/E"),
        ("eps_cagr_5y",   "EPS CAGR%"),
        ("mom_12m1m",     "Mom 12M%"),
        ("rel_strength",  "Rel Str"),
    ]:
        v = row.get(k)
        if v is not None:
            metric_parts.append(f"{lbl}={v}")

    user_msg = (
        f"Ticker: {ticker} — {nome} ({row.get('settore', 'N/D')})\n"
        f"VQM Score: {row.get('score_finale', 'N/D')}/10  ({row.get('classificazione', 'N/D')})\n"
        f"Value={row.get('score_value', 'N/D')}  Quality={row.get('score_quality', 'N/D')}  "
        f"Momentum={row.get('score_momentum', 'N/D')}\n"
        f"Metriche: {', '.join(metric_parts) or 'N/D'}\n\n"
        f"News recenti:\n{news or 'Nessuna notizia trovata.'}"
    )

    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = ChatOpenAI(
            api_key=LLM_API_KEY,
            model=LLM_MODEL,
            max_tokens=300,
            store=False,
        )
        system = _COMMENT_SYSTEM.format(language=ANALYSIS_LANGUAGE, ticker=ticker)
        response = llm.invoke([
            SystemMessage(content=system),
            HumanMessage(content=user_msg),
        ])
        return getattr(response, "content", "").strip()
    except Exception:
        return ""


# ── EXPORT JSON ──────────────────────────────────────────────────────────────

def _clean(v):
    """Rende un valore serializzabile in JSON (gestisce NaN, inf, pandas NA)."""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        # Arrotonda float a 2 decimali
        return round(f, 2)
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        return v
    return None


_VALUE_KEYS    = ["ev_ebitda", "p_fcf", "pe", "p_book"]
_QUALITY_KEYS  = ["roe", "ebitda_margin", "gross_margin", "de_ratio", "eps_cagr_5y"]
_MOMENTUM_KEYS = ["mom_12m1m", "eps_rev", "rel_strength"]
_EXTRA_KEYS    = [
    "operating_margin", "profit_margin", "rev_growth", "roa",
    "current_ratio", "dividend_yield", "peg", "52w_change",
    "target_price",
]


def export_json(results: list[dict], path: str, benchmark: str, ai_enabled: bool = False) -> None:
    """
    Produce un JSON strutturato:
      metadata  → informazioni run
      ranking   → lista titoli ordinata per score (con commento_ai se --ai)
      riepilogo → {BUY: [...], HOLD: [...], SELL: [...]}
      metriche_mancanti → {ticker: [metrica, ...]}
    """
    ranking:   list[dict]      = []
    riepilogo: dict[str, list] = {"BUY": [], "HOLD": [], "SELL": [], "N/D": []}
    missing:   dict[str, list] = {}

    vqm_metrics = _VALUE_KEYS + _QUALITY_KEYS + _MOMENTUM_KEYS

    for r in results:
        t = r.get("ticker", "?")
        mancanti = [m for m in vqm_metrics if r.get(m) is None]
        if mancanti:
            missing[t] = mancanti

        entry: dict = {
            "rank":            r.get("rank"),
            "ticker":          r.get("ticker"),
            "nome":            r.get("nome"),
            "settore":         r.get("settore"),
            "industria":       r.get("industria"),
            "prezzo":          _clean(r.get("prezzo")),
            "mktcap":          r.get("mktcap"),
            "valuta":          r.get("valuta", "EUR"),
            "value": {
                **{k: _clean(r.get(k)) for k in _VALUE_KEYS},
                "score": _clean(r.get("score_value")),
            },
            "quality": {
                **{k: _clean(r.get(k)) for k in _QUALITY_KEYS},
                "score": _clean(r.get("score_quality")),
            },
            "momentum": {
                **{k: _clean(r.get(k)) for k in _MOMENTUM_KEYS},
                "score": _clean(r.get("score_momentum")),
            },
            "score_finale":    _clean(r.get("score_finale")),
            "classificazione": r.get("classificazione", "N/D"),
            "extra":           {k: _clean(r.get(k)) for k in _EXTRA_KEYS if r.get(k) is not None},
        }
        if ai_enabled:
            entry["commento_ai"] = r.get("commento_ai", "")
        if r.get("_errore"):
            entry["errore"] = r["_errore"]

        ranking.append(entry)
        riepilogo.setdefault(r.get("classificazione", "N/D"), []).append(t)

    output = {
        "metadata": {
            "generato_il":   datetime.date.today().isoformat(),
            "benchmark":     benchmark,
            "n_tickers":     len(results),
            "ai_commentary": ai_enabled,
            "pesi_score":    {"value": 0.30, "quality": 0.40, "momentum": 0.30},
            "soglie":        {"BUY": ">=7.5", "HOLD": "5.0-7.4", "SELL": "<5.0"},
            "fonti": [
                "Yahoo Finance Statistics (EV/EBITDA, P/E, P/B, ROE, EBITDA Margin, Gross Margin, D/E)",
                "yfinance calcolato (P/FCF = marketCap/freeCashflow)",
                "yfinance income_stmt (EPS CAGR 5Y)",
                "yfinance prezzi storici (Momentum 12M-1M, Relative Strength)",
                "yfinance earningsQuarterlyGrowth o forwardEps/trailingEps (EPS Rev proxy)",
            ],
        },
        "ranking":           ranking,
        "riepilogo":         {k: v for k, v in riepilogo.items() if v},
        "metriche_mancanti": missing,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


# ── SAVE DIALOG ──────────────────────────────────────────────────────────────

def _ask_save_path(tickers: list[str]) -> str | None:
    """Apre una finestra Save As nativa per scegliere il path del JSON di output."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        n = len(tickers)
        joined = "_".join(tickers)
        safe_tickers = f"{n}tickers" if len(joined) > 80 else joined
        default_name = f"{datetime.date.today().isoformat()}_{safe_tickers}.json"
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.asksaveasfilename(
            parent=root,
            title="Salva risultati VQM",
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        root.destroy()
        return path if path else None
    except Exception:
        return None


# ── CORE SCREENER ─────────────────────────────────────────────────────────────

def _fetch_and_score(ticker: str, benchmark: str) -> tuple[str, dict, float]:
    """Fetch + score per un singolo ticker. Usato da ThreadPoolExecutor."""
    t0  = time.perf_counter()
    m   = fetch_metrics(ticker, benchmark)
    s   = calc_vqm_score(m)
    row = {**m, **s}
    row["classificazione"] = _classify(s.get("score_finale"))
    return ticker, row, time.perf_counter() - t0


def run_screener(
    tickers: list[str],
    benchmark: str   = SCREENER_BENCHMARK,
    output_path: str = "screener_vqm.json",
    ai: bool         = False,
    workers: int     = SCREENER_WORKERS,
) -> list[dict]:
    """
    Pipeline principale:
      1. Fetch parallelo metriche da Yahoo Finance
      2. Scoring VQM deterministico
      3. [opzionale] Commento AI per ticker (Tavily + LLM)
      4. Export JSON strutturato
    """
    total   = len(tickers)
    results: dict[str, dict] = {}

    # ── Fase 1: fetch parallelo ────────────────────────────────────────────
    _print_status(f"Recupero dati  0/{total}")
    done = 0

    with ThreadPoolExecutor(max_workers=min(total, workers)) as exe:
        futures = {exe.submit(_fetch_and_score, t, benchmark): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                _, row, elapsed = future.result()
                results[ticker] = row
                done += 1
                sf  = row.get("score_finale")
                cls = row.get("classificazione", "N/D")
                cls_color = (
                    Fore.GREEN  if cls == "BUY"  else
                    Fore.YELLOW if cls == "HOLD" else
                    Fore.RED    if cls == "SELL" else
                    Fore.WHITE
                )
                score_str = f"{sf:.1f}" if sf is not None else "N/D"
                _clear_line()
                print(
                    f"  {Fore.CYAN}●{Style.RESET_ALL}  "
                    f"{Fore.WHITE}{ticker:<12}{Style.RESET_ALL}"
                    f"{Style.DIM}{row.get('nome',''):<28}{Style.RESET_ALL}"
                    f"Score: {cls_color}{Style.BRIGHT}{score_str:<5}{Style.RESET_ALL}  "
                    f"{cls_color}{cls:<4}{Style.RESET_ALL}"
                    f"  {Style.DIM}({elapsed:.1f}s){Style.RESET_ALL}"
                )
                _print_status(f"Recupero dati  {done}/{total}")
            except Exception as exc:
                done += 1
                _clear_line()
                print(
                    f"  {Fore.RED}✗{Style.RESET_ALL}  "
                    f"{Fore.RED}{ticker:<12}{Style.RESET_ALL}"
                    f"{Style.DIM}{str(exc)[:50]}{Style.RESET_ALL}"
                )
                results[ticker] = {"ticker": ticker, "_errore": str(exc)}
                _print_status(f"Recupero dati  {done}/{total}")

    _clear_line()

    # Ordina per score_finale decrescente, preserva ordine input per parità
    ordered = sorted(
        [results[t] for t in tickers],
        key=lambda x: x.get("score_finale") or -1,
        reverse=True,
    )
    for i, r in enumerate(ordered, 1):
        r["rank"] = i

    # ── Fase 2: commento AI (opzionale) ───────────────────────────────────
    if ai:
        if not LLM_API_KEY:
            print(
                f"  {Fore.YELLOW}⚠{Style.RESET_ALL}  "
                f"{Style.DIM}--ai richiesto ma LLM_API_KEY non configurata. "
                f"Commenti AI saltati.{Style.RESET_ALL}"
            )
        else:
            valid = [r for r in ordered if not r.get("_errore")]
            n_ai  = len(valid)
            done_ai = 0
            _print_status(f"Commenti AI  0/{n_ai}")

            with ThreadPoolExecutor(max_workers=min(n_ai, 4)) as exe:
                futures_ai = {exe.submit(_ai_comment, r): r["ticker"] for r in valid}
                for future in as_completed(futures_ai):
                    ticker = futures_ai[future]
                    try:
                        results[ticker]["commento_ai"] = future.result()
                    except Exception:
                        results[ticker]["commento_ai"] = ""
                    done_ai += 1
                    _clear_line()
                    print(
                        f"  {Fore.CYAN}●{Style.RESET_ALL}  "
                        f"{Style.DIM}AI: {ticker:<12} commento generato{Style.RESET_ALL}"
                    )
                    _print_status(f"Commenti AI  {done_ai}/{n_ai}")

            _clear_line()

    # ── Fase 3: export JSON ────────────────────────────────────────────────
    export_json(ordered, output_path, benchmark, ai_enabled=ai)

    return ordered


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Finance Bot — VQM Screener: scoring quantitativo + AI commentary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python screener.py\n"
            "  python screener.py ISP.MI UCG.MI ENI.MI\n"
            "  python screener.py --ai\n"
            "  python screener.py --ai --out report.json\n"
            "  python screener.py --benchmark SPY\n"
        ),
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        metavar="TICKER",
        help="Ticker symbols da analizzare (sovrascrive i default se forniti).",
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        default=False,
        help="Genera un commento AI per ogni ticker (Tavily search + LLM).",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="screener_vqm.json",
        help="File JSON di output (default: screener_vqm.json).",
    )
    parser.add_argument(
        "--benchmark",
        type=str,
        default=SCREENER_BENCHMARK,
        help=f"Ticker benchmark per Relative Strength (default: {SCREENER_BENCHMARK}).",
    )
    args = parser.parse_args()

    tickers: list[str] = (
        [t.upper() for t in args.tickers]
        if args.tickers
        else DEFAULT_TICKERS
    )

    # ── Save dialog ─────────────────────────────────────────────────────────
    save_path = _ask_save_path(tickers) or args.out

    # ── Header ─────────────────────────────────────────────────────────────
    print()
    print(f"{Fore.CYAN}{Style.BRIGHT}◆  Finance Bot — VQM Screener{Style.RESET_ALL}")
    print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}")
    ticker_fmt = "  ".join(tickers) if len(tickers) <= 6 else f"{len(tickers)} titoli"
    ai_label = (
        f"{Fore.GREEN}{Style.BRIGHT}abilitato{Style.RESET_ALL}"
        if args.ai else
        f"{Style.DIM}disabilitato  (usa --ai per attivare){Style.RESET_ALL}"
    )
    print(
        f"{Style.DIM}Tickers:{Style.RESET_ALL}   {Fore.WHITE}{ticker_fmt}{Style.RESET_ALL}\n"
        f"{Style.DIM}Benchmark:{Style.RESET_ALL} {Fore.WHITE}{args.benchmark}{Style.RESET_ALL}\n"
        f"{Style.DIM}Output:{Style.RESET_ALL}    {Fore.WHITE}{save_path}{Style.RESET_ALL}\n"
        f"{Style.DIM}AI:{Style.RESET_ALL}        {ai_label}"
    )
    print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}\n")

    t_start = time.perf_counter()

    try:
        results = run_screener(
            tickers,
            benchmark=args.benchmark,
            output_path=save_path,
            ai=args.ai,
        )
    except KeyboardInterrupt:
        _clear_line()
        print(f"\n{Fore.YELLOW}  Interrotto.{Style.RESET_ALL}\n")
        sys.exit(0)

    elapsed_total = time.perf_counter() - t_start

    # ── TOP 10 ──────────────────────────────────────────────────────────────
    print(f"\n{Style.DIM}{'─' * 62}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{Style.BRIGHT}  TOP 10{Style.RESET_ALL}")
    print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}")
    for r in results[:10]:
        sf  = r.get("score_finale")
        cls = r.get("classificazione", "N/D")
        cls_color = (
            Fore.GREEN  if cls == "BUY"  else
            Fore.YELLOW if cls == "HOLD" else
            Fore.RED    if cls == "SELL" else
            Fore.WHITE
        )
        score_str = f"{sf:.1f}" if sf is not None else "N/D"
        print(
            f"  {Style.DIM}{r.get('rank'):>2}.{Style.RESET_ALL}  "
            f"{Fore.WHITE}{r.get('ticker'):<12}{Style.RESET_ALL}"
            f"{Style.DIM}{r.get('nome',''):<28}{Style.RESET_ALL}"
            f"Score: {cls_color}{Style.BRIGHT}{score_str:<5}{Style.RESET_ALL}  "
            f"{cls_color}{cls}{Style.RESET_ALL}"
        )

    # ── Footer ──────────────────────────────────────────────────────────────
    print(f"\n  {Fore.GREEN}✓{Style.RESET_ALL}  File salvato: {Fore.WHITE}{save_path}{Style.RESET_ALL}")
    print(f"{Style.DIM}  ⏱ {elapsed_total:.1f}s{Style.RESET_ALL}")
    print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{Style.BRIGHT}  Screener completato.{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
