"""Every run becomes a Langfuse-shaped trace with derived ids, and never fails the caller."""

from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from pydantic import SecretStr
from tesserix_adk.core import ModelCapabilities, Run, RunEvent, RunEventKind, RunState, Usage
from tesserix_adk.runtime import ModelResponse
from tesserix_adk.testing import ScriptedProvider

from agent_telemetry import (
    LangfuseRecorder,
    NoopRecorder,
    TelemetrySettings,
    bound_session,
    build_recorder,
    build_spans,
    current_session,
)
from kora_agents.definitions import DEFINITIONS
from kora_agents.runtime import RuntimeAgentService, StaticProviderFactory

SETTINGS = TelemetrySettings(
    endpoint="http://otel-gateway.observability:4318/v1/traces",
    product="kora",
    service_name="kora-ai-agents",
    release="main-abc123",
)


class CapturingExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Any) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class ExplodingExporter(SpanExporter):
    def export(self, spans: Any) -> SpanExportResult:
        raise RuntimeError("collector down")

    def shutdown(self) -> None:
        raise RuntimeError("still down")


def run_fixture(state: RunState = RunState.COMPLETED) -> Run[Any]:
    return Run(
        id="run_01",
        tenant="kora",
        user="user-9",
        agent_name="nutrition-coach",
        agent_version="1.2.0",
        definition_revision="rev-7",
        model="kora-auto",
        state=state,
        started_at=100.0,
        ended_at=104.5,
        usage=Usage(input_tokens=30, output_tokens=12, cached_tokens=4),
        events=[
            RunEvent(kind=RunEventKind.MODEL_CALL, name="kora-auto", at=100.5),
            RunEvent(
                kind=RunEventKind.MODEL_RESPONSE,
                name="kora-auto",
                at=101.5,
                usage=Usage(input_tokens=30, output_tokens=12, cached_tokens=4),
            ),
            RunEvent(kind=RunEventKind.TOOL_CALL, name="lookup", at=102.0),
            RunEvent(kind=RunEventKind.TOOL_ERROR, name="lookup", detail="timeout", at=103.0),
            RunEvent(kind=RunEventKind.GUARDRAIL_REDACTION, name="pii", at=103.5),
            RunEvent(kind=RunEventKind.OUTPUT_VALIDATED, at=104.0),
        ],
    )


def test_root_span_carries_langfuse_routing_and_product_attributes() -> None:
    root, *children = build_spans(run_fixture(), settings=SETTINGS, session_id="sess-1")
    attributes = dict(root.attributes or {})
    assert root.name == "nutrition-coach"
    assert attributes["langfuse.observation.type"] == "agent"
    assert attributes["langfuse.session.id"] == "sess-1"
    assert attributes["langfuse.user.id"] == "user-9"
    assert attributes["langfuse.release"] == "main-abc123"
    assert attributes["langfuse.trace.metadata.tenant"] == "kora"
    assert attributes["langfuse.trace.metadata.definition_revision"] == "rev-7"
    assert list(attributes["langfuse.tags"]) == ["kora", "nutrition-coach", "kora-ai-agents"]
    assert attributes["gen_ai.usage.cache_read_input_tokens"] == 4
    assert root.resource.attributes["service.namespace"] == "kora"
    assert root.resource.attributes["tesserix.signal"] == "ai"
    assert root.start_time == 100_000_000_000
    assert root.end_time == 104_500_000_000
    assert len(children) == 3


def test_events_pair_into_generation_tool_and_point_observations() -> None:
    _, generation, tool, redaction = build_spans(run_fixture(), settings=SETTINGS)
    assert dict(generation.attributes or {})["langfuse.observation.type"] == "generation"
    assert dict(generation.attributes or {})["gen_ai.usage.input_tokens"] == 30
    assert generation.start_time == 100_500_000_000
    assert generation.end_time == 101_500_000_000
    assert dict(tool.attributes or {})["langfuse.observation.type"] == "tool"
    assert dict(tool.attributes or {})["langfuse.observation.level"] == "ERROR"
    assert dict(tool.attributes or {})["langfuse.observation.status_message"] == "timeout"
    assert tool.start_time == 102_000_000_000
    assert dict(redaction.attributes or {})["langfuse.observation.level"] == "WARNING"
    assert redaction.start_time == redaction.end_time


def test_ids_are_derived_from_the_run_id_so_replays_upsert() -> None:
    first = build_spans(run_fixture(), settings=SETTINGS)
    second = build_spans(run_fixture(), settings=SETTINGS)
    assert first[0].context is not None
    assert [s.context.trace_id for s in first] == [s.context.trace_id for s in second]
    assert [s.context.span_id for s in first] == [s.context.span_id for s in second]
    assert all(
        s.parent is not None and s.parent.span_id == first[0].context.span_id for s in first[1:]
    )
    other = build_spans(run_fixture().model_copy(update={"id": "run_02"}), settings=SETTINGS)
    assert other[0].context.trace_id != first[0].context.trace_id


def test_a_failed_run_is_an_error_trace() -> None:
    run = run_fixture(RunState.FAILED).model_copy(update={"started_at": None, "ended_at": None})
    root = build_spans(run, settings=SETTINGS)[0]
    assert dict(root.attributes or {})["langfuse.observation.level"] == "ERROR"
    assert root.status.status_code.name == "ERROR"
    assert root.start_time == 100_500_000_000
    assert root.end_time == 104_000_000_000


def test_recorder_exports_spans_and_never_raises() -> None:
    exporter = CapturingExporter()
    recorder = LangfuseRecorder(SETTINGS, exporter=exporter)
    with bound_session("sess-2"):
        recorder.record(run_fixture())
    recorder.shutdown()
    assert len(exporter.spans) == 4
    assert dict(exporter.spans[0].attributes or {})["langfuse.session.id"] == "sess-2"
    broken = LangfuseRecorder(SETTINGS, exporter=ExplodingExporter())
    broken.record(run_fixture())
    broken.shutdown()


def test_recorder_swallows_span_building_failures() -> None:
    recorder = LangfuseRecorder(SETTINGS, exporter=CapturingExporter())
    recorder.record(run_fixture().model_copy(update={"events": [object()]}))  # type: ignore[list-item]


def test_build_recorder_is_noop_without_an_endpoint() -> None:
    off = TelemetrySettings(product="kora", service_name="kora-ai-agents")
    assert not off.enabled
    assert isinstance(build_recorder(off), NoopRecorder)
    assert "kora" in repr(off)
    on = build_recorder(SETTINGS)
    assert isinstance(on, LangfuseRecorder)
    on.shutdown()


def test_direct_langfuse_keys_become_basic_auth() -> None:
    from agent_telemetry.recorder import _exporter

    keyed = SETTINGS.model_copy(
        update={"public_key": SecretStr("pk"), "secret_key": SecretStr("sk")}
    )
    assert _exporter(keyed)._session.headers["Authorization"] == "Basic cGs6c2s="


def test_session_binding_is_scoped() -> None:
    assert current_session() is None
    with bound_session("abc"):
        assert current_session() == "abc"
    assert current_session() is None


async def test_runtime_records_every_run_it_completes() -> None:
    exporter = CapturingExporter()
    recorder = LangfuseRecorder(SETTINGS, exporter=exporter)
    scripted = ScriptedProvider(
        ModelResponse(content="Choose whole grains."),
        name="gateway",
        capabilities=ModelCapabilities(structured_output=True, context_window_tokens=32_768),
    )
    service = RuntimeAgentService(
        definitions=DEFINITIONS, providers=StaticProviderFactory(scripted), recorder=recorder
    )
    result = await service.run("nutrition-coach", "How can I improve lunch?", tenant="kora")
    recorder.shutdown()
    assert exporter.spans[0].name == "nutrition-coach"
    assert (
        dict(exporter.spans[0].attributes or {})["langfuse.trace.metadata.run_id"] == result.run_id
    )


@pytest.mark.parametrize("field", ["product", "service_name"])
def test_settings_require_product_and_service(field: str) -> None:
    values = {"product": "kora", "service_name": "kora-ai-agents"}
    values.pop(field)
    with pytest.raises(ValueError, match=field):
        TelemetrySettings(**values)  # type: ignore[arg-type]
