"""Production ASGI application: the roster, the gateway and both runtimes, wired once."""

from agent_telemetry import TelemetrySettings, build_recorder
from kora_agents.logging import configure_logging
from orchestrator_agent.api import create_app
from orchestrator_agent.config import Settings
from orchestrator_agent.gateway import gateway_provider
from orchestrator_agent.orchestration import OrchestratorService
from orchestrator_agent.supervision import SupervisorService
from orchestrator_agent.workers import A2AWorkerClient

configure_logging()
settings = Settings()
provider = gateway_provider(settings)
client = A2AWorkerClient(
    api_key=settings.worker_api_key,
    timeout=settings.step_timeout_seconds,
)
recorder = build_recorder(TelemetrySettings())
supervisor = SupervisorService(provider=provider, recorder=recorder)
orchestrator = OrchestratorService(
    workers={worker.name: worker for worker in settings.workers},
    client=client,
    supervisor=supervisor,
    max_steps=settings.max_steps,
    run_timeout_seconds=settings.run_timeout_seconds,
)


async def shutdown() -> None:
    """Drop the worker connections before the model one, so no call outlives the run."""
    recorder.shutdown()
    await client.aclose()
    await provider.aclose()


app = create_app(
    settings=settings,
    orchestrator=orchestrator,
    supervisor=supervisor,
    shutdown=shutdown,
)
