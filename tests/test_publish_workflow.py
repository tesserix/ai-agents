from pathlib import Path

import yaml

PUBLISH_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "publish.yml"


def test_registry_deploy_key_uses_mesh_safe_header() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert '-H "X-Agentic-Registry-Deploy-Key: ${REGISTRY_DEPLOY_KEY}"' in workflow
    assert '-H "Authorization: Bearer ${REGISTRY_DEPLOY_KEY}"' not in workflow


def test_registry_publish_validates_secret_without_printing_it() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert '[[ "${REGISTRY_DEPLOY_KEY}" =~ ^[[:xdigit:]]{64}$ ]]' in workflow
    assert "deploy key must be a 64-character hexadecimal value" in workflow


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
        "https://publish.aregistry.tesserix.app/v0/apply"
    )

    script = publish["run"]
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in script
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" in script
    assert "audience=agentregistry-publisher.tesserix.app" in script
    assert "X-GitHub-OIDC-Token: Bearer ${github_oidc_token}" in script
    assert '"${REGISTRY_PUBLISH_URL}"' in script
    assert "https://aregistry.tesserix.app/v0/apply" not in script
