"""Temporal worker entrypoint: `python -m orchestrator_agent.worker`.

Runs the same orchestrator the HTTP edge serves, behind a Temporal task queue, so a
product that needs a durable run starts `OrchestrationWorkflow` instead of calling
`POST /v1/orchestrations` — and gets retry and resumption without a second code path.
"""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from kora_agents.logging import configure_logging
from orchestrator_agent.config import Settings
from orchestrator_agent.durable import OrchestrationWorkflow, run_orchestration, use_orchestrator
from orchestrator_agent.gateway import gateway_provider
from orchestrator_agent.orchestration import OrchestratorService
from orchestrator_agent.supervision import SupervisorService
from orchestrator_agent.workers import A2AWorkerClient


async def serve() -> None:
    configure_logging()
    settings = Settings()
    if settings.temporal_address is None:
        raise SystemExit("ORCHESTRATOR_TEMPORAL_ADDRESS is required for the durable worker")
    provider = gateway_provider(settings)
    client = A2AWorkerClient(api_key=settings.worker_api_key, timeout=settings.step_timeout_seconds)
    use_orchestrator(
        OrchestratorService(
            workers={worker.name: worker for worker in settings.workers},
            client=client,
            supervisor=SupervisorService(provider=provider),
            max_steps=settings.max_steps,
            run_timeout_seconds=settings.run_timeout_seconds,
        )
    )
    temporal = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    try:
        worker = Worker(
            temporal,
            task_queue=settings.temporal_task_queue,
            workflows=[OrchestrationWorkflow],
            activities=[run_orchestration],
        )
        await worker.run()
    finally:
        use_orchestrator(None)
        await client.aclose()
        await provider.aclose()


if __name__ == "__main__":
    asyncio.run(serve())
