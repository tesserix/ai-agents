from pathlib import Path

PUBLISH_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "publish.yml"


def test_registry_deploy_key_uses_mesh_safe_header() -> None:
    workflow = PUBLISH_WORKFLOW.read_text()

    assert '-H "X-Agentic-Registry-Deploy-Key: ${REGISTRY_DEPLOY_KEY}"' in workflow
    assert '-H "Authorization: Bearer ${REGISTRY_DEPLOY_KEY}"' not in workflow
