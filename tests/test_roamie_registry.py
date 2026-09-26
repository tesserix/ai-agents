from pathlib import Path

import yaml


def test_roamie_agents_reference_published_skills():
    root = Path("registry/roamie")
    agents = [yaml.safe_load(p.read_text()) for p in root.glob("*.yaml")]
    skills = {
        yaml.safe_load(p.read_text())["metadata"]["name"] for p in (root / "skills").glob("*.yaml")
    }
    assert len(skills) == 10
    for agent in agents:
        assert agent["spec"]["skills"][0]["ref"] in skills


def test_roamie_catalog_names_are_unique_across_kinds():
    root = Path("registry/roamie")
    documents = [
        yaml.safe_load(p.read_text())
        for pattern in ("*.yaml", "skills/*.yaml")
        for p in root.glob(pattern)
    ]
    keys = [(d["metadata"]["namespace"], d["metadata"]["name"]) for d in documents]
    assert len(keys) == 20
    assert len(set(keys)) == len(keys)


def test_roamie_definitions_are_discoverable_without_enabling_routes():
    for pattern in ("*.yaml", "skills/*.yaml"):
        for path in Path("registry/roamie").glob(pattern):
            document = yaml.safe_load(path.read_text())
            assert document["metadata"]["visibility"] == "public"
            assert document["metadata"]["tag"] == "1.1.3"
            assert document["metadata"]["labels"]["agent.tesserix.app/gateway-export"] == "false"


def test_roamie_agents_resolve_pinned_runtime_and_dependencies():
    for path in Path("registry/roamie").glob("*.yaml"):
        document = yaml.safe_load(path.read_text())
        spec = document["spec"]
        name = document["metadata"]["name"].removesuffix("-agent")
        assert spec["definitionVersion"] == "v1"
        assert spec["framework"] == "tesserix-adk"
        image_name = (
            "ai-agents-roamie-manager" if name == "roamie-trip-manager" else "ai-agents-roamie"
        )
        released = {
            "ai-agents-roamie-manager": (
                "sha256:fb0a043f0e6b8015f86c2bfa5b560ca4afee2dda4090f945f3411b332bbb0b00"
            ),
            "ai-agents-roamie": (
                "sha256:eba4d1257cc1239063ee9ebf1b0e58caf4dfad3e7e5cd2eece30066e6cf0cb96"
            ),
        }
        assert spec["runtime"]["image"] == f"ghcr.io/tesserix/{image_name}@{released[image_name]}"
        assert spec["runtime"]["type"] == "container"
        assert "@sha256:" in spec["runtime"]["image"]
        assert len(spec["runtime"]["image"].split("@sha256:")[1]) == 64
        assert spec["skills"] == [{"ref": name, "version": "1.1.3"}]
        assert spec["tools"] == [{"ref": "roamie-travel-search", "version": "1.1.0"}]
        assert spec["mcpServers"] == [{"ref": "roamie-travel-mcp", "version": "1.1.0"}]


def test_published_manager_skill_matches_runtime_review_instructions():
    from roamie_agents.definitions import manager_definition

    definition = manager_definition()
    document = yaml.safe_load(Path("registry/roamie/skills/roamie-trip-manager.yaml").read_text())
    assert document["spec"]["instructions"] == definition.agent.instructions
    assert (
        document["metadata"]["annotations"]["roamie.tesserix.app/definition-revision"]
        == definition.revision
    )
