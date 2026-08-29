"""Production ASGI application: the cluster, the gateway and the agent, wired once."""

from kora_agents.logging import configure_logging
from sre_agent import tools
from sre_agent.api import create_app
from sre_agent.cluster import KubernetesReader, cluster_client
from sre_agent.config import Settings
from sre_agent.gateway import gateway_provider
from sre_agent.runtime import InvestigationService

configure_logging()
settings = Settings()
client = cluster_client(
    settings.cluster_url,
    token_path=settings.cluster_token_path,
    ca_path=settings.cluster_ca_path,
    timeout=settings.cluster_timeout_seconds,
)
tools.use_cluster(KubernetesReader(client, namespaces=settings.namespaces))
provider = gateway_provider(settings)
service = InvestigationService(provider=provider)


async def shutdown() -> None:
    """Drop the cluster connection before the model one, so no read outlives the run."""
    tools.use_cluster(None)
    await client.aclose()
    await provider.aclose()


app = create_app(settings=settings, service=service, shutdown=shutdown)
