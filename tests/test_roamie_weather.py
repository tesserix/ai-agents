from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from roamie_agents.contracts import Location, RecommendationRequest, Specialist
from roamie_agents.weather import GoogleWeatherSource

NOW = datetime(2026, 9, 26, 1, tzinfo=UTC)


def request(**changes):
    return RecommendationRequest(
        prompt="Plan outdoors",
        origin=Location(latitude=-37.8, longitude=145),
        start_date="2026-09-26",
        end_date="2026-09-28",
        **changes,
    )


async def test_forecasts_preserve_provider_dates_units_and_missing_coverage():
    def reply(req):
        assert req.url.host == "weather.googleapis.com"
        assert req.headers["X-Goog-Api-Key"] == "test-key"
        assert "key" not in req.url.params
        assert req.url.params["unitsSystem"] == "METRIC"
        return httpx.Response(
            200,
            json={
                "timeZone": {"id": "Australia/Melbourne"},
                "forecastDays": [
                    {
                        "displayDate": {"year": 2026, "month": 9, "day": 26},
                        "maxTemperature": {"degrees": 35, "unit": "CELSIUS"},
                        "minTemperature": {"degrees": 19, "unit": "CELSIUS"},
                        "daytimeForecast": {
                            "weatherCondition": {"description": {"text": "Rain"}},
                            "precipitation": {"probability": {"percent": 80}},
                            "uvIndex": 8,
                        },
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        source = GoogleWeatherSource(client, SecretStr("test-key"), clock=lambda: NOW)
        result = await source.search(Specialist.WEATHER, request())
    assert result.status == "ok"
    day = result.facts[0]
    assert day.weather.local_date.isoformat() == "2026-09-26"
    assert day.weather.maximum_celsius == 35
    assert day.weather.precipitation_percent == 80
    assert "indoor" in " ".join(day.weather.suggestions).lower()
    assert len(result.facts) == 2
    assert result.facts[-1].weather.coverage == "partial"
    assert "2026-09-27" in result.facts[-1].weather.missing_dates


async def test_far_future_never_fabricates_a_forecast_or_calls_provider():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected request"))
    ) as client:
        result = await GoogleWeatherSource(client, SecretStr("key"), clock=lambda: NOW).search(
            Specialist.WEATHER,
            RecommendationRequest(
                prompt="Trip",
                origin=Location(latitude=1, longitude=2),
                start_date="2027-01-01",
                end_date="2027-01-02",
            ),
        )
    assert result.facts[0].weather.coverage == "unavailable"
    assert result.facts[0].weather.maximum_celsius is None


@pytest.mark.parametrize("status", [403, 429, 500])
async def test_provider_errors_remain_unavailable(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private key error"))
    ) as client:
        result = await GoogleWeatherSource(client, SecretStr("key"), clock=lambda: NOW).search(
            Specialist.WEATHER, request()
        )
    assert result.status == "unavailable"
    assert "private" not in result.model_dump_json()


@pytest.mark.parametrize(
    "body",
    [
        None,
        {
            "forecastDays": [
                {
                    "displayDate": {"year": 2026, "month": 9, "day": 26},
                    "maxTemperature": {"degrees": 90, "unit": "FAHRENHEIT"},
                }
            ]
        },
        {"forecastDays": [{"displayDate": {"year": 2026, "month": 13, "day": 1}}]},
    ],
)
async def test_malformed_weather_never_becomes_advice(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        result = await GoogleWeatherSource(client, SecretStr("key"), clock=lambda: NOW).search(
            Specialist.WEATHER, request()
        )
    assert result.status == "unavailable"


async def test_missing_location_and_missing_key_do_not_call_upstream():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected request"))
    ) as client:
        source = GoogleWeatherSource(client, None, clock=lambda: NOW)
        assert (
            await source.search(Specialist.WEATHER, RecommendationRequest(prompt="Trip"))
        ).status == "unavailable"
        assert (await source.search(Specialist.WEATHER, request())).status == "unavailable"


async def test_weather_with_trip_dates_and_accessibility_retains_coverage():
    from tesserix_adk.core import ModelCapabilities
    from tesserix_adk.runtime import ModelResponse
    from tesserix_adk.testing import ScriptedProvider

    from roamie_agents.runtime import TravelService

    async with httpx.AsyncClient() as client:
        req = RecommendationRequest(
            prompt="Future trip",
            origin=Location(latitude=1, longitude=2),
            start_date="2027-01-01",
            end_date="2027-01-02",
            accessibility_requirements=("step-free",),
        )
        batch = await GoogleWeatherSource(client, None, clock=lambda: NOW).search(
            Specialist.WEATHER, req
        )
    provider = ScriptedProvider(
        ModelResponse(content='{"selected_ids":[]}'),
        capabilities=ModelCapabilities(structured_output=True, context_window_tokens=32768),
    )
    response = await TravelService(provider=provider, clock=lambda: NOW).recommend(
        Specialist.WEATHER, req, facts=batch.facts
    )
    assert response.status == "ok"
    assert response.recommendations[0].weather.coverage == "unavailable"
