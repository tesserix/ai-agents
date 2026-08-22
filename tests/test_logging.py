import io
import json

import structlog

from kora_agents.logging import configure_logging


def test_runtime_logging_is_json_structured() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)

    structlog.get_logger("test").info("request_completed", request_id="request-1")

    event = json.loads(stream.getvalue())
    assert event["event"] == "request_completed"
    assert event["request_id"] == "request-1"
    assert event["level"] == "info"
    assert event["timestamp"].endswith("Z")
