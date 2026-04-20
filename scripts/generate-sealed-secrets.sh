#!/usr/bin/env bash

# Generate new credentials and seal them using kubeseal
# This script creates fresh credentials for: MinIO, Grafana, Velero, Cloudflared

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SECRETS_DIR="$REPO_ROOT/secrets"
K8S_SECRETS_DIR="$REPO_ROOT/k8s/infra"

# Check if kubeseal is available
if ! command -v kubeseal &> /dev/null; then
  echo "Error: kubeseal not found. Please install it first:"
  echo "  brew install kubeseal  # macOS"
  echo "  # or download from https://github.com/bitnami-labs/sealed-secrets/releases"
  exit 1
fi

# Check if we can connect to the cluster
if ! kubectl cluster-info &> /dev/null; then
  echo "Error: Cannot connect to Kubernetes cluster. Please check your kubeconfig."
  exit 1
fi

# Check if sealed-secrets controller is ready
if ! kubectl get -n kube-system pods -l name=sealed-secrets-controller -o jsonpath='{.items[0].status.phase}' 2>/dev/null | grep -q Running; then
  echo "Error: Sealed-secrets controller is not running in the cluster."
  echo "Please deploy it first (it should be at sync-wave -3)."
  exit 1
fi

# Ensure directories exist
mkdir -p "$SECRETS_DIR"
mkdir -p "$K8S_SECRETS_DIR/minio"
mkdir -p "$K8S_SECRETS_DIR/monitoring"
mkdir -p "$K8S_SECRETS_DIR/velero"
mkdir -p "$K8S_SECRETS_DIR/cloudflared"

echo "Generating new credentials..."

# === MinIO Credentials ===
echo -n "MinIO... "
MINIO_ROOT_USER="minioadmin-$(openssl rand -hex 4)"
MINIO_ROOT_PASSWORD="minio-$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-32)"
echo "$MINIO_ROOT_USER" > "$SECRETS_DIR/minio-root-user.txt"
echo "$MINIO_ROOT_PASSWORD" > "$SECRETS_DIR/minio-root-password.txt"

kubectl create secret generic minio-credentials \
  --namespace minio \
  --from-literal=rootUser="$MINIO_ROOT_USER" \
  --from-literal=rootPassword="$MINIO_ROOT_PASSWORD" \
  --dry-run=client -o yaml | \
  kubeseal --controller-namespace kube-system --controller-name sealed-secrets-controller -o yaml \
  > "$K8S_SECRETS_DIR/minio/minio-credentials-secret.yaml"
echo "done"

# === Grafana Admin Credentials ===
echo -n "Grafana... "
GRAFANA_ADMIN_USER="admin"
GRAFANA_ADMIN_PASSWORD="grafana-$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-32)"
echo "$GRAFANA_ADMIN_USER" > "$SECRETS_DIR/grafana-admin-user.txt"
echo "$GRAFANA_ADMIN_PASSWORD" > "$SECRETS_DIR/grafana-admin-password.txt"

kubectl create secret generic grafana-admin-credentials \
  --namespace monitoring \
  --from-literal=user="$GRAFANA_ADMIN_USER" \
  --from-literal=password="$GRAFANA_ADMIN_PASSWORD" \
  --dry-run=client -o yaml | \
  kubeseal --controller-namespace kube-system --controller-name sealed-secrets-controller -o yaml \
  > "$K8S_SECRETS_DIR/monitoring/grafana-admin-secret.yaml"
echo "done"

# === Velero Credentials (for MinIO S3-compatible storage) ===
echo -n "Velero... "
# Use MinIO user/password for Velero
VELOCITY_USER="$MINIO_ROOT_USER"
VELOCITY_PASSWORD="$MINIO_ROOT_PASSWORD"

# Velero AWS plugin expects a "cloud" key with AWS credentials format
cat > /tmp/velero-creds.txt <<EOF
[default]
aws_access_key_id=$VELOCITY_USER
aws_secret_access_key=$VELOCITY_PASSWORD
EOF

kubectl create secret generic velero-credentials \
  --namespace velero \
  --from-file=cloud=/tmp/velero-creds.txt \
  --dry-run=client -o yaml | \
  kubeseal --controller-namespace kube-system --controller-name sealed-secrets-controller -o yaml \
  > "$K8S_SECRETS_DIR/velero/velero-credentials-secret.yaml"
rm /tmp/velero-creds.txt

echo "$VELOCITY_USER" > "$SECRETS_DIR/velero-access-key-id.txt"
echo "$VELOCITY_PASSWORD" > "$SECRETS_DIR/velero-secret-access-key.txt"
echo "done"

# === Cloudflared Credentials ===
echo -n "Cloudflared... "
# Note: Cloudflare tunnel credentials cannot be generated - they come from Cloudflare
# This script just creates a placeholder to remind the user
echo "CLOUDFLARE_TUNNEL_ID=placeholder" > "$SECRETS_DIR/cloudflared-tunnel-id.txt"
echo "CLOUDFLARE_TUNNEL_TOKEN=placeholder" > "$SECRETS_DIR/cloudflared-tunnel-token.txt"
echo "SKIPPED (requires Cloudflare API credentials)"

echo ""
echo "==================================================================="
echo "Credentials generated and sealed!"
echo "==================================================================="
echo ""
echo "IMPORTANT:"
echo "  1. Save the secrets directory securely:"
echo "     cp -r $SECRETS_DIR ~/safe-backup-location/"
echo ""
echo "  2. Cloudflared credentials need to be obtained from Cloudflare:"
echo "     - Log into https://dash.cloudflare.com"
echo "     - Navigate to Zero Trust > Networks > Tunnels"
echo "     - Create a new tunnel or get existing tunnel credentials"
echo "     - Update the sealed secret manually:"
echo "       kubectl -n kube-system get secret cloudflared-credentials -o json | kubeseal -o yaml > $K8S_SECRETS_DIR/cloudflared/cloudflared-credentials-secret.yaml"
echo ""
echo "  3. Commit the sealed secrets:"
echo "     git add $K8S_SECRETS_DIR/"
echo "     git commit -m 'Regenerate all credentials'"
echo ""
echo "  4. DO NOT commit the unencrypted secrets directory:"
echo "     echo 'secrets/' >> .gitignore"
echo ""
echo "Generated files:"
echo "  - $K8S_SECRETS_DIR/minio/minio-credentials-secret.yaml"
echo "  - $K8S_SECRETS_DIR/monitoring/grafana-admin-secret.yaml"
echo "  - $K8S_SECRETS_DIR/velero/velero-credentials-secret.yaml"
echo "  - $K8S_SECRETS_DIR/cloudflared/cloudflared-credentials-secret.yaml (update manually)"
echo ""
echo "Unencrypted credentials saved to: $SECRETS_DIR/"
echo "==================================================================="
