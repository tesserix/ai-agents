import httpx
import pytest
from pydantic import ValidationError

from sre_agent.cluster import cluster_client
from sre_agent.config import Settings
from sre_agent.definitions import INVESTIGATOR


def settings(**overrides) -> Settings:
    return Settings(api_key="service-secret", gateway_api_key="gateway-secret", **overrides)


def test_the_defaults_point_at_the_in_cluster_api_server_and_the_sre_model() -> None:
    loaded = settings()

    assert loaded.cluster_url == "https://kubernetes.default.svc"
    assert loaded.cluster_token_path.endswith("/serviceaccount/token")
    assert loaded.gateway_base_url == (
        "http://ai-gateway.agentgateway-system.svc.cluster.local:8080/openai/v1"
    )
    assert loaded.gateway_model == INVESTIGATOR.agent.model == "gpt-5.4"
    assert loaded.namespaces == ()


def test_neither_credential_reaches_a_log_or_a_traceback() -> None:
    loaded = settings()

    assert "service-secret" not in repr(loaded)
    assert "gateway-secret" not in repr(loaded)


def test_a_misspelled_environment_variable_fails_at_start_up_rather_than_silently() -> None:
    with pytest.raises(ValidationError):
        settings(cluster_ur1="https://elsewhere")


async def test_the_cluster_client_reloads_a_rotated_service_account_token(tmp_path) -> None:
    token = tmp_path / "token"
    token.write_text("sa-token-value")
    authorizations: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers["Authorization"])
        return httpx.Response(200, request=request)

    client = cluster_client(
        "https://kubernetes.default.svc",
        token_path=token,
        ca_path=None,
        timeout=20.0,
        transport=httpx.MockTransport(record),
    )
    try:
        await client.get("/version")
        token.write_text("rotated-sa-token")
        await client.get("/version")
    finally:
        await client.aclose()

    assert authorizations == ["Bearer sa-token-value", "Bearer rotated-sa-token"]
    assert client.base_url == httpx.URL("https://kubernetes.default.svc")


def test_a_cluster_client_without_a_mounted_token_is_still_usable_for_local_reads(tmp_path) -> None:
    client = cluster_client(
        "https://kubernetes.test", token_path=tmp_path / "absent", ca_path=None, timeout=20.0
    )

    assert "Authorization" not in client.headers
