"""Running one investigation: the ADK loop, wired to the tools and the guardrails.

The service owns the wiring and nothing else. What the agent may call comes from the
definition, what it may cost comes from the budget, and what it read is recorded as tool
spans so an evaluation — and, later, an incident row — can say which reads produced the
answer rather than taking the model's word for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tesserix_adk.guardrails import InjectionGuard, PIIGuard
from tesserix_adk.runtime import AgentRunner

from sre_agent.definitions import INVESTIGATOR, Investigation
from sre_agent.tools import registry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tesserix_adk.core import AgentDefinition, ModelProvider, RunEvent
    from tesserix_adk.tools import ToolCallSpan, ToolRegistry

DEFAULT_TENANT = "tesserix"


class InvestigationFailedError(Exception):
    """The run ended somewhere other than a validated answer."""

    def __init__(self, state: str, detail: str = "") -> None:
        self.state = state
        self.detail = detail
        said = f": {detail}" if detail else ""
        super().__init__(f"the investigation ended in state {state!r}{said}")


@dataclass(frozen=True, slots=True)
class InvestigationRun:
    """What one investigation produced, and what it read to produce it."""

    run_id: str
    findings: Investigation
    calls: tuple[ToolCallSpan, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def tools_called(self) -> tuple[str, ...]:
        """The tools that ran, in order, excluding calls the registry refused."""
        return tuple(span.tool for span in self.calls if span.outcome == "ok")


@dataclass
class _Spans:
    """Collects tool spans for one run, since the registry reports them as they close."""

    recorded: list[ToolCallSpan] = field(default_factory=list)

    def __call__(self, span: ToolCallSpan) -> None:
        self.recorded.append(span)


class InvestigationService:
    """Investigates on demand, with one provider and one cluster-backed tool registry.

    Args:
        provider: The model behind the run. In production the Solo gateway; in evaluation
            a scripted fake, so a suite cannot be flaked by a live model.
        definition: The reviewed agent. Defaults to the published investigator.
        tools: The registry to dispatch through. Defaults to a fresh one holding every
            read-only tool.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider,
        definition: AgentDefinition[Investigation] = INVESTIGATOR,
        tools: ToolRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._definition = definition
        self._tools = tools if tools is not None else registry()

    async def investigate(self, prompt: str, *, tenant: str = DEFAULT_TENANT) -> InvestigationRun:
        """Investigate `prompt` and return the findings with the reads behind them.

        Raises:
            InvestigationFailedError: If the run did not complete with a validated answer.
                A half-formed investigation is worse than none: it reads like a conclusion.
        """
        spans = _Spans()
        self._tools.observe(spans)
        agent = self._definition.agent
        runner = AgentRunner(
            provider=self._provider,
            tools=self._tools,
            guardrails={
                "pii": PIIGuard(tenant=tenant),
                "injection": InjectionGuard(instructions=agent.instructions),
            },
        )
        run = await runner.run(self._definition, prompt, tenant=tenant)
        if run.state.value != "completed" or not isinstance(run.output, Investigation):
            raise InvestigationFailedError(run.state.value, _why_it_ended(run.events))
        return InvestigationRun(
            run_id=run.id,
            findings=run.output,
            calls=tuple(spans.recorded),
            input_tokens=run.usage.input_tokens,
            output_tokens=run.usage.output_tokens,
        )


def _why_it_ended(events: Sequence[RunEvent]) -> str:
    """The detail the loop recorded as it terminated, for a report a human will read."""
    for event in reversed(events):
        if event.detail:
            return str(event.detail)
    return ""
