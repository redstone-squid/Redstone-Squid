# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
COPY --chown=root:root tests/__init__.py tests/__init__.py
COPY --chown=root:root tests/fuzz tests/fuzz
USER appuser

CMD ["python", "-m", "tests.fuzz.api.container_entrypoint"]
