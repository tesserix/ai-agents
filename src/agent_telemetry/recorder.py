"""Ship a run's spans to the collector without ever letting tracing fail a request."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from agent_telemetry.session import current_session
from agent_telemetry.settings import TelemetrySettings
from agent_telemetry.spans import build_spans, resource_for

if TYPE_CHECKING:
    from tesserix_adk.core import Run

logger = structlog.get_logger(__name__)


class Recorder(Protocol):
    """What a runtime calls once a run has ended."""

    def record(self, run: Run[Any]) -> None: ...

    def shutdown(self) -> None: ...


class NoopRecorder:
    """Tracing off: the default for tests and for a deployment with no endpoint."""

    def record(self, run: Run[Any]) -> None:
        return None

    def shutdown(self) -> None:
        return None


class LangfuseRecorder:
    """Queue spans behind a batch processor so export latency never reaches the caller."""

    def __init__(self, settings: TelemetrySettings, exporter: SpanExporter | None = None) -> None:
        self._settings = settings
        self._resource = resource_for(settings)
        self._processor = BatchSpanProcessor(
            exporter if exporter is not None else _exporter(settings),
            max_queue_size=settings.queue_size,
            schedule_delay_millis=int(settings.flush_interval_seconds * 1000),
            export_timeout_millis=int(settings.timeout_seconds * 1000),
        )

    def record(self, run: Run[Any]) -> None:
        try:
            for span in build_spans(
                run, settings=self._settings, session_id=current_session(), resource=self._resource
            ):
                self._processor.on_end(span)
        except Exception as error:  # tracing is fail-open by design
            logger.warning("trace_export_skipped", run_id=run.id, reason=type(error).__name__)

    def shutdown(self) -> None:
        try:
            self._processor.shutdown()  # type: ignore[no-untyped-call]
        except Exception as error:
            logger.warning("trace_flush_failed", reason=type(error).__name__)


def build_recorder(settings: TelemetrySettings) -> Recorder:
    if not settings.enabled:
        logger.info("tracing_disabled", product=settings.product, service=settings.service_name)
        return NoopRecorder()
    logger.info("tracing_enabled", endpoint=settings.endpoint, product=settings.product)
    return LangfuseRecorder(settings)


def _exporter(settings: TelemetrySettings) -> OTLPSpanExporter:
    headers: dict[str, str] = {}
    if settings.public_key is not None and settings.secret_key is not None:
        pair = f"{settings.public_key.get_secret_value()}:{settings.secret_key.get_secret_value()}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
    return OTLPSpanExporter(
        endpoint=settings.endpoint, headers=headers or None, timeout=int(settings.timeout_seconds)
    )
