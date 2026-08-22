import httpx

from kora_agents.config import Settings
from kora_agents.definitions import DEFINITIONS
from kora_agents.gateway import GatewayHeadersTransport, GatewayProviderFactory
from kora_agents.identity import delegated_end_user_token


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, request=request, json={"ok": True})


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
