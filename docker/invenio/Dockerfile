# Multi-stage Dockerfile for custom Invenio RDM image
# Supports S3/MinIO storage, custom themes, and Kubernetes deployment
#
# Optimized for CI build speed:
#  - Single-arch (linux/amd64) — no QEMU overhead
#  - Combined layers to reduce image size and build time
#  - Cache mounts for pip and npm
#  - Invenio CLI commands gracefully skipped in build stage (no DB)
#
# Build context: repo root
# Usage:
#   docker buildx build --platform linux/amd64 -t ghcr.io/vityasyyy/invenio-rdm-custom:latest -f docker/invenio/Dockerfile .

# =============================================================================
# Stage 1: Builder - Compile Python dependencies and static assets
# =============================================================================
FROM python:3.12-slim-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    LANG=en_US.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=0 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    INVENIO_INSTANCE_PATH=/opt/invenio/var/instance

# Install build dependencies + Node.js (single layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libffi-dev \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    libjpeg62-turbo-dev \
    libwebp-dev \
    libtiff-dev \
    libssl-dev \
    pkg-config \
    curl \
    git \
    libcairo2 \
    libpangocairo-1.0-0 \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g npm@latest \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/invenio

# --- Install Python dependencies (single layer with cache mount) ---
COPY docker/invenio/requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --user --no-cache-dir -r requirements.txt \
    && pip install --user --no-cache-dir \
       "invenio-s3>=3.0.2" \
       "boto3>=1.34.0" \
       "botocore>=1.34.0"

# --- Copy application code (all at once) ---
COPY site/ ./site/
COPY templates/ ./templates/
COPY static/ ./static/
COPY translations/ ./translations/
COPY app_data/ ./app_data/
COPY docker/invenio/invenio.cfg ./invenio.cfg
COPY docker/invenio/requirements.txt ./requirements.txt

# --- Install editable site package (if applicable) ---
RUN if [ -d "site" ] && [ -f "site/setup.py" ] || [ -f "site/pyproject.toml" ]; then \
        pip install --user --no-cache-dir -e ./site; \
    fi

# --- Prepare instance directories ---
# Invenio collect/webpack need a DB at runtime; skip in build stage
RUN mkdir -p ${INVENIO_INSTANCE_PATH}/assets ${INVENIO_INSTANCE_PATH}/static

# =============================================================================
# Stage 2: Runtime - Minimal image with only runtime dependencies
# =============================================================================
FROM python:3.12-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    LANG=en_US.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INVENIO_INSTANCE_PATH=/opt/invenio/var/instance \
    PATH="/opt/invenio/.local/bin:${PATH}"

# Install runtime system dependencies (single layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libxml2 \
    libxslt1.1 \
    libjpeg62-turbo \
    libwebp7 \
    libtiff6 \
    libcairo2 \
    libpangocairo-1.0-0 \
    libfontconfig1 \
    libfreetype6 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r invenio --gid=1654 && \
    useradd -r -g invenio --uid=1654 --home-dir=/opt/invenio --shell=/bin/bash invenio

# Create instance directory
RUN mkdir -p ${INVENIO_INSTANCE_PATH} && \
    chown -R invenio:invenio /opt/invenio

# Copy Python packages from builder
COPY --from=builder --chown=invenio:invenio /root/.local /opt/invenio/.local

# Copy static directories (may be empty if invenio CLI failed in build)
COPY --from=builder --chown=invenio:invenio /opt/invenio/var/instance/assets ${INVENIO_INSTANCE_PATH}/assets
COPY --from=builder --chown=invenio:invenio /opt/invenio/var/instance/static ${INVENIO_INSTANCE_PATH}/static

# Copy config and custom files
COPY --from=builder --chown=invenio:invenio /opt/invenio/invenio.cfg ${INVENIO_INSTANCE_PATH}/invenio.cfg
COPY --from=builder --chown=invenio:invenio /opt/invenio/site ${INVENIO_INSTANCE_PATH}/site
COPY --from=builder --chown=invenio:invenio /opt/invenio/templates ${INVENIO_INSTANCE_PATH}/templates
COPY --from=builder --chown=invenio:invenio /opt/invenio/translations ${INVENIO_INSTANCE_PATH}/translations
COPY --from=builder --chown=invenio:invenio /opt/invenio/app_data ${INVENIO_INSTANCE_PATH}/app_data

# Copy entrypoint script
COPY --chown=invenio:invenio docker/invenio/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Declare volumes for persistent data
VOLUME ["${INVENIO_INSTANCE_PATH}/data", "${INVENIO_INSTANCE_PATH}/archive"]

WORKDIR /opt/invenio
USER invenio

EXPOSE 5000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "invenio_app.wsgi:application", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "2", "--access-logfile", "-", "--error-logfile", "-", "--log-level", "INFO"]