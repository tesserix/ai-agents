from collections.abc import AsyncIterator

import httpx
from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from orchestrator_agent.config import WorkerEndpoint
from orchestrator_agent.supervision import SupervisorService
from orchestrator_agent.workers import A2AWorkerClient
from roamie_agents.api import Source
from roamie_agents.config import Settings
from roamie_agents.connections import gateway_provider, source_session, workload_tokens
from roamie_agents.contracts import Specialist
from roamie_agents.definitions import manager_definition
from roamie_agents.manager import PersonalTripManager
from roamie_agents.manager_api import create_manager_app
from roamie_agents.oauth import GatewayTransport


class ManagerSettings(Settings):
    model_config = SettingsConfigDict(env_prefix="ROAMIE_MANAGER_", frozen=True, extra="forbid")
    profile_signing_key: SecretStr = Field(min_length=32)
    identity_key: SecretStr = Field(min_length=32)
    a2a_gateway_origin: str = "http://agentgateway-mcp.agentgateway-system.svc.cluster.local:8082"


settings = ManagerSettings()
tokens = workload_tokens(settings, "roamie-trip-manager")
provider = gateway_provider(settings, tokens=tokens)
worker_http = httpx.AsyncClient(
    timeout=50,
    trust_env=False,
    follow_redirects=False,
    transport=GatewayTransport(tokens, origin=settings.a2a_gateway_origin),
)
worker_client = A2AWorkerClient(
    api_key=SecretStr("workload-credential-injected-by-transport"),
    timeout=50,
    client=worker_http,
)
manager = PersonalTripManager(
    workers={
        kind: WorkerEndpoint(
            name=f"roamie-{kind.value}",
            url=f"{settings.a2a_gateway_origin.rstrip('/')}/a2a/v1/roamie-{kind.value}",
        )
        for kind in Specialist
    },
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
    await worker_http.aclose()
    await provider.aclose()
    await tokens.aclose()


app = create_manager_app(
    manager=manager,
    sources=sources,
    on_close=close,
    api_key=settings.api_key,
    profile_signing_key=settings.profile_signing_key,
)
