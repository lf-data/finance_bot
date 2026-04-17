"""Shared fixtures for the finance_bot test suite."""
import pytest


@pytest.fixture
def sample_metrics():
    """Metrics dict aligned with _default thresholds that should produce a high score."""
    return {
        "ticker": "TEST",
        "nome": "Test Corp",
        "settore": "_default",
        # Value (lower is better for multiples, higher for yield)
        "ev_ebitda": 9.0,        # good=11, bad=18
        "p_fcf": 14.0,           # good=16, bad=27
        "pe": 13.0,              # good=15, bad=25
        "fcf_yield": 5.0,        # good=4,  bad=1
        # Quality
        "roe": 20.0,             # good=15, bad=5
        "ebitda_margin": 22.0,   # good=18, bad=8
        "roic": 14.0,            # good=12, bad=4
        "de_ratio": 0.5,         # good=0.7, bad=2.0 (lower_is_better)
        "eps_cagr_4y": 10.0,     # good=8,  bad=0
        # Momentum
        "mom_12m1m": 18.0,       # good=15, bad=-2
        "upside_consensus": 22.0, # good=20, bad=-5
        "fcf_growth": 12.0,      # good=10, bad=-5
    }
