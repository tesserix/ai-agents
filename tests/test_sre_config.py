import httpx
import pytest
from pydantic import ValidationError

from sre_agent.cluster import cluster_client
from sre_agent.config import Settings


def settings(**overrides) -> Settings:
    return Settings(api_key="service-secret", gateway_api_key="gateway-secret", **overrides)


def test_the_defaults_point_at_the_in_cluster_api_server_and_the_sre_model() -> None:
    loaded = settings()

    assert loaded.cluster_url == "https://kubernetes.default.svc"
    assert loaded.cluster_token_path.endswith("/serviceaccount/token")
    assert loaded.gateway_model == "sre-auto"
    assert loaded.namespaces == ()


def test_neither_credential_reaches_a_log_or_a_traceback() -> None:
    loaded = settings()

    assert "service-secret" not in repr(loaded)
    assert "gateway-secret" not in repr(loaded)


def test_a_misspelled_environment_variable_fails_at_start_up_rather_than_silently() -> None:
    with pytest.raises(ValidationError):
        settings(cluster_ur1="https://elsewhere")


def test_the_cluster_client_carries_the_service_account_token_it_was_pointed_at(tmp_path) -> None:
    token = tmp_path / "token"
    token.write_text("sa-token-value")

    client = cluster_client(
        "https://kubernetes.default.svc", token_path=token, ca_path=None, timeout=20.0
    )

    assert client.headers["Authorization"] == "Bearer sa-token-value"
    assert client.base_url == httpx.URL("https://kubernetes.default.svc")


def test_a_cluster_client_without_a_mounted_token_is_still_usable_for_local_reads(tmp_path) -> None:
    client = cluster_client(
        "https://kubernetes.test", token_path=tmp_path / "absent", ca_path=None, timeout=20.0
    )

    assert "Authorization" not in client.headers
