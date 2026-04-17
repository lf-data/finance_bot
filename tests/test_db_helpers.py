"""Tests for db.py helper functions — no real database connection required."""
import math
import pytest
import db


class TestClean:
    """Unit tests for db._clean: converts numeric values to float or None."""

    def test_none_returns_none(self):
        assert db._clean(None) is None

    def test_nan_returns_none(self):
        assert db._clean(float("nan")) is None

    def test_positive_inf_returns_none(self):
        assert db._clean(float("inf")) is None

    def test_negative_inf_returns_none(self):
        assert db._clean(float("-inf")) is None

    def test_integer(self):
        assert db._clean(42) == pytest.approx(42.0)

    def test_positive_float(self):
        assert db._clean(3.14) == pytest.approx(3.14, rel=1e-4)

    def test_negative_float(self):
        assert db._clean(-5.5) == pytest.approx(-5.5)

    def test_zero(self):
        assert db._clean(0) == pytest.approx(0.0)

    def test_rounds_to_4_decimals(self):
        result = db._clean(1.23456789)
        assert result == pytest.approx(1.2346, abs=1e-4)

    def test_non_numeric_string_returns_none(self):
        assert db._clean("not-a-number") is None

    def test_decimal_type(self):
        from decimal import Decimal
        assert db._clean(Decimal("3.14")) == pytest.approx(3.14, rel=1e-4)

    def test_large_value(self):
        result = db._clean(1_000_000_000.0)
        assert result == pytest.approx(1_000_000_000.0)

    def test_very_small_value(self):
        result = db._clean(0.0001)
        assert result is not None
        assert result > 0
