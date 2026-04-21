"""Flask web interface for the VQM Screener dashboard."""

import decimal
import datetime
import json as _json
import logging
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

from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.threading import ThreadingInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

trace.set_tracer_provider(TracerProvider())

span_processor = BatchSpanProcessor(ConsoleSpanExporter())
trace.get_tracer_provider().add_span_processor(span_processor)

Psycopg2Instrumentor().instrument()
RequestsInstrumentor().instrument()
URLLib3Instrumentor().instrument()
LoggingInstrumentor().instrument(set_logging_format=False)
ThreadingInstrumentor().instrument()
HTTPXClientInstrumentor().instrument()

app = Flask(__name__)

FlaskInstrumentor().instrument_app(app)

# ── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

tracer = trace.get_tracer(__name__)
logger.info("Flask app inizializzata")




# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn():
    logger.debug("Apertura connessione DB — host=%s port=%s db=%s", POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB)
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
    logger.debug("Esecuzione query — params=%s sql_preview=%.120s", params, sql.strip())
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [{k: _normalize(v) for k, v in row.items()} for row in cur.fetchall()]
            logger.debug("Query completata — %d righe restituite", len(rows))
            return rows
    except Exception as exc:
        logger.error("Errore query DB — %s", exc, exc_info=True)
        raise
    finally:
        conn.close()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    logger.info("GET / — dashboard richiesta da %s", request.remote_addr)
    return render_template("index.html")


@app.route("/sw.js")
def service_worker():
    """Serve il service worker dalla root per avere scope completo."""
    logger.debug("GET /sw.js — service worker servito a %s", request.remote_addr)
    resp = send_from_directory("static", "sw.js",
                               mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/tickers")
def api_tickers():
    """Lista di tutti i ticker con l'ultimo score disponibile."""
    logger.info("GET /api/tickers — richiesto da %s", request.remote_addr)
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
    logger.info("GET /api/tickers — %d ticker restituiti", len(rows))
    return jsonify(rows)


@app.route("/api/ticker/<ticker>")
def api_ticker_detail(ticker: str):
    """Serie storica completa per un ticker."""
    logger.info("GET /api/ticker/%s — richiesto da %s", ticker.upper(), request.remote_addr)
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
            sr.mom_12m1m, sr.eps_rev, sr.upside_consensus, sr.rel_strength, sr.fcf_growth, sr.score_momentum,
            -- Final
            sr.score_finale, sr.classificazione, sr.rank,
            -- Extra
            sr.operating_margin, sr.profit_margin, sr.rev_growth, sr.roa,
            sr.current_ratio, sr.dividend_yield, sr.peg, sr.week52_change, sr.wacc,
            -- AI
            sr.commento_ai
        FROM screener_results sr
        JOIN screener_runs run ON sr.run_id = run.id
        WHERE sr.ticker = %s AND sr.errore IS NULL
        ORDER BY sr.run_date ASC
    """, (ticker.upper(),))
    logger.info("GET /api/ticker/%s — %d run restituite", ticker.upper(), len(rows))
    return jsonify(rows)


@app.route("/api/thresholds")
def api_thresholds():
    """Soglie VQM per settore (per il drawer metriche). Struttura: {settore: {metrica: {good, bad, lower_is_better}}}."""
    logger.info("GET /api/thresholds — richiesto da %s", request.remote_addr)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thresholds.json")
    logger.debug("Caricamento thresholds da %s", path)
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
    logger.debug("GET /api/thresholds — %d settori restituiti", len(out))
    return jsonify(out)


@app.route("/api/runs")
def api_runs():
    """Lista delle ultime 30 esecuzioni."""
    logger.info("GET /api/runs — richiesto da %s", request.remote_addr)
    rows = _query("""
        SELECT id, run_at, run_date, benchmark, ai_enabled, n_tickers
        FROM screener_runs
        ORDER BY run_at DESC
        LIMIT 30
    """)
    logger.info("GET /api/runs — %d run restituite", len(rows))
    return jsonify(rows)


@app.route("/api/latest")
def api_latest():
    """Tutti i risultati dell'ultima esecuzione, ordinati per rank."""
    logger.info("GET /api/latest — richiesto da %s", request.remote_addr)
    rows = _query("""
        SELECT
            sr.ticker, sr.nome, sr.settore, sr.industria, sr.valuta, sr.benchmark,
            sr.prezzo, sr.mktcap,
            sr.ev_ebitda, sr.p_fcf, sr.pe, sr.p_book, sr.fcf_yield, sr.score_value,
            sr.roe, sr.ebitda_margin, sr.gross_margin, sr.de_ratio,
            sr.eps_cagr_5y, sr.eps_cagr_4y, sr.roic, sr.score_quality,
            sr.mom_12m1m, sr.eps_rev, sr.upside_consensus, sr.rel_strength, sr.fcf_growth, sr.score_momentum,
            sr.score_finale, sr.classificazione, sr.rank,
            sr.operating_margin, sr.profit_margin, sr.rev_growth, sr.roa,
            sr.current_ratio, sr.dividend_yield, sr.peg, sr.week52_change, sr.wacc,
            sr.commento_ai, sr.run_date
        FROM screener_results sr
        JOIN screener_runs run ON sr.run_id = run.id
        WHERE run.id = (SELECT MAX(id) FROM screener_runs)
          AND sr.errore IS NULL
        ORDER BY sr.rank ASC
    """)
    logger.info("GET /api/latest — %d titoli restituiti", len(rows))
    return jsonify(rows)


# ── Manual screener run ──────────────────────────────────────────────────────

_run_lock   = threading.Lock()
_run_status: dict = {"running": False, "error": None, "done": 0, "total": 0}


def _do_run() -> None:
    with tracer.start_as_current_span("screener-run"):
        from screener import run_screener, DEFAULT_TICKERS

        def _progress(done: int, total: int) -> None:
            _run_status["done"]  = done
            _run_status["total"] = total
            logger.debug("Screener avanzamento — %d/%d ticker completati", done, total)

        logger.info("Screener avviato in background — %d ticker da analizzare", len(DEFAULT_TICKERS))
        t_start = _time.monotonic()
        try:
            _run_status["done"]  = 0
            _run_status["total"] = len(DEFAULT_TICKERS)
            run_screener(DEFAULT_TICKERS, progress_callback=_progress)
            elapsed = _time.monotonic() - t_start
            logger.info("Screener completato con successo — %.1fs per %d ticker", elapsed, len(DEFAULT_TICKERS))
            _run_status["error"] = None
        except Exception as exc:  # noqa: BLE001
            elapsed = _time.monotonic() - t_start
            logger.error("Screener run fallito dopo %.1fs — %s", elapsed, exc, exc_info=True)
            _run_status["error"] = str(exc)
        finally:
            _run_status["running"] = False
            _run_lock.release()


@app.route("/api/run-screener", methods=["POST"])
def api_run_screener():
    """Avvia lo screener in background. Se già in esecuzione restituisce 409."""
    if not _run_lock.acquire(blocking=False):
        logger.warning("POST /api/run-screener — screener già in esecuzione, richiesta rifiutata (409)")
        return jsonify({"running": True, "error": None}), 409
    logger.info("POST /api/run-screener — avvio screener manuale da %s", request.remote_addr)
    _run_status["running"] = True
    _run_status["error"]   = None
    _run_status["done"]    = 0
    _run_status["total"]   = 0
    threading.Thread(target=_do_run, daemon=True, name="screener-manual").start()
    return jsonify({"running": True, "error": None}), 202


@app.route("/api/run-screener/status")
def api_run_screener_status():
    """Stato corrente dell'esecuzione: {running, error}."""
    logger.debug("GET /api/run-screener/status — running=%s done=%d total=%d",
                 _run_status["running"], _run_status["done"], _run_status["total"])
    return jsonify(_run_status)


# ── FX rates (cached 1h) ─────────────────────────────────────────────────────

_fx_cache: dict = {"rates": {"EUR": 1.0}, "ts": 0.0}
_FX_TTL = 3600  # 1 ora


@app.route("/api/fx-rates")
def api_fx_rates():
    """Tassi di cambio verso EUR, cachati 1h. Usa yfinance come fonte."""
    logger.info("GET /api/fx-rates — richiesto da %s", request.remote_addr)
    if _time.time() - _fx_cache["ts"] < _FX_TTL:
        age = int(_time.time() - _fx_cache["ts"])
        logger.debug("FX rates serviti da cache (età %ds, TTL %ds)", age, _FX_TTL)
        return jsonify(_fx_cache["rates"])
    logger.info("FX rates cache scaduta — recupero tassi aggiornati da yfinance")
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
                    logger.debug("Tasso FX aggiornato — %s/%s = %.6f", currency, "EUR", rates[currency])
            except Exception as fx_exc:
                logger.warning("Impossibile ottenere tasso FX per %s (%s) — %s", currency, symbol, fx_exc)
        if len(rates) > 1:  # almeno un tasso ottenuto
            _fx_cache["rates"] = rates
            _fx_cache["ts"]    = _time.time()
            logger.info("FX rates aggiornati — %d valute in cache", len(rates))
        else:
            logger.warning("Nessun tasso FX ottenuto — restituzione cache precedente")
        return jsonify(_fx_cache["rates"])
    except Exception as exc:
        logger.warning("FX rates fetch fallito: %s", exc, exc_info=True)
        return jsonify(_fx_cache["rates"])


if __name__ == "__main__":
    app.run(debug=False, port=5001, use_reloader=False, host="0.0.0.0")
