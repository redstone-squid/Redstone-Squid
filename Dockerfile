# syntax=docker/dockerfile:1

# Use Python slim image as base
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ARG GIT_COMMIT_HASH=unknown
ENV GIT_COMMIT_HASH=$GIT_COMMIT_HASH
ARG GIT_COMMIT_MESSAGE="no message"
ENV GIT_COMMIT_MESSAGE=$GIT_COMMIT_MESSAGE

# Prevents Python from writing pyc files.
ENV PYTHONDONTWRITEBYTECODE=1

# Keeps Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
ENV PYTHONUNBUFFERED=1

# Compiling Python source files to bytecode is typically desirable for
# production images as it tends to improve startup time (at the cost of increased installation time).
ENV UV_COMPILE_BYTECODE=1

# Silences warnings about not being able to use hard links since the cache and sync target are on separate file systems.
ENV UV_LINK_MODE=copy

# Redstone Squid uses GitPython, which by default tries to refresh its cache on every run.
# Without git installed, we need to set this environment variable to prevent it from
# trying to refresh the cache and failing on import.
ENV GIT_PYTHON_REFRESH=quiet

# Set working directory
WORKDIR /app

# Create a non-privileged user that the app will run under
# See https://docs.docker.com/go/dockerfile-user-best-practices/
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser

# Install system dependencies (May be needed for building some Python packages)
# RUN apt-get update && apt-get install -y build-essential && \
#     apt-get purge -y --auto-remove build-essential && apt-get clean && \
#     rm -rf /var/lib/apt/lists/*

# Download dependencies as a separate step to take advantage of Docker's caching.
# Leverage a cache mount to /root/.cache/uv to speed up subsequent builds.
# Leverage a bind mount to requirements.txt to avoid having to copy them into
# into this layer.
RUN --mount=from=ghcr.io/astral-sh/uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-editable

# Copy the application code
COPY --chown=appuser:appuser . .

# Switch to the non-privileged user to run the application.
USER appuser

# Expose port for the FastAPI server
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')" || exit 1

# Run the application
CMD ["./.venv/bin/python", "app.py"]
