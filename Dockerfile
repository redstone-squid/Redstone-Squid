# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89

ARG PYTHON_IMAGE=python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4
ARG DEBIAN_SNAPSHOT=20260803T000000Z
ARG FFMPEG_VERSION=7:7.1.5-0+deb13u1

FROM ${PYTHON_IMAGE} AS builder

ARG WITH_SCHEMATICS=0
ARG WITH_OBSERVABILITY=1
ARG DEBIAN_SNAPSHOT

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_NO_MANAGED_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN sed -ri \
      "s|^URIs: http://deb.debian.org/debian$|URIs: https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|; \
       s|^URIs: http://deb.debian.org/debian-security$|URIs: https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|" \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends \
      git \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=packages,target=packages \
    if [ "$WITH_SCHEMATICS" = "1" ] && [ "$WITH_OBSERVABILITY" = "1" ]; then \
      uv sync --locked --no-dev --no-editable --extra schematics --extra observability; \
    elif [ "$WITH_SCHEMATICS" = "1" ]; then \
      uv sync --locked --no-dev --no-editable --extra schematics; \
    elif [ "$WITH_OBSERVABILITY" = "1" ]; then \
      uv sync --locked --no-dev --no-editable --extra observability; \
    else \
      uv sync --locked --no-dev --no-editable; \
    fi

FROM ${PYTHON_IMAGE} AS runtime-base

ARG DEBIAN_SNAPSHOT
RUN sed -ri \
      "s|^URIs: http://deb.debian.org/debian$|URIs: https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|; \
       s|^URIs: http://deb.debian.org/debian-security$|URIs: https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|" \
      /etc/apt/sources.list.d/debian.sources

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
    TMPDIR=/var/lib/app/tmp \
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
    && install -d -o appuser -g appuser -m 0750 /var/log/app /var/lib/app /var/lib/app/objects \
    && install -d -o appuser -g appuser -m 0700 /var/lib/app/.cache /var/lib/app/media-tmp /var/lib/app/tmp

COPY --from=builder --chown=root:root /app/.venv /app/.venv
COPY --chown=root:root . .

# .po translation catalogs are tracked; compiled .mo binaries are gitignored build artifacts.
RUN pybabel compile -d locales -D squid

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/readyz', timeout=5).read()" || exit 1

CMD ["python", "-m", "squid.api.app"]

FROM runtime-base AS runtime-media

USER root
ARG FFMPEG_VERSION
RUN apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends "ffmpeg=${FFMPEG_VERSION}" \
    && rm -rf /var/lib/apt/lists/* \
    && test "$(dpkg-query --showformat='${Version}' --show ffmpeg)" = "$FFMPEG_VERSION" \
    && /usr/bin/ffmpeg -version \
    && /usr/bin/ffprobe -version
USER appuser

FROM runtime-base AS runtime-software-gpu

USER root
RUN apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends libvulkan1 mesa-vulkan-drivers \
    && rm -rf /var/lib/apt/lists/*
USER appuser

FROM runtime-base AS runtime-media-software-gpu

USER root
ARG FFMPEG_VERSION
RUN apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends \
      "ffmpeg=${FFMPEG_VERSION}" \
      libvulkan1 \
      mesa-vulkan-drivers \
    && rm -rf /var/lib/apt/lists/* \
    && test "$(dpkg-query --showformat='${Version}' --show ffmpeg)" = "$FFMPEG_VERSION" \
    && /usr/bin/ffmpeg -version \
    && /usr/bin/ffprobe -version
USER appuser

# Preserve the base image as the default target for local `docker build` callers.
FROM runtime-base AS runtime
