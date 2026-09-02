"""The durable path: one Temporal workflow wrapping a whole orchestration.

The workflow is deliberately one retryable activity. Splitting steps into separate
activities would need the ADK's journalled workflow stores; until a product needs
per-step replay, whole-run retry keeps the history free of worker answers and the
credential out of it entirely — the activity reads its configuration from the process,
never from workflow input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

from orchestrator_agent.orchestration import OrchestratorService

TASK_QUEUE_DEFAULT = "orchestrations"


@dataclass(frozen=True)
class OrchestrationInput:
    """What a durable run carries: the task and the tenant, never a credential."""

    raw_task: str
    tenant: str


@dataclass(frozen=True)
class OrchestrationOutput:
    run_id: str
    ok: bool
    report_json: str


class _Runtime:
    """The process-owned orchestrator the activity runs against."""

    service: OrchestratorService | None = None


def use_orchestrator(service: OrchestratorService | None) -> None:
    """Give the activity its orchestrator; the worker entrypoint calls this once."""
    _Runtime.service = service


@activity.defn(name="run_orchestration")
async def run_orchestration(payload: OrchestrationInput) -> OrchestrationOutput:
    service = _Runtime.service
    if service is None:
        raise RuntimeError("no orchestrator wired into this worker process")
    report = await service.run(payload.raw_task, tenant=payload.tenant)
    return OrchestrationOutput(
        run_id=report.run_id, ok=report.ok, report_json=report.model_dump_json()
    )


@workflow.defn(name="OrchestrationWorkflow")
class OrchestrationWorkflow:
    """Run one orchestration to completion, retrying transient worker failures whole."""

    @workflow.run
    async def run(self, payload: OrchestrationInput) -> OrchestrationOutput:
        return await workflow.execute_activity(
            run_orchestration,
            payload,
            start_to_close_timeout=timedelta(seconds=300),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                maximum_attempts=3,
                non_retryable_error_types=["UnsupportedTaskError"],
            ),
        )
