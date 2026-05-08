#!/bin/bash
# Entrypoint script for InvenioRDM in Kubernetes
# Handles initialization tasks before starting the main process

set -e

echo "InvenioRDM Custom Image Entrypoint"
echo "===================================="

# Ensure instance directory exists
mkdir -p ${INVENIO_INSTANCE_PATH}/data
mkdir -p ${INVENIO_INSTANCE_PATH}/archive

# The actual database/schema initialization is handled by the invenio-setup Job
# This entrypoint just ensures the environment is ready

# Check if we're running in a Kubernetes environment
if [ -n "${KUBERNETES_SERVICE_HOST}" ]; then
    echo "Running in Kubernetes environment"
fi

# Execute the command passed to the container
exec "$@"
