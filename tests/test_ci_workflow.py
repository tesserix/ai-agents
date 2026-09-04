from pathlib import Path

import yaml

ADK_BASE = (
    "ghcr.io/tesserix/base-python-adk-3.14:weekly"
    "@sha256:4e38ff684b5c9936b855cac13aa71db619de23bca6d379d01e6156c4f402a56b"
)
WORKFLOWS_REVISION = "8a269caaf014b3e053a29b40902484236323414a"


def _ci_jobs() -> dict:
    return yaml.safe_load(Path(".github/workflows/ci.yml").read_text())["jobs"]


def test_ci_reuses_the_reviewed_python_workflow_at_an_immutable_revision() -> None:
    job = _ci_jobs()["quality"]

    assert job["uses"] == (
        "tesserix/tesserix-workflows/.github/workflows/python-baked-deps-ci.yml"
        f"@{WORKFLOWS_REVISION}"
    )
    assert job["with"] == {
        "container_image": ADK_BASE,
        "uv_version": "0.12.5",
        "source_directory": "src",
        "coverage_min_lines": 90,
    }
    assert "secrets" not in job


def test_ci_reuses_the_reviewed_secret_scan_without_inheriting_secrets() -> None:
    job = _ci_jobs()["secret-scan"]

    assert job == {
        "uses": (
            f"tesserix/tesserix-workflows/.github/workflows/secret-scan.yml@{WORKFLOWS_REVISION}"
        )
    }


def test_the_runtime_image_uses_the_same_immutable_adk_base() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert f"ARG BASE_IMAGE={ADK_BASE}" in dockerfile


def test_each_service_has_a_distinct_runtime_target() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "FROM runtime-base AS kora-runtime" in dockerfile
    assert "FROM runtime-base AS sre-runtime" in dockerfile
    assert 'ENTRYPOINT ["uvicorn", "kora_agents.main:app"' in dockerfile
    assert 'ENTRYPOINT ["uvicorn", "sre_agent.main:app"' in dockerfile
