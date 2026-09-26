from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx
from pydantic import SecretStr, ValidationError

from roamie_agents.contracts import Evidence, RecommendationRequest, Specialist, WeatherDetails
from roamie_agents.evidence import EvidenceBatch

SOURCE = "https://developers.google.com/maps/documentation/weather/daily-forecast"


class GoogleWeatherSource:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: SecretStr | None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client, self.api_key, self.clock = client, api_key, clock

    async def search(self, specialist: Specialist, request: RecommendationRequest) -> EvidenceBatch:
        if (
            specialist != Specialist.WEATHER
            or request.origin is None
            or request.start_date is None
            or request.end_date is None
        ):
            return EvidenceBatch(status="unavailable")
        now = self.clock()
        dates = tuple(
            request.start_date + timedelta(days=i)
            for i in range((request.end_date - request.start_date).days + 1)
        )
        if len(dates) > 366:
            return EvidenceBatch(status="unavailable")
        # Allow a day at either UTC boundary; use provider-local displayDate as authoritative.
        far_future = request.start_date > now.date() + timedelta(days=10)
        facts: list[Evidence] = []
        if not far_future:
            if self.api_key is None:
                return EvidenceBatch(status="unavailable")
            try:
                response = await self.client.get(
                    "https://weather.googleapis.com/v1/forecast/days:lookup",
                    params={
                        "location.latitude": request.origin.latitude,
                        "location.longitude": request.origin.longitude,
                        "days": 10,
                        "pageSize": 10,
                        "unitsSystem": "METRIC",
                        "languageCode": request.language,
                    },
                    headers={"X-Goog-Api-Key": self.api_key.get_secret_value()},
                    timeout=8,
                )
                response.raise_for_status()
                if len(response.content) > 250_000:
                    return EvidenceBatch(status="unavailable")
                payload = response.json()
                for day in payload.get("forecastDays", []):
                    local = date(**day["displayDate"])
                    if local not in dates:
                        continue
                    part = day.get("daytimeForecast", {})
                    high, low = day.get("maxTemperature", {}), day.get("minTemperature", {})
                    if any(t and t.get("unit") != "CELSIUS" for t in (high, low)):
                        return EvidenceBatch(status="unavailable")
                    rain = part.get("precipitation", {}).get("probability", {}).get("percent")
                    uv = part.get("uvIndex")
                    suggestions = [
                        "Check updated forecasts and local alerts before outdoor activities."
                    ]
                    if rain is not None and rain >= 50:
                        suggestions.append(
                            "Keep an indoor alternative and allow extra travel time for rain."
                        )
                    if high.get("degrees", 0) >= 30:
                        suggestions.append(
                            "Consider cooler morning activities, shade and water breaks."
                        )
                    if uv is not None and uv >= 3:
                        suggestions.append("Plan sun protection for outdoor time.")
                    description = (
                        part.get("weatherCondition", {})
                        .get("description", {})
                        .get("text", "Daily forecast")
                    )
                    facts.append(
                        Evidence(
                            id=f"weather-{local}",
                            name=f"{local}: {description}"[:200],
                            category=Specialist.WEATHER,
                            source_url=SOURCE,
                            observed_at=now,
                            offer_expires_at=now + timedelta(hours=1),
                            location=request.origin,
                            weather=WeatherDetails(
                                coverage="forecast",
                                local_date=local,
                                timezone=payload.get("timeZone", {}).get("id"),
                                maximum_celsius=high.get("degrees"),
                                minimum_celsius=low.get("degrees"),
                                precipitation_percent=rain,
                                uv_index=uv,
                                suggestions=tuple(suggestions),
                            ),
                        )
                    )
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                ValidationError,
            ):
                return EvidenceBatch(status="unavailable")
        covered = {f.weather.local_date for f in facts if f.weather}
        missing = tuple(str(d) for d in dates if d not in covered)
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
                        (
                            "Forecasts are available only within the provider "
                            "forecast window. Recheck closer to departure; no "
                            "weather prediction or climate estimate is available "
                            "for the missing dates."
                        ),
                    )
                    if missing
                    else ("Forecasts can change. Recheck before each outdoor activity.",),
                ),
            )
        )
        return EvidenceBatch(status="ok", facts=facts)
