"""Production ASGI application."""

from kora_agents.api import create_app
from kora_agents.config import Settings
from kora_agents.definitions import DEFINITIONS
from kora_agents.gateway import GatewayProviderFactory
from kora_agents.runtime import RuntimeAgentService

settings = Settings()
providers = GatewayProviderFactory(settings)
service = RuntimeAgentService(definitions=DEFINITIONS, providers=providers)
app = create_app(settings=settings, service=service, shutdown=providers.aclose)
