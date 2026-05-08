# Multi-stage Dockerfile for custom Invenio RDM image
# Supports S3/MinIO storage, custom themes, and Kubernetes deployment
#
# Build context: repo root (so we can include custom assets/themes)
# Usage:
#   docker build -t ghcr.io/vityasyyy/invenio-rdm-custom:latest -f docker/invenio/Dockerfile .
#   docker push ghcr.io/vityasyyy/invenio-rdm-custom:latest

# =============================================================================
# Stage 1: Builder - Compile Python dependencies and static assets
# =============================================================================
FROM python:3.12-slim-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    LANG=en_US.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies + Node.js
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
    # Node.js 20 LTS
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g npm@latest \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /opt/invenio

# --- Install Python dependencies ---
# We install invenio-app-rdm with explicit invenio-s3 support
# Using a requirements.txt approach for reproducibility
COPY docker/invenio/requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Ensure invenio-s3 and boto3 are available
RUN pip install --user --no-cache-dir \
    "invenio-s3>=3.0.2" \
    "boto3>=1.34.0" \
    "botocore>=1.34.0"

# --- Copy application code ---
# Custom site code (Python modules)
COPY site/ ./site/

# Custom templates
COPY templates/ ./templates/

# Custom static assets (CSS, JS, images)
COPY static/ ./static/

# Custom translations
COPY translations/ ./translations/

# App data (fixtures, vocabularies, etc.)
COPY app_data/ ./app_data/

# Invenio config
COPY docker/invenio/invenio.cfg ./invenio.cfg

# --- Build static assets ---
ENV INVENIO_INSTANCE_PATH=/opt/invenio/var/instance
RUN mkdir -p ${INVENIO_INSTANCE_PATH}

# Copy config to instance path for asset building
COPY docker/invenio/invenio.cfg ${INVENIO_INSTANCE_PATH}/
COPY docker/invenio/requirements.txt ${INVENIO_INSTANCE_PATH}/

# Install app into the Python environment (editable if site/ exists)
RUN if [ -d "site" ] && [ -f "site/setup.py" -o -f "site/pyproject.toml" ]; then \
        pip install --user --no-cache-dir -e ./site; \
    fi

# Collect and build static assets
RUN invenio collect --verbose 2>/dev/null || true
RUN invenio webpack create 2>/dev/null || true

# If webpack project exists, build assets
RUN if [ -d "${INVENIO_INSTANCE_PATH}/assets" ]; then \
        cd ${INVENIO_INSTANCE_PATH}/assets && \
        npm install 2>/dev/null || true && \
        npm run build 2>/dev/null || true; \
    fi

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

# Install runtime system dependencies
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

# Create non-root user (matching front-matter's approach for compatibility)
# UID 1654 is arbitrary but consistent with their image
RUN groupadd -r invenio --gid=1654 && \
    useradd -r -g invenio --uid=1654 --home-dir=/opt/invenio --shell=/bin/bash invenio

# Create instance directory
RUN mkdir -p ${INVENIO_INSTANCE_PATH} && \
    chown -R invenio:invenio /opt/invenio

# Copy Python packages from builder
COPY --from=builder --chown=invenio:invenio /root/.local /opt/invenio/.local

# Copy built static assets and config from builder
COPY --from=builder --chown=invenio:invenio /opt/invenio/var/instance/assets ${INVENIO_INSTANCE_PATH}/assets
COPY --from=builder --chown=invenio:invenio /opt/invenio/var/instance/static ${INVENIO_INSTANCE_PATH}/static
COPY --from=builder --chown=invenio:invenio /opt/invenio/invenio.cfg ${INVENIO_INSTANCE_PATH}/invenio.cfg

# Copy custom files from builder
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
