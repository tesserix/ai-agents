import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from roamie_agents.api import Source
from roamie_agents.contracts import RecommendationRequest, Specialist
from roamie_agents.evidence import EvidenceBatch
from roamie_agents.open_meteo import PRIMARY_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class WeatherFailover:
    def __init__(
        self,
        primary: Source,
        fallback: Source,
        *,
        primary_seconds: float = PRIMARY_TIMEOUT_SECONDS,
        fallback_seconds: float = 6,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.primary, self.fallback, self.clock = primary, fallback, clock
        self.primary_seconds, self.fallback_seconds = primary_seconds, fallback_seconds

    async def search(self, specialist: Specialist, request: RecommendationRequest) -> EvidenceBatch:
        try:
            async with asyncio.timeout(self.primary_seconds):
                result = await self.primary.search(specialist, request)
            if result.status == "ok":
                has_forecast = any(
                    f.weather and f.weather.coverage == "forecast" for f in result.facts
                )
                in_window = (
                    request.start_date is not None
                    and request.start_date <= self.clock().date() + timedelta(days=10)
                )
                if has_forecast or not in_window:
                    return result
        except TimeoutError:
            pass
        logger.warning("roamie_weather_fallback", extra={"weather_fallback_provider": "google"})
        try:
            async with asyncio.timeout(self.fallback_seconds):
                return await self.fallback.search(specialist, request)
        except TimeoutError:
            return EvidenceBatch(status="unavailable")
