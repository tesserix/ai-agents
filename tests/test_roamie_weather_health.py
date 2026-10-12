import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "weather_health", Path(__file__).parents[1] / "docker/open-meteo/check_forecast.py"
)
HEALTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HEALTH)


@pytest.mark.parametrize(
    "values,expected",
    [
        ([], False),
        ([None], False),
        ([0], True),
        ([float("nan")], False),
        ([1000], False),
        ([True], False),
        (["20"], False),
        ([None, 20.5], True),
    ],
)
def test_readiness_requires_a_real_temperature(values, expected):
    assert HEALTH.valid_forecast({"daily": {"temperature_2m_max": values}}) is expected
