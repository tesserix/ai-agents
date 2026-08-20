from collections.abc import Mapping

from fastapi.testclient import TestClient

from kora_agents.api import create_app
from kora_agents.config import Settings
from kora_agents.execution import ExecutionResult
from kora_agents.runtime import AgentNotFoundError


class FakeAgentService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def run(self, agent_name: str, prompt: str, *, tenant: str) -> ExecutionResult:
        self.calls.append((agent_name, prompt, tenant))
        return ExecutionResult(
            run_id="run-1",
            agent_name=agent_name,
            state="completed",
            output={"answer": "balanced plate"},
            input_tokens=42,
            output_tokens=7,
            cached_tokens=10,
            estimated=False,
        )

    def cards(self) -> tuple[Mapping[str, object], ...]:
        return ({"name": "nutrition-coach", "version": "1.0.0"},)


def client() -> tuple[TestClient, FakeAgentService]:
    service = FakeAgentService()
    settings = Settings(
        api_key="service-secret",
        gateway_api_key="gateway-secret",
    )
    return TestClient(create_app(settings=settings, service=service)), service


def test_run_requires_bearer_authentication() -> None:
    app, service = client()

    response = app.post(
        "/v1/agents/nutrition-coach/runs",
        json={"prompt": "Help me plan lunch"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert service.calls == []


def test_run_uses_server_owned_tenant_and_returns_payload_free_usage() -> None:
    app, service = client()

    response = app.post(
        "/v1/agents/nutrition-coach/runs",
        headers={"Authorization": "Bearer service-secret", "X-Tesserix-Tenant": "other"},
        json={"prompt": "Help me plan lunch"},
    )

    assert response.status_code == 200
    assert service.calls == [("nutrition-coach", "Help me plan lunch", "kora")]
    assert response.json() == {
        "run_id": "run-1",
        "agent_name": "nutrition-coach",
        "state": "completed",
        "output": {"answer": "balanced plate"},
        "usage": {
            "input_tokens": 42,
            "output_tokens": 7,
            "cached_tokens": 10,
            "estimated": False,
        },
    }
    assert "prompt" not in response.text


def test_run_rejects_oversized_prompts_before_execution() -> None:
    app, service = client()

    response = app.post(
        "/v1/agents/meal-planner/runs",
        headers={"Authorization": "Bearer service-secret"},
        json={"prompt": "x" * 12_001},
    )

    assert response.status_code == 422
    assert service.calls == []


def test_cards_and_health_are_available_without_model_traffic() -> None:
    app, service = client()

    assert app.get("/healthz").json() == {"status": "ok"}
    assert app.get("/readyz").json() == {"status": "ready"}
    response = app.get("/v1/agents", headers={"Authorization": "Bearer service-secret"})
    assert response.status_code == 200
    assert response.json() == {"agents": [{"name": "nutrition-coach", "version": "1.0.0"}]}
    assert service.calls == []


def test_unknown_agent_returns_stable_not_found_error() -> None:
    class MissingAgentService(FakeAgentService):
        async def run(self, agent_name: str, prompt: str, *, tenant: str) -> ExecutionResult:
            raise AgentNotFoundError(agent_name)

    service = MissingAgentService()
    settings = Settings(api_key="service-secret", gateway_api_key="gateway-secret")
    app = TestClient(create_app(settings=settings, service=service))

    response = app.post(
        "/v1/agents/missing/runs",
        headers={"Authorization": "Bearer service-secret"},
        json={"prompt": "hello"},
    )

    assert response.status_code == 404
    assert response.json() == {"code": "agent_not_found", "message": "agent not found"}


def test_a2a_message_send_uses_same_guarded_execution_path() -> None:
    app, service = client()

    response = app.post(
        "/a2a/v1/nutrition-coach",
        headers={"Authorization": "Bearer service-secret"},
        json={
            "jsonrpc": "2.0",
            "id": "message-1",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Plan a balanced lunch"}],
                }
            },
        },
    )

    assert response.status_code == 200
    assert service.calls == [("nutrition-coach", "Plan a balanced lunch", "kora")]
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == "message-1"
    assert body["result"]["id"] == "run-1"
    assert body["result"]["status"]["state"] == "completed"
    assert body["result"]["metadata"]["usage"] == {
        "input_tokens": 42,
        "output_tokens": 7,
        "cached_tokens": 10,
        "estimated": False,
    }


def test_configured_prompt_limit_applies_to_a2a_joined_parts() -> None:
    service = FakeAgentService()
    settings = Settings(
        api_key="service-secret",
        gateway_api_key="gateway-secret",
        max_prompt_chars=5,
    )
    app = TestClient(create_app(settings=settings, service=service))

    response = app.post(
        "/a2a/v1/nutrition-coach",
        headers={"Authorization": "Bearer service-secret"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [
                        {"kind": "text", "text": "abc"},
                        {"kind": "text", "text": "def"},
                    ],
                }
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_failed"
    assert service.calls == []


def test_shutdown_callback_runs_with_application_lifespan() -> None:
    service = FakeAgentService()
    settings = Settings(api_key="service-secret", gateway_api_key="gateway-secret")
    stopped = False

    async def shutdown() -> None:
        nonlocal stopped
        stopped = True

    with TestClient(create_app(settings=settings, service=service, shutdown=shutdown)):
        assert not stopped

    assert stopped
