"""ADK-backed execution of the declared Kora agents."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel
from tesserix_adk.core import AgentDefinition, Message, ModelProvider, TextPart
from tesserix_adk.guardrails import InjectionGuard, PIIGuard
from tesserix_adk.runtime import AgentRunner

from agent_telemetry import NoopRecorder, Recorder
from kora_agents.execution import ExecutionResult
from kora_agents.safety import MedicalSafetyGuard


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
        run = await runner.run(definition, prompt, tenant=tenant)
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
        return ExecutionResult(
            run_id=run.id,
            agent_name=run.agent_name,
            state=run.state.value,
            output=output,
            input_tokens=run.usage.input_tokens,
            output_tokens=run.usage.output_tokens,
            cached_tokens=run.usage.cached_tokens,
            estimated=run.usage.estimated,
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
