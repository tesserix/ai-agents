import json

import pytest
from pydantic import SecretStr

from roamie_agents.contracts import RecommendationRequest, Specialist, TravelResponse
from roamie_agents.delegation import DelegationError, Delegations, WorkerPayload


def exchange():
    signer = Delegations(key=SecretStr("d" * 32), clock=lambda: 100)
    request = signer.request(
        manager_id="trip-manager-" + "a" * 64,
        revision="r1",
        specialist=Specialist.FOOD,
        payload=WorkerPayload(request=RecommendationRequest(prompt="Dinner"), facts=[]),
    )
    return signer, request


def test_worker_identity_is_narrowed_to_signed_manager_and_specialist():
    signer, request = exchange()
    verified, identity = signer.verify_request(request.model_dump_json(), Specialist.FOOD)
    assert verified.context.profile_revision == "r1"
    assert identity.agent == "roamie-food"
    assert identity.principal.subject == "trip-manager-" + "a" * 64
    assert identity.principal.tenant == "roamie"
    assert identity.principal.scopes == frozenset({"roamie.food"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("manager_id", "trip-manager-" + "b" * 64),
        ("profile_revision", "r2"),
        ("specialist", "shopping"),
    ],
)
def test_tampered_delegation_is_rejected(field, value):
    signer, request = exchange()
    body = json.loads(request.model_dump_json())
    body["context"][field] = value
    with pytest.raises(DelegationError):
        signer.verify_request(json.dumps(body), Specialist.FOOD)


def test_wrong_specialist_and_expired_delegation_are_rejected():
    signer, request = exchange()
    with pytest.raises(DelegationError):
        signer.verify_request(request.model_dump_json(), Specialist.SHOPPING)
    expired = Delegations(key=SecretStr("d" * 32), clock=lambda: 220)
    with pytest.raises(DelegationError):
        expired.verify_request(request.model_dump_json(), Specialist.FOOD)


def test_reply_is_bound_to_exact_request_and_cannot_be_substituted():
    signer, request = exchange()
    output = TravelResponse(status="no_matches", specialist=Specialist.FOOD)
    reply = signer.response(request, output)
    assert signer.verify_response(reply.model_dump_json(), request) == output
    _, another_request = exchange()
    with pytest.raises(DelegationError):
        signer.verify_response(reply.model_dump_json(), another_request)
    body = json.loads(reply.model_dump_json())
    body["response"]["specialist"] = "shopping"
    with pytest.raises(DelegationError):
        signer.verify_response(json.dumps(body), request)


def test_worker_lifecycle_and_unsigned_call_fail_before_model_execution():
    from fastapi.testclient import TestClient

    from roamie_agents.api import create_app
    from roamie_agents.config import Settings

    class UnreachableService:
        async def recommend(self, *args, **kwargs):
            pytest.fail("unbound request reached the specialist")

    closed = []

    async def close():
        closed.append(True)

    app = create_app(
        settings=Settings(api_key="a" * 32, delegation_key="d" * 32, mcp_schema_digest="1" * 64),
        service=UnreachableService(),
        on_close=close,
    )
    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
        response = client.post(
            "/a2a/v1/roamie-food",
            headers={"Authorization": "Bearer " + "a" * 32},
            json={
                "jsonrpc": "2.0",
                "id": "test",
                "method": "message/send",
                "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "{}"}]}},
            },
        )
        assert response.status_code == 401
    assert closed == [True]


def test_missing_workload_identity_fails_at_startup():
    from roamie_agents.config import Settings
    from roamie_agents.connections import workload_tokens

    with pytest.raises(ValueError, match="missing workload identity"):
        workload_tokens(
            Settings(api_key="a" * 32, delegation_key="d" * 32, mcp_schema_digest="1" * 64),
            "roamie-food",
        )


@pytest.mark.parametrize("reason", ["invalid_planning_stop", "secret provider payload"])
def test_worker_failure_logs_only_safe_reason_with_response_correlation(reason):
    from fastapi.testclient import TestClient
    from structlog.testing import capture_logs

    from roamie_agents.api import create_app
    from roamie_agents.config import Settings
    from roamie_agents.runtime import TravelFailure

    class FailedService:
        async def recommend(self, *args, **kwargs):
            raise TravelFailure(reason)

    signed = Delegations(key=SecretStr("d" * 32)).request(
        manager_id="trip-manager-" + "a" * 64,
        revision="private-revision",
        specialist=Specialist.FOOD,
        payload=WorkerPayload(request=RecommendationRequest(prompt="private prompt"), facts=[]),
    )
    app = create_app(
        settings=Settings(api_key="a" * 32, delegation_key="d" * 32, mcp_schema_digest="1" * 64),
        service=FailedService(),
    )
    with TestClient(app) as client, capture_logs() as logs:
        response = client.post(
            "/a2a/v1/roamie-food",
            headers={"Authorization": "Bearer " + "a" * 32},
            json={
                "jsonrpc": "2.0",
                "id": "private-client-id",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": signed.model_dump_json()}],
                    }
                },
            },
        )
    assert response.status_code == 502
    event = next(item for item in logs if item["event"] == "roamie_request_failed")
    assert event["request_id"] == response.json()["request_id"]
    assert event["component"] == "worker"
    assert event["reason"] == (reason if reason == "invalid_planning_stop" else "unclassified")
    assert "private" not in json.dumps(logs)
    assert "secret" not in json.dumps(logs)
    assert reason not in response.text
