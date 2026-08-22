"""Request-scoped delegation of an already-verified app-user credential."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

DELEGATED_IDENTITY_HEADER = "X-Kora-End-User-Token"

_end_user_token: ContextVar[str | None] = ContextVar("kora_end_user_token", default=None)


@contextmanager
def delegated_end_user_token(token: str | None) -> Iterator[None]:
    """Make one inbound credential available to this request's provider calls."""
    reset = _end_user_token.set(token or None)
    try:
        yield
    finally:
        _end_user_token.reset(reset)


def current_end_user_token() -> str | None:
    """Return the current request's delegated credential without logging it."""
    return _end_user_token.get()
