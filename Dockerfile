# syntax=docker/dockerfile:1
#
# Stages:
#   dev         uv and every dependency group. docker-compose.yaml and
#               docker-compose.test.yaml mount the working tree over /app.
#   production  the app and its runtime dependencies only, as a non-root
#               user. Released to ghcr.io by .github/workflows/release.yaml.
#
# The python tag matches .python-version and requires-python in
# pyproject.toml; change all three together.

FROM python:3.14-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH=/venv/bin:$PATH \
    HOST=0.0.0.0 \
    DATA_DIR=/data \
    CONTACT_SHEET_DIR=/contact-sheets
WORKDIR /app

FROM base AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
# Dependencies first, so a source change doesn't reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM build AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked
CMD ["python", "-m", "unifi_audit_daemon"]

FROM base AS production
# Numeric, so orchestrators can verify it isn't root.
RUN groupadd --system --gid 10001 app \
 && useradd --system --gid 10001 --uid 10001 --no-log-init --no-create-home app \
 && mkdir /data /contact-sheets \
 && chown 10001:10001 /data /contact-sheets
# The audit log (private) and the contact sheets (may be served), kept
# apart. A new named volume copies its directory's ownership, so the app
# user can write to it.
VOLUME /data /contact-sheets
COPY --from=build /venv /venv
USER 10001:10001
EXPOSE 8000
CMD ["unifi-audit-daemon"]
