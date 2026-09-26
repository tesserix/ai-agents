import importlib.util
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "pin_roamie_release", Path("scripts/pin_roamie_release.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_registry_pins_exact_commit_image_and_does_not_forward_github_token():
    def reply(request):
        if request.url.path == "/token":
            assert request.url.params["scope"] == "repository:tesserix/ai-agents-roamie:pull"
            return httpx.Response(200, json={"token": "pull-token"})
        assert request.url.path.endswith("/manifests/sha-abcdef0")
        assert request.headers["authorization"] == "Bearer pull-token"
        return httpx.Response(200, headers={"Docker-Content-Digest": "sha256:" + "a" * 64})

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        assert module.resolve(client, "ai-agents-roamie", "abcdef0123", "github-secret").endswith(
            "@sha256:" + "a" * 64
        )


def test_missing_digest_prevents_registry_publication():
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"token": "token"}))
    ) as client:
        with pytest.raises(ValueError, match="invalid image digest"):
            module.resolve(client, "ai-agents-roamie", "abcdef0123", "github-secret")
