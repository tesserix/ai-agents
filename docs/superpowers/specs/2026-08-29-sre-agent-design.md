# SRE Agent — Design

Status: approved for implementation (2026-08-29)

## Purpose

Give the platform an agent that investigates production problems in the
`tesseract-prod-in-gke` cluster and reports what it found, so that an on-call
human starts from evidence and a hypothesis rather than from a blank kubectl
prompt. It is the first Tesserix agent that uses tools; the three Kora agents
that share this repo are tool-less and gateway-only.

The agent recommends. It never acts: no write tool exists in the codebase, and
its Kubernetes credentials cannot mutate anything.

## Scope

In scope for v1:

- read-only Kubernetes, ArgoCD and Kargo tools;
- one investigation agent published to the Agentic Registry and reachable over
  A2A, so DevAI's SRE Studio and the `devai-sre` scan loop can call it;
- vector recall of past incidents from Qdrant, and write-back on close;
- two Slack feeds — deployment status, and alerts with investigation summaries;
- an offline evaluation suite that gates every publish.

Out of scope for v1, in the order we expect to want them:

1. Temporal workflows for the incident lifecycle (wait-and-recheck, escalation).
2. Write tools behind a human approval checkpoint.
3. Migration of DevAI's seven SRE agents onto ADK definitions.
4. Authoring this agent's configuration from SRE Studio.
5. A curation policy for what earns a place in long-term memory.

## Runtime model

Three different lifetimes, and confusing them is the main way this design could
go wrong:

| Thing | Lifetime | Where |
|---|---|---|
| The service | always on, stateless | Deployment (warm pool), restartable at any moment with no loss |
| A run | one trigger, then gone | in-process, bounded by ADK budgets |
| State | durable | Postgres and Qdrant, never the pod |

A warm Deployment rather than a Kubernetes Job per run: SRE Studio chat needs a
sub-second start, and one small always-on pod costs less than Job churn every
five minutes. The Job-per-run model in the platform design remains the right
answer for long batch work, and nothing here forecloses it — the runtime is the
same ADK either way.

### Triggers

| Trigger | Source | Produces |
|---|---|---|
| On demand | A2A call from SRE Studio or `devai-sre` | investigation findings, incident row |
| Scheduled | the existing `devai-sre` five-minute scan | proactive sweep, alerts on anomalies |
| Event | ArgoCD and Kargo webhooks, Kubernetes warning events | deployment feed, and investigations above a threshold |

Watchers and thresholds are ordinary deterministic code. The model is invoked
only once there is something to reason about; it never drives the sensitive or
high-volume loop.

### Event fabric

NATS JetStream, which the platform already runs for DevAI. Watchers publish to
`sre.deploy.argocd.*`, `sre.deploy.kargo.*`, `sre.events.k8s.*` and
`sre.findings.*`; independent durable consumers handle Slack deployment posts,
Slack alerts, and the decision to start an investigation. Decoupling them means
a pod restart mid-deploy loses no event, and a fourth reaction later is a new
subscriber rather than an edit to the watcher.

### Memory

| Layer | Store | Why |
|---|---|---|
| Working context | in-process | dies with the run, by design |
| Incidents, runs, deployment history, audit | Postgres `sre_*` tables | relational facts SRE Studio already lists; a vector store is the wrong shape for "open incidents" |
| Past incidents and their resolutions | Qdrant, `sre_memories` collection | retrieval by similarity: a new incident recalls older ones that looked like it |

Qdrant runs in the `ai-database` namespace as a three-node cluster with retained
per-node volumes. A dedicated collection rather than DevAI's `devai_memories`
keeps retention and erasure policies per domain.

The loop that makes this worth having: investigation recalls similar past
incidents before forming a hypothesis, and writes the resolved incident back on
close. Postgres holds the record; Qdrant holds the experience.

## Interfaces

### Tools

Every tool is typed, `read_only`, and returns a bounded payload.

| Group | Tools |
|---|---|
| Kubernetes | `list_pods`, `get_pod`, `get_pod_logs`, `list_events`, `list_deployments`, `get_deployment` |
| ArgoCD | `list_applications`, `get_application` |
| Kargo | `list_stages`, `get_promotions` |
| Memory | `recall_similar_incidents` |

Read-only is enforced three times over: no write tool exists, the ServiceAccount
holds a read ClusterRole that excludes Secrets, and tool results are treated as
untrusted input so a log line instructing the model to exfiltrate a key is data,
not an instruction.

### Agent

One agent, `sre-investigator`, with structured output: symptoms, evidence items
each citing the tool call that produced them, a root-cause hypothesis with a
confidence, recommended actions for a human to take, and the affected apps.
Budgets are tool-shaped rather than copied from the Kora agents: roughly eight
iterations, fifteen tool calls, ninety seconds. Guardrails: prompt injection and
PII.

Why one agent and not seven: DevAI's existing specialists earn their separation
from the scan pipeline they sit in, not from distinct reasoning. Proving one
investigator end to end tells us which splits are real before we encode them.

### Publishing

A registry manifest under `registry/`, published to the Agentic Registry, with
the A2A URL routed through the Solo Agent Gateway — the same path the Kora
agents take. Evaluations gate the publish.

## Evaluations

Offline, with scripted providers and recorded tool results, so a change in the
real cluster cannot make a test flap:

- a crashloop case that must call `list_pods` then `get_pod_logs`, must reach
  the database-timeout evidence, and must not claim a cause the evidence does
  not support;
- a healthy-cluster case that must decline to invent an incident;
- injection cases where tool output tells the agent to reveal credentials or
  call a tool that does not exist;
- trajectory and budget limits, and structured-output validation.

Security cases pass at 100% or the publish fails. Quality cases carry
thresholds and a regression limit against the current published version.

## Platform prerequisites

- `base-python-adk-3.14` — the ADK develops and releases on CPython 3.14, and
  new services start there rather than a minor behind. Added in
  `base-docker-images` PR #24.
- A read-only Kubernetes ServiceAccount and ClusterRole, owned by
  `tesserix-k8s`.
- Read-only ArgoCD and Kargo API tokens, and a Slack bot token, from GCP Secret
  Manager.
- Two Slack channels, created by the platform owner: deployments and alerts.

## Build order

1. Tools, with unit tests against recorded fixtures.
2. The agent definition and its structured output.
3. The evaluation suite, green.
4. The A2A service surface.
5. The registry manifest, and publish.
6. NATS consumers and the two Slack feeds.
7. The step-by-step guide for building the next agent — the durable output of
   doing this once.
8. The deployment chart, handed to the platform owner to apply.
