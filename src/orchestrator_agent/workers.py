"""Calling registered worker agents over A2A JSON-RPC.

The orchestrator holds one credential for all workers and reaches them only through the
URLs its operator configured. A worker's reply is parsed for text and state and nothing
else; whatever else the worker sent never becomes part of this process's behaviour.
"""

from __future__ import annotations

import secrets
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from orchestrator_agent.config import WorkerEndpoint


class WorkerCallError(Exception):
    """The worker could not be reached or did not answer the protocol."""

    def __init__(self, worker: str, reason: str) -> None:
        self.worker = worker
        self.reason = reason
        super().__init__(f"worker {worker!r} call failed: {reason}")


class WorkerReply(BaseModel):
    """What one delegated A2A call produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    worker: str
    run_id: str = ""
    state: str = "completed"
    text: str = ""


class A2AWorkerClient:
    """One HTTP client and one bearer credential shared by every worker call."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client if client is not None else httpx.AsyncClient(timeout=timeout)
        self._api_key = api_key

    async def send(self, worker: WorkerEndpoint, prompt: str) -> WorkerReply:
        payload = {
            "jsonrpc": "2.0",
            "id": f"orch_{secrets.token_hex(8)}",
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": prompt}]}},
        }
        try:
            response = await self._client.post(
                worker.url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as error:
            raise WorkerCallError(worker.name, f"http_{error.response.status_code}") from error
        except httpx.HTTPError as error:
            raise WorkerCallError(worker.name, type(error).__name__) from error
        except ValueError as error:
            raise WorkerCallError(worker.name, "invalid_json") from error
        return _reply(worker.name, body)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _reply(worker: str, body: object) -> WorkerReply:
    if not isinstance(body, dict):
        raise WorkerCallError(worker, "invalid_envelope")
    if "error" in body:
        error = body["error"]
        code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
        raise WorkerCallError(worker, f"jsonrpc_error_{code}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise WorkerCallError(worker, "missing_result")
    status = result.get("status")
    state = status.get("state", "completed") if isinstance(status, dict) else "completed"
    return WorkerReply(
        worker=worker,
        run_id=str(result.get("id", "")),
        state=str(state),
        text=_text(result),
    )


def _text(result: dict[str, Any]) -> str:
    pieces: list[str] = []
    for artifact in result.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        for part in artifact.get("parts") or []:
            if isinstance(part, dict) and part.get("kind") == "text":
                pieces.append(str(part.get("text", "")))
    return "\n".join(piece for piece in pieces if piece)
