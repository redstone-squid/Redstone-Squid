# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

FROM ${PYTHON_IMAGE} AS builder

ARG WITH_SCHEMATICS=0
ARG WITH_OBSERVABILITY=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_NO_MANAGED_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    if [ "$WITH_SCHEMATICS" = "1" ] && [ "$WITH_OBSERVABILITY" = "1" ]; then \
      uv sync --locked --no-dev --no-editable --extra schematics --extra observability; \
    elif [ "$WITH_SCHEMATICS" = "1" ]; then \
      uv sync --locked --no-dev --no-editable --extra schematics; \
    elif [ "$WITH_OBSERVABILITY" = "1" ]; then \
      uv sync --locked --no-dev --no-editable --extra observability; \
    else \
      uv sync --locked --no-dev --no-editable; \
    fi

FROM ${PYTHON_IMAGE} AS runtime

ARG WITH_SOFTWARE_GPU=0
RUN if [ "$WITH_SOFTWARE_GPU" = "1" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends mesa-vulkan-drivers libvulkan1 \
      && rm -rf /var/lib/apt/lists/*; \
    fi

ARG GIT_COMMIT_HASH=unknown
ENV SQUID_BUILD_COMMIT_HASH=$GIT_COMMIT_HASH \
    SQUID_OBSERVABILITY_RELEASE=$GIT_COMMIT_HASH
ARG GIT_COMMIT_MESSAGE="no message"
ENV SQUID_BUILD_COMMIT_MESSAGE=$GIT_COMMIT_MESSAGE

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GIT_PYTHON_REFRESH=quiet \
    SQUID_LOG_DIRECTORY=/var/log/app \
    XDG_CACHE_HOME=/var/lib/app/.cache \
    WGPU_BACKEND=vulkan

WORKDIR /app

ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser \
    && mkdir -p /var/log/app /var/lib/app \
    && chown appuser:appuser /var/log/app /var/lib/app

COPY --from=builder --chown=root:root /app/.venv /app/.venv
COPY --chown=root:root . .

# .po translation catalogs are tracked; compiled .mo binaries are gitignored build artifacts.
RUN pybabel compile -d locales -D squid

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/readyz', timeout=5).read()" || exit 1

CMD ["python", "-m", "squid.api.app"]
