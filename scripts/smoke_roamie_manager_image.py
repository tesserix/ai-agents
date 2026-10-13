import asyncio
import bz2
import json
import lzma
import os
import shutil
import sqlite3
import ssl
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr
from tesserix_adk.testing import ScriptedProvider

from orchestrator_agent.supervision import SupervisorService
from orchestrator_agent.workers import A2AWorkerClient
from roamie_agents.api import Source
from roamie_agents.manager import PersonalTripManager, Profile
from roamie_agents.manager_api import create_manager_app


def check_runtime() -> None:
    if os.getuid() != 10001:
        raise RuntimeError("manager must run as UID 10001")
    forbidden = [
        name for name in ("sh", "bash", "curl", "mount", "perl", "apt", "pip") if shutil.which(name)
    ]
    if forbidden:
        raise RuntimeError(f"unexpected runtime tools: {forbidden}")
    if Path("/usr/local/lib/python3.14/ensurepip").exists():
        raise RuntimeError("bundled installer must be build-only")
    if not ssl.create_default_context().get_ca_certs():
        raise RuntimeError("TLS trust store is missing")
    raw = b"synthetic-runtime-probe"
    if bz2.decompress(bz2.compress(raw)) != raw or lzma.decompress(lzma.compress(raw)) != raw:
        raise RuntimeError("compression libraries are unavailable")
    with sqlite3.connect(":memory:") as database:
        if database.execute("select 42").fetchone() != (42,):
            raise RuntimeError("SQLite is unavailable")
    ZoneInfo("Australia/Melbourne")
    uuid.uuid4()
    private_key = Ed25519PrivateKey.generate()
    private_key.public_key().verify(private_key.sign(raw), raw)


async def main() -> None:
    async def sources(token: str) -> AsyncIterator[Source]:
        raise RuntimeError("unauthorized requests must not reach a source")
        yield

    async def authority(profile: Profile) -> str:
        raise RuntimeError("unauthorized requests must not reach profile authority")

    async with httpx.AsyncClient() as worker_http:
        manager = PersonalTripManager(
            workers={},
            client=A2AWorkerClient(api_key=SecretStr("a" * 32), timeout=5, client=worker_http),
            supervisor=SupervisorService(provider=ScriptedProvider()),
            delegation_key=SecretStr("d" * 32),
            identity_key=SecretStr("c" * 32),
        )
        app = create_manager_app(
            manager=manager,
            sources=sources,
            api_key=SecretStr("a" * 32),
            profile_authority=authority,
            profile_signing_key=SecretStr("b" * 32),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            statuses = []
            for headers in (
                {},
                {"Authorization": "Bearer synthetic-invalid"},
                {"X-Roamie-Gateway-Token": "synthetic-forgery"},
            ):
                response = await client.post("/v1/trip-manager", content=b"{}", headers=headers)
                statuses.append(response.status_code)
            if statuses != [401, 401, 401]:
                raise RuntimeError(f"authentication did not fail closed: {statuses}")
    print(
        json.dumps(
            {
                "uid": os.getuid(),
                "authentication_statuses": statuses,
                "tls_and_native_libraries": "passed",
                "unused_tools": "absent",
            }
        )
    )


check_runtime()
asyncio.run(main())
