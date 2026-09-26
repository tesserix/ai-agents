# Weather and entry planning

The existing signed trip-manager request remains the trust boundary. Two new
specialists (`weather`, `entry-guidance`) consume structured evidence and return
through the manager's independent review. Trip planning runs these checks before
reviewing its itinerary and includes their exact sourced responses in `advisories`.
Missing provider evidence remains unavailable; it must never imply that a trip is
safe, visa-free or weather-validated.

Google Weather is queried by the manager at a fixed HTTPS endpoint with a server
secret, metric units, destination coordinates and provider-local display dates.
The provider offers up to ten days, not forecasts for arbitrary future dates.
Each response includes a coverage summary listing missing dates. No historical
average is substituted for a forecast. Rain/heat/UV suggestions are deterministic
planning options, not severe-weather alerts. Local alerts must be checked before
outdoor activities. Provider timeouts are eight seconds, one request per check;
429s and malformed responses fail unavailable without retry storms. Forecasts
expire after one hour. No coordinates, passport fields or provider credentials
are added to logs. The existing travel MCP contract stays pinned and unchanged;
passport and residency fields never go to the places provider.

Entry guidance currently provides navigation to official immigration websites
and a preparation checklist. It does not scrape pages or assert current legal
requirements, visa eligibility or prices. The current timestamp means the
checklist was prepared, not that a government page was fetched. Nationality,
residency, purpose and travel dates are necessary but not sufficient for a final
eligibility decision (passport type, transit route and personal circumstances
also matter). The output explicitly identifies unverified details. Verified fees
must retain the issuing currency; any future converted estimate needs a dated
exchange-rate source. A licensed, tested eligibility feed is a separate integration
and must not be represented as available until configured and validated.

Initial capacity assumption: eight concurrent requests per existing manager
replica, bounded by its existing 120-second operation budget and immutable signed
profile revision. No new queue, database or externally callable service is added.
Advisory failures must be visible, not silently replaced with model guesses.
Rollback is the previous worker/manager image and registry version. Production
activation also needs two distinct model-only workload identities, gateway route
allowlists, the weather secret reference and immutable release digests.

Validation: a real Google Weather call with a synthetic Melbourne location
returned the requested 2026-09-27 local date and complete coverage. The existing
GCP key was consumed in memory, never printed or committed. Registry publication
rewrites candidate image references to immutable digests from the same workflow
commit, after image and evaluation jobs pass; it fails closed if resolution fails.
