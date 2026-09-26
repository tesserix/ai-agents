from roamie_agents.api import create_app
from roamie_agents.config import Settings
from roamie_agents.connections import gateway_provider, workload_tokens
from roamie_agents.contracts import Specialist
from roamie_agents.runtime import TravelService

settings = Settings()
tokens = {kind: workload_tokens(settings, f"roamie-{kind.value}") for kind in Specialist}
providers = {
    kind: gateway_provider(settings, agent=f"roamie-{kind.value}", tokens=credential)
    for kind, credential in tokens.items()
}


async def close() -> None:
    for provider in providers.values():
        await provider.aclose()
    for credential in tokens.values():
        await credential.aclose()


app = create_app(settings=settings, service=TravelService(provider=providers), on_close=close)
