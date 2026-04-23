"""PostgreSQL persistence layer for the VQM Screener.

Schema (time-series per ticker):
  screener_runs    — one row per execution
  screener_results — one row per ticker per execution
"""

import datetime
import logging
import math

import psycopg2
from psycopg2.extras import execute_values

from config import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
)
from telemetry import get_tracer, get_meter

logger = logging.getLogger(__name__)

_tracer = get_tracer(__name__)
_meter  = get_meter(__name__)

# ── DB metrics instruments ────────────────────────────────────────────────
_save_run_duration = _meter.create_histogram(
    name="db.save_run.duration",
    description="Durata salvataggio run screener su DB (s)",
    unit="s",
)
_save_run_rows = _meter.create_histogram(
    name="db.save_run.rows",
    description="Numero di righe inserite per run",
    unit="{rows}",
)
_load_today_run_hits = _meter.create_counter(
    name="db.load_today_run.hits",
    description="Numero di volte che il run di oggi è stato trovato in cache DB",
    unit="{hits}",
)
_db_errors = _meter.create_counter(
    name="db.errors",
    description="Errori DB per operazione",
    unit="{errors}",
)


# ── Connection helpers ────────────────────────────────────────────────────────

def _connect(dbname: str) -> "psycopg2.connection":
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=dbname,
    )


def ensure_db() -> None:
    """Crea il database se non esiste (connettendosi prima a 'postgres')."""
    with _tracer.start_as_current_span("db.ensure_db"):
        conn = _connect("postgres")
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    (POSTGRES_DB,),
                )
                if not cur.fetchone():
                    cur.execute(f'CREATE DATABASE "{POSTGRES_DB}"')
                    logger.info("DB '%s' creato", POSTGRES_DB)
                else:
                    logger.debug("DB '%s' già esistente", POSTGRES_DB)
        finally:
            conn.close()


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS screener_runs (
    id          SERIAL PRIMARY KEY,
    run_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_date    DATE        NOT NULL,
    benchmark   TEXT,
    ai_enabled  BOOLEAN     NOT NULL DEFAULT FALSE,
    n_tickers   INTEGER
);

CREATE TABLE IF NOT EXISTS screener_results (
    id               SERIAL PRIMARY KEY,
    run_id           INTEGER     NOT NULL REFERENCES screener_runs(id) ON DELETE CASCADE,
    run_date         DATE        NOT NULL,
    ticker           TEXT        NOT NULL,
    nome             TEXT,
    settore          TEXT,
    industria        TEXT,
    valuta           TEXT,
    benchmark        TEXT,
    prezzo           NUMERIC(14, 4),
    mktcap           BIGINT,
    -- Value
    ev_ebitda        NUMERIC(10, 2),
    p_fcf            NUMERIC(10, 2),
    pe               NUMERIC(10, 2),
    p_book           NUMERIC(10, 2),
    fcf_yield        NUMERIC(10, 2),
    score_value      NUMERIC(5,  2),
    -- Quality
    roe              NUMERIC(10, 2),
    ebitda_margin    NUMERIC(10, 2),
    gross_margin     NUMERIC(10, 2),
    de_ratio         NUMERIC(10, 2),
    eps_cagr_5y      NUMERIC(10, 2),
    eps_cagr_4y      NUMERIC(10, 2),
    roic             NUMERIC(10, 2),
    score_quality    NUMERIC(5,  2),
    -- Momentum
    mom_12m1m        NUMERIC(10, 2),
    eps_rev          NUMERIC(10, 2),
    upside_consensus NUMERIC(10, 2),
    rel_strength     NUMERIC(10, 2),
    fcf_growth       NUMERIC(10, 2),
    score_momentum   NUMERIC(5,  2),
    -- Final
    score_finale     NUMERIC(5,  2),
    classificazione  TEXT,
    rank             INTEGER,
    -- Extra
    operating_margin NUMERIC(10, 2),
    profit_margin    NUMERIC(10, 2),
    rev_growth       NUMERIC(10, 2),
    roa              NUMERIC(10, 2),
    current_ratio    NUMERIC(10, 2),
    dividend_yield   NUMERIC(10, 4),
    peg              NUMERIC(10, 2),
    week52_change    NUMERIC(10, 2),
    wacc             NUMERIC(10, 2),
    -- AI & errors
    commento_ai      TEXT,
    errore           TEXT
);

CREATE INDEX IF NOT EXISTS idx_sr_ticker   ON screener_results(ticker);
CREATE INDEX IF NOT EXISTS idx_sr_run_date ON screener_results(run_date);
CREATE INDEX IF NOT EXISTS idx_sr_run_id   ON screener_results(run_id);
"""


def ensure_schema() -> None:
    """Crea tabelle e indici se non esistono; applica migrazioni per colonne nuove."""
    with _tracer.start_as_current_span("db.ensure_schema"):
        conn = _connect(POSTGRES_DB)
        try:
            with conn.cursor() as cur:
                cur.execute(_DDL)
                # Migrations: ADD COLUMN IF NOT EXISTS per colonne aggiunte dopo la creazione iniziale
                for stmt in (
                    "ALTER TABLE screener_results ADD COLUMN IF NOT EXISTS fcf_yield   NUMERIC(10, 2)",
                    "ALTER TABLE screener_results ADD COLUMN IF NOT EXISTS eps_cagr_4y NUMERIC(10, 2)",
                    "ALTER TABLE screener_results ADD COLUMN IF NOT EXISTS roic        NUMERIC(10, 2)",
                    "ALTER TABLE screener_results ADD COLUMN IF NOT EXISTS fcf_growth  NUMERIC(10, 2)",
                    "ALTER TABLE screener_results ADD COLUMN IF NOT EXISTS wacc        NUMERIC(10, 2)",
                    "ALTER TABLE screener_results ADD COLUMN IF NOT EXISTS upside_consensus NUMERIC(10, 2)",
                ):
                    cur.execute(stmt)
            conn.commit()
            logger.debug("Schema DB verificato/aggiornato")
        finally:
            conn.close()


# ── Clean helper ──────────────────────────────────────────────────────────────

def _clean(v):
    """Converte in float serializzabile (None per NaN/inf)."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (TypeError, ValueError):
        return None


# ── Save run ──────────────────────────────────────────────────────────────────

def load_today_run() -> tuple[int, list[dict]] | None:
    """
    Cerca un run effettuato oggi nel DB.
    Se esiste, restituisce (run_id, lista di dict con i risultati).
    Se non esiste, restituisce None.
    """
    with _tracer.start_as_current_span("db.load_today_run") as span:
        try:
            ensure_db()
            ensure_schema()
            conn = _connect(POSTGRES_DB)
        except Exception as exc:
            _db_errors.add(1, {"db.operation": "load_today_run"})
            span.record_exception(exc)
            logger.error("load_today_run: errore connessione DB — %s", exc, exc_info=True)
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM screener_runs WHERE run_date = CURRENT_DATE ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
                if not row:
                    logger.debug("load_today_run: nessun run trovato per oggi")
                    span.set_attribute("db.load_today_run.found", False)
                    return None
                run_id = row[0]
                span.set_attribute("db.load_today_run.found", True)
                span.set_attribute("db.load_today_run.run_id", run_id)
                _load_today_run_hits.add(1)

                cur.execute("""
                    SELECT
                        ticker, nome, settore, industria, valuta, benchmark,
                        prezzo, mktcap,
                        ev_ebitda, p_fcf, pe, p_book, fcf_yield, score_value,
                        roe, ebitda_margin, gross_margin, de_ratio, eps_cagr_5y, eps_cagr_4y, roic, score_quality,
                        mom_12m1m, eps_rev, upside_consensus, rel_strength, fcf_growth, score_momentum,
                        score_finale, classificazione, rank,
                        operating_margin, profit_margin, rev_growth, roa,
                        current_ratio, dividend_yield, peg, week52_change, wacc,
                        commento_ai
                    FROM screener_results
                    WHERE run_id = %s
                    ORDER BY rank ASC NULLS LAST
                """, (run_id,))
                cols = [d[0] for d in cur.description]
                results = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    # converti Decimal → float
                    for k, v in d.items():
                        if hasattr(v, 'is_finite'):   # Decimal
                            import math as _math
                            d[k] = float(v) if not (_math.isnan(float(v)) or _math.isinf(float(v))) else None
                    results.append(d)
                span.set_attribute("db.load_today_run.result_count", len(results))
                logger.debug("load_today_run: run_id=%d, %d risultati caricati", run_id, len(results))
                return run_id, results
        finally:
            conn.close()


def save_run(
    results: list[dict],
    benchmark: str,
    ai_enabled: bool = False,
) -> int:
    """
    Salva un'esecuzione dello screener nel database.
    Restituisce l'id del run inserito.
    """
    import time as _time
    _t0 = _time.monotonic()
    with _tracer.start_as_current_span(
        "db.save_run",
        attributes={
            "db.save_run.n_results": len(results),
            "db.save_run.ai_enabled": ai_enabled,
        },
    ) as span:
        return _save_run_inner(results, benchmark, ai_enabled, span, _t0)

def _save_run_inner(
    results: list[dict],
    benchmark: str,
    ai_enabled: bool,
    span,
    _t0: float,
) -> int:
    """Logica interna di save_run (eseguita dentro lo span)."""
    import time as _time
    ensure_db()
    ensure_schema()

    today = datetime.date.today()
    conn  = _connect(POSTGRES_DB)
    try:
        with conn.cursor() as cur:
            # Inserisci il run
            cur.execute(
                """
                INSERT INTO screener_runs (run_date, benchmark, ai_enabled, n_tickers)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (today, benchmark, ai_enabled, len(results)),
            )
            run_id = cur.fetchone()[0]

            # Prepara le righe dei risultati
            rows = []
            for r in results:
                rows.append((
                    run_id,
                    today,
                    r.get("ticker"),
                    r.get("nome"),
                    r.get("settore"),
                    r.get("industria"),
                    r.get("valuta"),
                    r.get("benchmark"),
                    _clean(r.get("prezzo")),
                    r.get("mktcap"),
                    # Value
                    _clean(r.get("ev_ebitda")),
                    _clean(r.get("p_fcf")),
                    _clean(r.get("pe")),
                    _clean(r.get("p_book")),
                    _clean(r.get("fcf_yield")),
                    _clean(r.get("score_value")),
                    # Quality
                    _clean(r.get("roe")),
                    _clean(r.get("ebitda_margin")),
                    _clean(r.get("gross_margin")),
                    _clean(r.get("de_ratio")),
                    _clean(r.get("eps_cagr_5y")),
                    _clean(r.get("eps_cagr_4y")),
                    _clean(r.get("roic")),
                    _clean(r.get("score_quality")),
                    # Momentum
                    _clean(r.get("mom_12m1m")),
                    _clean(r.get("eps_rev")),
                    _clean(r.get("upside_consensus")),
                    _clean(r.get("rel_strength")),
                    _clean(r.get("fcf_growth")),
                    _clean(r.get("score_momentum")),
                    # Final
                    _clean(r.get("score_finale")),
                    r.get("classificazione"),
                    r.get("rank"),
                    # Extra
                    _clean(r.get("operating_margin")),
                    _clean(r.get("profit_margin")),
                    _clean(r.get("rev_growth")),
                    _clean(r.get("roa")),
                    _clean(r.get("current_ratio")),
                    _clean(r.get("dividend_yield")),
                    _clean(r.get("peg")),
                    _clean(r.get("52w_change")),
                    _clean(r.get("wacc")),
                    # AI & errors
                    r.get("commento_ai") or None,
                    r.get("_errore") or None,
                ))

            execute_values(
                cur,
                """
                INSERT INTO screener_results (
                    run_id, run_date, ticker, nome, settore, industria, valuta, benchmark,
                    prezzo, mktcap,
                    ev_ebitda, p_fcf, pe, p_book, fcf_yield, score_value,
                    roe, ebitda_margin, gross_margin, de_ratio, eps_cagr_5y, eps_cagr_4y, roic, score_quality,
                    mom_12m1m, eps_rev, upside_consensus, rel_strength, fcf_growth, score_momentum,
                    score_finale, classificazione, rank,
                    operating_margin, profit_margin, rev_growth, roa, current_ratio,
                    dividend_yield, peg, week52_change, wacc,
                    commento_ai, errore
                ) VALUES %s
                """,
                rows,
            )

        conn.commit()
        elapsed = _time.monotonic() - _t0
        _save_run_duration.record(elapsed, {"db.operation": "save_run"})
        _save_run_rows.record(len(rows), {"db.operation": "save_run"})
        span.set_attribute("db.save_run.run_id", run_id)
        span.set_attribute("db.save_run.duration_s", round(elapsed, 2))
        logger.info("save_run: run_id=%d, %d risultati salvati in %.2fs", run_id, len(rows), elapsed)
    finally:
        conn.close()

    return run_id
