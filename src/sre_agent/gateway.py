"""The model behind an investigation: one provider, pointed at the Solo Agent Gateway.

The gateway routes on headers rather than on the model name, so the definition's metadata
travels with every request. Nothing here decides which model answers — that is the
gateway's job, and keeping it there is what lets the model change without a release.
"""

from __future__ import annotations

import httpx
from tesserix_adk.core import ModelCapabilities
from tesserix_adk.models.providers import OpenAICompatibleProvider

from sre_agent.config import Settings
from sre_agent.definitions import INVESTIGATOR

GATEWAY_API_KEY_VARIABLE = "SRE_AGENT_GATEWAY_API_KEY"

_CAPABILITIES = ModelCapabilities(
    structured_output=True,
    tool_calling=True,
    context_window_tokens=128_000,
)


class _RoutingSignals(httpx.AsyncBaseTransport):
    """Attach the non-sensitive signals the gateway's ExtProc routes on."""

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
        request.headers["x-tesserix-capability"] = self._capability
        request.headers["x-tesserix-context-kind"] = self._context_kind
        return await self._next.handle_async_request(request)

    async def aclose(self) -> None:
        await self._next.aclose()


class _SettingsSecrets:
    """Hands the provider one named key, and nothing else the process holds."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def secret(self, name: str) -> str | None:
        if name != GATEWAY_API_KEY_VARIABLE:
            return None
        return self._settings.gateway_api_key.get_secret_value()


def gateway_provider(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> OpenAICompatibleProvider:
    """The provider the investigator runs on, built once for the process."""
    metadata = INVESTIGATOR.agent.metadata
    return OpenAICompatibleProvider(
        settings.gateway_model,
        base_url=settings.gateway_base_url.rstrip("/").removesuffix("/v1"),
        name="solo-agentgateway",
        capabilities=_CAPABILITIES,
        api_key_variable=GATEWAY_API_KEY_VARIABLE,
        secrets=_SettingsSecrets(settings),
        timeout=settings.request_timeout_seconds,
        transport=_RoutingSignals(
            transport if transport is not None else httpx.AsyncHTTPTransport(),
            capability=metadata.get("capability", "json"),
            context_kind=metadata.get("context_kind", "structured"),
        ),
    )
