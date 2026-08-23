import httpx
import pytest
from tesserix_adk.core import ConfigurationError, Message, ModelRequest, TextPart

from kora_agents.config import Settings
from kora_agents.definitions import DEFINITIONS
from kora_agents.gateway import GatewayHeadersTransport, GatewayProviderFactory
from kora_agents.identity import delegated_end_user_token


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "completion-1",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "balanced plate"},
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )


def model_request() -> ModelRequest:
    return ModelRequest(
        model="kora-auto",
        messages=(Message(role="user", content=[TextPart(text="plan lunch")]),),
    )


async def test_gateway_transport_sets_optimizer_routing_signals() -> None:
    recording = RecordingTransport()
    transport = GatewayHeadersTransport(
        recording,
        capability="json",
        context_kind="structured",
    )

    async with httpx.AsyncClient(transport=transport) as client:
        await client.post("https://gateway.test/v1/chat/completions", json={"messages": []})

    request = recording.requests[0]
    assert request.headers["x-kora-ai-capability"] == "json"
    assert request.headers["x-kora-ai-context-kind"] == "structured"


async def test_gateway_transport_delegates_the_current_verified_user() -> None:
    recording = RecordingTransport()
    transport = GatewayHeadersTransport(
        recording,
        capability="text",
        context_kind="conversation",
    )

    async with httpx.AsyncClient(transport=transport) as client:
        with delegated_end_user_token("Bearer firebase-user-token"):
            await client.post("https://gateway.test/v1/chat/completions", json={"messages": []})
        await client.post("https://gateway.test/v1/chat/completions", json={"messages": []})

    assert recording.requests[0].headers["x-kora-end-user-token"] == "Bearer firebase-user-token"
    assert "x-kora-end-user-token" not in recording.requests[1].headers


async def test_provider_uses_one_api_version_and_both_gateway_credentials(monkeypatch) -> None:
    recording = RecordingTransport()
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda: recording)
    settings = Settings(
        api_key="service-key",
        gateway_api_key="gateway-key",
        gateway_base_url="https://gateway.test/v1/",
    )
    factory = GatewayProviderFactory(settings)

    try:
        provider = factory.for_definition(DEFINITIONS["nutrition-coach"])
        with delegated_end_user_token("Bearer firebase-user-token"):
            await provider.complete(model_request())
    finally:
        await factory.aclose()

    request = recording.requests[0]
    assert str(request.url) == "https://gateway.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer gateway-key"
    assert request.headers["x-kora-end-user-token"] == "Bearer firebase-user-token"


async def test_provider_refuses_a_blank_gateway_key_before_network(monkeypatch) -> None:
    recording = RecordingTransport()
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda: recording)
    settings = Settings(api_key="service-key", gateway_api_key="")
    factory = GatewayProviderFactory(settings)

    try:
        provider = factory.for_definition(DEFINITIONS["nutrition-coach"])
        with pytest.raises(ConfigurationError, match="KORA_AI_GATEWAY_API_KEY"):
            await provider.complete(model_request())
    finally:
        await factory.aclose()

    assert recording.requests == []


async def test_provider_factory_caches_each_route_and_closes_it() -> None:
    settings = Settings(api_key="service-key", gateway_api_key="gateway-key")
    factory = GatewayProviderFactory(settings)

    structured = factory.for_definition(DEFINITIONS["meal-planner"])
    cached = factory.for_definition(DEFINITIONS["meal-planner"])
    conversation = factory.for_definition(DEFINITIONS["nutrition-coach"])

    assert structured is cached
    assert structured is not conversation
    assert structured.name == "solo-agentgateway"
    await factory.aclose()
