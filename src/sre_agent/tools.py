"""The tools the investigator may call. All read-only, all bounded, all typed.

Each tool is one function: `@tool` derives the schema the model reads from the signature
and the docstring, so what the model is told and what the code accepts cannot drift apart.
The cluster the tools read is wired in by the service at startup rather than passed as an
argument — a model choosing which cluster to read would be a model choosing a blast radius.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from tesserix_adk.tools import Tool, ToolFailure, ToolRegistry, tool

from sre_agent.cluster import (
    MAX_ITEMS,
    MAX_LOG_LINES,
    DeploymentSummary,
    EventSummary,
    KubernetesReader,
    PodLogs,
    PodSummary,
)

_READ_TIMEOUT = 20.0
"""How long one cluster read may take before the run is told it failed."""

type _Namespace = Annotated[
    str,
    Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$",
    ),
]
type _ResourceName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=253,
        pattern=r"^[a-z0-9](?:[-.a-z0-9]*[a-z0-9])?$",
    ),
]
type _ContainerName = Annotated[
    str,
    Field(
        max_length=63,
        pattern=r"^(?:[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)?$",
    ),
]
type _LabelSelector = Annotated[str, Field(max_length=1_024)]
type _ItemLimit = Annotated[int, Field(ge=1, le=MAX_ITEMS)]
type _LogTail = Annotated[int, Field(ge=1, le=MAX_LOG_LINES)]

_cluster: KubernetesReader | None = None


def use_cluster(reader: KubernetesReader | None) -> None:
    """Point every tool at `reader`, or at nothing when the service shuts down."""
    global _cluster
    _cluster = reader


def cluster(tool_name: str) -> KubernetesReader:
    """The wired cluster, or a failure saying so rather than a guess."""
    if _cluster is None:
        raise ToolFailure(
            tool_name,
            "cluster_not_configured",
            detail="The agent has no cluster connection; the service was not wired.",
        )
    return _cluster


@tool(idempotency="read_only", timeout=_READ_TIMEOUT)
async def list_pods(
    namespace: _Namespace, label_selector: _LabelSelector = "", limit: _ItemLimit = 20
) -> tuple[PodSummary, ...]:
    """List the pods in a namespace with their phase, restarts and container state.

    Args:
        namespace: The Kubernetes namespace to read, for example "marketplace".
        label_selector: An optional selector such as "app=marketplace-order-service".
        limit: How many pods to return, at most 50.
    """
    return await cluster("list_pods").list_pods(
        namespace,
        label_selector=label_selector or None,
        limit=min(limit, MAX_ITEMS),
    )


@tool(idempotency="read_only", timeout=_READ_TIMEOUT)
async def get_pod(namespace: _Namespace, name: _ResourceName) -> PodSummary:
    """Read one pod, including why each container is in the state it is in.

    Args:
        namespace: The Kubernetes namespace the pod is in.
        name: The pod's full name, as returned by list_pods.
    """
    return await cluster("get_pod").get_pod(namespace, name)


@tool(idempotency="read_only", timeout=_READ_TIMEOUT)
async def get_pod_logs(
    namespace: _Namespace,
    name: _ResourceName,
    container: _ContainerName = "",
    tail_lines: _LogTail = 100,
    previous: bool = False,
) -> PodLogs:
    """Read the tail of a container's log, or of the instance that died before it.

    Args:
        namespace: The Kubernetes namespace the pod is in.
        name: The pod's full name.
        container: Which container to read, needed only where the pod has several.
        tail_lines: How many lines from the end to return, at most 200.
        previous: Read the previous instance's log instead, which is where a crash
            loop's cause is recorded.
    """
    return await cluster("get_pod_logs").pod_logs(
        namespace,
        name,
        container=container or None,
        tail_lines=min(tail_lines, MAX_LOG_LINES),
        previous=previous,
    )


@tool(idempotency="read_only", timeout=_READ_TIMEOUT)
async def list_events(
    namespace: _Namespace, warnings_only: bool = False, limit: _ItemLimit = 30
) -> tuple[EventSummary, ...]:
    """List recent Kubernetes events in a namespace, newest state first.

    Args:
        namespace: The Kubernetes namespace to read.
        warnings_only: Return only warning events, which is usually what an
            investigation wants.
        limit: How many events to return, at most 50.
    """
    return await cluster("list_events").list_events(
        namespace, warnings_only=warnings_only, limit=min(limit, MAX_ITEMS)
    )


@tool(idempotency="read_only", timeout=_READ_TIMEOUT)
async def list_deployments(
    namespace: _Namespace, limit: _ItemLimit = 30
) -> tuple[DeploymentSummary, ...]:
    """List the deployments in a namespace with desired against ready replicas.

    Args:
        namespace: The Kubernetes namespace to read.
        limit: How many deployments to return, at most 50.
    """
    return await cluster("list_deployments").list_deployments(
        namespace, limit=min(limit, MAX_ITEMS)
    )


@tool(idempotency="read_only", timeout=_READ_TIMEOUT)
async def get_deployment(namespace: _Namespace, name: _ResourceName) -> DeploymentSummary:
    """Read one deployment's rollout state, images and status conditions.

    Args:
        namespace: The Kubernetes namespace the deployment is in.
        name: The deployment's name.
    """
    return await cluster("get_deployment").get_deployment(namespace, name)


TOOLS: tuple[Tool[Any, Any], ...] = (
    list_pods,
    get_pod,
    get_pod_logs,
    list_events,
    list_deployments,
    get_deployment,
)


def registry() -> ToolRegistry:
    """A registry holding every tool, for an agent to be given a view of."""
    built = ToolRegistry()
    for each in TOOLS:
        built.register(each, origin="sre_agent.tools")
    return built
