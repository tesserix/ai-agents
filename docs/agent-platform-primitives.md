# Agent platform primitives

What each platform layer provides, where it lives, how the Roamie agents use it, and
where we stand against current agent-engineering practice (OWASP Agentic Security,
Anthropic's "Building effective agents" and context-engineering guidance, Google ADK
evaluation guidance). No single industry standard defines an agent package layout; the
target below is our own convention built on those practices.

Assessed 2026-10-12 against ADK v0.54.1, `agentic-registry` 19ea221 and this repository.

## 1. Layers and ownership

| Layer | Owns | Lives in |
|---|---|---|
| ADK runtime | Agent loop, schema validation, budgets, guardrails, approvals, checkpoints, credentials | `tesserix/agent-development-kit` (`tesserix_adk`) |
| Registry | Versioned agents, skills, tools, MCP servers; ownership; lifecycle | `tesserix/agentic-registry` |
| Gateway | Authentication, model/A2A/MCP routing, route-level authorisation | Solo agentgateway; resources in `registry/<product>/gateway` |
| Context manager | Token optimisation of model payloads (Headroom for JSON/RAG/MCP, RTK for CLI text) | Gateway ExtProc, selected by agent `metadata.context_kind` |
| Observability | One trace per ADK run, exported to Langfuse over OTLP | `src/agent_telemetry` |
| Agent packages | Mission, contracts, tools, evals per agent | `src/<agent>_agents`, `evals/`, `registry/`, `skills/` |

Rule: agents consume these through the interfaces below. An agent never implements its
own authentication, retries, budgets or token management.

## 2. ADK primitives

| Primitive | Module | What it guarantees | Roamie usage |
|---|---|---|---|
| `Agent`, `AgentDefinition.declared` | `core.agent` | Name, version, model alias, instructions, output type, owner, eval suite, tool allowlist | Every Roamie agent (`definitions.py`) |
| `BudgetLimits` | `core.budget` | Hard ceilings on input/output tokens, model calls, iterations, seconds | Every agent; manager raised to 32k input |
| `scopes`, `tool_scopes` | `core.agent` | A tool's scopes must be declared by the agent; caller authority is intersected | Not used: Roamie agents have no tools |
| `Principal`, `principal_scope` | `core` | Subject, tenant and scopes bound to the running task | Manager and worker scope every run to tenant `roamie` |
| `CredentialBroker`, `RunCredentials` | `tools`, `runtime.credentials` | Short-lived, audience- and scope-bound credentials minted per caller; refresh before expiry | Gateway client-credentials tokens for model and A2A calls |
| `InjectionGuard`, `screen` | `guardrails.injection`, `core.injection` | Flags instruction-like content in untrusted text | Every Roamie worker run |
| Output guardrails, `output_policy` | `guardrails.output`, `core.output_policy` | Validates model output before release | Roamie validates in its own code instead (section 4) |
| `ApprovalLedger` | `runtime.approvals` | Approval bound to the exact argument digest, spent once | Not needed yet: Roamie has no write actions |
| `AutonomyGrant`, `ActionRegistry`, `Ceiling` | `core.autonomy` | Per-tenant autonomy level, irreversible-action classes, spend ceilings | Not used |
| Checkpoints, suspension | `core.checkpoint`, `runtime.checkpoint`, `runtime.suspension` | Resume runs without repeating completed side effects | Not used: Roamie requests are stateless; the orchestrator uses Temporal |
| Idempotency, claim checks | `tools.claim_check`, `core.queue`, `core.activities` | Idempotency keys for writes; large payloads passed by reference | Not used |
| Sandbox | `tools.sandbox` | Isolated execution for code-running tools | Not used |
| Memory: scope, provenance, admission, erasure, compaction | `memory.*` | Tenant/run-scoped writes with provenance, retention and validated reuse | Not used: Roamie keeps no agent memory |
| Eval suite, dataset, judge, metrics | `evals.suite`, `evals.dataset`, `evals.judge` | Repeatable task-quality evaluation | Roamie runs YAML cases via `scripts/run_evals.py` |
| Eval gate | `evals.gate` (`GatePolicy`, `Tolerance`) | Per-metric regression tolerances, cost included, read by CI | Not wired for Roamie |
| Compression evals | `evals.compression` | Savings and task accuracy measured together per content type, against committed floors | Not wired for Roamie |
| Audit | `core.audit` | Records of runs and decisions | Via telemetry only |
| A2A and MCP adapters | `a2a`, `adapters.mcp` | Peer discovery and MCP sessions over HTTP | Manager↔worker A2A; MCP travel search |

## 3. Registry primitives

| Primitive | Behaviour | Roamie usage |
|---|---|---|
| Immutable tags | A published tag cannot change content (HTTP 409); new content needs a new tag | Shared Roamie skill tag, currently 1.1.7 |
| Content digest | Each artifact stores `sha256:<hex>` | All Roamie manifests |
| Lifecycle status | `active`, `deprecated`, `deleted` | Revocation path for a bad release |
| Tenant-scoped deploy keys | Publishing restricted per tenant | `AGENTIC_REGISTRY_ROAMIE_DEPLOY_KEY` |
| Skill, Agent, MCP server records | Agents pin skill, tool and MCP versions | `registry/roamie/*.yaml`, generated by `scripts/export_roamie_registry.py` |

## 4. Roamie-specific controls

| Control | Where | Purpose |
|---|---|---|
| Signed profile snapshot | `manager_api.py` | HMAC over exact bytes, 120-second expiry, bound to the delegated gateway token |
| Delegation envelopes | `delegation.py` | Signed request and response tied to tenant, manager identity, revision, specialist, digest and expiry |
| Manager-only specialists | Gateway policy `roamie.manager` role | Workers cannot be called directly |
| Two-stage independent review | `manager.py` | Request preflight and answer review by the supervisor model; confidence ≥ 0.8 |
| Deterministic evidence checks | `manager.py`, `runtime.py`, `planning.py` | Cited IDs, prices, budgets, dates, destinations and transit gaps verified in code, not by the model |
| Planning questions | `manager.require_planning_details` | Missing destination, dates, stays or budget return 422 questions before any search or model call |
| Profile revision checks | `manager.py` | A response is discarded if the profile changed during review |
| Bounded diagnostics | `diagnostics.py` | Allowlisted failure reasons; no prompts, profiles or credentials logged |

## 5. Comparison with the target practice

| Practice | Status | Evidence / gap |
|---|---|---|
| Versioned, reproducible agent release | Partial | Registry tags plus digests pin each release. Instructions live as text inside `definitions.py`; no `agent.yaml` or `AGENT.md` package |
| Runtime instructions separate from repo `AGENTS.md` | Met | Runtime instructions are in code; `AGENTS.md` serves coding assistants only |
| Explicit mission, scope, completion and failure rules | Partial | Present inside instruction strings; not in the seven-part structure |
| Executable, default-deny policy | Partial | Enforced in gateway JWT roles, signed envelopes and code checks. No policy-as-code bundle per agent |
| Authorisation per call, not per discovery | Partial | ADK `tool_scopes` supports it. Gateway policies are route-level; MCP tool-level rules are not defined |
| Permission intersection (user ∩ agent ∩ tenant ∩ task) | Met for Roamie | Subject and tenant come from authenticated context; manager narrows to profile constraints |
| Handoffs never widen privilege | Met | Workers inherit the manager principal restricted to specialist scope |
| Approvals bound to exact action | Available, unused | `ApprovalLedger` exists; Roamie has no write actions to gate |
| Secrets kept out of model context | Met | Credentials injected at call time; snapshots carry no keys |
| Prompt-injection defence that does not rely on the model | Met | `InjectionGuard`, evidence-only citations, deterministic validation |
| Runaway limits | Met | `BudgetLimits`, 60-second worker and 78-second manager deadlines, in-flight cap of 16 |
| Durable state, safe retries | N/A for Roamie | Stateless requests; ADK checkpoints and idempotency exist for stateful agents |
| Verified completion | Met | Worker output re-validated by the manager before release |
| Traceability | Met | Langfuse traces per run; run IDs and request IDs in responses |
| Context manager with protected instructions | Partial | Gateway optimiser compresses payloads. No declared profile of which context must never be compressed |
| Optimiser judged on cost per successful task | Available, unused | `evals.compression` measures this; not run for Roamie |
| Eval coverage | Gap | Two template cases per specialist. Missing: ambiguous, out-of-scope, injected evidence, wrong tenant, tool failure, duplicate request, long trip |
| Trajectory evaluation | Gap | No ADK primitive evaluates tool-call paths yet |
| Release gates in CI | Gap | `evals.gate` exists; Roamie CI does not use it |
| Threat model and runbook per agent | Gap | No `threat-model.md` or `runbook.md`; rollback is registry `deprecated` plus image pin |
| Canary and staged autonomy | Gap | Releases ship to all traffic; no canary and no autonomy levels |

## 6. Recommended order

1. ADK: a versioned `agent.yaml` schema, an explicit `AGENT.md` loader, `skills.lock`
   verification, and a scaffold plus CI check that rejects incomplete packages.
2. Roamie: move each of the nine agents into that package; add the seven-part
   `AGENT.md`, a per-agent policy file, a runbook and a threat model.
3. Evals: adversarial and failure cases per agent, `GatePolicy` in CI, and
   `evals.compression` runs with and without the optimiser.
4. Gateway: MCP tool-level authorisation rules, so discovery never implies permission.
