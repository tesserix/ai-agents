import json

from fastapi.testclient import TestClient

from sre_agent.api import create_app
from sre_agent.config import Settings
from sre_agent.definitions import Investigation
from sre_agent.runtime import InvestigationFailedError, InvestigationRun

FINDINGS = Investigation.model_validate(
    {
        "summary": "marketplace-order-service is crash looping on a database timeout.",
        "incident_suspected": True,
        "symptoms": ["The pod has restarted 7 times."],
        "evidence": [
            {
                "tool": "get_pod_logs",
                "subject": "marketplace/marketplace-order-service-7c9c6bd4f-lm8vt",
                "observation": "panic: database timeout after 30s",
            }
        ],
        "hypothesis": "The service cannot reach its database.",
        "confidence": "medium",
        "recommended_actions": [
            {
                "action": "Check the CNPG cluster in the marketplace namespace.",
                "reason": "Every replica fails at the same call.",
                "urgency": "now",
            }
        ],
        "affected_apps": ["marketplace-order-service"],
    }
)


class FakeInvestigator:
    def __init__(self, error: Exception | None = None) -> None:
        self.prompts: list[tuple[str, str]] = []
        self.error = error

    async def investigate(self, prompt: str, *, tenant: str) -> InvestigationRun:
        self.prompts.append((prompt, tenant))
        if self.error is not None:
            raise self.error
        return InvestigationRun(
            run_id="run-1",
            findings=FINDINGS,
            calls=(),
            input_tokens=120,
            output_tokens=40,
        )


def client(error: Exception | None = None) -> tuple[TestClient, FakeInvestigator]:
    service = FakeInvestigator(error)
    settings = Settings(api_key="service-secret", gateway_api_key="gateway-secret")
    return TestClient(create_app(settings=settings, service=service)), service


def authorized(app: TestClient, path: str, body: dict[str, object]):
    return app.post(path, json=body, headers={"Authorization": "Bearer service-secret"})


def a2a_body(text: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": "call-1",
        "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]}},
    }


def test_liveness_and_readiness_need_no_credential() -> None:
    app, _ = client()

    assert app.get("/healthz").json() == {"status": "ok"}
    assert app.get("/readyz").json() == {"status": "ready"}


def test_an_investigation_without_a_bearer_token_is_refused_and_never_runs() -> None:
    app, service = client()

    response = app.post("/v1/investigations", json={"prompt": "What is wrong?"})

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert service.prompts == []


def test_an_investigation_returns_the_findings_the_agent_validated() -> None:
    app, service = client()

    response = authorized(app, "/v1/investigations", {"prompt": "Investigate marketplace."})

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-1"
    assert body["agent_name"] == "sre-investigator"
    assert body["findings"]["incident_suspected"] is True
    assert body["findings"]["evidence"][0]["tool"] == "get_pod_logs"
    assert body["usage"] == {"input_tokens": 120, "output_tokens": 40}
    assert service.prompts == [("Investigate marketplace.", "tesserix")]


def test_the_tenant_is_server_owned_rather_than_caller_supplied() -> None:
    app, service = client()

    response = authorized(
        app, "/v1/investigations", {"prompt": "Investigate.", "tenant": "somebody-else"}
    )

    assert response.status_code == 422
    assert service.prompts == []


def test_a_prompt_over_the_configured_limit_is_rejected_before_the_model_is_called() -> None:
    app, service = client()

    response = authorized(app, "/v1/investigations", {"prompt": "x" * 12_001})

    assert response.status_code == 422
    assert service.prompts == []


def test_a_run_that_ended_without_findings_is_a_bad_gateway_without_internals() -> None:
    app, _ = client(InvestigationFailedError("failed", "delete_pod is not on the allowlist"))

    response = authorized(app, "/v1/investigations", {"prompt": "Restart the order service."})

    assert response.status_code == 502
    assert response.json() == {
        "code": "investigation_failed",
        "message": "the investigation did not produce findings",
    }


def test_the_a2a_edge_answers_with_the_findings_as_a_json_artifact() -> None:
    app, service = client()

    response = authorized(app, "/a2a/v1/sre-investigator", a2a_body("Investigate marketplace."))

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == "call-1"
    assert body["result"]["status"]["state"] == "completed"
    artifact = json.loads(body["result"]["artifacts"][0]["parts"][0]["text"])
    assert artifact["summary"] == FINDINGS.summary
    assert service.prompts == [("Investigate marketplace.", "tesserix")]


def test_the_a2a_edge_serves_only_the_agent_this_service_publishes() -> None:
    app, service = client()

    response = authorized(app, "/a2a/v1/nutrition-coach", a2a_body("Plan my lunch."))

    assert response.status_code == 404
    assert response.json()["code"] == "agent_not_found"
    assert service.prompts == []


def test_the_published_card_names_the_tools_and_says_the_agent_only_reads() -> None:
    app, _ = client()

    response = app.get("/v1/agents", headers={"Authorization": "Bearer service-secret"})

    card = response.json()["agents"][0]
    assert card["name"] == "sre-investigator"
    assert card["read_only"] is True
    assert "get_pod_logs" in card["tools"]


def test_the_a2a_edge_requires_a_bearer_token() -> None:
    app, service = client()

    response = app.post("/a2a/v1/sre-investigator", json=a2a_body("Investigate."))

    assert response.status_code == 401
    assert service.prompts == []


def test_every_response_carries_the_request_id_the_service_logged() -> None:
    app, _ = client()

    response = app.get("/healthz")

    assert len(response.headers["X-Request-ID"]) == 32
