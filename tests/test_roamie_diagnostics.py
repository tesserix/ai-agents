import json

from structlog.testing import capture_logs

from roamie_agents.diagnostics import record_failure
from roamie_agents.runtime import TravelFailure


def test_failure_metadata_is_bounded_and_correlates_with_model_run():
    error = TravelFailure("model_failed", run_id="run-123", state="budget_exhausted")
    with capture_logs() as logs:
        request_id = record_failure("worker", error)
    assert logs[0]["request_id"] == request_id
    assert logs[0]["run_id"] == "run-123"
    assert logs[0]["state"] == "budget_exhausted"


def test_untrusted_error_metadata_and_causes_are_not_logged():
    error = TravelFailure("private details", run_id="private token " * 200, state="private state")
    error.__cause__ = ValueError("private upstream body")
    with capture_logs() as logs:
        record_failure("manager", error)
    assert logs[0]["reason"] == "unclassified"
    assert logs[0]["run_id"] is None
    assert logs[0]["state"] is None
    assert "private" not in json.dumps(logs)
