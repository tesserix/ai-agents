"""Live smoke gate for the pinned, locally built Open-Meteo image."""

import json
import time
import urllib.parse
import urllib.request

from check_forecast import valid_forecast


def main() -> None:
    for city, latitude, longitude in (
        ("Tokyo", 35.6764, 139.65),
        ("Melbourne", -37.81, 144.96),
        ("New York", 40.71, -74.01),
    ):
        params = urllib.parse.urlencode(
            {
                "latitude": latitude,
                "longitude": longitude,
                "timezone": "auto",
                "forecast_days": 16,
                "daily": "temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,uv_index_max",
            }
        )
        for attempt in range(10):
            try:
                start = time.monotonic()
                with urllib.request.urlopen(
                    "http://127.0.0.1:18080/v1/forecast?" + params, timeout=30
                ) as response:
                    payload = json.load(response)
                if valid_forecast(payload):
                    print(
                        json.dumps(
                            {
                                "city": city,
                                "timezone": payload.get("timezone"),
                                "forecast_days": sum(
                                    v is not None for v in payload["daily"]["temperature_2m_max"]
                                ),
                                "seconds": round(time.monotonic() - start, 2),
                            }
                        ),
                        flush=True,
                    )
                    break
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            if attempt == 9:
                raise SystemExit(f"No usable live forecast for {city}")
            time.sleep(5)


if __name__ == "__main__":
    main()
