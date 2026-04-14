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
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import yfinance as yf
import colorama
from colorama import Fore, Style

import db as db_module
from config import (
    ANALYSIS_LANGUAGE,
    LLM_API_KEY,
    LLM_MODEL,
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

# Cache storico benchmark: evita di ri-scaricare N volte lo stesso indice
# quando si analizzano più ticker sullo stesso mercato (thread-safe: worst case
# è un double-fetch sul primo accesso parallelo, innocuo).
_bm_history_cache: dict[str, pd.DataFrame] = {}


def _rel_strength(ticker_obj: yf.Ticker, benchmark: str = "FTSEMIB.MI") -> float | None:
    """Rendimento 12M titolo - rendimento 12M benchmark (benchmark cachato)."""
    try:
        hist_t = ticker_obj.history(period="12mo", auto_adjust=True)
        if hist_t.empty:
            return None
        if benchmark not in _bm_history_cache:
            _bm_history_cache[benchmark] = yf.Ticker(benchmark).history(
                period="12mo", auto_adjust=True
            )
        hist_b = _bm_history_cache[benchmark]
        if hist_b.empty:
            return None
        ret_t = (hist_t["Close"].iloc[-1] / hist_t["Close"].iloc[0] - 1) * 100
        ret_b = (hist_b["Close"].iloc[-1] / hist_b["Close"].iloc[0] - 1) * 100
        return round(ret_t - ret_b, 2)
    except Exception:
        return None

def _eps_cagr_5y(ticker_obj: yf.Ticker, info: dict,
                 annual_inc=None) -> float | None:
    """
    CAGR EPS a 5 anni — 2 livelli di fallback:
    1. Net Income / shares da income_stmt annuale (fino a 4 anni → CAGR)
    2. Proxy: earningsGrowth / earningsQuarterlyGrowth / revenueGrowth da info
    annual_inc: dataframe già fetchato di t.income_stmt (evita double-fetch).
    """
    # Livello 1: Net Income / shares da income_stmt
    try:
        inc = annual_inc if annual_inc is not None else ticker_obj.income_stmt
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

# ── CHIAVI STANDARD STATEMENTS ──────────────────────────────────────────────

_NI_KEYS     = ("Net Income", "Net Income Common Stockholders",
                "Net Income From Continuing Operations")
_EQ_KEYS     = ("Stockholders Equity", "Common Stock Equity",
                "Total Equity Gross Minority Interest")
_REV_KEYS    = ("Total Revenue", "Revenue")
_GP_KEYS     = ("Gross Profit",)
_COGS_KEYS   = ("Cost Of Revenue", "Cost Of Goods Sold", "Reconciled Cost Of Revenue")
_OI_KEYS     = ("Operating Income", "Total Operating Income As Reported", "EBIT")
_EBITDA_KEYS = ("EBITDA", "Normalized EBITDA")
_DA_CF_KEYS  = ("Depreciation And Amortization", "Depreciation Amortization Depletion",
                "Depreciation")
_DA_INC_KEYS = ("Depreciation And Amortization", "Reconciled Depreciation",
                "Depreciation Amortization Depletion")
_OCF_KEYS    = ("Operating Cash Flow", "Cash Flows From Operations",
                "Net Cash Provided By Operating Activities")
_CAPEX_KEYS  = ("Capital Expenditure", "Capital Expenditures",
                "Purchase Of Property Plant And Equipment")
_DEBT_KEYS   = ("Total Debt", "Long Term Debt", "Total Long Term Debt")
_CASH_KEYS   = ("Cash And Cash Equivalents",
                "Cash Cash Equivalents And Short Term Investments")
_ASSETS_KEYS = ("Total Assets",)


def _ttm(keys: tuple, stmt, n: int = 4) -> float | None:
    """
    Somma degli ultimi n periodi (4 trimestri = TTM, oppure 1 anno) per una
    riga di income_stmt o cashflow. Le colonne in yfinance sono ordinate dal
    più recente al più vecchio, quindi iloc[:n] = ultimi n periodi.
    """
    if stmt is None or stmt.empty:
        return None
    for key in keys:
        if key in stmt.index:
            row = stmt.loc[key].dropna()
            if len(row) >= 1:
                take = min(n, len(row))
                return float(row.iloc[:take].sum())
    return None


def _mrq(keys: tuple, stmt) -> float | None:
    """Valore del trimestre/periodo più recente da uno statement (balance sheet)."""
    if stmt is None or stmt.empty:
        return None
    for key in keys:
        if key in stmt.index:
            row = stmt.loc[key].dropna()
            if len(row) >= 1 and pd.notna(row.iloc[0]):
                return float(row.iloc[0])
    return None


def fetch_metrics(ticker: str, benchmark: str = "FTSEMIB.MI") -> dict:
    """
    Scarica e calcola tutte le metriche VQM per un singolo ticker.

    Strategia di calcolo (priorità decrescente):
      1. Calcolo diretto da quarterly statements → TTM per flussi/conto economico,
         MRQ per balance sheet. Evita discrepanze con i valori pre-calcolati da Yahoo.
      2. Fallback su annual statements se quarterly non disponibili.
      3. Fallback su info dict come ultima risorsa.
    """
    result: dict = {"ticker": ticker}
    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}

        # ── Info base (sempre da info — non presenti negli statements) ─────
        result["nome"]      = info.get("shortName") or info.get("longName") or ticker
        result["settore"]   = info.get("sector")    or info.get("quoteType") or "N/D"
        result["industria"] = info.get("industry")  or "N/D"
        result["mktcap"]    = info.get("marketCap")
        result["valuta"]    = info.get("currency")  or "EUR"
        mktcap = result["mktcap"]

        # ── Fetch statements ────────────────────────────────────────────────
        # Quarterly preferito per TTM; annual come fallback
        try:
            q_inc = t.quarterly_income_stmt
        except Exception:
            q_inc = None
        try:
            q_bs = t.quarterly_balance_sheet
        except Exception:
            q_bs = None
        try:
            q_cf = t.quarterly_cashflow
        except Exception:
            q_cf = None
        try:
            a_inc = t.income_stmt
        except Exception:
            a_inc = None
        try:
            a_bs = t.balance_sheet
        except Exception:
            a_bs = None
        try:
            a_cf = t.cashflow
        except Exception:
            a_cf = None

        inc_q = q_inc if (q_inc is not None and not q_inc.empty) else None
        bs_q  = q_bs  if (q_bs  is not None and not q_bs.empty)  else None
        cf_q  = q_cf  if (q_cf  is not None and not q_cf.empty)  else None
        inc_a = a_inc if (a_inc is not None and not a_inc.empty) else None
        bs_a  = a_bs  if (a_bs  is not None and not a_bs.empty)  else None
        cf_a  = a_cf  if (a_cf  is not None and not a_cf.empty)  else None

        # Sorgente attiva: quarterly se disponibile, altrimenti annual
        inc = inc_q or inc_a
        bs  = bs_q  or bs_a
        cf  = cf_q  or cf_a
        n_periods = 4 if inc is inc_q else 1
        n_cf      = 4 if cf  is cf_q  else 1

        # ── TTM — somma ultimi 4 trimestri (o 1 anno) ─────────────────────
        ttm_revenue = _ttm(_REV_KEYS,    inc, n_periods)
        ttm_ni      = _ttm(_NI_KEYS,     inc, n_periods)
        ttm_gp      = _ttm(_GP_KEYS,     inc, n_periods)
        ttm_cogs    = _ttm(_COGS_KEYS,   inc, n_periods)
        ttm_oi      = _ttm(_OI_KEYS,     inc, n_periods)
        ttm_ebitda  = _ttm(_EBITDA_KEYS, inc, n_periods)
        ttm_ocf     = _ttm(_OCF_KEYS,    cf,  n_cf)
        ttm_capex   = _ttm(_CAPEX_KEYS,  cf,  n_cf)
        # D&A: cashflow più affidabile, poi income stmt
        ttm_da = (_ttm(_DA_CF_KEYS,  cf,  n_cf) or
                  _ttm(_DA_INC_KEYS, inc, n_periods))
        # EBITDA: valore diretto o EBIT + D&A
        if ttm_ebitda is None and ttm_oi is not None and ttm_da is not None:
            ttm_ebitda = ttm_oi + abs(ttm_da)

        # ── MRQ — balance sheet più recente ───────────────────────────────
        mrq_equity = _mrq(_EQ_KEYS,    bs)
        mrq_debt   = _mrq(_DEBT_KEYS,  bs)
        mrq_cash   = _mrq(_CASH_KEYS,  bs)
        mrq_assets = _mrq(_ASSETS_KEYS, bs)

        # Debt composito: LTD + Current Debt se "Total Debt" non c'è
        if mrq_debt is None and bs is not None and not bs.empty:
            _ltd = _mrq(("Long Term Debt And Capital Lease Obligation",
                         "Long Term Debt"), bs)
            _std = _mrq(("Current Debt And Capital Lease Obligation",
                         "Current Debt",
                         "Current Portion Of Long Term Debt"), bs)
            if _ltd is not None:
                mrq_debt = _ltd + (_std or 0.0)

        # ── VALUE ──────────────────────────────────────────────────────────

        # P/E = mktcap / TTM Net Income
        if mktcap and ttm_ni and ttm_ni > 0:
            result["pe"] = round(mktcap / ttm_ni, 2)
        if result.get("pe") is None:
            result["pe"] = _safe(info.get("trailingPE"))

        # EV = mktcap + total_debt − cash (MRQ)
        ev = None
        if mktcap:
            ev = mktcap + (mrq_debt or 0) - (mrq_cash or 0)
        if ev is None:
            ev = info.get("enterpriseValue")
        # EV/EBITDA = EV / TTM EBITDA
        if ev and ttm_ebitda and ttm_ebitda > 0:
            result["ev_ebitda"] = round(ev / ttm_ebitda, 2)
        if result.get("ev_ebitda") is None:
            result["ev_ebitda"] = _safe(info.get("enterpriseToEbitda"))

        # P/Book = mktcap / MRQ equity
        if mktcap and mrq_equity and mrq_equity > 0:
            result["p_book"] = round(mktcap / mrq_equity, 2)
        if result.get("p_book") is None:
            result["p_book"] = _safe(info.get("priceToBook"))

        # P/FCF = mktcap / TTM FCF  (FCF = OCF − |CapEx|)
        if ttm_ocf is not None and ttm_capex is not None:
            ttm_fcf = (ttm_ocf + ttm_capex          # CapEx negativo in yfinance
                       if ttm_capex < 0
                       else ttm_ocf - ttm_capex)
            if mktcap and ttm_fcf > 0:
                result["p_fcf"] = round(mktcap / ttm_fcf, 2)
        if result.get("p_fcf") is None:
            fcf_info = info.get("freeCashflow")
            if fcf_info and mktcap and fcf_info > 0:
                result["p_fcf"] = round(mktcap / fcf_info, 2)

        # ── QUALITY ────────────────────────────────────────────────────────

        # ROE = TTM Net Income / MRQ Equity * 100
        if ttm_ni is not None and mrq_equity and mrq_equity > 0:
            result["roe"] = round(ttm_ni / mrq_equity * 100, 2)
        if result.get("roe") is None:
            result["roe"] = _safe(info.get("returnOnEquity"), multiplier=100)

        # EBITDA Margin = TTM EBITDA / TTM Revenue * 100
        if ttm_ebitda is not None and ttm_revenue and ttm_revenue > 0:
            result["ebitda_margin"] = round(ttm_ebitda / ttm_revenue * 100, 2)
        if result.get("ebitda_margin") is None:
            result["ebitda_margin"] = _safe(info.get("ebitdaMargins"), multiplier=100)

        # Gross Margin = TTM Gross Profit / TTM Revenue * 100
        if ttm_gp is not None and ttm_revenue and ttm_revenue > 0:
            result["gross_margin"] = round(ttm_gp / ttm_revenue * 100, 2)
        elif ttm_cogs is not None and ttm_revenue and ttm_revenue > 0:
            result["gross_margin"] = round((ttm_revenue - ttm_cogs) / ttm_revenue * 100, 2)
        if result.get("gross_margin") is None:
            result["gross_margin"] = _safe(info.get("grossMargins"), multiplier=100)

        # D/E = MRQ Total Debt / MRQ Equity
        if mrq_debt is not None and mrq_equity and mrq_equity > 0:
            result["de_ratio"] = round(mrq_debt / mrq_equity, 2)
        if result.get("de_ratio") is None:
            de_info = _safe(info.get("debtToEquity"))
            # Yahoo a volte restituisce D/E come percentuale (82 → 0.82×)
            if de_info is not None and de_info > 20:
                de_info = round(de_info / 100, 2)
            result["de_ratio"] = de_info

        # EPS CAGR 5Y: da annual income_stmt (CAGR richiede storico pluriennale)
        # Passa inc_a già fetchato per evitare un secondo download annuale.
        result["eps_cagr_5y"] = _eps_cagr_5y(t, info, annual_inc=inc_a)

        # ── Pulizia multipli VALUE ─────────────────────────────────────────
        # P/E, EV/EBITDA, P/Book, P/FCF negativi (azienda in perdita o equity
        # negativo) non sono significativi come metrica value. Con
        # lower_is_better=True, un valore negativo otterrebbe score=10 (massimo),
        # il che è fuorviante. Si eliminano prima dello scoring.
        for _ratio in ("pe", "ev_ebitda", "p_book", "p_fcf"):
            if result.get(_ratio) is not None and result[_ratio] <= 0:
                del result[_ratio]
        # D/E negativo (equity negativo): la ratio è indefinita, non va scorata.
        if result.get("de_ratio") is not None and result["de_ratio"] < 0:
            del result["de_ratio"]

        # ── MOMENTUM ───────────────────────────────────────────────────────
        result["mom_12m1m"]    = _momentum_12m_1m(t)
        result["rel_strength"] = _rel_strength(t, benchmark)

        # EPS Revision proxy (yfinance non espone revisioni analisti dirette).
        # Priorità:
        #   1. Forward EPS vs Trailing EPS: segnala quanto le stime forward
        #      divergono dal realizzato; è il proxy più stabile.
        #   2. earningsQuarterlyGrowth: crescita YoY trimestrale — utile come
        #      indicatore di momentum EPS ma può essere enorme per turnaround
        #      (es. BMPS: da perdite a utili → +300%). Usato solo come fallback.
        # Il valore finale viene clampato a ±100% per evitare outlier nel display
        # (lo score VQM è già limitato 0-10 dalle soglie good/bad).
        eps_rev = None
        fwd = info.get("forwardEps")
        trl = info.get("trailingEps")
        if fwd and trl and trl != 0:
            eps_rev = round((fwd / trl - 1) * 100, 2)
        if eps_rev is None:
            for key in ("earningsQuarterlyGrowth", "earningsGrowth"):
                v = info.get(key)
                if v is not None:
                    eps_rev = _safe(v, multiplier=100)
                    break
        # Clamp outlier: revisioni reali raramente escono da ±100%
        if eps_rev is not None:
            eps_rev = round(max(-100.0, min(100.0, eps_rev)), 2)
        result["eps_rev"] = eps_rev

        # ── Metriche supplementari ─────────────────────────────────────────
        # Operating Margin = TTM Operating Income / TTM Revenue * 100
        if ttm_oi is not None and ttm_revenue and ttm_revenue > 0:
            result["operating_margin"] = round(ttm_oi / ttm_revenue * 100, 2)
        if result.get("operating_margin") is None:
            result["operating_margin"] = _safe(info.get("operatingMargins"), multiplier=100)

        # Profit Margin = TTM Net Income / TTM Revenue * 100
        if ttm_ni is not None and ttm_revenue and ttm_revenue > 0:
            result["profit_margin"] = round(ttm_ni / ttm_revenue * 100, 2)
        if result.get("profit_margin") is None:
            result["profit_margin"] = _safe(info.get("profitMargins"), multiplier=100)

        # ROA = TTM Net Income / MRQ Total Assets * 100
        if ttm_ni is not None and mrq_assets and mrq_assets > 0:
            result["roa"] = round(ttm_ni / mrq_assets * 100, 2)
        if result.get("roa") is None:
            result["roa"] = _safe(info.get("returnOnAssets"), multiplier=100)

        # Revenue Growth = (TTM Revenue / Prior-year TTM Revenue − 1) * 100
        rev_growth = None
        if inc_q is not None and not inc_q.empty:
            ttm_rev_now  = _ttm(_REV_KEYS, inc_q, 4)
            ttm_rev_prev = _ttm(_REV_KEYS, inc_q.iloc[:, 4:], 4)   # 4 trimestri precedenti
            if ttm_rev_now and ttm_rev_prev and ttm_rev_prev > 0:
                rev_growth = round((ttm_rev_now / ttm_rev_prev - 1) * 100, 2)
        if rev_growth is None:
            rev_growth = _safe(info.get("revenueGrowth"), multiplier=100)
        result["rev_growth"] = rev_growth

        result["current_ratio"]  = _safe(info.get("currentRatio"))
        result["dividend_yield"] = _safe(info.get("dividendYield"))
        result["peg"]            = _safe(info.get("trailingPegRatio") or info.get("pegRatio"))
        result["52w_change"]     = _safe(info.get("52WeekChange"),     multiplier=100)
        result["prezzo"]         = _safe(info.get("currentPrice") or info.get("regularMarketPrice"))

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


_VALUE_KEYS    = ["ev_ebitda", "p_fcf", "pe", "p_book"]
_QUALITY_KEYS  = ["roe", "ebitda_margin", "gross_margin", "de_ratio", "eps_cagr_5y"]
_MOMENTUM_KEYS = ["mom_12m1m", "eps_rev", "rel_strength"]
_EXTRA_KEYS    = [
    "operating_margin", "profit_margin", "rev_growth", "roa",
    "current_ratio", "dividend_yield", "peg", "52w_change",
]


# ── CORE SCREENER ─────────────────────────────────────────────────────────────

# Mappa suffisso ticker → benchmark Yahoo Finance
_SUFFIX_BENCHMARK: dict[str, str] = {
    # Italia
    ".MI":  "FTSEMIB.MI",
    # Germania
    ".DE":  "^GDAXI",
    ".F":   "^GDAXI",
    ".BE":  "^GDAXI",
    # Francia
    ".PA":  "^FCHI",
    # Spagna
    ".MC":  "^IBEX",
    # UK
    ".L":   "^FTSE",
    # Svizzera
    ".SW":  "^SSMI",
    # Paesi Bassi
    ".AS":  "^AEX",
    # Portogallo
    ".LS":  "^PSI20",
    # Giappone
    ".T":   "^N225",
    # Hong Kong
    ".HK":  "^HSI",
    # Canada
    ".TO":  "^GSPTSE",
    ".V":   "^GSPTSE",
    # Australia
    ".AX":  "^AXJO",
    # Brasile
    ".SA":  "^BVSP",
}
_US_BENCHMARK = "SPY"   # nessun suffisso → mercato USA


def _benchmark_for_ticker(ticker: str, override: str | None = None) -> str:
    """Restituisce il benchmark appropriato per il ticker in base al suffisso."""
    if override:
        return override
    upper = ticker.upper()
    for suffix, bench in _SUFFIX_BENCHMARK.items():
        if upper.endswith(suffix.upper()):
            return bench
    return _US_BENCHMARK


def _fetch_and_score(ticker: str, benchmark_override: str | None) -> tuple[str, dict, float]:
    """Fetch + score per un singolo ticker. Usato da ThreadPoolExecutor."""
    t0        = time.perf_counter()
    benchmark = _benchmark_for_ticker(ticker, benchmark_override)
    m   = fetch_metrics(ticker, benchmark)
    s   = calc_vqm_score(m)
    row = {**m, **s}
    row["classificazione"] = _classify(s.get("score_finale"))
    row["benchmark"] = benchmark
    return ticker, row, time.perf_counter() - t0


def run_screener(
    tickers: list[str],
    benchmark_override: str | None = None,
    ai: bool         = False,
    workers: int     = SCREENER_WORKERS,
) -> list[dict]:
    """
    Pipeline principale:
      1. Fetch parallelo metriche da Yahoo Finance
      2. Scoring VQM deterministico
      3. [opzionale] Commento AI per ticker (Tavily + LLM)
      4. Salvataggio su PostgreSQL
    benchmark_override: se None ogni ticker usa il benchmark della propria nazione.
    """
    if not tickers:
        return [], None

    total   = len(tickers)
    results: dict[str, dict] = {}

    # ── Fase 1: fetch parallelo ────────────────────────────────────────────
    _print_status(f"Recupero dati  0/{total}")
    done = 0
    fetch_log: list[tuple] = []   # (ticker, row, elapsed, error)

    with ThreadPoolExecutor(max_workers=min(total, workers)) as exe:
        futures = {exe.submit(_fetch_and_score, t, benchmark_override): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                _, row, elapsed = future.result()
                results[ticker] = row
                done += 1
                fetch_log.append((ticker, row, elapsed, None))
            except Exception as exc:
                done += 1
                results[ticker] = {"ticker": ticker, "_errore": str(exc)}
                fetch_log.append((ticker, {"ticker": ticker, "_errore": str(exc)}, 0.0, str(exc)))
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

    # Stampa tutti i risultati ordinati per score, colonna nome allineata al più lungo
    elapsed_map = {ticker: elapsed for ticker, _, elapsed, err in fetch_log if not err}
    valid_rows = [r for r in ordered if not r.get("_errore")]
    nome_col = max(28, max((len(r.get("nome", "")) for r in valid_rows), default=0) + 2)
    for r in ordered:
        if r.get("_errore"):
            print(
                f"  {Fore.RED}✗{Style.RESET_ALL}  "
                f"{Fore.RED}{r['ticker']:<12}{Style.RESET_ALL}"
                f"{Style.DIM}{r['_errore'][:50]}{Style.RESET_ALL}"
            )
        else:
            sf  = r.get("score_finale")
            cls = r.get("classificazione", "N/D")
            cls_color = (
                Fore.GREEN  if cls == "BUY"  else
                Fore.YELLOW if cls == "HOLD" else
                Fore.RED    if cls == "SELL" else
                Fore.WHITE
            )
            score_str = f"{sf:.1f}" if sf is not None else "N/D"
            elapsed = elapsed_map.get(r["ticker"], 0.0)
            print(
                f"  {Fore.CYAN}●{Style.RESET_ALL}  "
                f"{Fore.WHITE}{r['ticker']:<12}{Style.RESET_ALL}"
                f"{Style.DIM}{r.get('nome',''):<{nome_col}}{Style.RESET_ALL}"
                f"Score: {cls_color}{Style.BRIGHT}{score_str:<5}{Style.RESET_ALL}  "
                f"{cls_color}{cls:<4}{Style.RESET_ALL}"
                f"  {Style.DIM}({elapsed:.1f}s){Style.RESET_ALL}"
            )

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

    # ── Fase 3: salvataggio su PostgreSQL ───────────────────────────────
    run_id = db_module.save_run(ordered, benchmark_override or "auto", ai_enabled=ai)

    return ordered, run_id


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

def _print_results_summary(results: list[dict]) -> None:
    """Stampa la lista risultati (usata anche per dati caricati dal DB)."""
    if not results:
        return
    nome_col = max(28, max(len(r.get("nome", "") or "") for r in results) + 2)
    for r in results:
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
            f"  {Fore.CYAN}●{Style.RESET_ALL}  "
            f"{Fore.WHITE}{(r.get('ticker') or ''):<12}{Style.RESET_ALL}"
            f"{Style.DIM}{(r.get('nome') or ''):<{nome_col}}{Style.RESET_ALL}"
            f"Score: {cls_color}{Style.BRIGHT}{score_str:<5}{Style.RESET_ALL}  "
            f"{cls_color}{cls:<4}{Style.RESET_ALL}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Finance Bot — VQM Screener: scoring quantitativo + AI commentary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python screener.py\n"
            "  python screener.py ISP.MI UCG.MI ENI.MI\n"
            "  python screener.py --ai\n"
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
        "--benchmark",
        type=str,
        default=None,
        help="Forza un benchmark unico per tutti i ticker (es. SPY). Default: auto per nazione.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Riesegui anche se esiste già un run di oggi nel DB.",
    )
    args = parser.parse_args()

    tickers: list[str] = (
        [t.upper() for t in args.tickers]
        if args.tickers
        else DEFAULT_TICKERS
    )

    # ── Header ─────────────────────────────────────────────────────────────
    print()
    print(f"{Fore.CYAN}{Style.BRIGHT}◆  Finance Bot — VQM Screener{Style.RESET_ALL}")
    print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}")
    ticker_fmt = "  ".join(tickers) if len(tickers) <= 6 else f"{len(tickers)} titoli"
    bench_label = (
        f"{Fore.WHITE}{args.benchmark}{Style.RESET_ALL}"
        if args.benchmark else
        f"{Style.DIM}auto (per nazione){Style.RESET_ALL}"
    )
    ai_label = (
        f"{Fore.GREEN}{Style.BRIGHT}abilitato{Style.RESET_ALL}"
        if args.ai else
        f"{Style.DIM}disabilitato  (usa --ai per attivare){Style.RESET_ALL}"
    )
    print(
        f"{Style.DIM}Tickers:{Style.RESET_ALL}   {Fore.WHITE}{ticker_fmt}{Style.RESET_ALL}\n"
        f"{Style.DIM}Benchmark:{Style.RESET_ALL} {bench_label}\n"
        f"{Style.DIM}DB:{Style.RESET_ALL}        {Fore.WHITE}{db_module.POSTGRES_DB}@{db_module.POSTGRES_HOST}:{db_module.POSTGRES_PORT}{Style.RESET_ALL}\n"
        f"{Style.DIM}AI:{Style.RESET_ALL}        {ai_label}"
    )
    print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}\n")

    # ── Check run odierno ───────────────────────────────────────────────────
    if not args.force and not args.tickers:
        today_run = db_module.load_today_run()
        if today_run is not None:
            run_id, results = today_run
            elapsed_total = 0.0
            print(
                f"  {Fore.YELLOW}⟳{Style.RESET_ALL}  "
                f"{Style.DIM}Run di oggi già presente (run_id={run_id}). "
                f"Uso i dati esistenti.  (usa --force per rieseguire){Style.RESET_ALL}"
            )
            _print_results_summary(results)
            print(f"\n  {Fore.GREEN}✓{Style.RESET_ALL}  DB: {Fore.WHITE}{db_module.POSTGRES_DB} (run_id={run_id}){Style.RESET_ALL}")
            print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}")
            print(f"{Fore.CYAN}{Style.BRIGHT}  Screener completato.{Style.RESET_ALL}\n")
            return

    t_start = time.perf_counter()

    try:
        results, run_id = run_screener(
            tickers,
            benchmark_override=args.benchmark,
            ai=args.ai,
        )
    except KeyboardInterrupt:
        _clear_line()
        print(f"\n{Fore.YELLOW}  Interrotto.{Style.RESET_ALL}\n")
        sys.exit(0)

    elapsed_total = time.perf_counter() - t_start

    # ── Footer ──────────────────────────────────────────────────────────────
    db_info = f"{db_module.POSTGRES_DB} (run_id={run_id})"
    print(f"\n  {Fore.GREEN}✓{Style.RESET_ALL}  Salvato su DB: {Fore.WHITE}{db_info}{Style.RESET_ALL}")
    print(f"{Style.DIM}  ⏱ {elapsed_total:.1f}s{Style.RESET_ALL}")
    print(f"{Style.DIM}{'─' * 62}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{Style.BRIGHT}  Screener completato.{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
