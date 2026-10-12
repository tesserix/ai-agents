from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from roamie_agents.contracts import Evidence, RecommendationRequest, Specialist, WeatherDetails
from roamie_agents.evidence import EvidenceBatch

SOURCE = "https://open-meteo.com/"
ATTRIBUTION = "Open-Meteo data (CC BY 4.0): https://creativecommons.org/licenses/by/4.0/"
Temperature = Annotated[float, Field(ge=-100, le=70, allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
UVIndex = Annotated[float, Field(ge=0, le=30, allow_inf_nan=False)]


# Provider responses include unrelated metadata; validate all fields we actually consume.
class _Units(BaseModel):
    time: Literal["iso8601"]
    temperature_2m_max: Literal["°C"]
    temperature_2m_min: Literal["°C"]
    precipitation_probability_max: Literal["%"]
    uv_index_max: Literal[""]


class _Daily(BaseModel):
    time: list[date] = Field(max_length=16)
    temperature_2m_max: list[Temperature | None] = Field(max_length=16)
    temperature_2m_min: list[Temperature | None] = Field(max_length=16)
    precipitation_probability_max: list[Probability | None] = Field(max_length=16)
    uv_index_max: list[UVIndex | None] = Field(max_length=16)


class _Forecast(BaseModel):
    timezone: str = Field(min_length=1, max_length=80)
    daily_units: _Units
    daily: _Daily


class OpenMeteoSource:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: SecretStr | None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        evaluation: bool = False,
        self_hosted: bool = False,
    ) -> None:
        self.client, self.api_key, self.clock = client, api_key, clock
        self.evaluation, self.self_hosted = evaluation, self_hosted

    async def search(self, specialist: Specialist, request: RecommendationRequest) -> EvidenceBatch:
        if (
            specialist != Specialist.WEATHER
            or request.origin is None
            or request.start_date is None
            or request.end_date is None
        ):
            return EvidenceBatch(status="unavailable")
        count = (request.end_date - request.start_date).days + 1
        if not 1 <= count <= 366:
            return EvidenceBatch(status="unavailable")
        now = self.clock()
        dates = tuple(request.start_date + timedelta(days=i) for i in range(count))
        facts: list[Evidence] = []
        # Allow one day at the UTC boundary; provider-local dates remain authoritative.
        if request.start_date <= now.date() + timedelta(days=16):
            if self.api_key is None and not self.evaluation and not self.self_hosted:
                return EvidenceBatch(status="unavailable")
            host = "customer-api.open-meteo.com" if self.api_key else "api.open-meteo.com"
            origin = (
                "http://roamie-weather.roamie.svc.cluster.local:8080"
                if self.self_hosted
                else f"https://{host}"
            )
            headers = (
                {"X-Api-Key": self.api_key.get_secret_value()}
                if self.api_key and not self.self_hosted
                else {}
            )
            try:
                async with self.client.stream(
                    "GET",
                    f"{origin}/v1/forecast",
                    params={
                        "latitude": round(request.origin.latitude, 4),
                        "longitude": round(request.origin.longitude, 4),
                        "timezone": "auto",
                        "forecast_days": 16,
                        "temperature_unit": "celsius",
                        "daily": "temperature_2m_max,temperature_2m_min,"
                        "precipitation_probability_max,uv_index_max",
                    },
                    headers=headers,
                    timeout=8,
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 250_000:
                            return EvidenceBatch(status="unavailable")
                forecast = _Forecast.model_validate_json(body)
                ZoneInfo(forecast.timezone)
                daily = forecast.daily
                if daily.time != sorted(set(daily.time)):
                    return EvidenceBatch(status="unavailable")
                for local, high, low, rain, uv in zip(
                    daily.time,
                    daily.temperature_2m_max,
                    daily.temperature_2m_min,
                    daily.precipitation_probability_max,
                    daily.uv_index_max,
                    strict=True,
                ):
                    if high is not None and low is not None and high < low:
                        return EvidenceBatch(status="unavailable")
                    if local not in dates or high is None or low is None:
                        continue
                    suggestions = [
                        ATTRIBUTION,
                        "Check updated forecasts and local alerts before outdoor activities.",
                    ]
                    if rain is not None and rain >= 50:
                        suggestions.append(
                            "Keep an indoor alternative and allow extra travel time for rain."
                        )
                    if high >= 30:
                        suggestions.append(
                            "Consider cooler morning activities, shade and water breaks."
                        )
                    if uv is not None and uv >= 3:
                        suggestions.append("Plan sun protection for outdoor time.")
                    suggestions.append(
                        "Precipitation is the highest hourly probability for the day, "
                        "rounded to a whole percent."
                    )
                    facts.append(
                        Evidence(
                            id=f"weather-{local}",
                            name=f"{local}: Daily forecast",
                            category=Specialist.WEATHER,
                            source_url=SOURCE,
                            observed_at=now,
                            offer_expires_at=now + timedelta(hours=1),
                            location=request.origin,
                            weather=WeatherDetails(
                                coverage="forecast",
                                local_date=local,
                                timezone=forecast.timezone,
                                maximum_celsius=high,
                                minimum_celsius=low,
                                precipitation_percent=round(rain) if rain is not None else None,
                                uv_index=uv,
                                suggestions=tuple(suggestions),
                            ),
                        )
                    )
            except httpx.HTTPError, ValueError, ValidationError, ZoneInfoNotFoundError:
                return EvidenceBatch(status="unavailable")
        covered = {fact.weather.local_date for fact in facts if fact.weather}
        missing = tuple(str(day) for day in dates if day not in covered)
        facts.append(
            Evidence(
                id="weather-coverage",
                name="Weather forecast coverage for your trip",
                category=Specialist.WEATHER,
                source_url=SOURCE,
                observed_at=now,
                offer_expires_at=now + timedelta(hours=1),
                location=request.origin,
                weather=WeatherDetails(
                    coverage="complete" if not missing else "partial" if facts else "unavailable",
                    missing_dates=missing,
                    suggestions=(
                        ATTRIBUTION,
                        "Forecasts extend up to 16 days, depending on available model data. "
                        "No forecast is available for missing dates; recheck closer to departure.",
                    )
                    if missing
                    else (
                        ATTRIBUTION,
                        "Forecasts can change. Recheck before each outdoor activity.",
                    ),
                ),
            )
        )
        return EvidenceBatch(status="ok", facts=facts)
