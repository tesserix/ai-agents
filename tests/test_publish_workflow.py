import subprocess
from pathlib import Path

import yaml

PUBLISH_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "publish.yml"
ADK_BASE = (
    "ghcr.io/tesserix/base-python-adk-3.14:20260926"
    "@sha256:14ec2e75d17a4207d1ae9d2600f3c33a609db33e9833c6be993ecc1525be56c9"
)


def test_registry_deploy_key_uses_mesh_safe_header() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert '-H "X-Agentic-Registry-Deploy-Key: ${deploy_key}"' in workflow
    assert '-H "Authorization: Bearer ${REGISTRY_DEPLOY_KEY}"' not in workflow


def test_registry_publish_validates_secret_without_printing_it() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert 'valid_deploy_key "${REGISTRY_DEPLOY_KEY}"' in workflow
    assert 'valid_deploy_key "${REGISTRY_SRE_DEPLOY_KEY}"' in workflow
    assert "*[!0-9A-Fa-f]*" in workflow
    assert "*[!A-Za-z0-9_-]*" in workflow
    assert "deploy key must be 64-character hex or 72-character URL-safe" in workflow


def test_registry_publish_script_is_valid_posix_shell() -> None:
    job = yaml.safe_load(PUBLISH_WORKFLOW.read_text())["jobs"]["registry"]
    publish = next(
        step for step in job["steps"] if step.get("name") == "Publish reviewed Agent manifests"
    )

    script = publish["run"]
    assert "pipefail" not in script
    assert "[[" not in script
    assert "<<<" not in script

    result = subprocess.run(
        ["/bin/sh", "-n"],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_registry_publish_uses_the_runtime_python_for_json() -> None:
    job = yaml.safe_load(PUBLISH_WORKFLOW.read_text())["jobs"]["registry"]
    publish = next(
        step for step in job["steps"] if step.get("name") == "Publish reviewed Agent manifests"
    )

    assert "jq" not in publish["run"]
    assert "python -c" in publish["run"]


def test_registry_publish_preserves_http_response_for_diagnostics() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert "curl --fail" not in workflow
    assert 'cat "${response_file}" >&2' in workflow


def test_registry_publish_identifies_machine_client_to_cloudflare() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert "User-Agent: Tesserix-Agent-Publisher/1.0" in workflow
    assert '-H "Accept: application/json"' in workflow


def test_registry_publish_rejects_partial_multistatus_failures() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert "200) ;;" in workflow
    assert "207)" in workflow
    assert 'for entry in document.get("applied") or []' in workflow
    assert 'if entry.get("error")' in workflow
    assert "raise SystemExit(1)" in workflow


def test_registry_publish_uses_repository_bound_oidc_route():
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/publish.yml").read_text()
    )
    job = workflow["jobs"]["registry"]

    assert job["permissions"] == {"contents": "read", "id-token": "write", "packages": "read"}
    publish = next(
        step for step in job["steps"] if step.get("name") == "Publish reviewed Agent manifests"
    )
    assert publish["env"]["REGISTRY_PUBLISH_URL"] == (
        "https://publish-aregistry.tesserix.app/v0/apply"
    )

    script = publish["run"]
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in script
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" in script
    assert "audience=agentregistry-publisher.tesserix.app" in script
    assert "X-GitHub-OIDC-Token: Bearer ${github_oidc_token}" in script
    assert '"${REGISTRY_PUBLISH_URL}"' in script
    assert "https://aregistry.tesserix.app/v0/apply" not in script


def test_no_manifest_is_published_before_the_evaluation_suite_passes() -> None:
    job = yaml.safe_load(PUBLISH_WORKFLOW.read_text())["jobs"]["registry"]
    names = [step.get("name") for step in job["steps"]]

    gate = next(
        step
        for step in job["steps"]
        if step.get("name") == "Gate the publish on the evaluation suite"
    )
    assert "python -m sre_agent.evaluation evals/sre-investigator.yaml" in gate["run"]
    assert names.index("Gate the publish on the evaluation suite") < names.index(
        "Publish reviewed Agent manifests"
    )


def test_the_gate_runs_inside_the_adk_base_image_production_uses() -> None:
    job = yaml.safe_load(PUBLISH_WORKFLOW.read_text())["jobs"]["registry"]

    assert job["container"]["image"] == ADK_BASE
    assert job["env"]["UV_PROJECT_ENVIRONMENT"] == "/opt/adk-venv"


def test_kora_and_sre_publish_as_distinct_runtime_images() -> None:
    job = yaml.safe_load(PUBLISH_WORKFLOW.read_text())["jobs"]["image"]

    assert job["strategy"]["matrix"]["include"] == [
        {"target": "roamie-manager-runtime", "image": "ghcr.io/tesserix/ai-agents-roamie-manager"},
        {"target": "roamie-runtime", "image": "ghcr.io/tesserix/ai-agents-roamie"},
        {"target": "kora-runtime", "image": "ghcr.io/tesserix/ai-agents"},
        {"target": "sre-runtime", "image": "ghcr.io/tesserix/ai-agents-sre"},
        {"target": "orchestrator-runtime", "image": "ghcr.io/tesserix/ai-agents-orchestrator"},
    ]
    metadata = next(step for step in job["steps"] if step.get("id") == "meta")
    build = next(
        step for step in job["steps"] if "docker/build-push-action" in step.get("uses", "")
    )
    assert metadata["with"]["images"] == "${{ matrix.image }}"
    assert build["with"]["target"] == "${{ matrix.target }}"


def test_sre_publication_uses_a_separate_tenant_scoped_deploy_key() -> None:
    job = yaml.safe_load(PUBLISH_WORKFLOW.read_text())["jobs"]["registry"]
    publish = next(
        step for step in job["steps"] if step.get("name") == "Publish reviewed Agent manifests"
    )

    assert publish["env"]["REGISTRY_SRE_DEPLOY_KEY"] == (
        "${{ secrets.AGENTIC_REGISTRY_SRE_DEPLOY_KEY }}"
    )
    script = publish["run"]
    assert (
        "registry/sre-investigator.yaml|registry/supervisor.yaml|registry/orchestrator.yaml)"
        ' deploy_key="${REGISTRY_SRE_DEPLOY_KEY}"' in script
    )
    assert "X-Agentic-Registry-Deploy-Key: ${deploy_key}" in script


def test_roamie_publication_has_a_separate_tenant_key():
    workflow = PUBLISH_WORKFLOW.read_text()
    assert "AGENTIC_REGISTRY_ROAMIE_DEPLOY_KEY" in workflow
    assert "registry/roamie/skills/*.yaml" in workflow
    assert "inputs.publish_roamie" in workflow


def test_roamie_mcp_publication_uses_a_pinned_release_and_scoped_identity() -> None:
    job = yaml.safe_load(PUBLISH_WORKFLOW.read_text())["jobs"]["registry"]
    checkout = next(
        step for step in job["steps"] if step.get("name") == "Read released Roamie MCP manifest"
    )
    assert checkout["if"] == "github.event_name == 'push' || inputs.publish_roamie"
    assert checkout["with"]["repository"] == "tesserix/roamie"
    assert checkout["with"]["ref"] == "a0eb6de4c7f652b797c4613c331bc65b1d0ff5ff"
    assert checkout["with"]["persist-credentials"] is False
    publish = next(
        step for step in job["steps"] if step.get("name") == "Publish reviewed Agent manifests"
    )
    assert ".roamie-release/services/travel-mcp/mcpserver.json" in publish["run"]
    assert (
        'registry/roamie/*|.roamie-release/*) deploy_key="${REGISTRY_ROAMIE_DEPLOY_KEY}"'
        in publish["run"]
    )


def test_roamie_publication_runs_on_main_push_after_verification() -> None:
    job = yaml.safe_load(PUBLISH_WORKFLOW.read_text())["jobs"]["registry"]
    assert job["if"] == (
        "github.ref == 'refs/heads/main' && "
        "(github.event_name == 'push' || github.event_name == 'workflow_dispatch')"
    )
    assert set(job["needs"]) == {"verify", "image"}
    assert "github.ref == 'refs/heads/main'" in job["if"]
    publish = next(
        step for step in job["steps"] if step.get("name") == "Publish reviewed Agent manifests"
    )
    assert (
        publish["env"]["PUBLISH_ROAMIE"]
        == "${{ github.event_name == 'push' || inputs.publish_roamie }}"
    )


def test_roamie_dependencies_publish_before_agents():
    job = yaml.safe_load(PUBLISH_WORKFLOW.read_text())["jobs"]["registry"]
    publish = next(s for s in job["steps"] if s.get("name") == "Publish reviewed Agent manifests")
    assert (
        ".roamie-release/services/travel-mcp/tool.json "
        ".roamie-release/services/travel-mcp/mcpserver.json "
        "registry/roamie/skills/*.yaml registry/roamie/*.yaml"
    ) in publish["run"]
