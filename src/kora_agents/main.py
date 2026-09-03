"""Production ASGI application."""

from agent_telemetry import TelemetrySettings, build_recorder
from kora_agents.api import create_app
from kora_agents.config import Settings
from kora_agents.definitions import DEFINITIONS
from kora_agents.gateway import GatewayProviderFactory
from kora_agents.logging import configure_logging
from kora_agents.runtime import RuntimeAgentService

configure_logging()
settings = Settings()
providers = GatewayProviderFactory(settings)
recorder = build_recorder(TelemetrySettings())
service = RuntimeAgentService(definitions=DEFINITIONS, providers=providers, recorder=recorder)


async def shutdown() -> None:
    """Flush queued traces, then drop the gateway connection."""
    recorder.shutdown()
    await providers.aclose()


app = create_app(settings=settings, service=service, shutdown=shutdown)
