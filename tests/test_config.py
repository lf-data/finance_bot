"""Tests for config.py: default values and environment variable overrides."""
import importlib
import pytest
from unittest.mock import patch


def _reload_config(monkeypatch, env_overrides=None, env_deletes=None,
                   mock_dotenv=True):
    """Helper: clear/set env vars then reload config module.

    mock_dotenv=True  → load_dotenv() is a no-op so .env values don't interfere
                        with default-value tests.
    mock_dotenv=False → real load_dotenv() runs (use for override tests that set
                        explicit env vars whose value we control).
    """
    import config
    for key in (env_deletes or []):
        monkeypatch.delenv(key, raising=False)
    for key, val in (env_overrides or {}).items():
        monkeypatch.setenv(key, val)
    if mock_dotenv:
        with patch("dotenv.load_dotenv"):
            importlib.reload(config)
    else:
        importlib.reload(config)
    return config


class TestDefaults:
    def test_llm_model(self, monkeypatch):
        cfg = _reload_config(monkeypatch, env_deletes=["LLM_MODEL"])
        assert cfg.LLM_MODEL == "gpt-4o"

    def test_analysis_language(self, monkeypatch):
        cfg = _reload_config(monkeypatch, env_deletes=["ANALYSIS_LANGUAGE"])
        assert cfg.ANALYSIS_LANGUAGE == "italian"

    def test_screener_benchmark(self, monkeypatch):
        cfg = _reload_config(monkeypatch, env_deletes=["SCREENER_BENCHMARK"])
        assert cfg.SCREENER_BENCHMARK == "SWDA.MI"

    def test_screener_workers(self, monkeypatch):
        cfg = _reload_config(monkeypatch, env_deletes=["SCREENER_WORKERS"])
        assert cfg.SCREENER_WORKERS == 6

    def test_postgres_user(self, monkeypatch):
        cfg = _reload_config(monkeypatch, env_deletes=["POSTGRES_USER"])
        assert cfg.POSTGRES_USER == "postgres"

    def test_postgres_db(self, monkeypatch):
        cfg = _reload_config(monkeypatch, env_deletes=["POSTGRES_DB"])
        assert cfg.POSTGRES_DB == "finanalysis"

    def test_postgres_host(self, monkeypatch):
        cfg = _reload_config(monkeypatch, env_deletes=["POSTGRES_HOST"])
        assert cfg.POSTGRES_HOST == "localhost"

    def test_postgres_port(self, monkeypatch):
        cfg = _reload_config(monkeypatch, env_deletes=["POSTGRES_PORT"])
        assert cfg.POSTGRES_PORT == 5432

    def test_postgres_port_is_int(self, monkeypatch):
        cfg = _reload_config(monkeypatch, env_deletes=["POSTGRES_PORT"])
        assert isinstance(cfg.POSTGRES_PORT, int)

    def test_screener_workers_is_int(self, monkeypatch):
        cfg = _reload_config(monkeypatch, env_deletes=["SCREENER_WORKERS"])
        assert isinstance(cfg.SCREENER_WORKERS, int)


class TestEnvOverrides:
    def test_llm_model_override(self, monkeypatch):
        cfg = _reload_config(monkeypatch, {"LLM_MODEL": "gpt-3.5-turbo"})
        assert cfg.LLM_MODEL == "gpt-3.5-turbo"

    def test_screener_workers_override(self, monkeypatch):
        cfg = _reload_config(monkeypatch, {"SCREENER_WORKERS": "12"})
        assert cfg.SCREENER_WORKERS == 12

    def test_postgres_port_override(self, monkeypatch):
        cfg = _reload_config(monkeypatch, {"POSTGRES_PORT": "5433"})
        assert cfg.POSTGRES_PORT == 5433

    def test_analysis_language_override(self, monkeypatch):
        cfg = _reload_config(monkeypatch, {"ANALYSIS_LANGUAGE": "english"})
        assert cfg.ANALYSIS_LANGUAGE == "english"

    def test_postgres_db_override(self, monkeypatch):
        cfg = _reload_config(monkeypatch, {"POSTGRES_DB": "mydb"})
        assert cfg.POSTGRES_DB == "mydb"
