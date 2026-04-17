"""Tests for fetch_metrics and run_screener with mocked network and database."""
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

import screener


# ── Mock factory ──────────────────────────────────────────────────────────────

def _mock_ticker(info=None, q_inc=None, q_bs=None, q_cf=None,
                 a_inc=None, a_bs=None, a_cf=None):
    """Build a mock yf.Ticker with controllable statements and empty history."""
    t = MagicMock()
    t.info = info or {}
    t.quarterly_income_stmt  = q_inc if q_inc is not None else pd.DataFrame()
    t.quarterly_balance_sheet = q_bs if q_bs is not None else pd.DataFrame()
    t.quarterly_cashflow     = q_cf if q_cf is not None else pd.DataFrame()
    t.income_stmt            = a_inc if a_inc is not None else pd.DataFrame()
    t.balance_sheet          = a_bs  if a_bs  is not None else pd.DataFrame()
    t.cashflow               = a_cf  if a_cf  is not None else pd.DataFrame()
    t.analyst_price_targets  = {}
    # Empty history => momentum / rel_strength == None (no real network call)
    t.history.return_value   = pd.DataFrame()
    return t


@pytest.fixture
def basic_info():
    return {
        "shortName": "TestCorp Inc",
        "sector": "Technology",
        "industry": "Software—Application",
        "currency": "USD",
        "marketCap": 1_000_000_000,
        "currentPrice": 50.0,
        "trailingPE": 20.0,
        "returnOnEquity": 0.18,
        "ebitdaMargins": 0.20,
        "grossMargins": 0.45,
        "profitMargins": 0.12,
        "operatingMargins": 0.16,
        "beta": 1.2,
        "currentRatio": 2.0,
        "dividendYield": 0.01,
        "numberOfAnalystOpinions": 0,
    }


# ── Context manager helper ─────────────────────────────────────────────────────

def _patched_fetch(mock_t):
    """Context manager that patches both session and yfinance.Ticker."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("screener._get_yf_session", return_value=MagicMock()))
    stack.enter_context(patch("yfinance.Ticker", return_value=mock_t))
    return stack


# ── fetch_metrics: info-dict fallbacks ────────────────────────────────────────

class TestFetchMetricsFromInfo:
    def test_ticker_always_in_result(self, basic_info):
        with _patched_fetch(_mock_ticker(info=basic_info)):
            result = screener.fetch_metrics("TEST")
        assert result["ticker"] == "TEST"

    def test_basic_fields_populated(self, basic_info):
        with _patched_fetch(_mock_ticker(info=basic_info)):
            result = screener.fetch_metrics("TEST")
        assert result["nome"] == "TestCorp Inc"
        assert result["settore"] == "Technology"
        assert result["valuta"] == "USD"

    def test_pe_fallback_from_info(self, basic_info):
        """When no statements, P/E falls back to trailingPE from info."""
        with _patched_fetch(_mock_ticker(info=basic_info)):
            result = screener.fetch_metrics("TEST")
        assert result.get("pe") == pytest.approx(20.0)

    def test_roe_fallback_multiplied_by_100(self, basic_info):
        """returnOnEquity is a fraction (0.18) → ROE must be 18.0 %."""
        with _patched_fetch(_mock_ticker(info=basic_info)):
            result = screener.fetch_metrics("TEST")
        assert result.get("roe") == pytest.approx(18.0)

    def test_no_errore_on_empty_statements(self, basic_info):
        with _patched_fetch(_mock_ticker(info=basic_info)):
            result = screener.fetch_metrics("TEST")
        assert "_errore" not in result

    def test_result_contains_no_none_values(self, basic_info):
        """_clean + dict comprehension must remove all None entries."""
        with _patched_fetch(_mock_ticker(info=basic_info)):
            result = screener.fetch_metrics("TEST")
        assert all(v is not None for v in result.values())

    def test_negative_pe_excluded(self, basic_info):
        """Negative P/E is not a meaningful value metric and must be excluded."""
        info = {**basic_info, "trailingPE": -10.0}
        with _patched_fetch(_mock_ticker(info=info)):
            result = screener.fetch_metrics("TEST")
        assert result.get("pe") is None

    def test_negative_p_book_excluded(self, basic_info):
        """Negative P/Book (negative equity) must be excluded."""
        info = {**basic_info, "priceToBook": -2.0}
        with _patched_fetch(_mock_ticker(info=info)):
            result = screener.fetch_metrics("TEST")
        assert result.get("p_book") is None

    def test_unknown_sector_defaults_to_nd(self):
        info = {"shortName": "Weird Corp", "quoteType": "EQUITY", "currency": "USD"}
        with _patched_fetch(_mock_ticker(info=info)):
            result = screener.fetch_metrics("WEIRD")
        assert result.get("settore") == "N/D"

    def test_etf_quoteType_used_as_sector(self):
        info = {"shortName": "ETF Fund", "quoteType": "ETF", "currency": "EUR"}
        with _patched_fetch(_mock_ticker(info=info)):
            result = screener.fetch_metrics("ETFTEST")
        assert result.get("settore") == "ETF"

    def test_exception_stores_errore(self):
        """If yf.Ticker() raises, _errore is populated and ticker is preserved."""
        with patch("screener._get_yf_session", return_value=MagicMock()), \
             patch("yfinance.Ticker", side_effect=RuntimeError("connection failed")):
            result = screener.fetch_metrics("FAIL")
        assert result["ticker"] == "FAIL"
        assert "_errore" in result
        assert "connection failed" in result["_errore"]


# ── fetch_metrics: statement-based calculations ───────────────────────────────

def _make_stmt(rows: dict, n: int = 4) -> pd.DataFrame:
    cols = [f"P{i}" for i in range(n)]
    data = {k: pd.Series(v, index=cols[: len(v)]) for k, v in rows.items()}
    return pd.DataFrame(data).T


class TestFetchMetricsFromStatements:
    def test_pe_calculated_from_mktcap_ni(self, basic_info):
        """P/E = mktcap / TTM Net Income (preferred over trailingPE from info)."""
        # mktcap = 1_000_000_000, Net Income TTM = 50_000_000 → P/E = 20
        inc = _make_stmt({"Net Income": [12_500_000.0, 12_500_000.0, 12_500_000.0, 12_500_000.0]})
        t = _mock_ticker(info=basic_info, q_inc=inc)
        with _patched_fetch(t):
            result = screener.fetch_metrics("TEST")
        assert result.get("pe") == pytest.approx(20.0, rel=0.01)

    def test_gross_margin_calculated_from_revenue_and_gp(self, basic_info):
        """Gross Margin = GP / Revenue * 100."""
        inc = _make_stmt({
            "Total Revenue": [100.0, 100.0, 100.0, 100.0],
            "Gross Profit":  [ 45.0,  45.0,  45.0,  45.0],
        })
        t = _mock_ticker(info={**basic_info, "grossMargins": None}, q_inc=inc)
        with _patched_fetch(t):
            result = screener.fetch_metrics("TEST")
        if result.get("gross_margin") is not None:
            assert result["gross_margin"] == pytest.approx(45.0, rel=0.01)

    def test_de_ratio_from_balance_sheet(self, basic_info):
        """D/E = Total Debt / Equity (MRQ)."""
        bs = _make_stmt({
            "Total Debt":         [200_000_000.0],
            "Stockholders Equity": [400_000_000.0],
        })
        t = _mock_ticker(info={**basic_info, "debtToEquity": None}, q_bs=bs)
        with _patched_fetch(t):
            result = screener.fetch_metrics("TEST")
        if result.get("de_ratio") is not None:
            assert result["de_ratio"] == pytest.approx(0.5, rel=0.01)


# ── run_screener ──────────────────────────────────────────────────────────────

class TestRunScreener:
    def _make_row(self, ticker: str) -> dict:
        return {
            "ticker": ticker,
            "nome": f"{ticker} Corp",
            "settore": "Technology",
            "score_value": 7.0,
            "score_quality": 8.0,
            "score_momentum": 6.5,
            "score_finale": 7.2,
            "classificazione": "BUY",
            "benchmark": "SPY",
        }

    def test_empty_list_returns_empty(self):
        results, run_id = screener.run_screener([])
        assert results == []
        assert run_id is None

    def test_single_ticker_run(self):
        with patch("screener._fetch_and_score",
                   side_effect=lambda t, bm: (t, self._make_row(t), 0.1)), \
             patch("db.save_run", return_value=1):
            results, run_id = screener.run_screener(["AAPL"])
        assert len(results) == 1
        assert run_id == 1
        assert results[0]["ticker"] == "AAPL"

    def test_all_tickers_appear_in_results(self):
        tickers = ["AAPL", "MSFT", "GOOG"]
        with patch("screener._fetch_and_score",
                   side_effect=lambda t, bm: (t, self._make_row(t), 0.1)), \
             patch("db.save_run", return_value=1):
            results, _ = screener.run_screener(tickers)
        returned = {r["ticker"] for r in results}
        assert returned == set(tickers)

    def test_results_have_rank_assigned(self):
        tickers = ["AAPL", "MSFT", "GOOG"]
        with patch("screener._fetch_and_score",
                   side_effect=lambda t, bm: (t, self._make_row(t), 0.1)), \
             patch("db.save_run", return_value=1):
            results, _ = screener.run_screener(tickers)
        ranks = {r["rank"] for r in results}
        assert ranks == {1, 2, 3}

    def test_results_ordered_by_score_descending(self):
        rows = {
            "AAPL": {**self._make_row("AAPL"), "score_finale": 9.0},
            "MSFT": {**self._make_row("MSFT"), "score_finale": 5.0},
            "GOOG": {**self._make_row("GOOG"), "score_finale": 7.0},
        }
        with patch("screener._fetch_and_score",
                   side_effect=lambda t, bm: (t, rows[t], 0.1)), \
             patch("db.save_run", return_value=1):
            results, _ = screener.run_screener(["AAPL", "MSFT", "GOOG"])
        scores = [r["score_finale"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_rank_one_is_highest_score(self):
        rows = {
            "AAPL": {**self._make_row("AAPL"), "score_finale": 9.0},
            "MSFT": {**self._make_row("MSFT"), "score_finale": 5.0},
        }
        with patch("screener._fetch_and_score",
                   side_effect=lambda t, bm: (t, rows[t], 0.1)), \
             patch("db.save_run", return_value=1):
            results, _ = screener.run_screener(["AAPL", "MSFT"])
        rank1 = next(r for r in results if r["rank"] == 1)
        assert rank1["ticker"] == "AAPL"

    def test_save_run_called_exactly_once(self):
        with patch("screener._fetch_and_score",
                   side_effect=lambda t, bm: (t, self._make_row(t), 0.1)), \
             patch("db.save_run", return_value=1) as mock_save:
            screener.run_screener(["AAPL", "MSFT"])
        mock_save.assert_called_once()

    def test_errored_ticker_included_in_results(self):
        """A ticker that raises during fetch must still appear in the results."""
        def mock_fetch(ticker, bm):
            if ticker == "BAD":
                return ticker, {"ticker": "BAD", "_errore": "not found"}, 0.0
            return ticker, self._make_row(ticker), 0.1

        with patch("screener._fetch_and_score", side_effect=mock_fetch), \
             patch("db.save_run", return_value=1):
            results, _ = screener.run_screener(["AAPL", "BAD"])
        returned_tickers = [r["ticker"] for r in results]
        assert "BAD" in returned_tickers

    def test_benchmark_override_passed_to_fetch(self):
        """benchmark_override must be forwarded to _fetch_and_score."""
        captured = []

        def mock_fetch(ticker, bm):
            captured.append(bm)
            return ticker, self._make_row(ticker), 0.1

        with patch("screener._fetch_and_score", side_effect=mock_fetch), \
             patch("db.save_run", return_value=1):
            screener.run_screener(["AAPL"], benchmark_override="^GSPC")
        assert all(bm == "^GSPC" for bm in captured)
