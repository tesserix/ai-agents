# syntax=docker/dockerfile:1.18
FROM ghcr.io/open-meteo/open-meteo:1.6.0@sha256:e1517a01a061fd96e2a9017d022c5809bb51725bcd733bcbed435e7ea32dd9da
LABEL org.opencontainers.image.source="https://github.com/tesserix/ai-agents" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.upstream.revision="7988e4de62a3bf4e2ef4f3034a793af6e3bb3e84"
# Bundle corresponding upstream source alongside the unmodified upstream binary.
ADD --checksum=sha256:a9302a68cf33237cee6cdbc8879aabe588dc1fe450a5774e64c8cddddd93241d https://codeload.github.com/open-meteo/open-meteo/tar.gz/7988e4de62a3bf4e2ef4f3034a793af6e3bb3e84 /usr/share/openmeteo/source.tar.gz
COPY docker/open-meteo/check_forecast.py /app/check_forecast.py
ENV DATA_DIRECTORY=/app/data/ TEMP_DIRECTORY=/app/tmp/ \
    REMOTE_DATA_DIRECTORY=https://openmeteo.s3.amazonaws.com/data/ CACHE_SIZE=8GB
USER 999:999
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=20s --start-period=180s --retries=3 CMD ["python3", "/app/check_forecast.py"]
