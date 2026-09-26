import asyncio

import httpx
import pytest
from pydantic import SecretStr
from tesserix_adk.core import CredentialExpiredError

from roamie_agents.oauth import OAuthClient, WorkloadTokens


async def test_concurrent_calls_refresh_once_before_expiry_and_keep_agent_identity():
    now = [100.0]
    calls = []

    def issue(request):
        calls.append(request)
        assert request.url == "https://auth.tesserix.app/oauth/v2/token"
        assert b"grant_type=client_credentials" in request.content
        return httpx.Response(
            200,
            json={"access_token": f"token-{len(calls)}", "expires_in": 120, "token_type": "Bearer"},
        )

    tokens = WorkloadTokens(
        agent="roamie-food",
        client=OAuthClient(
            subject="food-subject", client_id="food-client", client_secret=SecretStr("s" * 32)
        ),
        clock=lambda: now[0],
        transport=httpx.MockTransport(issue),
    )
    try:
        first = await asyncio.gather(*(tokens.token() for _ in range(8)))
        assert first == ["token-1"] * 8
        assert tokens.identity.agent == "roamie-food"
        assert tokens.identity.principal.subject == "food-subject"
        now[0] = 191.0
        assert await tokens.token() == "token-2"
        assert len(calls) == 2
    finally:
        await tokens.aclose()


async def test_failed_refresh_never_reuses_expired_credential():
    now = [100.0]

    def issue(request):
        if now[0] > 100:
            return httpx.Response(401)
        return httpx.Response(
            200, json={"access_token": "token", "expires_in": 120, "token_type": "Bearer"}
        )

    tokens = WorkloadTokens(
        agent="roamie-food",
        client=OAuthClient(subject="food", client_id="food", client_secret=SecretStr("s" * 32)),
        clock=lambda: now[0],
        transport=httpx.MockTransport(issue),
    )
    try:
        assert await tokens.token() == "token"
        now[0] = 221
        with pytest.raises(CredentialExpiredError):
            await tokens.token()
    finally:
        await tokens.aclose()


async def test_gateway_token_is_never_sent_to_another_origin():
    from roamie_agents.oauth import GatewayTransport

    issued = []
    tokens = WorkloadTokens(
        agent="roamie-food",
        client=OAuthClient(subject="food", client_id="food", client_secret=SecretStr("s" * 32)),
        transport=httpx.MockTransport(lambda request: issued.append(request)),
    )
    gateway = GatewayTransport(
        tokens,
        origin="https://gateway.example",
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    try:
        async with httpx.AsyncClient(transport=gateway) as client:
            with pytest.raises(ValueError, match="destination mismatch"):
                await client.get("https://attacker.example/")
        assert not issued
    finally:
        await tokens.aclose()


@pytest.mark.parametrize(
    "body",
    [
        {"access_token": "ok", "expires_in": 0, "token_type": "Bearer"},
        {"access_token": "ok", "expires_in": True, "token_type": "Bearer"},
        {"access_token": "", "expires_in": 120, "token_type": "Bearer"},
        {"access_token": "ok", "expires_in": 120, "token_type": "Basic"},
    ],
)
async def test_invalid_token_response_fails_closed(body):
    tokens = WorkloadTokens(
        agent="roamie-food",
        client=OAuthClient(subject="food", client_id="food", client_secret=SecretStr("s" * 32)),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    )
    try:
        with pytest.raises(CredentialExpiredError):
            await tokens.token()
    finally:
        await tokens.aclose()


def test_specialists_cannot_share_a_configured_workload_identity():
    from pydantic import ValidationError

    from roamie_agents.config import Settings

    client = OAuthClient(subject="same", client_id="same", client_secret=SecretStr("s" * 32))
    with pytest.raises(ValidationError, match="distinct workload identity"):
        Settings(
            api_key="a" * 32,
            delegation_key="d" * 32,
            mcp_schema_digest="1" * 64,
            gateway_clients={"roamie-food": client, "roamie-shopping": client},
        )
