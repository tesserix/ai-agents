import httpx
from pydantic import SecretStr

from roamie_agents.manager import Profile
from roamie_agents.runtime import TravelFailure


class ProfileAuthority:
    def __init__(
        self, *, key: SecretStr, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url="http://roamie-api.roamie.svc.cluster.local:8080",
            timeout=5,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
            headers={"Authorization": "Bearer " + key.get_secret_value()},
        )

    async def current_revision(self, profile: Profile) -> str:
        try:
            async with self._http.stream(
                "POST", "/internal/v1/travel/profile/verify", json=profile.model_dump(mode="json")
            ) as response:
                if response.status_code == 409:
                    raise TravelFailure("profile_changed")
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > 2048:
                        raise TravelFailure("profile_authority_unavailable")
                    data.extend(chunk)
            import json

            body = json.loads(data)
            if not isinstance(body, dict) or not isinstance(body.get("revision"), str):
                raise TravelFailure("profile_authority_unavailable")
            return str(body["revision"])
        except (httpx.HTTPError, ValueError) as error:
            raise TravelFailure("profile_authority_unavailable") from error

    async def aclose(self) -> None:
        await self._http.aclose()
