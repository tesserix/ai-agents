from pathlib import Path

import yaml


def test_roamie_agents_reference_published_skills():
    root = Path("registry/roamie")
    agents = [yaml.safe_load(p.read_text()) for p in root.glob("*.yaml")]
    skills = {
        yaml.safe_load(p.read_text())["metadata"]["name"] for p in (root / "skills").glob("*.yaml")
    }
    assert len(skills) == 8
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
    assert len(keys) == 16
    assert len(set(keys)) == len(keys)


def test_roamie_definitions_are_discoverable_without_enabling_routes():
    for pattern in ("*.yaml", "skills/*.yaml"):
        for path in Path("registry/roamie").glob(pattern):
            document = yaml.safe_load(path.read_text())
            assert document["metadata"]["visibility"] == "public"
            assert document["metadata"]["tag"] == "1.0.3"
            assert document["metadata"]["labels"]["agent.tesserix.app/gateway-export"] == "false"


def test_roamie_agents_resolve_pinned_runtime_and_dependencies():
    for path in Path("registry/roamie").glob("*.yaml"):
        document = yaml.safe_load(path.read_text())
        spec = document["spec"]
        name = document["metadata"]["name"].removesuffix("-agent")
        assert spec["definitionVersion"] == "v1"
        assert spec["framework"] == "tesserix-adk"
        assert spec["runtime"]["type"] == "container"
        assert "@sha256:" in spec["runtime"]["image"]
        assert len(spec["runtime"]["image"].split("@sha256:")[1]) == 64
        assert spec["skills"] == [{"ref": name, "version": "1.0.3"}]
        assert spec["tools"] == [{"ref": "roamie-travel-search", "version": "1.0.1"}]
        assert spec["mcpServers"] == [{"ref": "roamie-travel-mcp", "version": "1.0.1"}]
