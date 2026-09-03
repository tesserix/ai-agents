"""The request-scoped session id every trace of that request carries."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_session: ContextVar[str | None] = ContextVar("agent_telemetry_session", default=None)


@contextmanager
def bound_session(session_id: str | None) -> Iterator[None]:
    """Attach `session_id` to every run recorded while the block is active."""
    reset = _session.set(session_id or None)
    try:
        yield
    finally:
        _session.reset(reset)


def current_session() -> str | None:
    return _session.get()
