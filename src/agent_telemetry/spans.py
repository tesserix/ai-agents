"""Translate one finished ADK run into OTLP spans with Langfuse's attribute convention.

Ids are derived from the run id, never random, so a replayed run upserts its trace and a
score can attach to exactly one trace (australis ADR-0003 D6). Message content is never
exported: the run's events carry names, states and usage, and that is what becomes spans.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags
from tesserix_adk.core import RunEventKind

if TYPE_CHECKING:
    from tesserix_adk.core import Run, RunEvent

    from agent_telemetry.settings import TelemetrySettings

SCOPE = InstrumentationScope("agent_telemetry", "1")
_NS = 1_000_000_000

_GENERATION_END = {RunEventKind.MODEL_RESPONSE, RunEventKind.ATTEMPT_FAILED}
_TOOL_END = {
    RunEventKind.TOOL_RESULT,
    RunEventKind.TOOL_ERROR,
    RunEventKind.TOOL_REFUSED,
    RunEventKind.TOOL_INDETERMINATE,
}
_ERROR_KINDS = {
    RunEventKind.ATTEMPT_FAILED,
    RunEventKind.TOOL_ERROR,
    RunEventKind.BUDGET_EXCEEDED,
    RunEventKind.DEADLINE_EXCEEDED,
    RunEventKind.SCHEMA_VIOLATION,
    RunEventKind.REPAIR_ABANDONED,
    RunEventKind.TERMINATED,
}
_WARNING_KINDS = {
    RunEventKind.TOOL_REFUSED,
    RunEventKind.TOOL_INDETERMINATE,
    RunEventKind.TOOL_RESULT_FLAGGED,
    RunEventKind.TOOL_RESULT_TRUNCATED,
    RunEventKind.GUARDRAIL_REFUSAL,
    RunEventKind.GUARDRAIL_REDACTION,
    RunEventKind.MODEL_FELL_BACK,
    RunEventKind.CONTEXT_DEGRADED,
    RunEventKind.REPEAT_DETECTED,
    RunEventKind.SCOPE_REFUSED,
    RunEventKind.FAN_OUT_REFUSED,
    RunEventKind.DELEGATION_REFUSED,
}


def resource_for(settings: TelemetrySettings) -> Resource:
    """The resource every span carries; the collector routes on `service.namespace`."""
    attributes: dict[str, str] = {
        "service.name": settings.service_name,
        "service.namespace": settings.product,
        "deployment.environment.name": settings.environment,
        "tesserix.product": settings.product,
        "tesserix.signal": "ai",
    }
    if settings.release:
        attributes["service.version"] = settings.release
    return Resource.create(attributes)


def build_spans(
    run: Run[Any],
    *,
    settings: TelemetrySettings,
    session_id: str | None = None,
    resource: Resource | None = None,
) -> tuple[ReadableSpan, ...]:
    """One agent root span per run, one child per model call, tool call or notable event."""
    resource = resource if resource is not None else resource_for(settings)
    trace_id = _trace_id(run.id)
    root_context = _context(trace_id, _span_id(run.id, 0))
    start, end = _bounds(run)
    children: list[ReadableSpan] = []
    for index, (name, kind, attributes, at, until, level) in enumerate(_observations(run), 1):
        children.append(
            ReadableSpan(
                name=name,
                context=_context(trace_id, _span_id(run.id, index)),
                parent=root_context,
                resource=resource,
                attributes={
                    "langfuse.observation.type": kind,
                    "langfuse.observation.level": level,
                    **attributes,
                },
                kind=SpanKind.CLIENT if kind == "generation" else SpanKind.INTERNAL,
                status=_status(level),
                start_time=_ns(at, start),
                end_time=_ns(until, _ns(at, start)),
                instrumentation_scope=SCOPE,
            )
        )
    root = ReadableSpan(
        name=run.agent_name,
        context=root_context,
        parent=None,
        resource=resource,
        attributes=_root_attributes(run, settings, session_id),
        kind=SpanKind.SERVER,
        status=_status("ERROR" if run.state.value != "completed" else "DEFAULT", run.state.value),
        start_time=start,
        end_time=end,
        instrumentation_scope=SCOPE,
    )
    return (root, *children)


def _root_attributes(
    run: Run[Any], settings: TelemetrySettings, session_id: str | None
) -> dict[str, Any]:
    tags = [settings.product, run.agent_name, settings.service_name]
    attributes: dict[str, Any] = {
        "langfuse.observation.type": "agent",
        "langfuse.trace.name": run.agent_name,
        "langfuse.tags": tags,
        "langfuse.environment": settings.environment,
        "langfuse.version": run.agent_version,
        "langfuse.trace.metadata.tenant": run.tenant,
        "langfuse.trace.metadata.run_id": run.id,
        "langfuse.trace.metadata.product": settings.product,
        "langfuse.trace.metadata.service": settings.service_name,
        "langfuse.trace.metadata.state": run.state.value,
        "langfuse.trace.metadata.depth": run.depth,
        "langfuse.observation.model.name": run.model,
        "langfuse.observation.level": "ERROR" if run.state.value != "completed" else "DEFAULT",
        "gen_ai.request.model": run.model,
        "gen_ai.usage.input_tokens": run.usage.input_tokens,
        "gen_ai.usage.output_tokens": run.usage.output_tokens,
        "adk.tenant": run.tenant,
        "adk.run_id": run.id,
        "adk.agent": run.agent_name,
        "adk.agent_version": run.agent_version,
        "adk.state": run.state.value,
        "adk.model": run.model,
        "adk.usage.estimated": run.usage.estimated,
        "tesserix.product": settings.product,
        "tesserix.signal": "ai",
    }
    if settings.release:
        attributes["langfuse.release"] = settings.release
    if session_id:
        attributes["langfuse.session.id"] = session_id
    if run.user:
        attributes["langfuse.user.id"] = run.user
    for key, value in (
        ("definition_revision", run.definition_revision),
        ("prompt_version", run.prompt_version),
        ("task_class", run.task_class),
    ):
        if value:
            attributes[f"langfuse.trace.metadata.{key}"] = value
    if run.path:
        attributes["langfuse.trace.metadata.path"] = "/".join(run.path)
    cached = run.usage.cached_tokens
    if cached:
        attributes["gen_ai.usage.cache_read_input_tokens"] = cached
    attributes["langfuse.observation.usage_details"] = _usage_json(
        run.usage.input_tokens, run.usage.output_tokens, cached
    )
    return attributes


def _observations(
    run: Run[Any],
) -> list[tuple[str, str, dict[str, Any], float | None, float | None, str]]:
    """Pair start/end events into observations; anything unpaired becomes a point event."""
    rows: list[tuple[str, str, dict[str, Any], float | None, float | None, str]] = []
    open_generation: RunEvent | None = None
    open_tools: dict[str, RunEvent] = {}
    for event in run.events:
        if event.kind is RunEventKind.MODEL_CALL:
            open_generation = event
        elif event.kind in _GENERATION_END:
            started = open_generation.at if open_generation is not None else event.at
            open_generation = None
            rows.append(
                (
                    event.name or run.model,
                    "generation",
                    _generation_attributes(run, event),
                    started,
                    event.at,
                    _level(event),
                )
            )
        elif event.kind is RunEventKind.TOOL_CALL and event.name:
            open_tools[event.name] = event
        elif event.kind in _TOOL_END and event.name:
            call = open_tools.pop(event.name, None)
            rows.append(
                (
                    event.name,
                    "tool",
                    _event_attributes(event),
                    call.at if call is not None else event.at,
                    event.at,
                    _level(event),
                )
            )
        elif event.kind in _ERROR_KINDS or event.kind in _WARNING_KINDS:
            rows.append(
                (
                    event.kind.value,
                    "event",
                    _event_attributes(event),
                    event.at,
                    event.at,
                    _level(event),
                )
            )
    return rows


def _generation_attributes(run: Run[Any], event: RunEvent) -> dict[str, Any]:
    attributes = _event_attributes(event)
    attributes["langfuse.observation.model.name"] = event.name or run.model
    attributes["gen_ai.request.model"] = event.name or run.model
    if event.usage is not None:
        cached = event.usage.cached_tokens
        attributes["gen_ai.usage.input_tokens"] = event.usage.input_tokens
        attributes["gen_ai.usage.output_tokens"] = event.usage.output_tokens
        if cached:
            attributes["gen_ai.usage.cache_read_input_tokens"] = cached
        attributes["langfuse.observation.usage_details"] = _usage_json(
            event.usage.input_tokens, event.usage.output_tokens, cached
        )
    return attributes


def _event_attributes(event: RunEvent) -> dict[str, Any]:
    attributes: dict[str, Any] = {"adk.event": event.kind.value}
    if event.name:
        attributes["adk.name"] = event.name
    if event.detail:
        attributes["langfuse.observation.status_message"] = event.detail[:500]
    return attributes


def _usage_json(input_tokens: int, output_tokens: int, cached: int) -> str:
    usage: dict[str, int] = {"input": input_tokens, "output": output_tokens}
    if cached:
        usage["cache_read_input_tokens"] = cached
    return json.dumps(usage, separators=(",", ":"))


def _level(event: RunEvent) -> str:
    if event.kind in _ERROR_KINDS:
        return "ERROR"
    if event.kind in _WARNING_KINDS:
        return "WARNING"
    return "DEFAULT"


def _status(level: str, detail: str | None = None) -> Status:
    if level == "ERROR":
        return Status(StatusCode.ERROR, detail)
    return Status(StatusCode.UNSET)


def _bounds(run: Run[Any]) -> tuple[int, int]:
    stamps = [event.at for event in run.events if event.at is not None]
    started = run.started_at if run.started_at is not None else (min(stamps) if stamps else None)
    ended = run.ended_at if run.ended_at is not None else (max(stamps) if stamps else None)
    start = _ns(started, int(time.time() * _NS))
    return start, _ns(ended, start)


def _ns(seconds: float | None, fallback: int) -> int:
    return fallback if seconds is None else int(seconds * _NS)


def _trace_id(run_id: str) -> int:
    return int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:16], "big") or 1


def _span_id(run_id: str, index: int) -> int:
    return int.from_bytes(hashlib.sha256(f"{run_id}:{index}".encode()).digest()[:8], "big") or 1


def _context(trace_id: int, span_id: int) -> SpanContext:
    return SpanContext(
        trace_id, span_id, is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED)
    )
