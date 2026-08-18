"""The application-facing agent execution contract."""

from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class ExecutionResult(BaseModel):
    """The payload-safe part of a completed ADK run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    agent_name: str
    state: str
    output: str | dict[str, object]
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated: bool


class AgentService(Protocol):
    """What the HTTP edge needs from the ADK runtime."""

    async def run(self, agent_name: str, prompt: str, *, tenant: str) -> ExecutionResult: ...

    def cards(self) -> tuple[Mapping[str, object], ...]: ...
