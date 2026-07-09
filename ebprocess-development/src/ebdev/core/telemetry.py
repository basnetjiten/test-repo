from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from ebdev.config import config


def setup_telemetry(service_name: str = "ebprocess-api") -> trace.Tracer:
    """Initialise OpenTelemetry SDK with exporters and instrumentations.

    Returns a tracer for manual instrumentation.
    """
    resource = Resource.create({SERVICE_NAME: service_name})

    provider = TracerProvider(resource=resource)

    # Always log spans to console in dev; optionally ship to OTLP endpoint
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    _otlp = config.OTLP_ENDPOINT
    if _otlp:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_otlp)))

    trace.set_tracer_provider(provider)

    # Instrument HTTPX automatically
    HTTPXClientInstrumentor().instrument()

    return trace.get_tracer(service_name)


def instrument_fastapi(app: FastAPI) -> None:
    """Apply FastAPI auto-instrumentation."""
    FastAPIInstrumentor.instrument_app(app)
