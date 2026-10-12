# Self-hosted global weather with Google failover

Status: implemented and locally validated; release in progress after repository visibility change.
User authorized this provider arrangement and deployment on 2026-10-12. The user explicitly selected the GitHub repository for public visibility;
`tesserix/ai-agents` is now public. The weather endpoint remains private.

## Decision and boundaries

Run pinned Open-Meteo 1.6.0 in the Roamie namespace as the primary. It reads the
public Open-Meteo AWS Open Data bucket and maintains a bounded local LRU cache.
This is an independently hosted API, not the restricted free hosted endpoint.
Google Weather is the secondary, using the existing OpenBao-managed secret.
Neither provider changes model routing: AI requests continue through Agent Gateway.

No new database, public API contract or signed-contract field. Configure
`ROAMIE_MANAGER_OPEN_METEO_SELF_HOSTED=true`; the adapter uses only the fixed
internal origin `http://roamie-weather.roamie.svc.cluster.local:8080`. It sends no
commercial key, Google key, prompt, account ID or gateway credential to that host.
Google receives its own key in X-Goog-Api-Key, never a query parameter. The
previous commercial Open-Meteo setting remains available but is not required.

Self-hosted Open-Meteo can supply up to 16 days globally; actual days and values
vary by model. Google has a shorter forecast window and does not provide daily
forecasts in JP, CN, CU, IR, KP, KR, SY or VN. A primary outage in these countries
still produces explicit unavailable guidance. “Global primary” is not a claim
that Google provides global fallback coverage.

The primary has a 35-second total deadline, including reads, to accommodate
self-hosted cold data loads observed at up to 29 seconds. Google fallback has a
six-second deadline; the manager retains its 80-second overall request limit.
429, HTTP errors, invalid responses and network failures trigger Google. Empty
near-term results also trigger Google, even if the primary returned HTTP200.
Successful primary forecasts do not call Google. Partial usable forecasts remain
from one provider; this change does not silently merge conflicting daily values.
Missing future dates do not waste Google quota. Cancellation propagates without
starting another provider. Two attempts maximum, no retry loop or task leakage.
A structured `roamie_weather_fallback` log records the fallback provider, without
locations, upstream bodies or secrets.

## Capacity, cache and deployment

Two replicas, each requesting 1 CPU, 8Gi memory and 20Gi ephemeral storage;
limits 2 CPUs, 10Gi memory and 22Gi ephemeral storage. The bounded LRU cache is
8GB on disk in a 20Gi emptyDir, disposable across restarts. Total steady requested
capacity: 2 CPUs, 16Gi memory and 40Gi disk; rolling surge can add a third pod.
No historical weather database, backup or migration. At 12/36 months the cache
remains bounded, not cumulative. Cost is cluster capacity, not zero; no new node
pool or purchased resource has been provisioned. Validate schedulable requests
before activation. Upstream examples recommend at least 8GB RAM and four cores;
our lower CPU request is a provisional low-traffic allocation requiring load
observation, not a measured production capacity guarantee.

Assumed traffic: 1,000 DAU × two checks × three cities = 6,000 city checks/day,
0.07 average requests/sec and approximately 0.7/sec at a 10× peak. This is sizing
context, not observed traffic. Monitor latency, fallback logs, memory/cache and
Google usage before increasing traffic. Google standard pricing lists 10,000
free monthly events, then starts at USD0.15/1,000; billing/region/contract vary.
The remote S3 distribution is an upstream availability dependency; Google is an
independent backup. No availability SLO is inferred from either provider.

The workload is ClusterIP-only, no ingress, non-root UID999, read-only root,
no service-account token, dropped capabilities, bounded resources and a PDB.
Ambient mesh source-principal policy permits only the manager. Network policies
allow manager traffic, DNS and outbound HTTPS. Existing namespace-wide policies
are additive, so source authorization relies on the mesh policy, not a claim
that the new NetworkPolicy narrows every pre-existing allow rule.

Startup/readiness executes a real 16-day Tokyo forecast query. A listening server
with an empty database is not ready. Readiness has 20 seconds; initial remote-data
warming may take repeated startup attempts. Liveness tests process availability
only; a transient upstream outage does not cause restart storms.

## Reusable image and release

`docker/open-meteo/Dockerfile` wraps immutable upstream digest
`sha256:e1517a01a061fd96e2a9017d022c5809bb51725bcd733bcbed435e7ea32dd9da`,
adds readiness and checksum-verified corresponding source for upstream revision
`7988e4de62a3bf4e2ef4f3034a793af6e3bb3e84`. The upstream binary is unmodified.
AGPLv3 software and CC BY4.0 weather-data obligations still apply; retain visible
provider attribution and make corresponding source available to network users.
Source is bundled at `/usr/share/openmeteo/source.tar.gz`; exact upstream source
is public at the linked GitHub revision below. Our wrapper/check code remains
in this repository and must accompany any distributed derivative image.

The release workflow validates the Python project, builds the runtime, requires
live forecasts in Tokyo/Melbourne/New York, then publishes only on explicit
workflow dispatch. Images receive an immutable SHA tag, provenance and SBOM.
The GitOps chart refuses enabling weather without an immutable digest. It stays
disabled until CI publishes tested images. Google stays configured for rollback.

The maintenance branch is based on the deployed compatible contracts plus the
already approved manager advisory deadline fix. Do not deploy a current-main
manager alone against older workers. Deploy the matching maintenance manager
image and weather image together through the owning GitOps repository after CI.

Rollback requires explicit approval: restore the previous manager digest and
set weather.enabled=false through GitOps. There is no user-data migration; the
cache can be rebuilt. Google-only rollback restores its known Japan limitation.

## Validation and open gates

- Maintenance backend: 452 tests passed, 91.20% coverage. Open-Meteo and failover
  modules have 100% measured line/branch coverage. Ruff, strict mypy, format and
  `uv build --wheel` passed.
- Current-main backend also passed 491 tests, 91.57% coverage, strict mypy,
  Ruff/format and actionlint.
- All 49 Roamie GitOps tests and enabled/disabled Helm renders/lint passed.
- Local pinned upstream image returned Tokyo16, Melbourne16, NewYork15 daily
  forecasts. First successful cold reads took 29.01s, 9.99s, 6.11s respectively;
  warm reads took 0.02s, 0.01s, 0.01s. These are local synthetic checks, not a
  production latency SLO or a guarantee that every day has every variable.
- The final packaged image became healthy after cache warming. The actual
  Roamie adapter against it returned three Tokyo days in 0.03s and three Kyoto
  days in 4.20s, correctly attributed to Open-Meteo.
- In an isolated QA process using the existing production credential, a simulated
  primary429 produced a real Google forecast for Melbourne. No live service was
  replaced and no credential was copied out or printed.
- An initial local run used the wrong environment variable and returned all nulls.
  Correct configuration is REMOTE_DATA_DIRECTORY=https://openmeteo.s3.amazonaws.com/data/
  and CACHE_SIZE=8GB. A proposed full upstream rebuild was abandoned once this
  configuration error was identified; the final image uses the official binary.
- Retried release run38213920022 attempt2: GitHub again refused to start jobs due
  to failed account payments/spending limit. No CI bypass or production rollout.
- Before completion: publish both tested images, set their exact GitOps digests,
  verify Argo health, real in-cluster primary responses, controlled isolated429
  fallback, and iOS/Android authenticated readiness. TestFlight5 predates these
  changes. Do not describe this work as deployed or production acceptance passed.

## Sources

- https://github.com/open-meteo/open-meteo/tree/7988e4de62a3bf4e2ef4f3034a793af6e3bb3e84
- https://github.com/open-meteo/open-meteo/blob/7988e4de62a3bf4e2ef4f3034a793af6e3bb3e84/docs/getting-started.md
- https://github.com/open-meteo/open-data
- https://open-meteo.com/en/licence
- https://developers.google.com/maps/documentation/weather/coverage
- https://developers.google.com/maps/billing-and-pricing/pricing
