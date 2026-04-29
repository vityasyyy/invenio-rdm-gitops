# Custom Invenio RDM Image with S3 Support

## Why

The official `demo-inveniordm` image does not include `invenio-s3`, which is required for S3-compatible object storage (MinIO).

## Build

```bash
cd docker/invenio
./build.sh
```

## Usage

Update `k8s/apps/invenio/invenio-deployment.yaml` and `k8s/apps/invenio/invenio-worker-deployment.yaml` to use the custom image.

## Storage Configuration

The image installs `invenio-s3` but you still need to configure the storage factory via environment variables or `invenio.cfg`:

```python
FILES_REST_STORAGE_FACTORY = 'invenio_s3.storage.s3fs_storage_factory'
```
