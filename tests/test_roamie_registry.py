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
        assert agent["spec"]["skills"][0]["name"] in skills


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
            assert document["metadata"]["tag"] == "1.0.1"
            assert document["metadata"]["labels"]["agent.tesserix.app/gateway-export"] == "false"
