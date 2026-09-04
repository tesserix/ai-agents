"""One bounded adapter from the agent boundary to the trusted Australis tool."""

import asyncio
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from document_agent.contracts import (
    Citation,
    DocumentRequest,
    DocumentResult,
    ProcessingDecision,
    decide,
)


class AustralisToolUnavailable(Exception):
    """The configured Australis tool could not provide a safe result."""


class DocumentTool(Protocol):
    """The runtime-wired provider-neutral tool; identity stays outside tool arguments."""

    async def extract_document(self, request: DocumentRequest) -> DocumentResult: ...


class DocumentAgentState(StrEnum):
    WAIT = "wait"
    REVIEW = "review"
    REJECT = "reject"
    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"


class DocumentAgentResponse(BaseModel):
    """A safe, cited interpretation of one untrusted tool result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: DocumentAgentState
    result: DocumentResult | None = None
    citations: tuple[Citation, ...] = ()

    @model_validator(mode="after")
    def result_and_citations_are_consistent(self) -> DocumentAgentResponse:
        if self.result is None:
            if self.state is not DocumentAgentState.UNAVAILABLE or self.citations:
                raise ValueError("only unavailable responses may omit a result")
            return self
        if self.state is DocumentAgentState.UNAVAILABLE:
            raise ValueError("unavailable responses cannot include a result")
        if self.citations != tuple(self.result.citations):
            raise ValueError("agent citations must be sourced from the tool result")
        return self


class DocumentAgentService:
    """Call Australis once and route its result without interpreting document content."""

    def __init__(self, tool: DocumentTool, *, timeout_seconds: float = 20.0) -> None:
        if not 0 < timeout_seconds <= 60:
            raise ValueError("document tool timeout must be between zero and sixty seconds")
        self._tool = tool
        self._timeout_seconds = timeout_seconds

    async def run(self, request: DocumentRequest) -> DocumentAgentResponse:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._tool.extract_document(request)
        except (TimeoutError, AustralisToolUnavailable):
            return DocumentAgentResponse(state=DocumentAgentState.UNAVAILABLE)

        return DocumentAgentResponse(
            state=_state_for(decide(result)),
            result=result,
            citations=tuple(result.citations),
        )


def _state_for(decision: ProcessingDecision) -> DocumentAgentState:
    match decision:
        case ProcessingDecision.WAIT:
            return DocumentAgentState.WAIT
        case ProcessingDecision.REVIEW:
            return DocumentAgentState.REVIEW
        case ProcessingDecision.REJECT:
            return DocumentAgentState.REJECT
        case ProcessingDecision.COMPLETE:
            return DocumentAgentState.COMPLETE


__all__ = [
    "AustralisToolUnavailable",
    "DocumentAgentResponse",
    "DocumentAgentService",
    "DocumentAgentState",
    "DocumentTool",
]
