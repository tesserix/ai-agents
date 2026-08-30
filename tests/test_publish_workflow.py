from pathlib import Path

import yaml

PUBLISH_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "publish.yml"
ADK_BASE = (
    "ghcr.io/tesserix/base-python-adk-3.14:20260829"
    "@sha256:5a6fd1863ed7f37f3929cc596d0ec063c3077c11713cd334f14d1df2b30ef386"
)


def test_registry_deploy_key_uses_mesh_safe_header() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert '-H "X-Agentic-Registry-Deploy-Key: ${deploy_key}"' in workflow
    assert '-H "Authorization: Bearer ${REGISTRY_DEPLOY_KEY}"' not in workflow


def test_registry_publish_validates_secret_without_printing_it() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert ('[[ "${REGISTRY_DEPLOY_KEY}" =~ ^([[:xdigit:]]{64}|[A-Za-z0-9_-]{72})$ ]]') in workflow
    assert (
        '[[ "${REGISTRY_SRE_DEPLOY_KEY}" =~ ^([[:xdigit:]]{64}|[A-Za-z0-9_-]{72})$ ]]'
    ) in workflow
    assert "deploy key must be 64-character hex or 72-character URL-safe" in workflow


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
    assert 'jq -e \'all(.applied[]?; (.error // "") == "")\'' in workflow


def test_registry_publish_uses_repository_bound_oidc_route():
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/publish.yml").read_text()
    )
    job = workflow["jobs"]["registry"]

    assert job["permissions"] == {"contents": "read", "id-token": "write"}
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
        {"target": "kora-runtime", "image": "ghcr.io/tesserix/ai-agents"},
        {"target": "sre-runtime", "image": "ghcr.io/tesserix/ai-agents-sre"},
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
    assert 'registry/sre-investigator.yaml) deploy_key="${REGISTRY_SRE_DEPLOY_KEY}"' in script
    assert "X-Agentic-Registry-Deploy-Key: ${deploy_key}" in script
