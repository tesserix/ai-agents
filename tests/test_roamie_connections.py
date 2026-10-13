import asyncio
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


@pytest.mark.parametrize("stateful", [False, True])
@pytest.mark.parametrize(
    "fail_at,status,failures",
    [
        (None, 200, 0),
        ("initialize", 500, 1),
        ("tools/list", 500, 1),
        ("tools/call", 500, 1),
        ("tools/call", 500, 2),
        ("tools/call", 403, 1),
    ],
)
async def test_adk_mcp_session_discovers_and_calls_only_pinned_gateway_tool(
    stateful, fail_at, status, failures, monkeypatch
):
    from tesserix_adk.testing import FakeClock

    monkeypatch.setattr("tesserix_adk.adapters.mcp_resilience.SystemClock", FakeClock)
    contract = json.loads(await asyncio.to_thread(Path("contracts/roamie-travel.json").read_text))
    shape = {key: contract[key] for key in ("input", "output")}
    digest = hashlib.sha256(
        json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    calls = []

    def reply(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == fail_at and calls.count(fail_at) <= failures:
            return httpx.Response(status)
        assert request.url.host == "mcp.tesserix.app"
        assert request.headers["authorization"] == "Bearer delegated"
        if stateful and body["method"] != "initialize":
            assert request.headers["Mcp-Session-Id"] == "travel-session"
            assert request.headers["MCP-Protocol-Version"] == "2025-06-18"
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
            200,
            headers={"Mcp-Session-Id": "travel-session"}
            if stateful and body["method"] == "initialize"
            else {},
            json={"jsonrpc": "2.0", "id": body["id"], "result": results[body["method"]]},
        )

    async with asynccontextmanager(source_session)(
        settings(mcp_schema_digest=digest), "delegated", transport=httpx.MockTransport(reply)
    ) as source:
        if failures == 2 or status == 403:
            from roamie_agents.runtime import TravelFailure

            with pytest.raises(TravelFailure, match="provider_unavailable"):
                await source.search(Specialist.FOOD, RecommendationRequest(prompt="Dinner"))
        else:
            batch = await source.search(Specialist.FOOD, RecommendationRequest(prompt="Dinner"))
            assert batch.status == "unavailable"
    assert calls[-1] == "tools/call"
    if fail_at:
        assert calls.count(fail_at) == (1 if status == 403 else 2)


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


async def test_weather_uses_own_transport_without_gateway_credentials():
    def weather_reply(request):
        assert request.url.host == "weather.googleapis.com"
        assert "authorization" not in request.headers
        assert request.headers["X-Goog-Api-Key"] == "weather-only"
        return httpx.Response(200, json={"forecastDays": []})

    from datetime import date

    from roamie_agents.contracts import Location

    async with asynccontextmanager(source_session)(
        settings(mcp_schema_digest="1" * 64, weather_api_key="weather-only"),
        "private-gateway-token",
        transport=httpx.MockTransport(lambda _: pytest.fail("weather must not call the gateway")),
        weather_transport=httpx.MockTransport(weather_reply),
    ) as source:
        batch = await source.search(
            Specialist.WEATHER,
            RecommendationRequest(
                prompt="Weather",
                origin=Location(latitude=1, longitude=2),
                start_date=date.today(),
                end_date=date.today(),
            ),
        )
    assert batch.status == "ok"


async def test_open_meteo_key_selects_commercial_weather_without_other_credentials():
    from datetime import date

    from roamie_agents.contracts import Location

    def reply(request):
        assert request.url.host == "customer-api.open-meteo.com"
        assert request.headers["X-Api-Key"] == "open-meteo-test"
        assert "authorization" not in request.headers
        assert "x-goog-api-key" not in request.headers
        assert "open-meteo-test" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "timezone": "Asia/Tokyo",
                "daily_units": {
                    "time": "iso8601",
                    "temperature_2m_max": "°C",
                    "temperature_2m_min": "°C",
                    "precipitation_probability_max": "%",
                    "uv_index_max": "",
                },
                "daily": {
                    "time": [date.today().isoformat()],
                    "temperature_2m_max": [25],
                    "temperature_2m_min": [15],
                    "precipitation_probability_max": [30],
                    "uv_index_max": [4],
                },
            },
        )

    async with asynccontextmanager(source_session)(
        settings(
            mcp_schema_digest="1" * 64,
            weather_api_key="google-test",
            open_meteo_api_key="open-meteo-test",
        ),
        "private-gateway-token",
        transport=httpx.MockTransport(lambda _: pytest.fail("weather is not an LLM request")),
        weather_transport=httpx.MockTransport(reply),
    ) as source:
        result = await source.search(
            Specialist.WEATHER,
            RecommendationRequest(
                prompt="Weather",
                origin=Location(latitude=35.6, longitude=139.6),
                destination_country="JP",
                start_date=date.today(),
                end_date=date.today(),
            ),
        )
    assert result.status == "ok"
    assert str(result.facts[0].source_url) == "https://open-meteo.com/"


async def test_self_hosted_setting_needs_no_commercial_key_and_uses_google_failover():
    from datetime import date

    from roamie_agents.contracts import Location

    calls = []

    def reply(request):
        calls.append(request.url.host)
        if len(calls) == 1:
            assert "x-api-key" not in request.headers
            assert "x-goog-api-key" not in request.headers
            return httpx.Response(429)
        assert request.headers["x-goog-api-key"] == "google-test"
        return httpx.Response(200, json={"forecastDays": []})

    async with asynccontextmanager(source_session)(
        settings(
            mcp_schema_digest="1" * 64, weather_api_key="google-test", open_meteo_self_hosted=True
        ),
        "private-gateway-token",
        transport=httpx.MockTransport(lambda _: pytest.fail("weather is not AI")),
        weather_transport=httpx.MockTransport(reply),
    ) as source:
        result = await source.search(
            Specialist.WEATHER,
            RecommendationRequest(
                prompt="Weather",
                origin=Location(latitude=-37.81, longitude=144.96),
                destination_country="AU",
                start_date=date.today(),
                end_date=date.today(),
            ),
        )
    assert result.status == "ok"
    assert calls == ["roamie-weather.roamie.svc.cluster.local", "weather.googleapis.com"]
