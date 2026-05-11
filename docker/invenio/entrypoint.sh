#!/bin/bash
# Entrypoint script for InvenioRDM in Kubernetes
# Handles initialization tasks before starting the main process.
#
# On first start (or after an image upgrade), builds static assets
# (CSS/JS bundles) which require a running DB/Redis/Search connection.

set -e

echo "InvenioRDM Custom Image Entrypoint"
echo "===================================="

# Ensure instance directories exist
mkdir -p ${INVENIO_INSTANCE_PATH}/data
mkdir -p ${INVENIO_INSTANCE_PATH}/archive

# Build static assets if manifest.json doesn't exist.
# This is the critical step that was missing — Invenio's webpack bundles
# (theme.css, manifest.json, etc.) must be compiled at runtime because
# the build requires a fully initialized Flask app with DB/cache access.
#
# This is idempotent: if the assets were already built (e.g., from a
# previous pod restart with the same image), npm build is skipped.
MANIFEST_PATH="${INVENIO_INSTANCE_PATH}/static/dist/manifest.json"

if [ ! -f "$MANIFEST_PATH" ]; then
    echo "Building static assets (first start or upgrade)..."

    echo "  [1/3] Collecting static files..."
    invenio collect -v 2>/dev/null || { echo "WARNING: invenio collect failed"; }

    echo "  [2/3] Creating webpack project..."
    invenio webpack create 2>/dev/null || { echo "WARNING: invenio webpack create failed"; }

    ASSETS_DIR="${INVENIO_INSTANCE_PATH}/assets"
    if [ -d "$ASSETS_DIR" ] && [ -f "$ASSETS_DIR/package.json" ]; then
        echo "  [3/3] Installing dependencies and building webpack bundles..."
        cd "$ASSETS_DIR"
        npm install --production=false 2>&1 | tail -3 || { echo "WARNING: npm install failed"; }
        npm run build 2>&1 | tail -5 || { echo "WARNING: npm run build failed"; }
        cd /opt/invenio
    else
        echo "  [3/3] Skipped — no webpack project found at $ASSETS_DIR"
    fi

    if [ -f "$MANIFEST_PATH" ]; then
        echo "Static assets built successfully."
    else
        echo "WARNING: manifest.json not found after build. Invenio may return 500 errors."
        echo "  This can happen if DB/Redis/Search are not fully ready yet."
        echo "  The pod will restart and retry on next startup."
    fi
else
    echo "Static assets already exist, skipping build."
fi

# Check if we're running in a Kubernetes environment
if [ -n "${KUBERNETES_SERVICE_HOST}" ]; then
    echo "Running in Kubernetes environment"
fi

# Execute the command passed to the container
exec "$@"