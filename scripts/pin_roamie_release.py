"""Pin generated registry manifests to the exact images built by this workflow."""

import os
import re
from pathlib import Path

import httpx
import yaml


def resolve(client: httpx.Client, image: str, commit: str, token: str) -> str:
    authorization = client.get(
        "https://ghcr.io/token",
        params={"service": "ghcr.io", "scope": f"repository:tesserix/{image}:pull"},
        auth=(os.environ.get("GITHUB_ACTOR", "github-actions"), token),
    )
    authorization.raise_for_status()
    bearer = authorization.json()["token"]
    response = client.get(
        f"https://ghcr.io/v2/tesserix/{image}/manifests/sha-{commit[:7]}",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Accept": (
                "application/vnd.oci.image.index.v1+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json, "
                "application/vnd.oci.image.manifest.v1+json"
            ),
        },
    )
    response.raise_for_status()
    digest = response.headers.get("Docker-Content-Digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("registry returned an invalid image digest")
    return f"ghcr.io/tesserix/{image}@{digest}"


def main() -> None:
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        images = {
            name: resolve(client, name, os.environ["GITHUB_SHA"], os.environ["GHCR_READ_TOKEN"])
            for name in ("ai-agents-roamie", "ai-agents-roamie-manager")
        }
    for path in Path("registry/roamie").glob("*.yaml"):
        document = yaml.safe_load(path.read_text())
        name = (
            "ai-agents-roamie-manager"
            if document["metadata"]["name"] == "roamie-trip-manager-agent"
            else "ai-agents-roamie"
        )
        document["spec"]["runtime"]["image"] = images[name]
        path.write_text(yaml.safe_dump(document, sort_keys=False))
    print("Pinned Roamie registry manifests to the images from this workflow commit.")


if __name__ == "__main__":
    main()
