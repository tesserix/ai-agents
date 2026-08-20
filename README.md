# Kora AI Agents

Kora's deployable AI agents, built on `tesserix-adk` and routed only through the
Solo Agent Gateway. The ADK is not a declared dependency: it comes preinstalled
in `/opt/adk-venv` from `ghcr.io/tesserix/base-python-adk-3.13`, which pins the
newest reviewed ADK release, so build and CI both run against that one version.
The service currently publishes:

- `nutrition-coach`: bounded free-text nutrition guidance;
- `meal-planner`: validated structured meal plans of at most seven days.

Every run is fixed to the `kora` tenant, has no tools by default, applies PII,
prompt-injection, and medical-safety guardrails, and enforces ADK token, call,
iteration, and wall-clock budgets. The gateway receives only non-sensitive
capability/context headers so ExtProc can choose RTK for CLI-like text and
Headroom for JSON, RAG, MCP, or conversation context.

## Configuration

All settings use the `KORA_AGENTS_` prefix:

| Variable | Purpose |
| --- | --- |
| `KORA_AGENTS_API_KEY` | Bearer key for the internal run and A2A endpoints |
| `KORA_AGENTS_GATEWAY_API_KEY` | Bearer key for `kora-ai` |
| `KORA_AGENTS_GATEWAY_BASE_URL` | OpenAI-compatible gateway URL |
| `KORA_AGENTS_GATEWAY_MODEL` | Gateway routing model name (`kora-auto`) |

Raw keys belong in GitHub Actions or GCP Secret Manager, never in Git. Registry
publishing uses `AGENTIC_REGISTRY_DEPLOY_KEY`; the registry stores only its
SHA-256 digest and limits it to the `kora` tenant. The publish workflow sends
the opaque key in `X-Agentic-Registry-Deploy-Key`; `Authorization` is reserved
for JWTs validated by the service mesh.

## Development

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src/
uv run pytest --cov --cov-fail-under=90
uv build
```

The container runs as UID/GID 65532 with a read-only-compatible filesystem.
Kubernetes deployment is owned by `tesserix-k8s`; this repository publishes the
image and the Agentic Registry manifests, but makes no imperative cluster change.
