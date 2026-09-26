from roamie_agents.api import create_app
from roamie_agents.config import Settings
from roamie_agents.connections import gateway_provider
from roamie_agents.runtime import TravelService

settings = Settings()
provider = gateway_provider(settings)
app = create_app(settings=settings, service=TravelService(provider=provider))
