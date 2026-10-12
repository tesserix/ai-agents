import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from roamie_agents.contracts import (
    Evidence,
    Location,
    RecommendationRequest,
    Specialist,
    WeatherDetails,
)
from roamie_agents.evidence import EvidenceBatch
from roamie_agents.open_meteo import OpenMeteoSource
from roamie_agents.weather import GoogleWeatherSource
from roamie_agents.weather_failover import WeatherFailover

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def request(country="AU"):
    return RecommendationRequest(
        prompt="Weather",
        origin=Location(latitude=-37.8, longitude=145),
        destination_country=country,
        start_date="2026-10-12",
        end_date="2026-10-13",
    )


@pytest.mark.parametrize("failure", [429, 503, "timeout", "malformed"])
async def test_self_hosted_failure_uses_google_without_crossing_credentials(failure):
    hosts = []

    def reply(req):
        hosts.append(req.url.host)
        if len(hosts) == 1:
            assert req.url.host == "roamie-weather.roamie.svc.cluster.local"
            assert "x-api-key" not in req.headers
            assert "x-goog-api-key" not in req.headers
            if failure == "timeout":
                raise httpx.ReadTimeout("timeout")
            return httpx.Response(200 if failure == "malformed" else failure, json={})
        assert req.url.host == "weather.googleapis.com"
        assert req.headers["X-Goog-Api-Key"] == "google-test"
        assert "x-api-key" not in req.headers
        return httpx.Response(
            200,
            json={
                "timeZone": {"id": "Australia/Melbourne"},
                "forecastDays": [
                    {
                        "displayDate": {"year": 2026, "month": 10, "day": 12},
                        "maxTemperature": {"degrees": 23, "unit": "CELSIUS"},
                        "minTemperature": {"degrees": 14, "unit": "CELSIUS"},
                        "daytimeForecast": {},
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        source = WeatherFailover(
            OpenMeteoSource(client, None, self_hosted=True, clock=lambda: NOW),
            GoogleWeatherSource(client, SecretStr("google-test"), clock=lambda: NOW),
        )
        result = await source.search(Specialist.WEATHER, request())
    assert len(hosts) == 2
    assert result.status == "ok"
    assert "google.com" in str(result.facts[0].source_url)
    assert result.facts[0].weather.maximum_celsius == 23


async def test_failed_primary_in_japan_returns_honest_google_coverage_guidance():
    calls = []

    def reply(req):
        calls.append(req.url.host)
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        source = WeatherFailover(
            OpenMeteoSource(client, None, self_hosted=True, clock=lambda: NOW),
            GoogleWeatherSource(client, SecretStr("key"), clock=lambda: NOW),
        )
        result = await source.search(Specialist.WEATHER, request("JP"))
    assert calls == ["roamie-weather.roamie.svc.cluster.local"]
    assert result.facts[0].weather.coverage == "unavailable"
    assert result.facts[0].weather.maximum_celsius is None


async def test_successful_primary_does_not_spend_google_quota():
    class Primary:
        async def search(self, specialist, request):
            return EvidenceBatch(
                status="ok",
                facts=[
                    Evidence(
                        id="day",
                        name="Forecast",
                        category=Specialist.WEATHER,
                        source_url="https://open-meteo.com/",
                        observed_at=NOW,
                        weather=WeatherDetails(
                            coverage="forecast", local_date=NOW.date(), maximum_celsius=22
                        ),
                    )
                ],
            )

    class Fallback:
        async def search(self, specialist, request):
            pytest.fail("unexpected Google call")

    result = await WeatherFailover(Primary(), Fallback()).search(Specialist.WEATHER, request())
    assert result.status == "ok"


async def test_total_deadline_cancels_primary_then_runs_fallback():
    cancelled = asyncio.Event()

    class Primary:
        async def search(self, specialist, request):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

    class Fallback:
        async def search(self, specialist, request):
            return EvidenceBatch(status="unavailable")

    result = await WeatherFailover(Primary(), Fallback(), primary_seconds=0).search(
        Specialist.WEATHER, request()
    )
    assert result.status == "unavailable"
    assert cancelled.is_set()


async def test_caller_cancellation_never_starts_fallback():
    class Primary:
        async def search(self, specialist, request):
            raise asyncio.CancelledError()

    class Fallback:
        async def search(self, specialist, request):
            pytest.fail("cancelled caller")

    with pytest.raises(asyncio.CancelledError):
        await WeatherFailover(Primary(), Fallback()).search(Specialist.WEATHER, request())


async def test_empty_primary_forecast_within_google_window_uses_fallback():
    calls = []

    class Primary:
        async def search(self, specialist, request):
            calls.append("primary")
            return EvidenceBatch(status="ok")

    class Fallback:
        async def search(self, specialist, request):
            calls.append("google")
            return EvidenceBatch(status="unavailable")

    await WeatherFailover(Primary(), Fallback(), clock=lambda: NOW).search(
        Specialist.WEATHER, request()
    )
    assert calls == ["primary", "google"]


async def test_slow_fallback_is_bounded():
    class Primary:
        async def search(self, specialist, request):
            return EvidenceBatch(status="unavailable")

    class Fallback:
        async def search(self, specialist, request):
            await asyncio.Future()

    result = await WeatherFailover(Primary(), Fallback(), fallback_seconds=0).search(
        Specialist.WEATHER, request()
    )
    assert result.status == "unavailable"


async def test_cold_self_hosted_forecast_has_time_to_load_without_google(monkeypatch):
    loop = asyncio.get_running_loop()
    real_time = loop.time
    elapsed = 0
    monkeypatch.setattr(loop, "time", lambda: real_time() + elapsed)

    async def reply(req):
        nonlocal elapsed
        assert req.url.host == "roamie-weather.roamie.svc.cluster.local"
        elapsed += 12
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if req.extensions["timeout"]["read"] < 12:
            raise httpx.ReadTimeout("cold forecast data load")
        return httpx.Response(
            200,
            json={
                "timezone": "Asia/Tokyo",
                "daily_units": {
                    "time": "iso8601",
                    "temperature_2m_max": "°C",
                    "temperature_2m_min": "°C",
                    "precipitation_probability_max": "%",
                    "uv_index_max": "",
                },
                "daily": {
                    "time": ["2026-10-12"],
                    "temperature_2m_max": [23],
                    "temperature_2m_min": [14],
                    "precipitation_probability_max": [20],
                    "uv_index_max": [3],
                },
            },
        )

    class Fallback:
        async def search(self, specialist, request):
            pytest.fail("cold primary prematurely fell back to Google")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await WeatherFailover(
            OpenMeteoSource(client, None, self_hosted=True, clock=lambda: NOW), Fallback()
        ).search(Specialist.WEATHER, request("JP"))
    assert result.status == "ok"
    assert result.facts[0].weather.maximum_celsius == 23
    assert str(result.facts[0].source_url) == "https://open-meteo.com/"
