from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROAMIE_AGENTS_", frozen=True, extra="forbid")

    api_key: SecretStr = Field(min_length=32)
    gateway_api_key: SecretStr = Field(min_length=32)
    gateway_base_url: str = "http://ai-gateway.agentgateway-system.svc.cluster.local:8080/roamie/v1"
    gateway_model: str = "roamie-auto"
    mcp_gateway_origin: str = "https://mcp.tesserix.app"
    mcp_gateway_path: str = "/mcp/roamie/roamie-travel-mcp"
    mcp_tool: str = "travel_search"
    mcp_schema_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_in_flight: int = Field(default=8, ge=1, le=32)

    @model_validator(mode="after")
    def gateway_only(self) -> Self:
        origin = urlsplit(self.mcp_gateway_origin)
        if origin.scheme != "https" or not origin.hostname or origin.path not in ("", "/"):
            raise ValueError("MCP gateway must be an HTTPS origin")
        if origin.username or origin.password or origin.query or origin.fragment:
            raise ValueError("invalid MCP gateway origin")
        if not self.mcp_gateway_path.startswith("/mcp/roamie/") or any(
            value in self.mcp_gateway_path for value in ("..", "?", "#", "%")
        ):
            raise ValueError("MCP route must belong to Roamie")
        return self
