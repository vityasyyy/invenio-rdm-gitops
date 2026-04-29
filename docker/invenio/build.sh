#!/bin/bash
set -e

REGISTRY="ghcr.io"
ORG="your-org"
IMAGE="inveniordm-s3"
TAG="13.0.0-post1-s3"

FULL_IMAGE="${REGISTRY}/${ORG}/${IMAGE}:${TAG}"

echo "Building ${FULL_IMAGE}..."
docker build -t "${FULL_IMAGE}" .

echo "Pushing ${FULL_IMAGE}..."
docker push "${FULL_IMAGE}"

echo "Done! Update your deployment to use: ${FULL_IMAGE}"
