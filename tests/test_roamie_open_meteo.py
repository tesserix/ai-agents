import asyncio
from datetime import UTC, date, datetime

import httpx
import pytest
from pydantic import SecretStr

from roamie_agents.contracts import Location, RecommendationRequest, Specialist
from roamie_agents.open_meteo import OpenMeteoSource

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def request():
    return RecommendationRequest(
        prompt="Tokyo weather",
        origin=Location(latitude=35.6764, longitude=139.65),
        destination_country="JP",
        start_date="2026-10-20",
        end_date="2026-10-22",
    )


def payload():
    return {
        "timezone": "Asia/Tokyo",
        "daily_units": {
            "time": "iso8601",
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "precipitation_probability_max": "%",
            "uv_index_max": "",
        },
        "daily": {
            "time": ["2026-10-20", "2026-10-21"],
            "temperature_2m_max": [31.5, 24],
            "temperature_2m_min": [21, 18],
            "precipitation_probability_max": [70, None],
            "uv_index_max": [6, None],
        },
    }


async def test_japan_forecast_retains_local_dates_units_nulls_and_attribution():
    def reply(req):
        assert req.url.host == "customer-api.open-meteo.com"
        assert req.headers["X-Api-Key"] == "test-weather-key"
        assert "test-weather-key" not in str(req.url)
        assert "authorization" not in req.headers
        assert req.url.params["timezone"] == "auto"
        assert req.url.params["forecast_days"] == "16"
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await OpenMeteoSource(
            client, SecretStr("test-weather-key"), clock=lambda: NOW
        ).search(Specialist.WEATHER, request())
    assert result.status == "ok"
    day = result.facts[0]
    assert day.weather.local_date.isoformat() == "2026-10-20"
    assert day.weather.timezone == "Asia/Tokyo"
    assert day.weather.maximum_celsius == 31.5
    assert day.weather.precipitation_percent == 70
    assert str(day.source_url) == "https://open-meteo.com/"
    assert result.facts[1].weather.precipitation_percent is None
    assert result.facts[-1].weather.coverage == "partial"
    assert result.facts[-1].weather.missing_dates == ("2026-10-22",)
    assert "creativecommons.org/licenses/by/4.0" in " ".join(day.weather.suggestions)


async def test_commercial_mode_never_falls_back_to_free_api_without_a_key():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected request"))
    ) as client:
        result = await OpenMeteoSource(client, None, clock=lambda: NOW).search(
            Specialist.WEATHER, request()
        )
    assert result.status == "unavailable"


async def test_explicit_evaluation_uses_free_endpoint_without_credentials():
    def reply(req):
        assert req.url.host == "api.open-meteo.com"
        assert "x-api-key" not in req.headers
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await OpenMeteoSource(client, None, evaluation=True, clock=lambda: NOW).search(
            Specialist.WEATHER, request()
        )
    assert result.status == "ok"


@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_http_failures_are_unavailable_without_leaking_provider_body(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="private upstream detail")
        )
    ) as client:
        result = await OpenMeteoSource(client, SecretStr("key"), clock=lambda: NOW).search(
            Specialist.WEATHER, request()
        )
    assert result.status == "unavailable"
    assert result.facts == []
    assert "private" not in result.model_dump_json()


async def test_timeout_is_unavailable_and_cancellation_propagates():
    for error in (httpx.ReadTimeout("timeout"), asyncio.CancelledError()):

        def reply(_, error=error):
            raise error

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            source = OpenMeteoSource(client, SecretStr("key"), clock=lambda: NOW)
            if isinstance(error, asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    await source.search(Specialist.WEATHER, request())
            else:
                assert (await source.search(Specialist.WEATHER, request())).status == "unavailable"


@pytest.mark.parametrize(
    "case",
    [
        "null",
        "units",
        "length",
        "duplicate",
        "order",
        "range",
        "nan",
        "reversed",
        "timezone",
        "huge",
        "invalid_date",
    ],
)
async def test_invalid_provider_data_never_becomes_forecasts(case):
    body = payload()
    if case == "null":
        body = None
    elif case == "units":
        body["daily_units"]["temperature_2m_max"] = "°F"
    elif case == "length":
        body["daily"]["uv_index_max"] = [3]
    elif case == "duplicate":
        body["daily"]["time"] = ["2026-10-20", "2026-10-20"]
    elif case == "order":
        body["daily"]["time"].reverse()
    elif case == "range":
        body["daily"]["precipitation_probability_max"][0] = 101
    elif case == "nan":
        body["daily"]["temperature_2m_max"][0] = "NaN"
    elif case == "reversed":
        body["daily"]["temperature_2m_min"][0] = 40
    elif case == "timezone":
        body["timezone"] = "Not/AZone"
    elif case == "huge":
        body["unused"] = "x" * 250_001
    elif case == "invalid_date":
        body["daily"]["time"][0] = "2026-13-01"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        result = await OpenMeteoSource(client, SecretStr("key"), clock=lambda: NOW).search(
            Specialist.WEATHER, request()
        )
    assert result.status == "unavailable"
    assert result.facts == []


async def test_null_temperatures_and_dates_outside_trip_are_missing_not_zero():
    body = payload()
    body["daily"]["temperature_2m_max"][0] = None
    body["daily"]["time"][1] = "2026-10-24"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        result = await OpenMeteoSource(client, SecretStr("key"), clock=lambda: NOW).search(
            Specialist.WEATHER, request()
        )
    assert len(result.facts) == 1
    assert result.facts[0].weather.coverage == "unavailable"
    assert result.facts[0].weather.missing_dates == ("2026-10-20", "2026-10-21", "2026-10-22")
    assert result.facts[0].weather.maximum_celsius is None


async def test_full_coverage_and_zero_probability_are_preserved():
    body = payload()
    body["daily"]["precipitation_probability_max"] = [0, 49.5]
    req = request().model_copy(update={"end_date": date(2026, 10, 21)})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        result = await OpenMeteoSource(client, SecretStr("key"), clock=lambda: NOW).search(
            Specialist.WEATHER, req
        )
    assert result.facts[-1].weather.coverage == "complete"
    assert result.facts[-1].weather.missing_dates == ()
    assert result.facts[0].weather.precipitation_percent == 0
    assert result.facts[1].weather.precipitation_percent == 50


async def test_missing_inputs_and_future_dates_do_not_call_provider():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected request"))
    ) as client:
        source = OpenMeteoSource(client, SecretStr("key"), clock=lambda: NOW)
        for field in ("origin", "start_date", "end_date"):
            assert (
                await source.search(Specialist.WEATHER, request().model_copy(update={field: None}))
            ).status == "unavailable"
        assert (await source.search(Specialist.FOOD, request())).status == "unavailable"
        future = request().model_copy(
            update={"start_date": date(2027, 1, 1), "end_date": date(2027, 1, 3)}
        )
        result = await source.search(Specialist.WEATHER, future)
        assert result.facts[0].weather.coverage == "unavailable"
        assert len(result.facts[0].weather.missing_dates) == 3
        too_long = request().model_copy(update={"end_date": date(2028, 1, 1)})
        assert (await source.search(Specialist.WEATHER, too_long)).status == "unavailable"
