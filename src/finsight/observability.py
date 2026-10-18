"""Tracing (OpenTelemetry), Prometheus metrics and cost estimation. Never records question text, SQL results or secrets."""
import functools
import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path

import yaml
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from prometheus_client import Counter, Gauge, Histogram

PRICING = Path(__file__).resolve().parents[2] / "observability" / "pricing.yaml"
_provider: TracerProvider | None = None

HTTP_LATENCY = Histogram("finsight_http_request_seconds", "API latency", ["method", "route", "status"])
BI_QUERIES = Counter("finsight_bi_queries_total", "Text-to-SQL requests", ["status", "mode"])
BI_LLM_SECONDS = Histogram("finsight_bi_llm_seconds", "LLM time per BI request")
BI_TOKENS = Counter("finsight_llm_tokens_total", "LLM tokens", ["kind"])
CONSUMER_EVENTS = Counter("finsight_consumer_events_total", "Events handled by the consumer", ["outcome"])
SCORING_SECONDS = Histogram("finsight_scoring_seconds", "Per-event scoring + persistence time",
                            buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5))
DQ_FAILURES = Counter("finsight_data_quality_failures_total", "Events rejected to the dead-letter table", ["kind"])
CONSUMER_LAG = Gauge("finsight_consumer_lag", "Messages behind the log end offset", ["partition"])
INVESTIGATIONS = Counter("finsight_investigations_total", "Investigator runs", ["llm_status"])


def setup_tracing(exporter: SpanExporter | None = None) -> TracerProvider:
    """OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set; tests inject an in-memory exporter."""
    global _provider
    if _provider is not None and exporter is None:
        return _provider
    provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "finsight-api")}))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    _provider = provider
    return provider


def tracer():
    return (_provider or setup_tracing()).get_tracer("finsight")


@contextmanager
def span(name: str, **attrs):
    with tracer().start_as_current_span(name) as s:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v)
        yield s


def traced(name: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            with span(name):
                return fn(*a, **k)
        return wrapper
    return deco


def question_fingerprint(q: str) -> str:
    return hashlib.sha256(q.encode()).hexdigest()[:12]


def estimate_cost(model: str | None, input_tokens: int, output_tokens: int) -> dict:
    cfg = yaml.safe_load(PRICING.read_text())
    p = cfg["per_million_tokens"].get(model or "", cfg["per_million_tokens"]["default"])
    usd = (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000
    return {"estimated_usd": round(usd, 6), "pricing_version": cfg["version"], "is_estimate": True,
            "note": "Estimate from versioned pricing metadata; not a billed amount."}


class Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self.t) * 1000
