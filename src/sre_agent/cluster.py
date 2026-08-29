"""Read-only reads of the Kubernetes API, summarised to what a run can afford to read.

The agent investigates and never acts, so nothing here issues a verb other than GET. That
is the innermost of three guards: the ServiceAccount is bound to a read ClusterRole that
excludes Secrets, and no write tool exists for the model to reach for.

Raw Kubernetes objects are far larger than the evidence they carry — a pod is kilobytes of
managed fields around one waiting reason. Every read is projected onto a small model, so a
run spends its budget on reasoning rather than on `metadata.annotations`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Collection, Mapping
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict
from tesserix_adk.tools import ToolErrorMap, ToolRefusal, permanent, refusal, transient

MAX_ITEMS = 50
"""How many objects one list read may return."""

MAX_LOG_LINES = 200
"""How many log lines one read may return."""

MAX_LINE_CHARS = 500
"""How much of one line, or one upstream message, reaches the model."""

_CORE = "/api/v1/namespaces/{namespace}"
_APPS = "/apis/apps/v1/namespaces/{namespace}"

_ERRORS = ToolErrorMap(
    {
        httpx.TimeoutException: transient(
            "cluster_timeout", message="The Kubernetes API did not answer in time."
        ),
        httpx.TransportError: transient(
            "cluster_unreachable", message="The Kubernetes API could not be reached."
        ),
    },
    statuses={
        401: permanent(
            "cluster_unauthenticated",
            message="The cluster rejected the agent's credentials.",
        ),
        403: refusal(
            "forbidden",
            "The agent's read-only role does not cover that object.",
        ),
        404: refusal("not_found", "No such object in that namespace."),
        429: transient("cluster_throttled", message="The Kubernetes API is rate limiting reads."),
        500: transient("cluster_error", message="The Kubernetes API failed to serve the read."),
        502: transient("cluster_error", message="The Kubernetes API failed to serve the read."),
        503: transient("cluster_error", message="The Kubernetes API is unavailable."),
        504: transient("cluster_error", message="The Kubernetes API timed out serving the read."),
    },
)


class _Bounded(BaseModel):
    """A payload the model reads: frozen, closed, and small enough to quote."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ContainerSummary(_Bounded):
    """One container's state, and why it is in it."""

    name: str
    image: str
    ready: bool
    restarts: int
    state: str
    reason: str = ""
    message: str = ""
    exit_code: int | None = None


class PodSummary(_Bounded):
    """A pod, as far as an investigation needs to see it."""

    name: str
    namespace: str
    phase: str
    node: str
    started_at: str
    ready: bool
    restarts: int
    containers: tuple[ContainerSummary, ...]


class PodLogs(_Bounded):
    """The tail of one container's log, and whether older lines were dropped."""

    pod: str
    container: str
    lines: tuple[str, ...]
    truncated: bool


class EventSummary(_Bounded):
    """One Kubernetes event, with the object it was recorded against."""

    type: str
    reason: str
    object: str
    message: str
    count: int
    last_seen: str


class ConditionSummary(_Bounded):
    """One status condition, which is usually where a rollout says what is wrong."""

    type: str
    status: str
    reason: str = ""
    message: str = ""


class DeploymentSummary(_Bounded):
    """A deployment's rollout state: what was asked for against what is running."""

    name: str
    namespace: str
    desired: int
    ready: int
    updated: int
    available: int
    generation: int
    observed_generation: int
    images: tuple[str, ...]
    conditions: tuple[ConditionSummary, ...]


class _ServiceAccountAuth(httpx.Auth):
    """Load the projected token for every request so Kubernetes may rotate it."""

    def __init__(self, token_path: Path) -> None:
        self._token_path = token_path

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await asyncio.to_thread(self._read_token)
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request

    def _read_token(self) -> str:
        try:
            return self._token_path.read_text().strip() if self._token_path.is_file() else ""
        except FileNotFoundError:
            return ""


def cluster_client(
    url: str,
    *,
    token_path: str | Path | None,
    ca_path: str | Path | None,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """A client for one cluster, carrying the ServiceAccount token and the cluster's CA.

    A token or CA that is not mounted is left off rather than faked, which is what a local
    run against a proxied API server needs.
    """
    token_file = Path(token_path) if token_path else None
    verify: str | bool = str(ca_path) if ca_path and Path(ca_path).is_file() else True
    return httpx.AsyncClient(
        base_url=url,
        auth=_ServiceAccountAuth(token_file) if token_file else None,
        verify=verify,
        timeout=timeout,
        transport=transport,
    )


class KubernetesReader:
    """Reads one cluster, within an optional namespace allowlist.

    Args:
        client: The HTTP client, already carrying the ServiceAccount credentials and the
            cluster's CA. Owned by the caller, so one client serves every tool.
        namespaces: The namespaces the agent may read. Empty means every namespace the
            role already allows, which is the cluster's decision rather than this code's.
        max_items: The ceiling on any one list read.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        namespaces: Collection[str] = (),
        max_items: int = MAX_ITEMS,
    ) -> None:
        self._client = client
        self._namespaces = frozenset(namespaces)
        self._max_items = max_items

    async def list_pods(
        self,
        namespace: str,
        *,
        label_selector: str | None = None,
        field_selector: str | None = None,
        limit: int = MAX_ITEMS,
    ) -> tuple[PodSummary, ...]:
        """The pods in `namespace`, newest fields only, capped at `limit`."""
        payload = await self._json(
            "list_pods",
            namespace,
            f"{_CORE.format(namespace=namespace)}/pods",
            self._selectors(label_selector, field_selector, limit),
        )
        return tuple(_pod(item) for item in self._items(payload, limit))

    async def get_pod(self, namespace: str, name: str) -> PodSummary:
        """One pod, with each container's state and the reason it is in it."""
        payload = await self._json(
            "get_pod", namespace, f"{_CORE.format(namespace=namespace)}/pods/{name}", {}
        )
        return _pod(payload)

    async def pod_logs(
        self,
        namespace: str,
        name: str,
        *,
        container: str | None = None,
        tail_lines: int = MAX_LOG_LINES,
        previous: bool = False,
    ) -> PodLogs:
        """The tail of a container's log, or of the instance that died before this one."""
        tail = min(tail_lines, MAX_LOG_LINES)
        params: dict[str, str] = {"tailLines": str(tail), "timestamps": "false"}
        if container:
            params["container"] = container
        if previous:
            params["previous"] = "true"
        text = await self._text(
            "get_pod_logs",
            namespace,
            f"{_CORE.format(namespace=namespace)}/pods/{name}/log",
            params,
        )
        lines = [line for line in text.splitlines() if line]
        return PodLogs(
            pod=name,
            container=container or "",
            lines=tuple(_clipped(line) for line in lines[-tail:]),
            truncated=len(lines) > tail,
        )

    async def list_events(
        self,
        namespace: str,
        *,
        warnings_only: bool = False,
        limit: int = MAX_ITEMS,
    ) -> tuple[EventSummary, ...]:
        """Recent events in `namespace`, optionally only the warnings."""
        payload = await self._json(
            "list_events",
            namespace,
            f"{_CORE.format(namespace=namespace)}/events",
            self._selectors(None, "type=Warning" if warnings_only else None, limit),
        )
        return tuple(_event(item) for item in self._items(payload, limit))

    async def list_deployments(
        self, namespace: str, *, limit: int = MAX_ITEMS
    ) -> tuple[DeploymentSummary, ...]:
        """The deployments in `namespace`, with their rollout state."""
        payload = await self._json(
            "list_deployments",
            namespace,
            f"{_APPS.format(namespace=namespace)}/deployments",
            self._selectors(None, None, limit),
        )
        return tuple(_deployment(item) for item in self._items(payload, limit))

    async def get_deployment(self, namespace: str, name: str) -> DeploymentSummary:
        """One deployment: what was asked for, what is running, and what it says about it."""
        payload = await self._json(
            "get_deployment",
            namespace,
            f"{_APPS.format(namespace=namespace)}/deployments/{name}",
            {},
        )
        return _deployment(payload)

    def _selectors(
        self, label_selector: str | None, field_selector: str | None, limit: int
    ) -> dict[str, str]:
        params = {"limit": str(min(limit, self._max_items))}
        if label_selector:
            params["labelSelector"] = label_selector
        if field_selector:
            params["fieldSelector"] = field_selector
        return params

    def _items(self, payload: Mapping[str, Any], limit: int) -> list[Mapping[str, Any]]:
        items = payload.get("items") or []
        return list(items)[: min(limit, self._max_items)]

    async def _json(
        self, tool: str, namespace: str, path: str, params: Mapping[str, str]
    ) -> Mapping[str, Any]:
        response = await self._read(tool, namespace, path, params)
        return dict(response.json())

    async def _text(self, tool: str, namespace: str, path: str, params: Mapping[str, str]) -> str:
        return (await self._read(tool, namespace, path, params)).text

    async def _read(
        self, tool: str, namespace: str, path: str, params: Mapping[str, str]
    ) -> httpx.Response:
        """One GET, with the namespace guard ahead of it and the taxonomy behind it."""
        self._permitted(tool, namespace)
        try:
            response = await self._client.get(path, params=dict(params))
            if response.status_code >= 400:
                raise _ApiStatus(response.status_code, _status_message(response))
        except Exception as failure:
            raise _ERRORS.classify(failure, tool=tool) from failure
        return response

    def _permitted(self, tool: str, namespace: str) -> None:
        if self._namespaces and namespace not in self._namespaces:
            raise ToolRefusal(
                tool,
                "namespace_not_permitted",
                f"The agent may only read: {', '.join(sorted(self._namespaces))}.",
            )


class _ApiStatus(Exception):
    """A Kubernetes `Status` reply, carrying the code the error map classifies on."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


def _status_message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message", ""))
    except ValueError:
        return ""


def _clipped(text: str, limit: int = MAX_LINE_CHARS) -> str:
    """Text as the model will see it: bounded, and honest about being cut."""
    text = text.strip()
    return text if len(text) <= limit else f"{text[:limit]}…"


def _pod(item: Mapping[str, Any]) -> PodSummary:
    metadata = item.get("metadata", {})
    status = item.get("status", {})
    containers = tuple(_container(each) for each in status.get("containerStatuses") or ())
    return PodSummary(
        name=metadata.get("name", ""),
        namespace=metadata.get("namespace", ""),
        phase=status.get("phase", ""),
        node=item.get("spec", {}).get("nodeName", ""),
        started_at=status.get("startTime", ""),
        ready=all(container.ready for container in containers) if containers else False,
        restarts=sum(container.restarts for container in containers),
        containers=containers,
    )


def _container(item: Mapping[str, Any]) -> ContainerSummary:
    state, detail = _container_state(item.get("state") or {})
    terminated = (item.get("lastState") or {}).get("terminated") or {}
    if state == "terminated":
        terminated = detail
    return ContainerSummary(
        name=item.get("name", ""),
        image=item.get("image", ""),
        ready=bool(item.get("ready")),
        restarts=int(item.get("restartCount", 0)),
        state=state,
        reason=str(detail.get("reason", "")),
        message=_clipped(str(detail.get("message", ""))),
        exit_code=terminated.get("exitCode"),
    )


def _container_state(state: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """Which of the mutually exclusive states a container is in, and what it says."""
    for name in ("waiting", "running", "terminated"):
        if (detail := state.get(name)) is not None:
            return name, detail
    return "unknown", {}


def _event(item: Mapping[str, Any]) -> EventSummary:
    involved = item.get("involvedObject", {})
    return EventSummary(
        type=item.get("type", ""),
        reason=item.get("reason", ""),
        object=f"{involved.get('kind', '')}/{involved.get('name', '')}",
        message=_clipped(str(item.get("message", ""))),
        count=int(item.get("count", 1)),
        last_seen=item.get("lastTimestamp") or item.get("eventTime") or "",
    )


def _deployment(item: Mapping[str, Any]) -> DeploymentSummary:
    metadata = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    containers = spec.get("template", {}).get("spec", {}).get("containers") or ()
    return DeploymentSummary(
        name=metadata.get("name", ""),
        namespace=metadata.get("namespace", ""),
        desired=int(spec.get("replicas", 0)),
        ready=int(status.get("readyReplicas", 0)),
        updated=int(status.get("updatedReplicas", 0)),
        available=int(status.get("availableReplicas", 0)),
        generation=int(metadata.get("generation", 0)),
        observed_generation=int(status.get("observedGeneration", 0)),
        images=tuple(container.get("image", "") for container in containers),
        conditions=tuple(_condition(each) for each in status.get("conditions") or ()),
    )


def _condition(item: Mapping[str, Any]) -> ConditionSummary:
    return ConditionSummary(
        type=item.get("type", ""),
        status=item.get("status", ""),
        reason=str(item.get("reason", "")),
        message=_clipped(str(item.get("message", ""))),
    )
