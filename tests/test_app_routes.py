"""Tests for Flask API routes in app.py.
All database calls are mocked so no real PostgreSQL connection is needed.
"""
import datetime
import decimal
import pytest
from unittest.mock import patch, MagicMock

import app as flask_app


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c
    # Cleanup shared mutable state after every test
    flask_app._run_status["running"] = False
    flask_app._run_status["error"] = None
    # Release the lock if a test left it acquired
    try:
        if flask_app._run_lock.locked():
            flask_app._run_lock.release()
    except Exception:
        pass


# ── _normalize ────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_decimal_to_float(self):
        assert flask_app._normalize(decimal.Decimal("3.14")) == pytest.approx(3.14)

    def test_date_to_iso_string(self):
        d = datetime.date(2024, 3, 15)
        assert flask_app._normalize(d) == "2024-03-15"

    def test_datetime_to_iso_string(self):
        dt = datetime.datetime(2024, 3, 15, 10, 30, 0)
        assert flask_app._normalize(dt) == "2024-03-15T10:30:00"

    def test_string_unchanged(self):
        assert flask_app._normalize("hello") == "hello"

    def test_int_unchanged(self):
        assert flask_app._normalize(42) == 42

    def test_none_unchanged(self):
        assert flask_app._normalize(None) is None

    def test_float_unchanged(self):
        assert flask_app._normalize(3.14) == pytest.approx(3.14)


# ── Static / template routes ──────────────────────────────────────────────────

class TestStaticRoutes:
    def test_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_content_type_html(self, client):
        resp = client.get("/")
        assert "text/html" in resp.content_type

    def test_sw_js_returns_200(self, client):
        resp = client.get("/sw.js")
        assert resp.status_code == 200

    def test_sw_js_service_worker_allowed_header(self, client):
        resp = client.get("/sw.js")
        assert resp.headers.get("Service-Worker-Allowed") == "/"

    def test_sw_js_cache_control_no_cache(self, client):
        resp = client.get("/sw.js")
        assert "no-cache" in resp.headers.get("Cache-Control", "")

    def test_sw_js_content_type_javascript(self, client):
        resp = client.get("/sw.js")
        assert "javascript" in resp.content_type


# ── /api/tickers ──────────────────────────────────────────────────────────────

class TestApiTickers:
    def test_status_200(self, client):
        with patch("app._query", return_value=[]):
            resp = client.get("/api/tickers")
        assert resp.status_code == 200

    def test_returns_json_list(self, client):
        mock_data = [{"ticker": "AAPL", "score_finale": 7.5, "settore": "Technology"}]
        with patch("app._query", return_value=mock_data):
            resp = client.get("/api/tickers")
        data = resp.get_json()
        assert isinstance(data, list)
        assert data[0]["ticker"] == "AAPL"

    def test_empty_result(self, client):
        with patch("app._query", return_value=[]):
            resp = client.get("/api/tickers")
        assert resp.get_json() == []


# ── /api/ticker/<ticker> ──────────────────────────────────────────────────────

class TestApiTickerDetail:
    def test_status_200(self, client):
        with patch("app._query", return_value=[]):
            resp = client.get("/api/ticker/AAPL")
        assert resp.status_code == 200

    def test_ticker_param_upcased(self, client):
        """The route must pass the ticker in uppercase to _query."""
        with patch("app._query", return_value=[]) as mock_q:
            client.get("/api/ticker/aapl")
        assert "AAPL" in str(mock_q.call_args)

    def test_returns_historical_series(self, client):
        mock_data = [
            {"ticker": "AAPL", "run_date": "2024-01-01", "score_finale": 7.0},
            {"ticker": "AAPL", "run_date": "2024-02-01", "score_finale": 7.5},
        ]
        with patch("app._query", return_value=mock_data):
            resp = client.get("/api/ticker/AAPL")
        data = resp.get_json()
        assert len(data) == 2


# ── /api/thresholds ───────────────────────────────────────────────────────────

class TestApiThresholds:
    def test_status_200(self, client):
        resp = client.get("/api/thresholds")
        assert resp.status_code == 200

    def test_returns_dict(self, client):
        resp = client.get("/api/thresholds")
        assert isinstance(resp.get_json(), dict)

    def test_reserved_keys_excluded(self, client):
        resp = client.get("/api/thresholds")
        data = resp.get_json()
        assert "pesi" not in data
        assert "tickers" not in data

    def test_each_metric_has_required_fields(self, client):
        resp = client.get("/api/thresholds")
        data = resp.get_json()
        for sector_metrics in data.values():
            for threshold in sector_metrics.values():
                assert "good" in threshold
                assert "bad" in threshold
                assert "lower_is_better" in threshold

    def test_default_sector_present(self, client):
        resp = client.get("/api/thresholds")
        data = resp.get_json()
        assert "_default" in data


# ── /api/runs ─────────────────────────────────────────────────────────────────

class TestApiRuns:
    def test_status_200(self, client):
        with patch("app._query", return_value=[]):
            resp = client.get("/api/runs")
        assert resp.status_code == 200

    def test_returns_list(self, client):
        mock = [{"id": 1, "run_date": "2024-01-01", "n_tickers": 10, "benchmark": "SWDA.MI"}]
        with patch("app._query", return_value=mock):
            resp = client.get("/api/runs")
        assert resp.get_json() == mock


# ── /api/latest ───────────────────────────────────────────────────────────────

class TestApiLatest:
    def test_status_200(self, client):
        with patch("app._query", return_value=[]):
            resp = client.get("/api/latest")
        assert resp.status_code == 200

    def test_returns_ranked_data(self, client):
        mock = [
            {"ticker": "AAPL", "rank": 1, "score_finale": 9.0},
            {"ticker": "MSFT", "rank": 2, "score_finale": 7.5},
        ]
        with patch("app._query", return_value=mock):
            resp = client.get("/api/latest")
        data = resp.get_json()
        assert len(data) == 2
        assert data[0]["rank"] == 1


# ── /api/run-screener ─────────────────────────────────────────────────────────

class TestApiRunScreener:
    def test_status_endpoint_returns_200(self, client):
        resp = client.get("/api/run-screener/status")
        assert resp.status_code == 200

    def test_status_has_running_key(self, client):
        resp = client.get("/api/run-screener/status")
        data = resp.get_json()
        assert "running" in data

    def test_status_initially_not_running(self, client):
        flask_app._run_status["running"] = False
        resp = client.get("/api/run-screener/status")
        assert resp.get_json()["running"] is False

    def test_post_returns_202_when_idle(self, client):
        """POST when screener is not running → 202 Accepted."""
        flask_app._run_status["running"] = False
        with patch("threading.Thread") as mock_thread:
            instance = MagicMock()
            mock_thread.return_value = instance
            resp = client.post("/api/run-screener")
        assert resp.status_code == 202
        # Cleanup lock acquired by the route
        try:
            flask_app._run_lock.release()
        except Exception:
            pass

    def test_post_202_body_has_running_true(self, client):
        flask_app._run_status["running"] = False
        with patch("threading.Thread"):
            resp = client.post("/api/run-screener")
        data = resp.get_json()
        assert data["running"] is True
        try:
            flask_app._run_lock.release()
        except Exception:
            pass

    def test_post_returns_409_when_already_running(self, client):
        """POST when screener is already running → 409 Conflict."""
        acquired = flask_app._run_lock.acquire(blocking=False)
        try:
            if acquired:
                resp = client.post("/api/run-screener")
                assert resp.status_code == 409
        finally:
            if acquired:
                flask_app._run_lock.release()

    def test_post_409_body_has_running_true(self, client):
        acquired = flask_app._run_lock.acquire(blocking=False)
        try:
            if acquired:
                resp = client.post("/api/run-screener")
                assert resp.get_json()["running"] is True
        finally:
            if acquired:
                flask_app._run_lock.release()


# ── /api/fx-rates ─────────────────────────────────────────────────────────────

class TestApiFxRates:
    def _reset_cache(self):
        flask_app._fx_cache["ts"] = 0.0  # force cache miss

    def test_status_200(self, client):
        self._reset_cache()
        with patch("yfinance.Ticker") as mock_yf:
            fi = MagicMock()
            fi.last_price = None
            fi.regular_market_previous_close = None
            mock_yf.return_value.fast_info = fi
            resp = client.get("/api/fx-rates")
        assert resp.status_code == 200

    def test_eur_always_present_with_value_1(self, client):
        self._reset_cache()
        with patch("yfinance.Ticker") as mock_yf:
            fi = MagicMock()
            fi.last_price = None
            fi.regular_market_previous_close = None
            mock_yf.return_value.fast_info = fi
            resp = client.get("/api/fx-rates")
        data = resp.get_json()
        assert "EUR" in data
        assert data["EUR"] == 1.0

    def test_returns_cached_rates_when_fresh(self, client):
        """A fresh cache should be returned immediately without calling yfinance."""
        import time
        flask_app._fx_cache["rates"] = {"EUR": 1.0, "USD": 0.92}
        flask_app._fx_cache["ts"] = time.time()  # mark as fresh
        with patch("yfinance.Ticker") as mock_yf:
            resp = client.get("/api/fx-rates")
            mock_yf.assert_not_called()
        data = resp.get_json()
        assert data["USD"] == pytest.approx(0.92)
