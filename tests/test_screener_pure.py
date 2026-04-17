"""Tests for pure deterministic functions in screener.py.
No network calls, no database connections.
"""
import pytest
import pandas as pd
import screener


# ── DataFrame factory ─────────────────────────────────────────────────────────

def _make_stmt(keys_vals: dict, n_cols: int = 4) -> pd.DataFrame:
    """Build a minimal DataFrame that mimics a yfinance financial statement.

    Rows  = line items (e.g. "Net Income", "Total Revenue").
    Cols  = periods ordered most-recent-first, as yfinance returns them.
    """
    cols = [f"P{i}" for i in range(n_cols)]
    data = {k: pd.Series(v, index=cols[: len(v)]) for k, v in keys_vals.items()}
    return pd.DataFrame(data).T


# ── _safe ─────────────────────────────────────────────────────────────────────

class TestSafe:
    def test_none_returns_none(self):
        assert screener._safe(None) is None

    def test_basic_float(self):
        assert screener._safe(10.5) == pytest.approx(10.5)

    def test_multiplier_applied(self):
        assert screener._safe(0.15, multiplier=100) == pytest.approx(15.0)

    def test_custom_decimals(self):
        assert screener._safe(3.14159, decimals=2) == pytest.approx(3.14)

    def test_integer_input(self):
        assert screener._safe(5) == pytest.approx(5.0)

    def test_string_non_numeric_returns_none(self):
        assert screener._safe("abc") is None

    def test_zero(self):
        assert screener._safe(0) == pytest.approx(0.0)

    def test_negative(self):
        assert screener._safe(-12.5) == pytest.approx(-12.5)

    def test_multiplier_with_zero(self):
        assert screener._safe(0, multiplier=100) == pytest.approx(0.0)


# ── _ttm ──────────────────────────────────────────────────────────────────────

class TestTtm:
    def test_sums_four_periods(self):
        stmt = _make_stmt({"Total Revenue": [100.0, 200.0, 150.0, 50.0]})
        assert screener._ttm(("Total Revenue",), stmt, n=4) == pytest.approx(500.0)

    def test_sums_only_n_periods(self):
        stmt = _make_stmt({"Total Revenue": [100.0, 200.0, 150.0, 50.0]})
        assert screener._ttm(("Total Revenue",), stmt, n=2) == pytest.approx(300.0)

    def test_first_matching_key_wins(self):
        stmt = _make_stmt({"Total Revenue": [100.0, 100.0], "Revenue": [99.0, 99.0]})
        assert screener._ttm(("Total Revenue", "Revenue"), stmt, n=2) == pytest.approx(200.0)

    def test_falls_back_to_second_key(self):
        stmt = _make_stmt({"Revenue": [50.0, 50.0]})
        assert screener._ttm(("Total Revenue", "Revenue"), stmt, n=2) == pytest.approx(100.0)

    def test_none_stmt_returns_none(self):
        assert screener._ttm(("Total Revenue",), None) is None

    def test_empty_df_returns_none(self):
        assert screener._ttm(("Total Revenue",), pd.DataFrame()) is None

    def test_fewer_periods_than_n(self):
        """If fewer periods exist than n, sum all available."""
        stmt = _make_stmt({"Net Income": [100.0, 200.0]})
        assert screener._ttm(("Net Income",), stmt, n=4) == pytest.approx(300.0)

    def test_missing_key_returns_none(self):
        stmt = _make_stmt({"Operating Income": [100.0]})
        assert screener._ttm(("Total Revenue",), stmt) is None

    def test_n_equals_one(self):
        stmt = _make_stmt({"EBITDA": [300.0, 250.0, 200.0]})
        assert screener._ttm(("EBITDA",), stmt, n=1) == pytest.approx(300.0)


# ── _mrq ──────────────────────────────────────────────────────────────────────

class TestMrq:
    def test_returns_most_recent_period(self):
        stmt = _make_stmt({"Total Assets": [1000.0, 900.0, 800.0]})
        assert screener._mrq(("Total Assets",), stmt) == pytest.approx(1000.0)

    def test_none_stmt_returns_none(self):
        assert screener._mrq(("Total Assets",), None) is None

    def test_empty_df_returns_none(self):
        assert screener._mrq(("Total Assets",), pd.DataFrame()) is None

    def test_fallback_key(self):
        stmt = _make_stmt({"Common Stock Equity": [500.0]})
        result = screener._mrq(("Stockholders Equity", "Common Stock Equity"), stmt)
        assert result == pytest.approx(500.0)

    def test_missing_key_returns_none(self):
        stmt = _make_stmt({"Cash": [100.0]})
        assert screener._mrq(("Total Assets",), stmt) is None


# ── _mrq_nth ──────────────────────────────────────────────────────────────────

class TestMrqNth:
    def test_nth_zero_equals_mrq(self):
        stmt = _make_stmt({"Total Assets": [1000.0, 800.0, 600.0]})
        assert screener._mrq_nth(("Total Assets",), stmt, 0) == pytest.approx(1000.0)

    def test_nth_one_is_second_period(self):
        stmt = _make_stmt({"Total Assets": [1000.0, 800.0, 600.0]})
        assert screener._mrq_nth(("Total Assets",), stmt, 1) == pytest.approx(800.0)

    def test_nth_two_is_third_period(self):
        stmt = _make_stmt({"Total Assets": [1000.0, 800.0, 600.0]})
        assert screener._mrq_nth(("Total Assets",), stmt, 2) == pytest.approx(600.0)

    def test_nth_out_of_range_returns_none(self):
        stmt = _make_stmt({"Total Assets": [1000.0]})
        assert screener._mrq_nth(("Total Assets",), stmt, 5) is None

    def test_none_stmt_returns_none(self):
        assert screener._mrq_nth(("Total Assets",), None, 0) is None


# ── _score_metric ─────────────────────────────────────────────────────────────

class TestScoreMetric:
    # ── higher-is-better ───────────────────────────────────────────────────
    def test_hib_none_returns_none(self):
        assert screener._score_metric(None, good=20, bad=0) is None

    def test_hib_at_good_returns_10(self):
        assert screener._score_metric(20, good=20, bad=0) == pytest.approx(10.0)

    def test_hib_at_bad_returns_0(self):
        assert screener._score_metric(0, good=20, bad=0) == pytest.approx(0.0)

    def test_hib_midpoint_returns_5(self):
        assert screener._score_metric(10, good=20, bad=0) == pytest.approx(5.0)

    def test_hib_above_good_clamped_to_10(self):
        assert screener._score_metric(30, good=20, bad=0) == pytest.approx(10.0)

    def test_hib_below_bad_clamped_to_0(self):
        assert screener._score_metric(-5, good=20, bad=0) == pytest.approx(0.0)

    def test_hib_invalid_thresholds_returns_none(self):
        # good <= bad is invalid for higher-is-better
        assert screener._score_metric(10, good=5, bad=10) is None

    def test_hib_equal_thresholds_returns_none(self):
        assert screener._score_metric(5, good=5, bad=5) is None

    # ── lower-is-better ────────────────────────────────────────────────────
    def test_lib_at_good_returns_10(self):
        # val == good (the low/optimal value) → score 10
        assert screener._score_metric(5, good=5, bad=30, lower_is_better=True) == pytest.approx(10.0)

    def test_lib_at_bad_returns_0(self):
        # val == bad (the high/worst value) → score 0
        assert screener._score_metric(30, good=5, bad=30, lower_is_better=True) == pytest.approx(0.0)

    def test_lib_midpoint_returns_5(self):
        assert screener._score_metric(17.5, good=5, bad=30, lower_is_better=True) == pytest.approx(5.0)

    def test_lib_below_good_clamped_to_10(self):
        assert screener._score_metric(0, good=5, bad=30, lower_is_better=True) == pytest.approx(10.0)

    def test_lib_above_bad_clamped_to_0(self):
        assert screener._score_metric(40, good=5, bad=30, lower_is_better=True) == pytest.approx(0.0)

    def test_lib_invalid_thresholds_returns_none(self):
        # bad <= good is invalid for lower-is-better (bad must be strictly > good)
        assert screener._score_metric(5.0, good=5, bad=5, lower_is_better=True) is None


# ── _classify ─────────────────────────────────────────────────────────────────

class TestClassify:
    def test_buy_at_threshold(self):
        assert screener._classify(7.5) == "BUY"

    def test_buy_above_threshold(self):
        assert screener._classify(10.0) == "BUY"
        assert screener._classify(9.9) == "BUY"

    def test_hold_at_lower_boundary(self):
        assert screener._classify(5.0) == "HOLD"

    def test_hold_just_below_buy(self):
        assert screener._classify(7.49) == "HOLD"

    def test_sell_just_below_hold(self):
        assert screener._classify(4.99) == "SELL"

    def test_sell_at_zero(self):
        assert screener._classify(0.0) == "SELL"

    def test_none_returns_nd(self):
        assert screener._classify(None) == "N/D"


# ── _benchmark_for_ticker ─────────────────────────────────────────────────────

class TestBenchmarkForTicker:
    def test_italian_mi(self):
        assert screener._benchmark_for_ticker("ENI.MI") == "FTSEMIB.MI"

    def test_german_de(self):
        assert screener._benchmark_for_ticker("SIE.DE") == "^GDAXI"

    def test_french_pa(self):
        assert screener._benchmark_for_ticker("MC.PA") == "^FCHI"

    def test_us_no_suffix(self):
        assert screener._benchmark_for_ticker("AAPL") == "SPY"

    def test_us_long_ticker(self):
        assert screener._benchmark_for_ticker("NVDA") == "SPY"

    def test_dutch_as(self):
        assert screener._benchmark_for_ticker("ASML.AS") == "^AEX"

    def test_swiss_sw(self):
        assert screener._benchmark_for_ticker("NESN.SW") == "^SSMI"

    def test_uk_l(self):
        assert screener._benchmark_for_ticker("HSBA.L") == "^FTSE"

    def test_spanish_mc(self):
        assert screener._benchmark_for_ticker("ITX.MC") == "^IBEX"

    def test_override_takes_precedence(self):
        assert screener._benchmark_for_ticker("ENI.MI", override="SPY") == "SPY"

    def test_override_none_uses_suffix(self):
        assert screener._benchmark_for_ticker("ENI.MI", override=None) == "FTSEMIB.MI"

    def test_case_insensitive_suffix(self):
        # ticker might be passed lowercase — suffixes should still match
        assert screener._benchmark_for_ticker("eni.mi") == "FTSEMIB.MI"


# ── _load_vqm_config / module-level constants ─────────────────────────────────

class TestLoadVqmConfig:
    def test_default_sector_present(self):
        assert "_default" in screener._THRESHOLDS

    def test_default_has_all_pillars(self):
        default = screener._THRESHOLDS["_default"]
        assert "value" in default
        assert "quality" in default
        assert "momentum" in default

    def test_threshold_entries_are_4_tuples(self):
        for entry in screener._THRESHOLDS["_default"]["value"]:
            metrica, good, bad, lib = entry
            assert isinstance(metrica, str)
            assert isinstance(lib, bool)

    def test_weights_sum_to_one(self):
        w = screener._VQM_WEIGHTS
        total = w["value"] + w["quality"] + w["momentum"]
        assert abs(total - 1.0) < 0.01

    def test_weights_all_positive(self):
        for v in screener._VQM_WEIGHTS.values():
            assert v > 0

    def test_default_tickers_is_list(self):
        assert isinstance(screener.DEFAULT_TICKERS, list)

    def test_default_tickers_not_empty(self):
        assert len(screener.DEFAULT_TICKERS) > 0

    def test_default_tickers_contains_known_tickers(self):
        assert "AAPL" in screener.DEFAULT_TICKERS
        assert "MSFT" in screener.DEFAULT_TICKERS


# ── calc_vqm_score ────────────────────────────────────────────────────────────

class TestCalcVqmScore:
    def test_returns_all_pillar_keys(self, sample_metrics):
        result = screener.calc_vqm_score(sample_metrics)
        for key in ("score_value", "score_quality", "score_momentum", "score_finale"):
            assert key in result

    def test_scores_in_0_10_range(self, sample_metrics):
        result = screener.calc_vqm_score(sample_metrics)
        for key in ("score_value", "score_quality", "score_momentum", "score_finale"):
            assert result[key] is not None
            assert 0.0 <= result[key] <= 10.0

    def test_empty_metrics_all_none(self):
        result = screener.calc_vqm_score({})
        assert result["score_value"] is None
        assert result["score_quality"] is None
        assert result["score_momentum"] is None
        assert result["score_finale"] is None

    def test_excellent_metrics_produce_high_score(self):
        """All metrics beating the 'good' threshold → score_finale >= 7.5."""
        metrics = {
            "settore": "_default",
            "ev_ebitda": 8.0,       # good=11 (lower_is_better) → score 10
            "p_fcf": 12.0,          # good=16 (lower_is_better) → score 10
            "pe": 10.0,             # good=15 (lower_is_better) → score 10
            "fcf_yield": 6.0,       # good=4  (higher_is_better) → score 10
            "roe": 25.0,            # good=15 → score 10
            "ebitda_margin": 25.0,  # good=18 → score 10
            "roic": 18.0,           # good=12 → score 10
            "de_ratio": 0.3,        # good=0.7 (lower_is_better) → score 10
            "eps_cagr_4y": 12.0,    # good=8  → score 10
            "mom_12m1m": 20.0,      # good=15 → score 10
            "upside_consensus": 25.0, # good=20 → score 10
            "fcf_growth": 15.0,     # good=10 → score 10
        }
        result = screener.calc_vqm_score(metrics)
        assert result["score_finale"] is not None
        assert result["score_finale"] >= 7.5

    def test_terrible_metrics_produce_low_score(self):
        """All metrics past the 'bad' threshold → score_finale <= 2.5."""
        metrics = {
            "settore": "_default",
            "ev_ebitda": 30.0,      # bad=18
            "p_fcf": 50.0,          # bad=27
            "pe": 60.0,             # bad=25
            "fcf_yield": -5.0,      # bad=1
            "roe": 0.0,             # bad=5
            "ebitda_margin": 3.0,   # bad=8
            "roic": 0.0,            # bad=4
            "de_ratio": 5.0,        # bad=2.0
            "eps_cagr_4y": -5.0,    # bad=0
            "mom_12m1m": -10.0,     # bad=-2
            "upside_consensus": -10.0, # bad=-5
            "fcf_growth": -10.0,    # bad=-5
        }
        result = screener.calc_vqm_score(metrics)
        assert result["score_finale"] is not None
        assert result["score_finale"] <= 2.5

    def test_partial_pillar_still_computes_finale(self):
        """Only quality metrics provided → finale computed from quality alone."""
        metrics = {
            "settore": "_default",
            "roe": 20.0,
            "ebitda_margin": 20.0,
        }
        result = screener.calc_vqm_score(metrics)
        assert result["score_quality"] is not None
        assert result["score_finale"] is not None

    def test_financial_services_sector_uses_correct_thresholds(self):
        """Financial Services uses P/Book instead of EV/EBITDA for value."""
        metrics = {
            "settore": "Financial Services",
            "pe": 9.0,      # good=9 (lower_is_better) for FinServ
            "p_book": 0.9,  # good=0.9 (lower_is_better) for FinServ
            "roe": 15.0,    # good=13
        }
        result = screener.calc_vqm_score(metrics)
        assert result["score_value"] is not None

    def test_score_finale_is_weighted_combination(self, sample_metrics):
        """score_finale must be consistent with pillar scores and VQM weights."""
        result = screener.calc_vqm_score(sample_metrics)
        w = screener._VQM_WEIGHTS
        parts, weights = [], []
        if result["score_value"] is not None:
            parts.append(result["score_value"] * w["value"])
            weights.append(w["value"])
        if result["score_quality"] is not None:
            parts.append(result["score_quality"] * w["quality"])
            weights.append(w["quality"])
        if result["score_momentum"] is not None:
            parts.append(result["score_momentum"] * w["momentum"])
            weights.append(w["momentum"])
        expected = sum(parts) / sum(weights) if weights else None
        assert result["score_finale"] == pytest.approx(expected, abs=0.01)

    def test_classify_consistent_with_score(self, sample_metrics):
        result = screener.calc_vqm_score(sample_metrics)
        sf = result["score_finale"]
        cls = screener._classify(sf)
        if sf >= 7.5:
            assert cls == "BUY"
        elif sf >= 5.0:
            assert cls == "HOLD"
        else:
            assert cls == "SELL"
