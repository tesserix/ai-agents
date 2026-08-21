"""Validated service configuration."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-owned configuration for the agent service."""

    model_config = SettingsConfigDict(
        env_prefix="KORA_AGENTS_",
        frozen=True,
        extra="forbid",
    )

    api_key: SecretStr
    gateway_api_key: SecretStr
    gateway_base_url: str = "http://kora-ai.agentgateway-system.svc.cluster.local:8080/v1"
    gateway_model: str = "kora-auto"
    tenant_id: str = "kora"
    max_prompt_chars: int = Field(default=12_000, ge=1, le=50_000)
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=60)

    def __repr__(self) -> str:
        """Keep both service credentials out of logs and tracebacks."""
        return "Settings(api_key=[redacted], gateway_api_key=[redacted])"
