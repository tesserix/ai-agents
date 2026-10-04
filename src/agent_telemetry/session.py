"""The request-scoped session id and caller span every trace of that request carries."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry.trace import SpanContext, TraceFlags

_session: ContextVar[str | None] = ContextVar("agent_telemetry_session", default=None)
_parent: ContextVar[SpanContext | None] = ContextVar("agent_telemetry_parent", default=None)
_TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})")


@contextmanager
def bound_session(session_id: str | None, traceparent: str | None = None) -> Iterator[None]:
    """Attach `session_id`, and the caller's W3C span if valid, to runs recorded in the block."""
    reset_session = _session.set(session_id or None)
    reset_parent = _parent.set(_parse(traceparent))
    try:
        yield
    finally:
        _parent.reset(reset_parent)
        _session.reset(reset_session)


def current_session() -> str | None:
    return _session.get()


def current_parent() -> SpanContext | None:
    return _parent.get()


def _parse(header: str | None) -> SpanContext | None:
    match = _TRACEPARENT.fullmatch(header or "")
    if match is None:
        return None
    trace_id, span_id = int(match[1], 16), int(match[2], 16)
    if not trace_id or not span_id:
        return None
    return SpanContext(trace_id, span_id, is_remote=True, trace_flags=TraceFlags(int(match[3], 16)))
