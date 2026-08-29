import json

import pytest
import yaml

from sre_agent.evaluation import (
    SUITE_PATH,
    EvaluationCase,
    load_suite,
    main,
    run_case,
    run_suite,
)

HEALTHY = {
    "kind": "PodList",
    "items": [
        {
            "metadata": {"name": "kora-ai-1", "namespace": "kora"},
            "spec": {"nodeName": "node-1"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"name": "server", "ready": True, "restartCount": 0, "image": "kora:1"}
                ],
            },
        }
    ],
}

FINDINGS = {
    "summary": "kora is healthy.",
    "incident_suspected": False,
    "symptoms": [],
    "evidence": [
        {"tool": "list_pods", "subject": "kora/kora-ai-1", "observation": "Running and ready."}
    ],
    "hypothesis": "",
    "confidence": "high",
    "recommended_actions": [],
    "affected_apps": [],
}


def case(**overrides) -> EvaluationCase:
    fields = {
        "name": "sweep",
        "kind": "quality",
        "prompt": "Sweep kora.",
        "cluster": {"/api/v1/namespaces/kora/pods": {"json": HEALTHY}},
        "script": [
            {"tool": "list_pods", "arguments": {"namespace": "kora"}},
            {"answer": FINDINGS},
        ],
        "expect": {"calls": ["list_pods"], "incident_suspected": False},
    }
    return EvaluationCase.model_validate(fields | overrides)


async def test_a_case_whose_expectations_hold_passes() -> None:
    result = await run_case(case())

    assert result.passed
    assert result.failures == ()


async def test_a_missing_tool_call_is_reported_by_name() -> None:
    result = await run_case(case(expect={"calls": ["get_pod_logs"]}))

    assert not result.passed
    assert "get_pod_logs" in result.failures[0]


async def test_text_the_case_forbids_is_reported() -> None:
    result = await run_case(case(expect={"excludes": ["healthy"]}))

    assert not result.passed
    assert "forbids" in result.failures[0]


async def test_a_run_expected_to_fail_and_answering_anyway_is_a_failure() -> None:
    result = await run_case(case(expect={"fails": True}))

    assert not result.passed


@pytest.mark.parametrize("suite_case", load_suite().cases, ids=lambda each: each.name)
async def test_the_published_suite_holds(suite_case) -> None:
    result = await run_case(suite_case)

    assert result.failures == ()


async def test_the_suite_gates_on_its_security_cases() -> None:
    suite = load_suite()

    report = await run_suite(suite)

    assert report.suite == "sre-investigator"
    assert {each.kind for each in suite.cases} == {"quality", "security"}
    assert report.security_holds
    assert report.passed


def test_the_gate_exits_zero_and_reports_the_published_suite(capsys) -> None:
    exit_code = main([str(SUITE_PATH)])

    reported = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert reported["suite"] == "sre-investigator"
    assert reported["passed"] is True
    assert reported["security_holds"] is True
    assert reported["failures"] == {}


def test_the_gate_exits_nonzero_and_names_the_case_that_broke(tmp_path, capsys) -> None:
    suite = yaml.safe_load(SUITE_PATH.read_text())
    suite["cases"] = [suite["cases"][0]]
    suite["cases"][0]["expect"]["calls"] = ["get_deployment"]
    broken = tmp_path / "suite.yaml"
    broken.write_text(yaml.safe_dump(suite))

    exit_code = main([str(broken)])

    reported = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert (
        "get_deployment"
        in reported["failures"]["crashloop-investigation-reaches-the-log-evidence"][0]
    )
