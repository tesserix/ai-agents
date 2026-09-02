"""Validated configuration for the orchestrator service.

The worker roster is configuration, not code: any product points the orchestrator at its
own registered A2A agents without a rebuild. Each entry is a name the orchestration task
may address and the gateway URL that serves it, so the process can reach exactly the
agents its operator listed and nothing else.
"""

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerEndpoint(BaseModel):
    """One delegatable agent: an addressable name, its A2A URL, and a health probe."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=500)
    probe: str = Field(
        default="Reply with a one-sentence confirmation that you are able to answer.",
        min_length=1,
        max_length=500,
    )


class Settings(BaseSettings):
    """Environment-owned configuration for the orchestrator service."""

    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRATOR_",
        frozen=True,
        extra="forbid",
    )

    api_key: SecretStr
    worker_api_key: SecretStr
    gateway_api_key: SecretStr
    gateway_base_url: str = "http://ai-gateway.agentgateway-system.svc.cluster.local:8080/vertex/v1"
    gateway_model: str = "gemini-2.5-flash"
    tenant_id: str = "tesserix"
    workers: tuple[WorkerEndpoint, ...] = ()
    """JSON list of worker endpoints; empty leaves the orchestrator with nothing to run."""

    max_prompt_chars: int = Field(default=12_000, ge=1, le=50_000)
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=90)
    max_steps: int = Field(default=6, ge=1, le=12)
    step_timeout_seconds: float = Field(default=60.0, gt=0, le=120)
    run_timeout_seconds: float = Field(default=240.0, gt=0, le=600)

    temporal_address: str | None = None
    """Host:port of the Temporal frontend; unset disables the durable path entirely."""
    temporal_namespace: str = "default"
    temporal_task_queue: str = "orchestrations"

    def __repr__(self) -> str:
        """Keep both service credentials out of logs and tracebacks."""
        return "Settings(api_key=[redacted], worker_api_key=[redacted])"
