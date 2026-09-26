import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from tesserix_adk.core import Message, ModelRequest, TextPart

from roamie_agents.config import Settings
from roamie_agents.connections import GatewaySecrets, gateway_provider, source_session
from roamie_agents.contracts import RecommendationRequest, Specialist
from roamie_agents.oauth import OAuthClient, WorkloadTokens


def settings(**values):
    return Settings(delegation_key="d" * 32, api_key="a" * 32, **values)


async def test_roamie_model_uses_only_model_gateway_credential():
    def reply(request):
        assert request.url.path == "/roamie/v1/chat/completions"
        assert request.url.host == "ai-gateway.agentgateway-system.svc.cluster.local"
        assert request.headers["authorization"] == "Bearer workload-token"
        return httpx.Response(
            200,
            json={
                "id": "completion",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "{}"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    config = settings(mcp_schema_digest="1" * 64)
    assert GatewaySecrets(config).secret("other") is None
    tokens = WorkloadTokens(
        agent="roamie-trip-manager",
        client=OAuthClient(
            subject="manager", client_id="manager", client_secret=SecretStr("s" * 32)
        ),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"access_token": "workload-token", "expires_in": 120, "token_type": "Bearer"},
            )
        ),
    )
    provider = gateway_provider(config, tokens=tokens, transport=httpx.MockTransport(reply))
    try:
        result = await provider.complete(
            ModelRequest(
                model="roamie-auto",
                messages=(Message(role="user", content=[TextPart(text="Plan")]),),
            )
        )
        assert result.content == "{}"
    finally:
        await provider.aclose()
        await tokens.aclose()


async def test_adk_mcp_session_discovers_and_calls_only_pinned_gateway_tool():
    contract = json.loads(Path("contracts/roamie-travel.json").read_text())
    shape = {key: contract[key] for key in ("input", "output")}
    digest = hashlib.sha256(
        json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    calls = []

    def reply(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        assert request.url.host == "mcp.tesserix.app"
        assert request.headers["authorization"] == "Bearer delegated"
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        results = {
            "initialize": {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "travel", "version": "1"},
                "capabilities": {"tools": {}},
            },
            "tools/list": {
                "tools": [
                    {
                        "name": "travel_search",
                        "description": "Travel",
                        "inputSchema": shape["input"],
                        "outputSchema": shape["output"],
                    }
                ]
            },
            "tools/call": {
                "content": [],
                "structuredContent": {"status": "unavailable", "facts": []},
            },
        }
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": results[body["method"]]}
        )

    async with asynccontextmanager(source_session)(
        settings(mcp_schema_digest=digest), "delegated", transport=httpx.MockTransport(reply)
    ) as source:
        batch = await source.search(Specialist.FOOD, RecommendationRequest(prompt="Dinner"))
    assert batch.status == "unavailable"
    assert calls[-1] == "tools/call"


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://aiplatform.googleapis.com/v1",
        "https://generativelanguage.googleapis.com/v1beta",
        "https://api.openai.com/v1",
        "http://ai-gateway.agentgateway-system.svc.cluster.local:8080/v1",
        "http://ai-gateway.agentgateway-system.svc.cluster.local:8080/roamie/v1?key=secret",
    ],
)
def test_model_settings_reject_provider_and_unscoped_gateway_endpoints(endpoint):
    with pytest.raises(ValidationError, match="Roamie model requests must use Agent Gateway"):
        settings(mcp_schema_digest="1" * 64, gateway_base_url=endpoint)
