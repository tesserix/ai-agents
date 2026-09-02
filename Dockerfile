# syntax=docker/dockerfile:1.18
ARG BASE_IMAGE=ghcr.io/tesserix/base-python-adk-3.14:20260829@sha256:5a6fd1863ed7f37f3929cc596d0ec063c3077c11713cd334f14d1df2b30ef386
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1

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
    uv sync --frozen --no-dev --no-editable --inexact

FROM ${BASE_IMAGE} AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=builder --chown=10001:10001 /opt/adk-venv /opt/adk-venv
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

# Preserve the repository's original default for local `docker build` callers.
FROM kora-runtime AS runtime
