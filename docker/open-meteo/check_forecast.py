"""A running HTTP server with an empty weather database is not ready."""

import json
import math
import urllib.request


def valid_forecast(payload: dict) -> bool:
    daily = payload.get("daily", {})
    values = daily.get("temperature_2m_max", [])
    return any(
        isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(v)
        and -100 <= v <= 70
        for v in values
    )


if __name__ == "__main__":
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8080/v1/forecast?latitude=35.68&longitude=139.65"
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,uv_index_max"
            "&forecast_days=16&timezone=auto",
            timeout=15,
        ) as response:
            if not valid_forecast(json.load(response)):
                raise SystemExit(1)
    except (OSError, ValueError, TypeError, AttributeError):
        raise SystemExit(1) from None
