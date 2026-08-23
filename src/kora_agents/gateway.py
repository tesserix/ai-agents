"""Solo Agent Gateway provider construction and optimizer signals."""

from collections.abc import Mapping
from typing import Any

import httpx
from tesserix_adk.core import AgentDefinition, ModelCapabilities
from tesserix_adk.models.providers import OpenAICompatibleProvider

from kora_agents.config import Settings
from kora_agents.identity import DELEGATED_IDENTITY_HEADER, current_end_user_token


class GatewayHeadersTransport(httpx.AsyncBaseTransport):
    """Attach non-sensitive signals used by gateway routing and ExtProc."""

    def __init__(
        self,
        next_transport: httpx.AsyncBaseTransport,
        *,
        capability: str,
        context_kind: str,
    ) -> None:
        self._next = next_transport
        self._capability = capability
        self._context_kind = context_kind

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.headers["x-kora-ai-capability"] = self._capability
        request.headers["x-kora-ai-context-kind"] = self._context_kind
        if end_user_token := current_end_user_token():
            request.headers[DELEGATED_IDENTITY_HEADER] = end_user_token
        return await self._next.handle_async_request(request)

    async def aclose(self) -> None:
        await self._next.aclose()


class _SettingsSecrets:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def secret(self, name: str) -> str | None:
        if name != "KORA_AI_GATEWAY_API_KEY":
            return None
        return self._settings.gateway_api_key.get_secret_value()


class GatewayProviderFactory:
    """Build one pooled ADK provider for each optimizer route shape."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._providers: dict[tuple[str, str], OpenAICompatibleProvider] = {}

    def for_definition(self, definition: AgentDefinition[Any]) -> OpenAICompatibleProvider:
        metadata: Mapping[str, str] = definition.agent.metadata
        capability = metadata.get("capability", "text")
        context_kind = metadata.get("context_kind", "conversation")
        key = (capability, context_kind)
        provider = self._providers.get(key)
        if provider is not None:
            return provider
        provider = OpenAICompatibleProvider(
            self._settings.gateway_model,
            base_url=self._settings.gateway_base_url.rstrip("/").removesuffix("/v1"),
            name="solo-agentgateway",
            capabilities=ModelCapabilities(
                structured_output=True,
                context_window_tokens=32_768,
            ),
            api_key_variable="KORA_AI_GATEWAY_API_KEY",
            secrets=_SettingsSecrets(self._settings),
            timeout=self._settings.request_timeout_seconds,
            transport=GatewayHeadersTransport(
                httpx.AsyncHTTPTransport(),
                capability=capability,
                context_kind=context_kind,
            ),
        )
        self._providers[key] = provider
        return provider

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
