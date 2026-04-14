"""Flask web interface for the VQM Screener dashboard."""

import atexit
import decimal
import datetime
import threading

from flask import Flask, jsonify, render_template
import psycopg2
import psycopg2.extras
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

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
            sr.ev_ebitda, sr.p_fcf, sr.pe, sr.p_book, sr.score_value,
            -- Quality
            sr.roe, sr.ebitda_margin, sr.gross_margin, sr.de_ratio,
            sr.eps_cagr_5y, sr.score_quality,
            -- Momentum
            sr.mom_12m1m, sr.eps_rev, sr.rel_strength, sr.score_momentum,
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
            sr.ev_ebitda, sr.p_fcf, sr.pe, sr.p_book, sr.score_value,
            sr.roe, sr.ebitda_margin, sr.gross_margin, sr.de_ratio,
            sr.eps_cagr_5y, sr.score_quality,
            sr.mom_12m1m, sr.eps_rev, sr.rel_strength, sr.score_momentum,
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


# ── Scheduler ────────────────────────────────────────────────────────────────

def _scheduled_run() -> None:
    """Eseguito dallo scheduler: skip se un run di oggi è già presente."""
    import db as db_module
    from screener import run_screener, DEFAULT_TICKERS

    if db_module.load_today_run() is not None:
        app.logger.info("Scheduler: run di oggi già presente, skip.")
        return
    app.logger.info("Scheduler: avvio run automatico...")
    try:
        run_screener(DEFAULT_TICKERS)
        app.logger.info("Scheduler: run completato.")
    except Exception as exc:  # noqa: BLE001
        app.logger.error("Scheduler: run fallito — %s", exc)


_scheduler = BackgroundScheduler(daemon=True)
_scheduler.add_job(_scheduled_run, CronTrigger(hour=0, minute=0))  # mezzanotte ogni giorno
_scheduler.start()
atexit.register(lambda: _scheduler.shutdown(wait=False))

# Avvia anche subito in background al primo lancio del server
threading.Thread(target=_scheduled_run, daemon=True, name="screener-startup").start()


if __name__ == "__main__":
    app.run(debug=False, port=5001, use_reloader=False, host="0.0.0.0")
