# syntax=docker/dockerfile:1.18
ARG BASE_IMAGE=ghcr.io/tesserix/base-python-adk-3.14:20260926@sha256:14ec2e75d17a4207d1ae9d2600f3c33a609db33e9833c6be993ecc1525be56c9
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1
ARG MANAGER_PACKAGE_IMAGE=cgr.dev/chainguard/wolfi-base@sha256:256c88bb066424045021090932d50be93c8e3f2ce3586707702a401b146f3e43
ARG RUST_IMAGE=rust:1.94-slim-trixie@sha256:cf09adf8c3ebaba10779e5c23ff7fe4df4cccdab8a91f199b0c142c53fef3e1a

FROM ${UV_IMAGE} AS uv
FROM ${BASE_IMAGE} AS builder

USER root
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/adk-venv
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
# --inexact: the ADK is baked into the venv by the base image and is not in the lock.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --inexact
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --inexact \
    && uv pip check --python /opt/adk-venv/bin/python

FROM ${BASE_IMAGE} AS runtime-base

USER root

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder --chown=10001:10001 /opt/adk-venv /opt/adk-venv
# Production only imports installed wheels; package installers are build-only.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade \
       libpcre2-8-0=10.46-1~deb13u3 \
       libssl3t64=3.5.7-1~deb13u3 \
       openssl=3.5.7-1~deb13u3 \
       openssl-provider-legacy=3.5.7-1~deb13u3 \
    && rm -rf /var/lib/apt/lists/*
RUN /opt/adk-venv/bin/python -m pip uninstall --yes setuptools pip \
    && /usr/local/bin/python -m pip uninstall --yes setuptools pip
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]

FROM runtime-base AS kora-runtime

ENTRYPOINT ["uvicorn", "kora_agents.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM runtime-base AS sre-runtime

ENTRYPOINT ["uvicorn", "sre_agent.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM runtime-base AS orchestrator-runtime

ENTRYPOINT ["uvicorn", "orchestrator_agent.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]


FROM runtime-base AS roamie-runtime
ENTRYPOINT ["uvicorn", "roamie_agents.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM ${RUST_IMAGE} AS manager-temporal-builder
COPY --from=uv /uv /bin/uv
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
       build-essential protobuf-compiler libprotobuf-dev pkg-config python3-dev \
    && rm -rf /var/lib/apt/lists/*
COPY docker/manager/build-requirements.txt /tmp/build-requirements.txt
RUN uv venv --python /usr/bin/python3 /opt/build-tools \
    && uv pip install --python /opt/build-tools/bin/python --require-hashes \
       -r /tmp/build-requirements.txt
ADD --checksum=sha256:6453cb20e18df485e16578b22c82a9c4bcb1cf7eedd94147dfd373551d80f5b6 \
    https://files.pythonhosted.org/packages/13/85/9ece6f400552ed21951c8c5c534056e7d015cb065e3384470ec23ac24f28/temporalio-1.34.0.tar.gz /tmp/temporalio.tar.gz
RUN mkdir /temporal-source && tar -xzf /tmp/temporalio.tar.gz --strip-components=1 -C /temporal-source
COPY docker/manager/temporal-Cargo.lock /temporal-source/temporalio/bridge/Cargo.lock
WORKDIR /temporal-source
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/temporal-source/temporalio/bridge/target \
    CARGO_BUILD_JOBS=2 /opt/build-tools/bin/maturin build --release --locked \
    --interpreter /usr/bin/python3 --compatibility linux --out /wheels

FROM ${MANAGER_PACKAGE_IMAGE} AS manager-os
COPY docker/manager/runtime-packages.lock /tmp/runtime-packages.lock
RUN mkdir -p /manager-root/etc/apk/keys \
    && cp /etc/apk/keys/* /manager-root/etc/apk/keys/ \
    && cp /etc/apk/repositories /manager-root/etc/apk/ \
    && xargs apk --root /manager-root --initdb --no-scripts add --no-cache < /tmp/runtime-packages.lock

FROM builder AS manager-files
USER root
COPY --from=manager-temporal-builder /wheels /tmp/temporal-wheels
RUN uv pip install --python /opt/adk-venv/bin/python --no-deps --reinstall /tmp/temporal-wheels/*.whl \
    && uv pip check --python /opt/adk-venv/bin/python \
    && /opt/adk-venv/bin/python -m pip uninstall --yes setuptools pip
COPY --from=manager-os /manager-root /manager-root
COPY scripts/export_manager_runtime.py /tmp/export_manager_runtime.py
RUN /usr/local/bin/python /tmp/export_manager_runtime.py

FROM scratch AS roamie-manager-runtime
ENV VIRTUAL_ENV=/opt/adk-venv \
    PATH=/opt/adk-venv/bin:/usr/bin:/bin \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=manager-files /manager-root/ /
COPY --from=manager-files /opt/adk-venv /opt/adk-venv
WORKDIR /app
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["/opt/adk-venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
ENTRYPOINT ["/opt/adk-venv/bin/uvicorn", "roamie_agents.manager_main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

# Preserve the repository's original default for local `docker build` callers.
FROM kora-runtime AS runtime
