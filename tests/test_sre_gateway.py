import httpx
from tesserix_adk.core import Message, ModelRequest, TextPart

from sre_agent.config import Settings
from sre_agent.gateway import gateway_provider


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
                        "message": {"role": "assistant", "content": "{}"},
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )


def settings(**overrides) -> Settings:
    return Settings(api_key="service-secret", gateway_api_key="gateway-secret", **overrides)


def request() -> ModelRequest:
    return ModelRequest(
        model="sre-auto",
        messages=(Message(role="user", content=[TextPart(text="investigate")]),),
    )


async def test_the_provider_calls_the_gateway_with_its_key_and_routing_signals() -> None:
    transport = RecordingTransport()
    provider = gateway_provider(settings(), transport=transport)

    await provider.complete(request())

    sent = transport.requests[0]
    assert sent.headers["authorization"] == "Bearer gateway-secret"
    assert sent.headers["x-tesserix-capability"] == "json"
    assert sent.headers["x-tesserix-context-kind"] == "structured"
    await provider.aclose()


async def test_a_gateway_url_configured_with_its_version_suffix_is_not_doubled() -> None:
    transport = RecordingTransport()
    provider = gateway_provider(
        settings(gateway_base_url="http://sre-ai.agentgateway-system.svc.cluster.local:8080/v1"),
        transport=transport,
    )

    await provider.complete(request())

    assert transport.requests[0].url.path == "/v1/chat/completions"
    await provider.aclose()
