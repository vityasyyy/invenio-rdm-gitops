#!/bin/bash
# Entrypoint script for InvenioRDM in Kubernetes
# In production (K8s), static assets are pre-built into the Docker image.
# The build-assets init container copies them from the image to the emptyDir volume.
# This entrypoint validates that assets are present and starts gunicorn.

set -e

echo "InvenioRDM Custom Image Entrypoint"
echo "===================================="

# Ensure instance directories exist
mkdir -p ${INVENIO_INSTANCE_PATH}/data
mkdir -p ${INVENIO_INSTANCE_PATH}/archive
mkdir -p ${INVENIO_INSTANCE_PATH}/assets/templates/custom_fields

# Validate that static assets were built by the init container
MANIFEST_PATH="${INVENIO_INSTANCE_PATH}/static/dist/manifest.json"

if [ ! -f "$MANIFEST_PATH" ]; then
    echo "WARNING: manifest.json not found. Pre-built static assets are missing."
    echo "  The image may be incomplete, or the init container failed to copy assets."
    echo "  Invenio may return 500 errors."
elif grep -qE '"status".*:"(compile|error)"' "$MANIFEST_PATH" 2>/dev/null; then
    echo "WARNING: manifest.json indicates incomplete build."
    echo "  The image build may have failed, or assets were corrupted."
    echo "  Invenio may return 500 errors."
else
    echo "Static assets OK, starting application."
fi

# Execute the command passed to the container
exec "$@"