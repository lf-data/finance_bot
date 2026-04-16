"""Flask web interface for the VQM Screener dashboard."""

import decimal
import datetime
import json as _json
import os
import threading
import time as _time

from flask import Flask, jsonify, render_template, request, send_from_directory
import psycopg2
import psycopg2.extras

from config import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
)

app = Flask(__name__)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
    )


def _normalize(v):
    """Converte Decimal → float e date/datetime → stringa ISO per JSON."""
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def _query(sql: str, params=None) -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [{k: _normalize(v) for k, v in row.items()} for row in cur.fetchall()]
    finally:
        conn.close()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/sw.js")
def service_worker():
    """Serve il service worker dalla root per avere scope completo."""
    resp = send_from_directory("static", "sw.js",
                               mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/tickers")
def api_tickers():
    """Lista di tutti i ticker con l'ultimo score disponibile."""
    rows = _query("""
        SELECT DISTINCT ON (ticker)
            ticker, nome, settore, industria, valuta, benchmark,
            prezzo, mktcap,
            score_value, score_quality, score_momentum, score_finale,
            classificazione, rank, run_date
        FROM screener_results
        WHERE errore IS NULL
        ORDER BY ticker, run_date DESC
    """)
    return jsonify(rows)


@app.route("/api/ticker/<ticker>")
def api_ticker_detail(ticker: str):
    """Serie storica completa per un ticker."""
    rows = _query("""
        SELECT
            sr.run_date,
            sr.ticker, sr.nome, sr.settore, sr.industria, sr.valuta, sr.benchmark,
            sr.prezzo, sr.mktcap,
            -- Value
            sr.ev_ebitda, sr.p_fcf, sr.pe, sr.p_book, sr.fcf_yield, sr.score_value,
            -- Quality
            sr.roe, sr.ebitda_margin, sr.gross_margin, sr.de_ratio,
            sr.eps_cagr_5y, sr.eps_cagr_4y, sr.roic, sr.score_quality,
            -- Momentum
            sr.mom_12m1m, sr.eps_rev, sr.rel_strength, sr.fcf_growth, sr.score_momentum,
            -- Final
            sr.score_finale, sr.classificazione, sr.rank,
            -- Extra
            sr.operating_margin, sr.profit_margin, sr.rev_growth, sr.roa,
            sr.current_ratio, sr.dividend_yield, sr.peg, sr.week52_change,
            -- AI
            sr.commento_ai
        FROM screener_results sr
        JOIN screener_runs run ON sr.run_id = run.id
        WHERE sr.ticker = %s AND sr.errore IS NULL
        ORDER BY sr.run_date ASC
    """, (ticker.upper(),))
    return jsonify(rows)


@app.route("/api/thresholds")
def api_thresholds():
    """Soglie VQM per settore (per il drawer metriche). Struttura: {settore: {metrica: {good, bad, lower_is_better}}}."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thresholds.json")
    with open(path, encoding="utf-8") as f:
        raw = _json.load(f)
    reserved = {"pesi", "tickers"}
    out: dict = {}
    for sector, pillars in raw.items():
        if sector in reserved:
            continue
        out[sector] = {}
        for entries in pillars.values():
            for e in entries:
                out[sector][e["metrica"]] = {
                    "good": e["good"],
                    "bad":  e["bad"],
                    "lower_is_better": e["lower_is_better"],
                }
    return jsonify(out)


@app.route("/api/runs")
def api_runs():
    """Lista delle ultime 30 esecuzioni."""
    rows = _query("""
        SELECT id, run_at, run_date, benchmark, ai_enabled, n_tickers
        FROM screener_runs
        ORDER BY run_at DESC
        LIMIT 30
    """)
    return jsonify(rows)


@app.route("/api/latest")
def api_latest():
    """Tutti i risultati dell'ultima esecuzione, ordinati per rank."""
    rows = _query("""
        SELECT
            sr.ticker, sr.nome, sr.settore, sr.industria, sr.valuta, sr.benchmark,
            sr.prezzo, sr.mktcap,
            sr.ev_ebitda, sr.p_fcf, sr.pe, sr.p_book, sr.fcf_yield, sr.score_value,
            sr.roe, sr.ebitda_margin, sr.gross_margin, sr.de_ratio,
            sr.eps_cagr_5y, sr.eps_cagr_4y, sr.roic, sr.score_quality,
            sr.mom_12m1m, sr.eps_rev, sr.rel_strength, sr.fcf_growth, sr.score_momentum,
            sr.score_finale, sr.classificazione, sr.rank,
            sr.operating_margin, sr.profit_margin, sr.rev_growth, sr.roa,
            sr.current_ratio, sr.dividend_yield, sr.peg, sr.week52_change,
            sr.commento_ai, sr.run_date
        FROM screener_results sr
        JOIN screener_runs run ON sr.run_id = run.id
        WHERE run.id = (SELECT MAX(id) FROM screener_runs)
          AND sr.errore IS NULL
        ORDER BY sr.rank ASC
    """)
    return jsonify(rows)


# ── Manual screener run ──────────────────────────────────────────────────────

_run_lock   = threading.Lock()
_run_status: dict = {"running": False, "error": None}


def _do_run() -> None:
    from screener import run_screener, DEFAULT_TICKERS
    try:
        run_screener(DEFAULT_TICKERS)
        _run_status["error"] = None
    except Exception as exc:  # noqa: BLE001
        app.logger.error("Screener run fallito — %s", exc)
        _run_status["error"] = str(exc)
    finally:
        _run_status["running"] = False
        _run_lock.release()


@app.route("/api/run-screener", methods=["POST"])
def api_run_screener():
    """Avvia lo screener in background. Se già in esecuzione restituisce 409."""
    if not _run_lock.acquire(blocking=False):
        return jsonify({"running": True, "error": None}), 409
    _run_status["running"] = True
    _run_status["error"]   = None
    threading.Thread(target=_do_run, daemon=True, name="screener-manual").start()
    return jsonify({"running": True, "error": None}), 202


@app.route("/api/run-screener/status")
def api_run_screener_status():
    """Stato corrente dell'esecuzione: {running, error}."""
    return jsonify(_run_status)


# ── FX rates (cached 1h) ─────────────────────────────────────────────────────

_fx_cache: dict = {"rates": {"EUR": 1.0}, "ts": 0.0}
_FX_TTL = 3600  # 1 ora


@app.route("/api/fx-rates")
def api_fx_rates():
    """Tassi di cambio verso EUR, cachati 1h. Usa yfinance come fonte."""
    if _time.time() - _fx_cache["ts"] < _FX_TTL:
        return jsonify(_fx_cache["rates"])
    try:
        import yfinance as yf
        pairs = {
            "USD": "USDEUR=X",
            "CHF": "CHFEUR=X",
            "GBP": "GBPEUR=X",
            "JPY": "JPYEUR=X",
            "SEK": "SEKEUR=X",
            "NOK": "NOKEUR=X",
            "DKK": "DKKEUR=X",
        }
        rates: dict = {"EUR": 1.0}
        for currency, symbol in pairs.items():
            try:
                info = yf.Ticker(symbol).fast_info
                price = getattr(info, "last_price", None) or getattr(info, "regular_market_previous_close", None)
                if price and price > 0:
                    rates[currency] = round(float(price), 6)
            except Exception:
                pass
        if len(rates) > 1:  # almeno un tasso ottenuto
            _fx_cache["rates"] = rates
            _fx_cache["ts"]    = _time.time()
        return jsonify(_fx_cache["rates"])
    except Exception as exc:
        app.logger.warning("FX rates fetch fallito: %s", exc)
        return jsonify(_fx_cache["rates"])


if __name__ == "__main__":
    app.run(debug=False, port=5001, use_reloader=False, host="0.0.0.0")
