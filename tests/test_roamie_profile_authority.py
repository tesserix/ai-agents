import httpx
import pytest
from pydantic import SecretStr

from roamie_agents.manager import Profile
from roamie_agents.profile_authority import ProfileAuthority
from roamie_agents.runtime import TravelFailure


async def test_profile_authority_checks_the_complete_signed_profile_with_backend():
    profile = Profile(subject="user", trip_id="trip", revision="revision")

    def reply(request):
        assert request.url.path == "/internal/v1/travel/profile/verify"
        assert request.headers["authorization"] == "Bearer " + "a" * 32
        assert b'"subject":"user"' in request.content
        return httpx.Response(200, json={"revision": "revision"})

    authority = ProfileAuthority(key=SecretStr("a" * 32), transport=httpx.MockTransport(reply))
    try:
        assert await authority.current_revision(profile) == "revision"
    finally:
        await authority.aclose()


@pytest.mark.parametrize("status", [401, 409, 503])
async def test_profile_authority_denies_unavailable_or_changed_profiles(status):
    authority = ProfileAuthority(
        key=SecretStr("a" * 32), transport=httpx.MockTransport(lambda _: httpx.Response(status))
    )
    try:
        with pytest.raises(TravelFailure):
            await authority.current_revision(
                Profile(subject="user", trip_id="trip", revision="old")
            )
    finally:
        await authority.aclose()
