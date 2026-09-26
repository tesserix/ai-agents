from collections.abc import AsyncIterator

import httpx
from tesserix_adk.adapters.mcp_transport import HttpTransport, TransportSession
from tesserix_adk.core import ModelCapabilities
from tesserix_adk.core.config import McpServerConfig
from tesserix_adk.models.providers import OpenAICompatibleProvider

from roamie_agents.api import Source
from roamie_agents.config import Settings
from roamie_agents.evidence import MCPSource
from roamie_agents.oauth import GatewayTransport, WorkloadTokens


class GatewaySecrets:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def secret(self, name: str) -> str | None:
        return "workload-credential-injected-by-transport" if name == "ROAMIE_MODEL_KEY" else None


def gateway_provider(
    settings: Settings,
    *,
    agent: str = "roamie-trip-manager",
    tokens: WorkloadTokens | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OpenAICompatibleProvider:
    tokens = tokens or workload_tokens(settings, agent)
    return OpenAICompatibleProvider(
        settings.gateway_model,
        base_url=settings.gateway_base_url.removesuffix("/v1"),
        name="solo-agentgateway",
        capabilities=ModelCapabilities(
            structured_output=True,
            context_window_tokens=32768,
        ),
        api_key_variable="ROAMIE_MODEL_KEY",
        secrets=GatewaySecrets(settings),
        timeout=60,
        transport=GatewayTransport(tokens, origin=settings.gateway_base_url, transport=transport),
    )


async def source_session(
    settings: Settings, token: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[Source]:
    config = McpServerConfig(
        name="roamie-travel-mcp",
        endpoint=settings.mcp_gateway_origin.rstrip("/") + settings.mcp_gateway_path,
        allow=(settings.mcp_tool,),
        max_tools=6,
        max_result_bytes=65536,
        max_message_bytes=131072,
        read_timeout_seconds=25,
        timeout_seconds=25,
    )
    async with httpx.AsyncClient(
        timeout=25, trust_env=False, follow_redirects=False, transport=transport
    ) as client:
        session = TransportSession(
            HttpTransport(
                config,
                client=client,
                headers={"Authorization": f"Bearer {token}"},
            ),
            config=config,
        )
        try:
            yield MCPSource(
                session=session,
                tool_name=settings.mcp_tool,
                schema_digest=settings.mcp_schema_digest,
            )
        finally:
            await session.close()


def workload_tokens(settings: Settings, agent: str) -> WorkloadTokens:
    if agent not in settings.gateway_clients:
        raise ValueError(f"missing workload identity for {agent}")
    return WorkloadTokens(agent=agent, client=settings.gateway_clients[agent])
