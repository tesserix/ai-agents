# Roamie travel agents

Tracking: https://github.com/tesserix/roamie/issues/94

Eight Tesserix ADK definitions: personal trip manager plus trip, food, routes,
activities, shopping, memories and currency-exchange specialists. Run the private
workers with `uvicorn roamie_agents.main:app --port 8080`, and the manager with
`uvicorn roamie_agents.manager_main:app --port 8080` in separate processes.
Images have separate `roamie-runtime` and `roamie-manager-runtime` Docker targets.

## Trust and request flow

Authenticated Roamie backend -> signed profile snapshot -> personal trip manager
-> pinned MCP evidence through the MCP gateway -> request AI review -> specialist
through A2A gateway -> deterministic evidence comparison -> response AI review
-> profile revision check -> response. Model calls use the model gateway.
Specialists expose only private A2A handlers. Gateway ACLs must make the manager
the only specialist caller; shared worker credentials alone do not establish this.

The manager's opaque identity is an HMAC of tenant, subject and trip. Allergies,
diets, budget, accessibility, dates and photo consent cannot be relaxed by a prompt. Model selections
are projected from provider facts; the model cannot create prices, routes or ETAs.
AI approval alone is insufficient: unsupported factual claims are rejected.

`POST /v1/trip-manager` requires workload Bearer authentication,
`X-Roamie-Gateway-Token` and `X-Roamie-Profile-Signature`. The signature is lowercase
HMAC-SHA256 of the exact request bytes. `ProfileSnapshot` is the canonical schema.
The backend must derive subject from authenticated identity, bind the gateway token
using its SHA256 digest, and set issued/expiry seconds within a 120-second window.
Never ship the signing key to mobile. The mobile app must discard a response whose
`profile_revision` differs from current local state. A snapshot is not a live
profile store: the API's revision callback currently checks the signed snapshot.

## Status and release gates

Implemented and locally tested: ADK definitions, A2A workers, dual AI review,
signed profile API, pinned MCP schema checks, deterministic facts and FX ranking,
evaluation fixtures, candidate internal manifests and disabled Helm configuration.
This is not a complete user-facing integration or a production deployment.

Required before activation:
- Deploy and smoke-test the implemented Rust signing bridge and mobile revision check.
- Verify provider disclosures for enforced accessibility, dates, allergies and preferences.
- Verify registry onboarding, route eligibility, tenant scopes and manager-only A2A ACLs.
- Connect routing/ETA, shopping offers, image generation and live shop quote providers.
- Add end-to-end tests against deployed gateways, API authentication and provider sandbox.
- Build images, pin real digests, provision secrets and explicitly approve production rollout.

Memories currently select consented evidence; image generation is not implemented.
Routes and shopping only return evidence supplied by an activated provider. No
provider availability or discount is invented. Klook remains unavailable pending a
partner contract. Booking Demand is a separate read-only adapter, not yet composed
into Roamie's `travel_search` evidence source.

FX comparison uses Decimal, explicit currency exponents, fees, amount bands and
cash rounding. Both reference and quote must be fresh (15 minutes), currency pairs
must match, and net received determines rank. Roamie's existing daily FX endpoint
is not a real-time shop quote source and cannot meet this freshness policy.

## Configuration and checks

See `roamie_agents.config.Settings` and `manager_main.ManagerSettings` for all
required environment fields. Use distinct randomly generated local-only keys for
development. Never commit keys. The chart references secret names only and remains
disabled; fake credentials must not enable live providers.

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src/
uv run pytest
uv run python scripts/export_roamie_contract.py
uv run python scripts/export_roamie_registry.py
uv build
```

`contracts/roamie-travel.json` contains bounded wire schemas and full validation
schemas. Pin the generated digest of input/output schemas, not a hand-written hash.
Copy the generated contract into the product MCP package when changing it and
recompile its manifest. Registry files are candidates with gateway export disabled.

## Continuation verification and Solo.io integration

The live GCP gateway was inspected on 2026-09-26. `agentgateway.tesserix.app`
redirects to HTTPS and Zitadel; its UI is agentgateway-console. Models route via
`ai-gateway` and MCP/A2A via `agentgateway-mcp`. No separate Roamie Gateway is needed.
Generated resources under `registry/roamie/gateway` passed Kubernetes server-side
dry-run admission. They are not installed. Import these through the Registry's
AgentGateway admin API, then let route-sync reconcile; do not kubectl apply them.

The model path is `/roamie/v1/chat/completions` on ai-gateway:8080. A2A uses exact
specialist paths on agentgateway-mcp:8082. JWT policies require `roamie.models` or
`roamie.manager`; runtime access also requires the existing platform runtime role.
The manager's WORKER_API_KEY must be its gateway JWT, not the worker upstream key.
Gateway injects the worker's separate upstream key from `roamie-agent-upstream`.
Short-lived JWT provisioning/refresh remains a release dependency; random strings
cannot pass gateway JWT validation. Model and MCP token fields have the same requirement.

Eight generated Skill records and SKILL.md files share the ADK definitions.
Manual Publish now has `publish_roamie` and requires a separate protected
`AGENTIC_REGISTRY_ROAMIE_DEPLOY_KEY`. Gateway resources are administrative records
and must not be published with this tenant key.

Roamie now contains authenticated POST /v1/trip-manager and a private workload-key
GET /internal/v1/travel/nearby. The signing bridge binds the authenticated subject,
exact body, expiry and delegated gateway-token digest. Mobile Trip screen forwards
preferences and current coordinates, persists profile revisions and discards stale
responses. The preferences remain user-owned device snapshots, not an authoritative
shared profile database. Multi-device revision coordination is not implemented.

Unconfigured providers still return unavailable. No production rollout, registry
publication, commit or push has happened. The gateway dry run did not mutate state.

## Bound workload and delegation identities

Each logical agent requires a distinct Zitadel machine subject and client ID in
`ROAMIE_AGENTS_GATEWAY_CLIENTS` (workers) or `ROAMIE_MANAGER_GATEWAY_CLIENTS` (manager).
Each value contains `subject`, `client_id`, and `client_secret`; supply the JSON
through Secret Manager/External Secrets, never a values file. Missing identities
stop startup and duplicate subjects or client IDs are rejected. The manager map
contains `roamie-trip-manager`; worker keys are `roamie-{specialist}`. Static
`GATEWAY_API_KEY` and `WORKER_API_KEY` settings are no longer used.

The ADK credential broker/cache obtains short-lived client-credentials tokens,
refreshes 30 seconds before expiry, coalesces concurrent refreshes and denies
calls when credentials cannot be refreshed. Credential transports restrict the
destination origin and never forward gateway credentials to another origin.

Manager and worker services additionally share a separate 32-character minimum
`DELEGATION_KEY`. Every A2A request and response is signed with distinct signing
purposes. The envelope binds tenant, personal manager identity, profile revision,
specialist, unpredictable request ID, complete request digest and a 120-second
expiry. Direct unsigned worker payloads and responses from another request fail
closed. Both manager AI review passes inherit the personal manager principal;
workers inherit that principal with only their specialist scope.

This is the application protocol implementation. Activation still requires the
corresponding Zitadel role grants, gateway subject restrictions, MCP verified
claim mapping and a live end-to-end probe. The manager now verifies its complete
profile with the private Roamie API before evidence collection and during review;
that API must include the authoritative-profile migration and routes before rollout. These
checks must pass before enabling the currently disabled workload chart.


### Planning review bounds

The manager checks the parent request before launching the itinerary worker and its
independent weather/entry checks concurrently. Every specialist retains its own
request and response review. The final itinerary review waits for these results;
failed or cancelled requests cancel and await unfinished supporting work. Only
clarification needed by an optional check becomes an explicit unavailable warning.
Ownership, revision, signature and main-request review failures still stop delivery.

Three itineraries plus their provider evidence exceeded the generic supervisor's
16,000 input-token ceiling in a live Hanoi validation (17,205 tokens). Roamie's
manager permits 32,000 input tokens per review run, with the existing two-call,
output, model-context and time limits retained. The complete manager request stays
within its 78-second deadline; token limits are not removed.

### Failure diagnostics

Manager and worker failures emit `roamie_request_failed` with a server-generated request ID and an allowlisted reason. Worker responses include the same request ID. Planner runs emit `roamie_model_finished`; post-generation validation failures retain the ADK run ID and state for correlation. Unknown exception text, prompts, profile contents and credentials are excluded. These diagnostics do not bypass failed reviews or retry rejected plans. Customer activation remains gated on authenticated app and representative reliability checks (issue #65).
