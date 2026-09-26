from collections.abc import AsyncIterator

import httpx
from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from orchestrator_agent.supervision import SupervisorService
from orchestrator_agent.workers import A2AWorkerClient
from roamie_agents.api import Source
from roamie_agents.config import Settings
from roamie_agents.connections import gateway_provider, source_session, workload_tokens
from roamie_agents.definitions import manager_definition
from roamie_agents.discovery import RegistryWorkers
from roamie_agents.manager import PersonalTripManager
from roamie_agents.manager_api import create_manager_app
from roamie_agents.oauth import GatewayTransport
from roamie_agents.profile_authority import ProfileAuthority


class ManagerSettings(Settings):
    model_config = SettingsConfigDict(env_prefix="ROAMIE_MANAGER_", frozen=True, extra="forbid")
    profile_signing_key: SecretStr = Field(min_length=32)
    identity_key: SecretStr = Field(min_length=32)
    registry_origin: str = "http://agentregistry.agentregistry-system.svc.cluster.local:12121"
    a2a_gateway_origin: str = "http://agentgateway-mcp.agentgateway-system.svc.cluster.local:8082"


settings = ManagerSettings()
tokens = workload_tokens(settings, "roamie-trip-manager")
authority = ProfileAuthority(key=settings.api_key)
provider = gateway_provider(settings, tokens=tokens)
worker_http = httpx.AsyncClient(
    timeout=65,
    trust_env=False,
    follow_redirects=False,
    transport=GatewayTransport(tokens, origin=settings.a2a_gateway_origin),
)
worker_client = A2AWorkerClient(
    api_key=SecretStr("workload-credential-injected-by-transport"),
    timeout=65,
    client=worker_http,
)
registry_http = httpx.AsyncClient(timeout=2, trust_env=False, follow_redirects=False)
manager = PersonalTripManager(
    workers={},
    discovery=RegistryWorkers(
        registry_http,
        registry_origin=settings.registry_origin,
        gateway_origin=settings.a2a_gateway_origin,
    ),
    client=worker_client,
    supervisor=SupervisorService(provider=provider, definition=manager_definition()),
    identity_key=settings.identity_key,
    delegation_key=settings.delegation_key,
)


async def sources(token: str) -> AsyncIterator[Source]:
    async for source in source_session(
        settings,
        "workload-credential-injected-by-transport",
        transport=GatewayTransport(tokens, origin=settings.mcp_gateway_origin),
    ):
        yield source


async def close() -> None:
    await registry_http.aclose()
    await worker_http.aclose()
    await provider.aclose()
    await tokens.aclose()
    await authority.aclose()


app = create_manager_app(
    manager=manager,
    sources=sources,
    on_close=close,
    api_key=settings.api_key,
    profile_signing_key=settings.profile_signing_key,
    profile_authority=authority.current_revision,
)
