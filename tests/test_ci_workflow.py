from pathlib import Path

import yaml


def test_ci_runs_from_the_environment_created_by_frozen_sync() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    commands = [step["run"] for step in workflow["jobs"]["verify"]["steps"] if "run" in step]
    verification_commands = [command for command in commands if command.startswith("uv run ")]

    assert verification_commands
    assert all(
        command.startswith("uv run --offline --frozen ") for command in verification_commands
    )
