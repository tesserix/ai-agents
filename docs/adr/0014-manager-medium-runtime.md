# Manager runtime and native TLS security patches

The deployed manager has 26 MEDIUM Debian-library findings without listed Debian fixes and one MEDIUM Rustls finding in Temporal 1.34.0. Refreshing the pinned Debian13 distroless image does not change its digest or these packages.

Keep the manager API, workload identity, signed delegation, profile privacy checks, two replicas, resource requests and limits unchanged. This release adds no service, hop, datastore, traffic, provider access or cloud resource; it carries the existing availability and latency budgets. Native dependencies must keep the Python 3.14 ABI.

Build a shell-free runtime from the signed Wolfi Python 3.14 packages in `docker/manager/runtime-packages.lock`. Pin the complete package closure, retain its real APK inventory, and copy no Debian interpreter or system library. The pinned package-assembly image and repository keys validate the package signatures. Remove the optional Python installer files while retaining their conservative package inventory.

Rebuild Temporal 1.34.0 from its official PyPI source distribution, verified by its SHA256 in the Dockerfile. Commit the genuine Cargo resolution with Rustls 0.23.45 and Rustls WebPKI 0.103.15; build the native wheel with `--locked`, then install that wheel only into the manager target. Cargo also corrects the existing tempfile 3.27.0 dependency edge to getrandom 0.3.4 without changing its version or checksum; all other package entries are unchanged. Other runtime targets keep their existing dependency trees. The packaged Cargo.lock describes the code actually compiled; editing or hiding scanner metadata is not a fix.

Retain non-root UID/GID 10001, read-only filesystem support, no shell or package manager, and TLS certificate verification. The publication gate must reject every MEDIUM, HIGH or CRITICAL finding, including unfixed findings, without an ignore file. Qualify the full application suite at the existing 90% coverage floor and exercise the installed runtime, authentication failure paths and native modules. Scan the final image, not just the base.

The public Chainguard Python image was considered but rejected because it moved to Python 3.15. A Debian base refresh was rejected because no replacement packages or new base digest were available for the reported findings.

Publish and review an immutable manager digest before a manager-only GitOps pin. Rollout remains separate and requires approval for that exact candidate. Rollback is a reviewed revert of the manager digest pin; do not weaken authentication or scanner gates. No schema migration or state changes are needed. Provider outages and agent-review failures keep their existing behavior. Rebuilding the native wheel increases build time; no new recurring cloud resources are introduced.
