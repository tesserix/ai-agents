import asyncio
import time
from collections.abc import Callable

import httpx
from pydantic import Field, SecretStr
from tesserix_adk.core import AgentIdentity, Principal
from tesserix_adk.runtime.credentials import RunCredentials
from tesserix_adk.tools import Credential, CredentialBroker, CredentialRequest

from roamie_agents.base import Contract

ZITADEL_ENDPOINT = "https://auth.tesserix.app/oauth/v2/token"
PROJECT_ID = "387190457387450503"
SCOPE = (
    f"openid profile urn:zitadel:iam:org:project:id:{PROJECT_ID}:aud "
    "urn:zitadel:iam:org:projects:roles"
)


class OAuthClient(Contract):
    subject: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret: SecretStr = Field(min_length=16)


class TokenClock:
    def __init__(self, now: Callable[[], float]) -> None:
        self._now = now

    def now(self) -> float:
        return self._now()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class WorkloadTokens:
    def __init__(
        self,
        *,
        agent: str,
        client: OAuthClient,
        clock: Callable[[], float] = time.time,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.identity = AgentIdentity.resolve(
            agent=agent,
            declared=("gateway.invoke",),
            principal=Principal(
                subject=client.subject, tenant="roamie", scopes=frozenset({"gateway.invoke"})
            ),
            now=clock(),
        )
        self._client = client
        self._clock = clock
        self._http = httpx.AsyncClient(
            transport=transport, timeout=10, follow_redirects=False, trust_env=False
        )
        token_clock = TokenClock(clock)
        self._credentials = RunCredentials(
            CredentialBroker(self, clock=token_clock), identity=self.identity, clock=token_clock
        )

    async def issue(self, request: CredentialRequest) -> Credential:
        if (
            request.attribution.subject != self.identity.principal.subject
            or request.audience != "agentgateway"
            or request.scopes != frozenset({"gateway.invoke"})
        ):
            raise ValueError("workload credential audience mismatch")
        response = await self._http.post(
            ZITADEL_ENDPOINT,
            auth=httpx.BasicAuth(
                self._client.client_id, self._client.client_secret.get_secret_value()
            ),
            data={"grant_type": "client_credentials", "scope": SCOPE},
        )
        response.raise_for_status()
        body = response.json()
        token, lifetime = body.get("access_token"), body.get("expires_in")
        if (
            body.get("token_type", "").lower() != "bearer"
            or not isinstance(token, str)
            or not token
            or isinstance(lifetime, bool)
            or not isinstance(lifetime, int)
            or not 30 < lifetime <= 86400
        ):
            raise ValueError("invalid workload credential response")
        return Credential(
            token=SecretStr(token),
            expires_at=self._clock() + lifetime,
            audience=request.audience,
            scopes=request.scopes,
            attribution=request.attribution,
        )

    async def token(self) -> str:
        value = await self._credentials.for_call(
            audience="agentgateway", needs=("gateway.invoke",), run_id=self.identity.agent
        )
        return value.token.get_secret_value()

    async def aclose(self) -> None:
        await self._http.aclose()


class GatewayTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        tokens: WorkloadTokens,
        *,
        origin: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._tokens = tokens
        self._origin = httpx.URL(origin)
        self._transport = transport or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (request.url.scheme, request.url.host, request.url.port) != (
            self._origin.scheme,
            self._origin.host,
            self._origin.port,
        ):
            raise ValueError("gateway credential destination mismatch")
        request.headers["Authorization"] = "Bearer " + await self._tokens.token()
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._transport.aclose()
