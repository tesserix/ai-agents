"""Request-scoped delegation of an already-verified app-user credential."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

INBOUND_DELEGATED_IDENTITY_HEADER = "X-Kora-Delegated-End-User-Token"
MODEL_GATEWAY_END_USER_AUTH_HEADER = "X-Kora-End-User-Token"

_user_id: ContextVar[str | None] = ContextVar("kora_user_id", default=None)

_end_user_token: ContextVar[str | None] = ContextVar("kora_end_user_token", default=None)


@contextmanager
def delegated_end_user_token(token: str | None, *, user_id: str | None = None) -> Iterator[None]:
    """Make one inbound credential available to this request's provider calls."""
    reset = _end_user_token.set(token or None)
    user_reset = _user_id.set(user_id or None)
    try:
        yield
    finally:
        _end_user_token.reset(reset)
        _user_id.reset(user_reset)


def current_end_user_token() -> str | None:
    """Return the current request's delegated credential without logging it."""
    return _end_user_token.get()


def current_user_id() -> str | None:
    return _user_id.get()
