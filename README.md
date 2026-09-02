# Tesserix AI Agents

Deployable AI agents, built on `tesserix-adk` and routed only through the Solo
Agent Gateway. The ADK is not a declared dependency: it comes preinstalled in
`/opt/adk-venv` from `ghcr.io/tesserix/base-python-adk-3.14`, which pins the
newest reviewed ADK release, so build and CI both run against that one version.

Three services live here: the Kora agents (`kora_agents`), the SRE
investigator (`sre_agent`), and the global supervisor/orchestrator pair
(`orchestrator_agent`), which belongs to the `tesserix` tenant and to no product.
`docs/building-an-agent.md` is the step-by-step path the SRE agent took, for the
next one.

## Kora agents

The service publishes:

- `nutrition-coach`: bounded free-text nutrition guidance;
- `meal-planner`: validated structured meal plans of at most 62 days (two calendar months);
- `plan-supervisor`: an independent A2A review of planner drafts against the user's
  grounded health context, habits, constraints, and reviewed nutrition evidence.

Plans follow one explicit chain: meal planner draft → plan supervisor review →
Kora API validation → user approval. The supervisor's final structured plan,
not the planner draft, is the only plan that can become an approval card.

Every run is fixed to the `kora` tenant, has no tools by default, applies PII,
prompt-injection, and medical-safety guardrails, and enforces ADK token, call,
iteration, and wall-clock budgets. The gateway receives only non-sensitive
capability/context headers so ExtProc can choose RTK for CLI-like text and
Headroom for JSON, RAG, MCP, or conversation context.

## SRE investigator

`sre-investigator` investigates production symptoms in `tesseract-prod-in-gke`
with six read-only Kubernetes tools — pods, container states, logs, events and
deployments — and returns findings whose every claim cites the tool call that
produced it, with a hypothesis, a confidence and actions for a human to take.

It recommends and never acts. That is enforced three times over: no tool in the
package changes state, the ServiceAccount holds a read ClusterRole that excludes
Secrets, and tool output is treated as untrusted data, so a log line telling the
model to reveal a token is reported as a finding rather than obeyed. A claimed
incident without evidence fails validation instead of reaching a reader.

`POST /v1/investigations` serves internal callers; `POST /a2a/v1/sre-investigator`
serves the registry over A2A JSON-RPC. Both take a bearer key. Run its suite
with `uv run python -m sre_agent.evaluation evals/sre-investigator.yaml`, which
is the same gate the publish workflow applies before any manifest is sent.

## Supervisor and orchestrator

`supervisor` and `orchestrator` are product-agnostic coordination agents any
Tesserix service can reuse. Their skills are deliberately niche: the supervisor
only judges another agent's answer (approve, amend or reject, with a confidence
and a grounded summary), and the orchestrator only routes tasks across the
worker roster its operator configured — it never answers a task itself.

Information moves between agents under two rules. Every carried-forward answer
crosses in an `<untrusted-data>` envelope, so a worker's output is data for the
next worker, never instructions. And a supervised step's answer is passed on
only after the supervisor approves it; a rejection stops the pipeline with the
verdict in the report. Each run is bounded by the ADK delegation ledger
(step-count ceilings, depth 1) and a monotonic wall-clock deadline.

The task contract is JSON with three shapes — `status` probes every worker,
`delegate` sends one prompt to one named worker, `pipeline` runs up to twelve
supervised steps. `docs/agents/supervisor.md` and `docs/agents/orchestrator.md`
are the definition documents other agents and operators read. An optional
Temporal worker (`python -m orchestrator_agent.worker`) runs the same
orchestrations durably on the `orchestrations` task queue.

## Configuration

All settings use the `KORA_AGENTS_` prefix:

| Variable | Purpose |
| --- | --- |
| `KORA_AGENTS_API_KEY` | Bearer key for the internal run and A2A endpoints |
| `KORA_AGENTS_GATEWAY_API_KEY` | Bearer key for `kora-ai` |
| `KORA_AGENTS_GATEWAY_BASE_URL` | OpenAI-compatible gateway URL |
| `KORA_AGENTS_GATEWAY_MODEL` | Gateway routing model name (`kora-auto`) |

The investigator uses the `SRE_AGENT_` prefix:

| Variable | Purpose |
| --- | --- |
| `SRE_AGENT_API_KEY` | Bearer key for the investigation and A2A endpoints |
| `SRE_AGENT_GATEWAY_API_KEY` | Workload credential presented to the shared model gateway |
| `SRE_AGENT_GATEWAY_BASE_URL` | OpenAI-compatible shared model-gateway URL |
| `SRE_AGENT_GATEWAY_MODEL` | Reviewed Vertex model name (`gemini-2.5-flash`) |
| `SRE_AGENT_CLUSTER_URL` | Kubernetes API server, `https://kubernetes.default.svc` in cluster |
| `SRE_AGENT_CLUSTER_TOKEN_PATH` | Mounted ServiceAccount token |
| `SRE_AGENT_CLUSTER_CA_PATH` | Mounted cluster CA |
| `SRE_AGENT_NAMESPACES` | Optional JSON array narrowing what the agent may read |

The orchestrator uses the `ORCHESTRATOR_` prefix:

| Variable | Purpose |
| --- | --- |
| `ORCHESTRATOR_API_KEY` | Bearer key for the orchestration, supervision and A2A endpoints |
| `ORCHESTRATOR_WORKER_API_KEY` | Bearer key presented to every worker A2A endpoint |
| `ORCHESTRATOR_GATEWAY_API_KEY` | Workload credential presented to the shared model gateway |
| `ORCHESTRATOR_GATEWAY_BASE_URL` | OpenAI-compatible shared model-gateway URL |
| `ORCHESTRATOR_GATEWAY_MODEL` | Reviewed Vertex model name (`gemini-2.5-flash`) |
| `ORCHESTRATOR_WORKERS` | JSON array of `{name, url, probe}` worker endpoints |
| `ORCHESTRATOR_TEMPORAL_ADDRESS` | Temporal frontend; empty disables the durable worker |
| `ORCHESTRATOR_TEMPORAL_NAMESPACE` | Temporal namespace (`default`) |
| `ORCHESTRATOR_TEMPORAL_TASK_QUEUE` | Durable orchestration queue (`orchestrations`) |

Raw keys belong in GitHub Actions or GCP Secret Manager, never in Git. Registry
publishing uses separate `AGENTIC_REGISTRY_DEPLOY_KEY` (Kora) and
`AGENTIC_REGISTRY_SRE_DEPLOY_KEY` (Tesserix SRE) credentials. The Registry
stores only their SHA-256 digests and limits each to its own tenant. The publish
workflow sends the selected opaque key in `X-Agentic-Registry-Deploy-Key`;
`Authorization` is reserved for JWTs validated by the service mesh.

## Development

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src/
uv run pytest --cov --cov-fail-under=90
uv build
```

Run the reviewed suites end to end through Agent Gateway and Vertex with a
short-lived Firebase user token. Secrets are read only from the environment:

```bash
KORA_EVAL_GATEWAY_ORIGIN=https://gateway.example \
KORA_EVAL_GATEWAY_API_KEY=... \
KORA_EVAL_END_USER_TOKEN=... \
uv run python scripts/run_evals.py
```

The containers run as UID/GID 10001 with a read-only-compatible filesystem.
`kora-runtime` publishes `ghcr.io/tesserix/ai-agents`; `sre-runtime` publishes
`ghcr.io/tesserix/ai-agents-sre`; `orchestrator-runtime` publishes
`ghcr.io/tesserix/ai-agents-orchestrator`. Each image has its own fixed ASGI entrypoint,
so deployment configuration cannot accidentally boot the other service.
Kubernetes deployment is owned by `tesserix-k8s`; this repository publishes
the images and Agentic Registry manifests, but makes no imperative cluster
change.
