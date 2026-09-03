"""Environment-owned tracing configuration, one prefix for every service."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelemetrySettings(BaseSettings):
    """Where AI traces go and how they are labelled. Disabled unless an endpoint is set."""

    model_config = SettingsConfigDict(env_prefix="AGENT_TELEMETRY_", frozen=True, extra="forbid")

    endpoint: str = ""
    product: str = Field(min_length=1, max_length=40)
    service_name: str = Field(min_length=1, max_length=80)
    environment: str = Field(default="prod", min_length=1, max_length=40)
    release: str = Field(default="", max_length=80)
    # Optional Basic-auth pair for sending straight to Langfuse instead of the collector.
    public_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    queue_size: int = Field(default=4096, ge=64, le=65536)
    flush_interval_seconds: float = Field(default=2.0, gt=0, le=30)

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint)

    def __repr__(self) -> str:
        return (
            f"TelemetrySettings(endpoint={self.endpoint!r}, product={self.product!r}, "
            f"service_name={self.service_name!r}, environment={self.environment!r})"
        )
