"""The orchestrator service: config, worker client, supervision and the pipeline core."""

import json

import httpx
import pytest
from pydantic import SecretStr
from tesserix_adk.core import ModelCapabilities
from tesserix_adk.runtime import ModelResponse
from tesserix_adk.testing import ScriptedProvider

from orchestrator_agent.config import Settings, WorkerEndpoint
from orchestrator_agent.definitions import ORCHESTRATOR, SUPERVISOR, Verdict
from orchestrator_agent.orchestration import (
    OrchestrationTask,
    OrchestratorService,
    UnsupportedTaskError,
)
from orchestrator_agent.supervision import (
    SupervisionFailedError,
    SupervisionRun,
    SupervisorService,
)
from orchestrator_agent.workers import A2AWorkerClient, WorkerCallError


def provider(*responses: ModelResponse) -> ScriptedProvider:
    return ScriptedProvider(
        *responses,
        name="gateway",
        capabilities=ModelCapabilities(structured_output=True, context_window_tokens=32_768),
    )


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "api_key": SecretStr("edge-key"),
        "worker_api_key": SecretStr("worker-key"),
        "gateway_api_key": SecretStr("gateway-key"),
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


APPROVE = Verdict(decision="approve", confidence=0.9, summary="Grounded and on task.")
REJECT = Verdict(
    decision="reject", confidence=0.8, summary="Invents facts.", issues=["invented salmon"]
)


class FakeSupervisor:
    """Scripted verdicts, recording what was judged."""

    def __init__(self, *verdicts: Verdict, fail: bool = False) -> None:
        self._verdicts = list(verdicts)
        self._fail = fail
        self.judged: list[tuple[str, str]] = []

    async def supervise(
        self, *, task: str, answer: str, context: str = "", tenant: str = "tesserix"
    ) -> SupervisionRun:
        if self._fail:
            raise SupervisionFailedError("failed")
        self.judged.append((task, answer))
        verdict = self._verdicts.pop(0) if self._verdicts else APPROVE
        return SupervisionRun(run_id="sup_1", verdict=verdict)


class FakeClient:
    """Scripted worker replies keyed by worker name, recording every prompt."""

    def __init__(self, replies: dict[str, list[str]], errors: dict[str, str] | None = None):
        self._replies = {name: list(texts) for name, texts in replies.items()}
        self._errors = errors or {}
        self.prompts: list[tuple[str, str]] = []

    async def send(self, worker: WorkerEndpoint, prompt: str):
        from orchestrator_agent.workers import WorkerReply

        self.prompts.append((worker.name, prompt))
        if worker.name in self._errors:
            raise WorkerCallError(worker.name, self._errors[worker.name])
        texts = self._replies.get(worker.name, [])
        text = texts.pop(0) if texts else ""
        return WorkerReply(worker=worker.name, run_id=f"run_{worker.name}", text=text)

    async def aclose(self) -> None:  # pragma: no cover - protocol completeness
        return None


def roster(*names: str) -> dict[str, WorkerEndpoint]:
    return {name: WorkerEndpoint(name=name, url=f"http://gateway/a2a/v1/{name}") for name in names}


def service(
    client: FakeClient,
    supervisor: FakeSupervisor | None = None,
    workers: dict[str, WorkerEndpoint] | None = None,
    **kwargs: object,
) -> OrchestratorService:
    return OrchestratorService(
        workers=workers if workers is not None else roster("planner", "reviewer"),
        client=client,  # type: ignore[arg-type]
        supervisor=supervisor if supervisor is not None else FakeSupervisor(),
        **kwargs,  # type: ignore[arg-type]
    )


def test_settings_parse_worker_roster_from_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", "a")
    monkeypatch.setenv("ORCHESTRATOR_WORKER_API_KEY", "b")
    monkeypatch.setenv("ORCHESTRATOR_GATEWAY_API_KEY", "c")
    monkeypatch.setenv(
        "ORCHESTRATOR_WORKERS",
        '[{"name": "coach", "url": "http://gateway/a2a/v1/coach", "probe": "Say OK."}]',
    )

    loaded = Settings()

    assert loaded.workers[0].name == "coach"
    assert loaded.workers[0].probe == "Say OK."
    assert "redacted" in repr(loaded)


def test_task_contract_refuses_free_text_and_unknown_shapes() -> None:
    with pytest.raises(UnsupportedTaskError):
        OrchestrationTask.parse("plan my week")
    with pytest.raises(UnsupportedTaskError):
        OrchestrationTask.parse('["not", "an", "object"]')
    with pytest.raises(UnsupportedTaskError):
        OrchestrationTask.parse('{"task": "explode"}')


async def test_status_probes_every_configured_worker() -> None:
    client = FakeClient({"planner": ["ready"], "reviewer": ["ready"]})

    report = await service(client).run('{"task": "status"}', tenant="tesserix")

    assert report.ok
    assert [step.agent for step in report.steps] == ["planner", "reviewer"]
    assert json.loads(report.output) == {"planner": "ok", "reviewer": "ok"}


async def test_status_reports_a_failing_worker_without_hiding_the_rest() -> None:
    client = FakeClient({"reviewer": ["ready"]}, errors={"planner": "http_502"})

    report = await service(client).run('{"task": "status"}', tenant="tesserix")

    assert not report.ok
    states = {step.agent: step.state for step in report.steps}
    assert states == {"planner": "failed", "reviewer": "completed"}


async def test_delegate_supervises_the_answer_by_default() -> None:
    client = FakeClient({"planner": ["a fine draft"]})
    supervisor = FakeSupervisor(APPROVE)

    report = await service(client, supervisor).run(
        '{"task": "delegate", "agent": "planner", "prompt": "draft it"}', tenant="tesserix"
    )

    assert report.ok
    assert report.output == "a fine draft"
    assert report.steps[0].verdict == APPROVE
    assert supervisor.judged == [("draft it", "a fine draft")]


async def test_rejected_answer_halts_the_run_and_is_discarded() -> None:
    client = FakeClient({"planner": ["salmon nonsense"], "reviewer": ["never called"]})
    supervisor = FakeSupervisor(REJECT)
    task = json.dumps(
        {
            "task": "pipeline",
            "steps": [
                {"agent": "planner", "prompt": "draft"},
                {"agent": "reviewer", "prompt": "review"},
            ],
        }
    )

    report = await service(client, supervisor).run(task, tenant="tesserix")

    assert not report.ok
    assert report.steps[0].state == "rejected"
    assert report.steps[0].note == ""
    assert report.output == ""
    assert [name for name, _ in client.prompts] == ["planner"]


async def test_pipeline_carries_answers_forward_only_inside_the_untrusted_envelope() -> None:
    client = FakeClient({"planner": ["THE DRAFT"], "reviewer": ["approved plan"]})
    task = json.dumps(
        {
            "task": "pipeline",
            "context": "vegetarian",
            "steps": [
                {"agent": "planner", "prompt": "draft"},
                {"agent": "reviewer", "prompt": "review the draft"},
            ],
        }
    )

    report = await service(client).run(task, tenant="tesserix")

    assert report.ok
    assert report.output == "approved plan"
    reviewer_prompt = client.prompts[1][1]
    assert reviewer_prompt.startswith("CONTEXT:\nvegetarian")
    assert '<untrusted-data source="delegated_agent">' in reviewer_prompt
    assert "THE DRAFT" in reviewer_prompt
    planner_prompt = client.prompts[0][1]
    assert "untrusted-data" not in planner_prompt


async def test_unknown_worker_is_refused_before_anything_runs() -> None:
    client = FakeClient({})

    with pytest.raises(UnsupportedTaskError, match="not-registered"):
        await service(client).run(
            '{"task": "delegate", "agent": "not-registered", "prompt": "hi"}',
            tenant="tesserix",
        )
    assert client.prompts == []


async def test_step_ceiling_refuses_the_step_that_exceeds_it() -> None:
    client = FakeClient({"planner": ["one", "two", "three"]})
    steps = [{"agent": "planner", "prompt": f"step {index}"} for index in range(3)]

    report = await service(client, max_steps=2).run(
        json.dumps({"task": "pipeline", "steps": steps}), tenant="tesserix"
    )

    assert not report.ok
    assert report.steps[2].state == "refused"
    assert report.steps[2].note == "step_ceiling"


async def test_deadline_refuses_steps_after_time_is_spent() -> None:
    client = FakeClient({"planner": ["answer"]})

    report = await service(client, run_timeout_seconds=0.000001).run(
        '{"task": "delegate", "agent": "planner", "prompt": "hi"}', tenant="tesserix"
    )

    assert not report.ok
    assert report.steps[0].note == "deadline"


async def test_supervision_failure_fails_the_step_rather_than_passing_it() -> None:
    client = FakeClient({"planner": ["draft"]})

    report = await service(client, FakeSupervisor(fail=True)).run(
        '{"task": "delegate", "agent": "planner", "prompt": "draft"}', tenant="tesserix"
    )

    assert not report.ok
    assert report.steps[0].state == "supervision_failed"


def test_cards_publish_both_global_agents_and_the_roster() -> None:
    cards = service(FakeClient({})).cards()

    assert [card["name"] for card in cards] == ["orchestrator", "supervisor"]
    assert all(card["workers"] == ["planner", "reviewer"] for card in cards)


async def test_supervisor_service_returns_a_validated_verdict() -> None:
    scripted = provider(
        ModelResponse(content=APPROVE.model_dump_json()),
    )

    run = await SupervisorService(provider=scripted).supervise(
        task="Name a protein source.",
        answer="Lentils, per the context.",
        context="Lentils have 9g protein per 100g.",
        tenant="tesserix",
    )

    assert run.verdict.decision == "approve"
    assert run.run_id


async def test_supervisor_service_raises_when_no_verdict_is_produced() -> None:
    scripted = provider(ModelResponse(content="not a verdict at all"))

    with pytest.raises(SupervisionFailedError):
        await SupervisorService(provider=scripted).supervise(
            task="Judge this.", answer="whatever", tenant="tesserix"
        )


def _a2a_result(text: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "id": "run_9",
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"kind": "text", "text": text}]}],
        },
    }


async def test_worker_client_speaks_a2a_and_sends_the_bearer_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_a2a_result("hello from worker"))

    client = A2AWorkerClient(
        api_key=SecretStr("worker-key"),
        timeout=5.0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    reply = await client.send(WorkerEndpoint(name="coach", url="http://gateway/a2a/v1/coach"), "hi")
    await client.aclose()

    assert reply.text == "hello from worker"
    assert reply.run_id == "run_9"
    assert seen["auth"] == "Bearer worker-key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["method"] == "message/send"
    assert body["params"]["message"]["parts"][0]["text"] == "hi"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(503, text="down"), "http_503"),
        (httpx.Response(200, text="not json"), "invalid_json"),
        (httpx.Response(200, json={"jsonrpc": "2.0", "id": "1"}), "missing_result"),
        (
            httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "error": {"code": -32600}}),
            "jsonrpc_error_-32600",
        ),
    ],
)
async def test_worker_client_turns_protocol_failures_into_typed_errors(
    response: httpx.Response, reason: str
) -> None:
    client = A2AWorkerClient(
        api_key=SecretStr("worker-key"),
        timeout=5.0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: response)),
    )

    with pytest.raises(WorkerCallError) as failure:
        await client.send(WorkerEndpoint(name="coach", url="http://gateway/a2a"), "hi")
    await client.aclose()

    assert failure.value.reason == reason


def test_global_definitions_belong_to_no_product() -> None:
    for definition in (SUPERVISOR, ORCHESTRATOR):
        assert definition.owner.team == "tesserix"
        assert definition.agent.guardrails == ("pii", "injection")
    assert SUPERVISOR.agent.output_type is Verdict
    assert ORCHESTRATOR.agent.free_text
