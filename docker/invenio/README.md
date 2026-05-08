# Custom Invenio RDM Image with CI/CD Pipeline

## Overview

This directory contains everything needed to build, push, and deploy a custom InvenioRDM Docker image with:
- **S3/MinIO support** (`invenio-s3` pre-installed)
- **Custom themes/templates** (infrastructure ready)
- **Modern stack** (Python 3.12, Debian Bookworm, Gunicorn)
- **Automated CI/CD** (GitHub Actions builds on every push)
- **Automated CD** (ArgoCD Image Updater deploys new images automatically)

## Architecture

```
Git Push → GitHub Actions → Build Image → Push to GHCR
                                              ↓
                                    ArgoCD Image Updater
                                              ↓
                                       Update Manifests
                                              ↓
                                         ArgoCD Sync
                                              ↓
                                        Deploy to K8s
```

## Directory Structure

```
docker/invenio/
├── Dockerfile           # Multi-stage build definition
├── requirements.txt     # Python dependencies
├── invenio.cfg         # Invenio configuration (S3, DB, Redis, etc.)
├── entrypoint.sh       # Container entrypoint script
└── README.md           # This file

site/                   # Custom Python modules (optional)
templates/              # Custom Jinja2 templates (optional)
static/                 # Custom CSS/JS/images (optional)
translations/           # Custom i18n translations (optional)
app_data/               # Fixtures, vocabularies (optional)
```

## How It Works

### 1. Build Trigger
The image builds automatically when you push to `main` (tagged with Git SHA) or push a git tag like `v1.0.0` (tagged with semver).

### 2. Image Tags
| Trigger | Tag Format | Example |
|---------|-----------|---------|
| Push to `main` | `git-sha` + `latest` | `abc1234`, `latest` |
| Push tag `v1.2.3` | `1.2.3` + `1.2` | `v1.2.3`, `1.2` |
| Manual workflow | Custom tag | `my-custom-tag` |

### 3. Registry
Images are pushed to GitHub Container Registry:
```
ghcr.io/vityasyyy/invenio-rdm-custom:latest
ghcr.io/vityasyyy/invenio-rdm-custom:abc1234
ghcr.io/vityasyyy/invenio-rdm-custom:v1.0.0
```

### 4. Auto-Deployment
ArgoCD Image Updater watches the registry. When a new `latest` image appears:
1. It updates the image tag in the Kubernetes manifests
2. Commits the change back to Git
3. ArgoCD detects the change and syncs the cluster

## Customization

### Adding Custom Themes

1. **Templates**: Add Jinja2 templates to `templates/semantic-ui/invenio_app_rdm/`
2. **Styles**: Add CSS to `static/css/` and reference in templates
3. **Images**: Add logos/images to `static/images/`
4. **Site Code**: Add custom Python modules to `site/`

After making changes, push to `main` — the CI pipeline will rebuild and redeploy automatically.

### Example: Custom Homepage

```html
<!-- templates/semantic-ui/invenio_app_rdm/frontpage.html -->
{% extends "invenio_app_rdm/frontpage.html" %}

{% block page_body %}
  <div class="custom-hero">
    <h1>My Research Repository</h1>
    <p>Custom welcome message here</p>
  </div>
  {{ super() }}
{% endblock %}
```

## Manual Build

If you want to build locally:

```bash
cd /path/to/repo

# Build
docker build -t ghcr.io/vityasyyy/invenio-rdm-custom:latest -f docker/invenio/Dockerfile .

# Test
docker run --rm ghcr.io/vityasyyy/invenio-rdm-custom:latest python -c "import invenio_s3; print('OK')"

# Push (requires GHCR auth)
docker push ghcr.io/vityasyyy/invenio-rdm-custom:latest
```

## Troubleshooting

### Build fails with Node.js errors
The static asset build requires Node.js. If you see webpack errors, check that your `static/` and `templates/` files are valid.

### Image doesn't start
Check the entrypoint script logs. Common issues:
- Missing environment variables (check `invenio-app-config` ConfigMap)
- Database not reachable (check init containers)
- S3 credentials missing (check `invenio-app-secrets`)

### ArgoCD Image Updater not working
1. Check if Image Updater pod is running: `kubectl -n argocd get pods -l app.kubernetes.io/name=argocd-image-updater`
2. Check logs: `kubectl -n argocd logs -l app.kubernetes.io/name=argocd-image-updater`
3. Verify annotations on the Application: `kubectl -n argocd get application invenio-bootstrap -o yaml | grep image-updater`

## Security Notes

- The image runs as non-root user (UID 1654)
- Read-only root filesystem where possible
- Security contexts and capabilities dropped in Kubernetes manifests
- GHCR authentication uses GitHub Actions token (no long-lived secrets)
