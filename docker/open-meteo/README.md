# Roamie weather runtime

Pinned, unmodified Open-Meteo binary with a real-forecast readiness probe and
corresponding upstream source. AGPLv3 software / CC BY4.0 weather data.

Build: `docker build -f docker/open-meteo/Dockerfile -t roamie-weather:qa .`
The image defaults to the public AWS Open Data distribution and an 8GB bounded
LRU disk cache. Production runs privately in the Roamie namespace. Use writable
mounts for `/app/data`, `/app/tmp` and `/tmp`, UID/GID999, a read-only root,
no privileges/capabilities and no service-account token. Set `CACHE_SIZE=512MB`
for constrained local evaluation only. Do not confuse this with Open-Meteo's
non-commercial hosted API.

`python3 docker/open-meteo/smoke.py` expects this container at localhost:18080 and
requires live forecasts from three continents. Initial remote-cache warming can
be slow. Empty responses fail readiness. `weather-image.yml` runs this gate
before publishing; the deployable image digest must come from that successful
workflow. Source archive: `/usr/share/openmeteo/source.tar.gz`.

See [deployment decision and validation](../../docs/adr/0013-weather-provider.md).
