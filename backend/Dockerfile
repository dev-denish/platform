# syntax=docker/dockerfile:1.7
#
# Existing implementation (MVP): a single-stage backend image running uvicorn as
# root with --reload, shipping build tooling in the runtime image.
#
# Why insufficient: root containers violate least-privilege (a container escape is an
# instant host-level compromise), --reload is a dev feature that watches the
# filesystem and runs a single process, and shipping compilers bloats the image and
# widens the CVE surface.
#
# Enterprise solution: a multi-stage build. The builder compiles/collects wheels into
# a virtualenv; the runtime stage copies ONLY that venv + app code, runs as a
# non-root user, and serves via gunicorn with uvicorn workers (multiple processes,
# graceful shutdown on SIGTERM). rasterio's manylinux wheel bundles GDAL, so the
# runtime image needs no system GDAL packages.

# ---------- builder ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /build
COPY pyproject.toml ./
# Install runtime dependencies (resolved from pyproject) plus the production server.
RUN pip install \
      "fastapi==0.115.6" "uvicorn[standard]==0.34.0" "gunicorn==23.0.0" \
      "pydantic==2.10.4" "pydantic-settings==2.7.0" \
      "psycopg[binary,pool]==3.2.3" "bcrypt==4.2.1" "pyjwt==2.10.1" \
      "python-multipart==0.0.20" "numpy==2.2.1" "rasterio==1.4.3" "pillow==11.1.0" "scipy==1.18.0" \
      "alembic==1.14.0" "sqlalchemy==2.0.36" "structlog==24.4.0" "slowapi==0.1.9" \
      "arq==0.28.0" "prometheus-client==0.25.0" \
      "boto3==1.35.90"

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/*

# Non-root user.
RUN groupadd --system --gid 10001 dmrv \
 && useradd --system --uid 10001 --gid dmrv --home-dir /app --no-create-home dmrv \
 && mkdir -p /app /var/lib/dmrv/data /var/lib/dmrv/staging \
 && chown -R dmrv:dmrv /app /var/lib/dmrv/data /var/lib/dmrv/staging

COPY --from=builder --chown=dmrv:dmrv /opt/venv /opt/venv

WORKDIR /app
COPY --chown=dmrv:dmrv app ./app
COPY --chown=dmrv:dmrv migrations ./migrations
COPY --chown=dmrv:dmrv alembic.ini ./alembic.ini
COPY --chown=dmrv:dmrv scripts ./scripts

USER dmrv

EXPOSE 8001

# Liveness for orchestrators that use Docker healthchecks (K8s uses /livez directly).
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/livez').status==200 else 1)" || exit 1

# 2 workers by default; tune via WEB_CONCURRENCY. Graceful shutdown drains on SIGTERM.
CMD ["sh", "-c", "gunicorn app.asgi:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:8001 --graceful-timeout 30 --timeout 120 --access-logfile - --error-logfile -"]
