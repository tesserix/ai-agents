"""Validated configuration for the investigator service.

Two credentials and one cluster address decide what this process can reach, so all three
are environment-owned and closed: a misspelled variable fails at start-up rather than
leaving the agent pointed somewhere nobody intended.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ACCOUNT = "/var/run/secrets/kubernetes.io/serviceaccount"


class Settings(BaseSettings):
    """Environment-owned configuration for the SRE investigator service."""

    model_config = SettingsConfigDict(
        env_prefix="SRE_AGENT_",
        frozen=True,
        extra="forbid",
    )

    api_key: SecretStr
    gateway_api_key: SecretStr
    gateway_base_url: str = "http://ai-gateway.agentgateway-system.svc.cluster.local:8080/openai/v1"
    gateway_model: str = "gpt-5.4"
    tenant_id: str = "tesserix"
    cluster_url: str = "https://kubernetes.default.svc"
    cluster_token_path: str = f"{SERVICE_ACCOUNT}/token"
    cluster_ca_path: str = f"{SERVICE_ACCOUNT}/ca.crt"
    namespaces: tuple[str, ...] = ()
    """The namespaces the agent may read. Empty defers to what the ClusterRole allows."""

    max_prompt_chars: int = Field(default=12_000, ge=1, le=50_000)
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=90)
    cluster_timeout_seconds: float = Field(default=20.0, gt=0, le=60)

    def __repr__(self) -> str:
        """Keep both service credentials out of logs and tracebacks."""
        return "Settings(api_key=[redacted], gateway_api_key=[redacted])"
