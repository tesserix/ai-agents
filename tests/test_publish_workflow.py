from pathlib import Path

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


def test_registry_publish_rejects_partial_multistatus_failures() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert "200) ;;" in workflow
    assert "207)" in workflow
    assert 'jq -e \'all(.applied[]?; (.error // "") == "")\'' in workflow
