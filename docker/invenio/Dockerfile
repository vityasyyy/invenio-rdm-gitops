# Custom Invenio RDM image with S3 support
FROM ghcr.io/inveniosoftware/demo-inveniordm/demo-inveniordm:13.0.0-post1

USER root

# Install invenio-s3 for S3-compatible storage (MinIO)
RUN pip install --no-cache-dir invenio-s3

# Ensure boto3 is available (should already be in base image)
RUN pip install --no-cache-dir boto3 botocore

USER 1000
