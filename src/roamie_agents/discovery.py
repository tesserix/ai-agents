import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from tesserix_adk.a2a.card import AgentCard
from tesserix_adk.a2a.discovery import PeerNeed, RegistryPeers

from orchestrator_agent.config import WorkerEndpoint
from roamie_agents.contracts import Specialist


class RegistryWorkers:
    def __init__(
        self, client: httpx.AsyncClient, *, registry_origin: str, gateway_origin: str
    ) -> None:
        self._client = client
        self._registry = registry_origin.rstrip("/")
        self._gateway = gateway_origin.rstrip("/")
        self._peers = RegistryPeers(
            self._fetch,
            permitted={"roamie": tuple(f"roamie-{kind.value}" for kind in Specialist)},
            stale_seconds=0,
            ttl_seconds=60,
        )

    async def find(self, specialist: Specialist) -> WorkerEndpoint:
        peer = await self._peers.find(
            PeerNeed(agent=f"roamie-{specialist.value}", version="1.1.2", tenant="roamie")
        )
        return WorkerEndpoint(name=peer.card.agent, url=peer.card.audience)

    async def _fetch(self, need: PeerNeed) -> Sequence[Mapping[str, Any]]:
        async with self._client.stream(
            "GET",
            f"{self._registry}/v0/agents/{need.agent}-agent/1.1.2/resolved",
            params={"namespace": "roamie"},
            timeout=2,
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 262144:
                    raise ValueError("registry graph exceeds size limit")
        document = json.loads(body)
        agent = document["agent"]
        expected = {
            "skills": (need.agent, "1.1.2"),
            "tools": ("roamie-travel-search", "1.1.0"),
            "mcpServers": ("roamie-travel-mcp", "1.1.0"),
        }
        if document.get("unresolved"):
            raise ValueError("registry dependencies unresolved")
        self._identity(agent, f"{need.agent}-agent", "1.1.2")
        for kind, (name, version) in expected.items():
            entries = document["resolved"][kind]
            if len(entries) != 1:
                raise ValueError("unexpected registry dependency count")
            self._identity(entries[0], name, version)
        endpoint = agent["spec"]["a2a"]["url"]
        if endpoint != f"{self._gateway}/a2a/v1/{need.agent}":
            raise ValueError("registry endpoint outside permitted gateway route")
        return [
            AgentCard(agent=need.agent, audience=endpoint, version="1.1.2").model_dump(mode="json")
        ]

    @staticmethod
    def _identity(document: Any, name: str, version: str) -> None:
        metadata = document["metadata"]
        if (
            metadata["name"] != name
            or metadata["namespace"] != "roamie"
            or metadata["tag"] != version
        ):
            raise ValueError("registry artifact identity mismatch")
