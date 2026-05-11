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

# Build static assets if manifest.json doesn't contain real built assets.
# `invenio webpack create` writes a placeholder manifest with "status": "compile"
# and empty assets/chunks. A successful npm build overwrites it with real entries.
# We must re-run the build whenever the manifest is incomplete, not just absent.
MANIFEST_PATH="${INVENIO_INSTANCE_PATH}/static/dist/manifest.json"
NEEDS_BUILD=true

if [ -f "$MANIFEST_PATH" ]; then
    # Check if manifest contains real built assets (not just the placeholder)
    if grep -q '"status".*:"compile"' "$MANIFEST_PATH" 2>/dev/null || \
       ! grep -q '"theme.css"' "$MANIFEST_PATH" 2>/dev/null; then
        echo "manifest.json exists but is incomplete (placeholder), rebuilding..."
        rm -f "$MANIFEST_PATH"
    else
        echo "Static assets already built, skipping build."
        NEEDS_BUILD=false
    fi
fi

if [ "$NEEDS_BUILD" = true ]; then
    echo "Building static assets..."

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

    if [ -f "$MANIFEST_PATH" ] && ! grep -q '"status".*:"compile"' "$MANIFEST_PATH" 2>/dev/null; then
        echo "Static assets built successfully."
    else
        echo "WARNING: manifest.json is still incomplete after build."
        echo "  Invenio may return 500 errors until the next restart retries the build."
    fi
fi

# Check if we're running in a Kubernetes environment
if [ -n "${KUBERNETES_SERVICE_HOST}" ]; then
    echo "Running in Kubernetes environment"
fi

# Execute the command passed to the container
exec "$@"