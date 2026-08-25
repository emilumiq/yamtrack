# Both stages must use the same Python version, otherwise the virtualenv copied
# from the builder ends up in a site-packages path the runtime Python ignores.
ARG PYTHON_VERSION=3.12
ARG ALPINE_VERSION=3.23

# --- Builder stage: build the virtualenv with uv ---
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-alpine${ALPINE_VERSION} AS builder

# Disable development dependencies
ENV UV_NO_DEV=1
# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1
# Copy from cache instead of symlinking (cache is discarded with the builder)
ENV UV_LINK_MODE=copy

WORKDIR /yamtrack

COPY ./pyproject.toml ./pyproject.toml
COPY ./uv.lock ./uv.lock

RUN uv sync --locked

# --- Final stage: minimal runtime image ---
FROM python:${PYTHON_VERSION}-alpine${ALPINE_VERSION}

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

# Define build argument with default value
ARG VERSION=dev
# Set it as an environment variable
ENV VERSION=$VERSION
# Put the virtualenv on PATH so python/gunicorn/celery resolve directly
ENV PATH="/yamtrack/.venv/bin:$PATH"

WORKDIR /yamtrack

COPY ./entrypoint.sh /entrypoint.sh

RUN apk add --no-cache shadow \
    && chmod +x /entrypoint.sh \
    # create user abc for later PUID/PGID mapping
    && useradd -U -M -s /bin/sh abc

# Copy the pre-built virtualenv from the builder stage
COPY --from=builder /yamtrack/.venv /yamtrack/.venv

# Django app
COPY src ./
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["/entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:8000/health/ || exit 1
