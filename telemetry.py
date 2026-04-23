"""OpenTelemetry SDK bootstrap — traces, metrics, logs.

Chiamare ``setup_telemetry()`` **una sola volta** all'avvio del processo,
prima di qualsiasi altra importazione che usi OTel.

Tutti i segnali vengono inviati all'OTLP Collector via gRPC (porta 4317).
L'endpoint è configurabile tramite la variabile d'ambiente
``OTEL_EXPORTER_OTLP_ENDPOINT`` (default: ``http://localhost:4317``).

Variabili d'ambiente utili
--------------------------
OTEL_EXPORTER_OTLP_ENDPOINT   URL del collector  (default: http://localhost:4317)
OTEL_SERVICE_NAME              Nome servizio      (default: finance-bot)
OTEL_SERVICE_VERSION           Versione           (default: 1.0.0)
OTEL_DEPLOYMENT_ENVIRONMENT    Ambiente           (default: production)
"""

import atexit
import logging
import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "finance-bot")
_SERVICE_VERSION = os.getenv("OTEL_SERVICE_VERSION", "1.0.0")
_ENVIRONMENT = os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "production")

_INITIALIZED = False
_TRACER_PROVIDER = None
_METER_PROVIDER = None
_LOGGER_PROVIDER = None


def _build_resource() -> Resource:
    return Resource.create(
        {
            "service.name": _SERVICE_NAME,
            "service.version": _SERVICE_VERSION,
            "deployment.environment": _ENVIRONMENT,
        }
    )


def setup_telemetry() -> None:
    """Inizializza TracerProvider, MeterProvider e LoggerProvider con OTLP gRPC."""
    global _INITIALIZED, _TRACER_PROVIDER, _METER_PROVIDER, _LOGGER_PROVIDER
    if _INITIALIZED:
        return

    resource = _build_resource()

    # ── Traces ────────────────────────────────────────────────────────────────
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=_ENDPOINT, insecure=True)
        )
    )
    trace.set_tracer_provider(tracer_provider)
    _TRACER_PROVIDER = tracer_provider

    # ── Metrics ───────────────────────────────────────────────────────────────
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=_ENDPOINT, insecure=True),
        export_interval_millis=15_000,  # ogni 15 secondi
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    _METER_PROVIDER = meter_provider

    # ── Logs ──────────────────────────────────────────────────────────────────
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=_ENDPOINT, insecure=True)
        )
    )
    # Aggancia il LoggingHandler di OTel al root logger Python.
    # LoggingInstrumentor() inietta trace_id/span_id nei record già formattati;
    # LoggingHandler li trasmette anche al collector.
    otel_handler = LoggingHandler(level=logging.DEBUG, logger_provider=logger_provider)
    logging.getLogger().addHandler(otel_handler)
    _LOGGER_PROVIDER = logger_provider
    _INITIALIZED = True
    atexit.register(shutdown_telemetry)

    logger.info(
        "OTel bootstrap completato — endpoint=%s service=%s env=%s",
        _ENDPOINT,
        _SERVICE_NAME,
        _ENVIRONMENT,
    )


def shutdown_telemetry() -> None:
    """Flush/shutdown esplicito per processi brevi come CLI e test."""
    global _INITIALIZED
    if not _INITIALIZED:
        return

    for provider in (_METER_PROVIDER, _TRACER_PROVIDER, _LOGGER_PROVIDER):
        try:
            if provider is not None:
                provider.shutdown()
        except Exception:
            logger.debug("Errore durante shutdown telemetry", exc_info=True)

    _INITIALIZED = False


def get_tracer(name: str):
    """Shortcut: restituisce un Tracer dal provider globale."""
    return trace.get_tracer(name)


def get_meter(name: str):
    """Shortcut: restituisce un Meter dal provider globale."""
    return metrics.get_meter(name)
