# Versioned Roamie catalog dependencies

The eight public agents reference immutable skill revisions and the released
travel Tool and MCPServer rather than embedding descriptions that the registry
cannot resolve. Each agent identifies a digest-pinned ADK container and its
actual signed HTTP endpoint. This does not assert generic JSON-RPC compatibility.
The workers share one container; the manager has its own container and identity.

Catalog revision 1.0.2 pins skills at 1.0.2 and the travel tool/MCP at 1.0.1.
The MCP runtime version remains 0.1.0rc6. Publish dependencies first so a newly
visible agent has a resolvable graph. A failed publication fails CI and can be
retried idempotently. Consumers must reject unresolved dependencies.

Public metadata is not runtime authorization. Running these images requires the
Secret Manager-backed identities, profile authority, signed delegation and
upstream configuration in the owning tesserix-k8s Roamie chart. Never inject
credentials into catalog definitions. Gateway export remains disabled because
native registry-owned GatewayResources own the authenticated routes.

This changes publication only, adding one artifact and no request-path hops or
customer traffic. Existing gateway deadlines and profile isolation remain in
force. Dynamic runtime discovery and an authenticated app-to-manager smoke test
are separate required validation steps, tracked in issue #49.
