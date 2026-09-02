# Orchestrator — agent definition

Global, product-agnostic orchestration. The orchestrator belongs to the `tesserix`
tenant and is reusable by any product: which agents it can reach is configuration
(`ORCHESTRATOR_WORKERS`, a JSON roster of registered A2A endpoints), never code, so a
product adopts it by pointing it at its own published agents.

It follows the pattern the large multi-agent systems converge on (a coordinator that
routes and verifies, workers that answer): the orchestrator never answers a task
itself, every worker answer crosses back as labelled untrusted data, and a supervisor
verdict gates each hand-off before anything is passed on.

## Task contract

Callers send one JSON object:

```json
{"task": "status"}
{"task": "delegate", "agent": "nutrition-coach", "prompt": "…", "supervise": true}
{"task": "pipeline", "context": "…", "steps": [
  {"agent": "meal-planner", "prompt": "…"},
  {"agent": "plan-supervisor", "prompt": "…", "carry_forward": true, "supervise": true}
]}
```

Anything else is refused with `unsupported_task` — the orchestrator does not guess.
The reply is an `OrchestrationReport`: run id, per-step reports (worker run id, state,
supervisor verdict) and the final approved output.

## Skills

- **orchestrate-agent-pipeline** — ordered delegated steps; previous answers travel
  only inside an `<untrusted-data>` envelope; the pipeline halts at the first failed,
  refused or rejected step.
- **delegate-single-task** — one task to one named worker, supervised by default,
  fully traceable to the worker's own run id.
- **probe-agent-fleet** — every configured worker answers its declared probe, giving
  one grounded view of fleet health.

## Ceilings

Each run is bounded by the ADK delegation ledger (`max_steps` delegations, depth 1,
no delegation to itself) and a wall-clock deadline (`run_timeout_seconds`); a step
past either ceiling is reported `refused`, not silently dropped. Worker calls carry
one shared bearer credential and a per-step timeout.

## How information moves

1. The caller's `context` is prepended to each step prompt as a trusted `CONTEXT` block.
2. A worker's answer is never merged into trusted text: the next step receives it as
   `PREVIOUS STEP OUTPUT` inside an `<untrusted-data source="delegated_agent">` envelope.
3. When `supervise` is set (the default), the supervisor judges the answer against the
   step's prompt; a `reject` verdict halts the run and the answer is discarded.

The deterministic behaviour contract lives in `evals/orchestrator.yaml` and
`tests/test_orchestration.py`. The durable path (`OrchestrationWorkflow` on Temporal
task queue `orchestrations`) wraps the same runtime; see
`src/orchestrator_agent/durable.py`.
