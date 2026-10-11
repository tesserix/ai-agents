"""ADK-backed execution of the declared Kora agents."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel
from tesserix_adk.core import AgentDefinition, Message, ModelProvider, TextPart
from tesserix_adk.guardrails import InjectionGuard, PIIGuard
from tesserix_adk.runtime import AgentRunner

from agent_telemetry import NoopRecorder, Recorder
from kora_agents.execution import ExecutionResult
from kora_agents.identity import agent_execution
from kora_agents.safety import MedicalSafetyGuard
from kora_agents.verification import COACH_VERIFIER, CoachVerdict


class AgentNotFoundError(Exception):
    """The requested agent is not in the reviewed definition set."""


class ExecutionFailedError(Exception):
    """An ADK run reached a non-success terminal state."""


class ProviderFactory(Protocol):
    """Select the provider carrying one definition's optimizer signals."""

    def for_definition(self, definition: AgentDefinition[Any]) -> ModelProvider: ...


class StaticProviderFactory:
    """Use one provider for deterministic tests and local evaluation."""

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    def for_definition(self, _definition: AgentDefinition[Any]) -> ModelProvider:
        return self._provider


class RuntimeAgentService:
    """Run only definitions present in the injected, reviewable catalog."""

    def __init__(
        self,
        *,
        definitions: Mapping[str, AgentDefinition[Any]],
        providers: ProviderFactory,
        recorder: Recorder | None = None,
    ) -> None:
        self._definitions = dict(definitions)
        self._providers = providers
        self._recorder = recorder if recorder is not None else NoopRecorder()

    async def run(self, agent_name: str, prompt: str, *, tenant: str) -> ExecutionResult:
        try:
            async with asyncio.timeout(55):
                return await self._run(agent_name, prompt, tenant=tenant)
        except TimeoutError as exc:
            raise ExecutionFailedError("deadline_exceeded") from exc

    async def _run(self, agent_name: str, prompt: str, *, tenant: str) -> ExecutionResult:
        definition = self._definitions.get(agent_name)
        if definition is None:
            raise AgentNotFoundError(agent_name)
        runner = AgentRunner(
            provider=self._providers.for_definition(definition),
            guardrails={
                "pii": PIIGuard(tenant=tenant),
                "injection": InjectionGuard(instructions=definition.agent.instructions),
                "medical_safety": MedicalSafetyGuard(),
            },
        )
        run_id = str(uuid4())
        with agent_execution(definition.agent.name, definition.agent.version, run_id):
            run = await runner.run(definition, prompt, tenant=tenant, run_id=run_id)
        self._recorder.record(run)
        if run.state.value != "completed":
            raise ExecutionFailedError(run.state.value)
        output: str | dict[str, object]
        if isinstance(run.output, BaseModel):
            output = run.output.model_dump(mode="json")
        elif isinstance(run.output, str):
            output = run.output
        elif definition.agent.free_text:
            output = _last_assistant_text(run.messages)
        else:
            raise ExecutionFailedError("unsupported_output")
        review_usage = None
        if agent_name == "nutrition-coach":
            reviewer = AgentRunner(
                provider=self._providers.for_definition(COACH_VERIFIER),
                guardrails={
                    "pii": PIIGuard(tenant=tenant),
                    "injection": InjectionGuard(instructions=COACH_VERIFIER.agent.instructions),
                    "medical_safety": MedicalSafetyGuard(),
                },
            )
            review_id = str(uuid4())
            with agent_execution("nutrition-verifier", COACH_VERIFIER.agent.version, review_id):
                review = await reviewer.run(
                    COACH_VERIFIER,
                    json.dumps({"request": prompt, "candidate": output}),
                    tenant=tenant,
                    run_id=review_id,
                )
            self._recorder.record(review)
            verdict = review.output
            if (
                review.state.value != "completed"
                or not isinstance(verdict, CoachVerdict)
                or not verdict.supported
                or not verdict.safe
                or verdict.issues
            ):
                raise ExecutionFailedError("verification_failed")
            review_usage = review.usage
        return ExecutionResult(
            run_id=run.id,
            agent_name=run.agent_name,
            state=run.state.value,
            output=output,
            input_tokens=run.usage.input_tokens
            + (review_usage.input_tokens if review_usage else 0),
            output_tokens=run.usage.output_tokens
            + (review_usage.output_tokens if review_usage else 0),
            cached_tokens=run.usage.cached_tokens
            + (review_usage.cached_tokens if review_usage else 0),
            estimated=run.usage.estimated or bool(review_usage and review_usage.estimated),
        )

    def cards(self) -> tuple[Mapping[str, object], ...]:
        return tuple(
            {
                "name": definition.agent.name,
                "version": definition.agent.version,
                "revision": definition.revision,
                "description": definition.agent.instructions.split(".", maxsplit=1)[0] + ".",
                "capability": definition.agent.metadata.get("capability", "text"),
            }
            for definition in self._definitions.values()
        )


def _last_assistant_text(messages: Sequence[Message]) -> str:
    for message in reversed(messages):
        if getattr(message, "role", None) != "assistant":
            continue
        content = getattr(message, "content", ())
        text = "".join(part.text for part in content if isinstance(part, TextPart))
        if text:
            return text
    raise ExecutionFailedError("missing_output")
