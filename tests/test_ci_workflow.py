from pathlib import Path

import yaml

ADK_BASE = (
    "ghcr.io/tesserix/base-python-adk-3.14:20260829"
    "@sha256:5a6fd1863ed7f37f3929cc596d0ec063c3077c11713cd334f14d1df2b30ef386"
)


def _verify_job() -> dict:
    return yaml.safe_load(Path(".github/workflows/ci.yml").read_text())["jobs"]["verify"]


def test_ci_runs_from_the_environment_created_by_frozen_sync() -> None:
    commands = [step["run"] for step in _verify_job()["steps"] if "run" in step]
    verification_commands = [command for command in commands if command.startswith("uv run ")]

    assert verification_commands
    assert all(command.startswith("uv run --no-sync ") for command in verification_commands)


def test_ci_verifies_inside_the_adk_base_image() -> None:
    job = _verify_job()
    commands = [step["run"] for step in job["steps"] if "run" in step]

    assert job["container"]["image"] == ADK_BASE
    assert job["env"]["UV_PROJECT_ENVIRONMENT"] == "/opt/adk-venv"
    # Without --inexact the sync prunes the ADK, which the lock deliberately omits.
    assert any(command.startswith("uv sync ") and "--inexact" in command for command in commands)


def test_the_runtime_image_uses_the_same_immutable_adk_base() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert f"ARG BASE_IMAGE={ADK_BASE}" in dockerfile


def test_each_service_has_a_distinct_runtime_target() -> None:
    dockerfile = Path("Dockerfile").read_text()

    assert "FROM runtime-base AS kora-runtime" in dockerfile
    assert "FROM runtime-base AS sre-runtime" in dockerfile
    assert 'ENTRYPOINT ["uvicorn", "kora_agents.main:app"' in dockerfile
    assert 'ENTRYPOINT ["uvicorn", "sre_agent.main:app"' in dockerfile
