"""Recorded Kubernetes API responses, replayed so cluster state cannot flap a test."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "k8s"


def recorded(name: str) -> dict:
    """The recorded body of one Kubernetes API response."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


class ReplayTransport(httpx.AsyncBaseTransport):
    """Answers each request from a handler chosen by path, and records what was asked."""

    def __init__(self, responses: dict[str, Callable[[httpx.Request], httpx.Response]]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for path, handler in self.responses.items():
            if request.url.path == path:
                return handler(request)
        return httpx.Response(404, request=request, json={"kind": "Status", "code": 404})


def json_body(name: str) -> Callable[[httpx.Request], httpx.Response]:
    """A handler replying with a recorded fixture."""
    return lambda request: httpx.Response(200, request=request, json=recorded(name))


def text_body(text: str) -> Callable[[httpx.Request], httpx.Response]:
    """A handler replying with plain text, as the log endpoint does."""
    return lambda request: httpx.Response(200, request=request, text=text)


def status(code: int, reason: str = "") -> Callable[[httpx.Request], httpx.Response]:
    """A handler replying with a Kubernetes Status error."""
    return lambda request: httpx.Response(
        code,
        request=request,
        json={"kind": "Status", "status": "Failure", "code": code, "message": reason},
    )


def raises(error: Exception) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that fails the way a network fault does."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler
