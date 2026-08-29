"""The offline evaluation suite: scripted model, replayed cluster, deterministic checks.

An evaluation that calls a live model against a live cluster measures the weather. These
cases script both sides — the turns the model takes and the bodies the API server returns
— so a failure means the agent's declaration changed, which is the only thing the suite is
there to notice.

Security cases (prompt injection, the allowlist, the refusal to invent an incident) pass
completely or the publish does not happen. Quality cases carry thresholds.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field
from tesserix_adk.testing import FakeModelProvider, ScriptedTurn

from sre_agent import tools
from sre_agent.cluster import KubernetesReader
from sre_agent.runtime import InvestigationFailedError, InvestigationService

SUITE_PATH = Path("evals/sre-investigator.yaml")
CLUSTER_URL = "https://kubernetes.default.svc"


class ClusterReply(BaseModel):
    """What the replayed API server answers one path with."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    status: int = 200
    body: Any | None = Field(default=None, alias="json")
    text: str = ""


class ScriptStep(BaseModel):
    """One turn the scripted model takes: a tool call, or the answer it ends on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    answer: dict[str, Any] | None = None
    text: str = ""
    repeat: int = Field(default=1, ge=1, le=40)


class Expectations(BaseModel):
    """What the case asserts about the run, all machine-checkable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calls: tuple[str, ...] = ()
    forbidden_calls: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    incident_suspected: bool | None = None
    confidence: Literal["low", "medium", "high"] | None = None
    max_tool_calls: int | None = None
    fails: bool = False
    fails_because: str = ""
    no_cluster_reads: bool = False


class EvaluationCase(BaseModel):
    """One scripted investigation and the contract its result must meet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: Literal["quality", "security"]
    prompt: str = Field(min_length=1, max_length=4_000)
    cluster: dict[str, ClusterReply] = Field(default_factory=dict)
    script: tuple[ScriptStep, ...] = Field(min_length=1, max_length=40)
    expect: Expectations


class EvaluationSuite(BaseModel):
    """Every case the investigator is published against."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str = Field(pattern=r"^[a-z0-9-]+$")
    cases: tuple[EvaluationCase, ...] = Field(min_length=1, max_length=50)


class CaseResult(BaseModel):
    """One case's outcome, with a message per broken expectation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: Literal["quality", "security"]
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Whether every expectation held."""
        return not self.failures


class SuiteReport(BaseModel):
    """What the suite found, in the shape the publish gate reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    results: tuple[CaseResult, ...]

    @property
    def failures(self) -> tuple[CaseResult, ...]:
        """The cases that did not hold."""
        return tuple(result for result in self.results if not result.passed)

    @property
    def security_holds(self) -> bool:
        """Whether every security case passed, which the publish requires."""
        return all(result.passed for result in self.results if result.kind == "security")

    @property
    def passed(self) -> bool:
        """Whether the suite as a whole permits a publish."""
        return not self.failures


class _ScriptedCluster(httpx.AsyncBaseTransport):
    """Replays one case's recorded API bodies, and counts what was asked for."""

    def __init__(self, replies: dict[str, ClusterReply]) -> None:
        self._replies = replies
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self._replies.get(request.url.path)
        if reply is None:
            return httpx.Response(404, request=request, json={"kind": "Status", "code": 404})
        if reply.body is not None:
            return httpx.Response(reply.status, request=request, json=reply.body)
        return httpx.Response(reply.status, request=request, text=reply.text)


def load_suite(path: Path = SUITE_PATH) -> EvaluationSuite:
    """Read and validate the suite the definition names."""
    return EvaluationSuite.model_validate(yaml.safe_load(path.read_text()))


def _turns(script: tuple[ScriptStep, ...]) -> list[ScriptedTurn]:
    turns: list[ScriptedTurn] = []
    for step in script:
        for _ in range(step.repeat):
            if step.tool:
                turns.append(ScriptedTurn.calling(step.tool, step.arguments))
            elif step.answer is not None:
                turns.append(ScriptedTurn.returning(step.answer))
            else:
                turns.append(ScriptedTurn.saying(step.text))
    return turns


async def run_case(case: EvaluationCase) -> CaseResult:
    """Run one case against a scripted model and a replayed cluster."""
    transport = _ScriptedCluster(case.cluster)
    client = httpx.AsyncClient(transport=transport, base_url=CLUSTER_URL)
    tools.use_cluster(KubernetesReader(client))
    service = InvestigationService(provider=FakeModelProvider(*_turns(case.script), strict=False))
    try:
        try:
            run = await service.investigate(case.prompt)
        except InvestigationFailedError as ended:
            return CaseResult(
                name=case.name,
                kind=case.kind,
                failures=_when_it_failed(case, transport, str(ended)),
            )
        return CaseResult(name=case.name, kind=case.kind, failures=_checked(case, run, transport))
    finally:
        tools.use_cluster(None)
        await client.aclose()


async def run_suite(suite: EvaluationSuite) -> SuiteReport:
    """Run every case, in order, and report what held."""
    return SuiteReport(
        suite=suite.suite, results=tuple([await run_case(case) for case in suite.cases])
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the suite and report it, as the publish gate: zero only if every case held.

    Prints one JSON object, so a failed publish says which case broke without anyone
    opening the run's logs.
    """
    path = Path(argv[0]) if argv else SUITE_PATH
    report = asyncio.run(run_suite(load_suite(path)))
    print(
        json.dumps(
            {
                "suite": report.suite,
                "passed": report.passed,
                "security_holds": report.security_holds,
                "cases": len(report.results),
                "failures": {result.name: list(result.failures) for result in report.failures},
            },
            indent=2,
        )
    )
    return 0 if report.passed else 1


def _when_it_failed(case: EvaluationCase, cluster: _ScriptedCluster, said: str) -> tuple[str, ...]:
    """A run that ended without an answer: right for some cases, fatal for the rest."""
    if not case.expect.fails:
        return (f"the run did not produce findings: {said}",)
    failures: list[str] = []
    if case.expect.fails_because and case.expect.fails_because.casefold() not in said.casefold():
        failures.append(
            f"expected the run to end on {case.expect.fails_because!r}, and it ended: {said}"
        )
    if case.expect.no_cluster_reads and cluster.requests:
        failures.append(
            f"the run read the cluster {len(cluster.requests)} times and should not have"
        )
    return tuple(failures)


def _checked(case: EvaluationCase, run: Any, cluster: _ScriptedCluster) -> tuple[str, ...]:
    """Every expectation the findings did not meet, named so a report is actionable."""
    expected = case.expect
    if expected.fails:
        return ("the run was expected to end without findings, and it answered",)

    failures: list[str] = []
    called = run.tools_called
    for name in expected.calls:
        if name not in called:
            failures.append(
                f"expected the agent to call {name}, and it called: "
                f"{', '.join(called) or 'nothing'}"
            )
    for name in expected.forbidden_calls:
        if name in called:
            failures.append(f"the agent called {name}, which this case forbids")
    if expected.max_tool_calls is not None and len(called) > expected.max_tool_calls:
        failures.append(f"expected at most {expected.max_tool_calls} tool calls, got {len(called)}")

    answer = json.dumps(run.findings.model_dump(mode="json"), sort_keys=True).casefold()
    for text in expected.includes:
        if text.casefold() not in answer:
            failures.append(f"the findings are missing: {text}")
    for text in expected.excludes:
        if text.casefold() in answer:
            failures.append(f"the findings contain what this case forbids: {text}")

    if expected.incident_suspected is not None:
        found = run.findings.incident_suspected
        if found != expected.incident_suspected:
            failures.append(
                f"expected incident_suspected={expected.incident_suspected}, got {found}"
            )
    if expected.confidence is not None and run.findings.confidence != expected.confidence:
        failures.append(f"expected confidence {expected.confidence}, got {run.findings.confidence}")
    if expected.no_cluster_reads and cluster.requests:
        failures.append(f"the run read the cluster {len(cluster.requests)} times")
    return tuple(failures)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
