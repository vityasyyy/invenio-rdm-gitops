#!/usr/bin/env bash

# Centralized sealed-secret generator for infra and Invenio.
# Plaintext is written only under the gitignored secrets/ directory.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_SECRETS_DIR="$REPO_ROOT/secrets"
INFRA_SECRETS_DIR="$REPO_ROOT/k8s/infra"
APPS_SECRETS_DIR="$REPO_ROOT/k8s/apps"
EXTERNAL_LB_SECRETS_DIR="$REPO_ROOT/external-lb/k8s"

MINIO_ROOT_USER_VALUE=""
MINIO_ROOT_PASSWORD_VALUE=""

usage() {
  cat <<'EOF'
Usage:
  ./scripts/generate-sealed-secrets.sh [component...]

Components:
  all          Generate every managed sealed secret (default)
  minio        Regenerate MinIO root credentials
  grafana      Regenerate Grafana admin credentials
  velero       Regenerate Velero S3 credentials
  invenio      Regenerate Invenio app secret bundle
  cloudflared  Seal cloudflared tunnel token if CLOUDFLARE_TUNNEL_TOKEN is set

Examples:
  ./scripts/generate-sealed-secrets.sh
  ./scripts/generate-sealed-secrets.sh invenio
  INVENIO_SECRET_KEY="$(openssl rand -hex 32)" ./scripts/generate-sealed-secrets.sh invenio
  CLOUDFLARE_TUNNEL_TOKEN="$TOKEN" ./scripts/generate-sealed-secrets.sh cloudflared
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

random_hex() {
  local bytes="${1:-16}"
  openssl rand -hex "$bytes"
}

ensure_cluster_access() {
  if ! command -v kubeseal &>/dev/null; then
    die "kubeseal not found. Install it first with: brew install kubeseal"
  fi

  if ! kubectl cluster-info &>/dev/null; then
    die "Cannot connect to Kubernetes cluster. Check your kubeconfig."
  fi

  if ! kubectl get -n kube-system pods -l name=sealed-secrets-controller \
    -o jsonpath='{.items[0].status.phase}' 2>/dev/null | grep -q Running; then
    die "Sealed Secrets controller is not running in kube-system."
  fi
}

ensure_dirs() {
  mkdir -p \
    "$LOCAL_SECRETS_DIR/minio" \
    "$LOCAL_SECRETS_DIR/monitoring" \
    "$LOCAL_SECRETS_DIR/velero" \
    "$LOCAL_SECRETS_DIR/invenio" \
    "$LOCAL_SECRETS_DIR/cloudflared" \
    "$INFRA_SECRETS_DIR/minio" \
    "$INFRA_SECRETS_DIR/monitoring" \
    "$INFRA_SECRETS_DIR/velero" \
    "$APPS_SECRETS_DIR/invenio" \
    "$EXTERNAL_LB_SECRETS_DIR"
}

write_text() {
  local path="$1"
  local value="$2"
  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$value" >"$path"
}

seal_literals() {
  local namespace="$1"
  local secret_name="$2"
  local output_path="$3"
  shift 3

  local args=()
  local literal
  for literal in "$@"; do
    args+=(--from-literal="$literal")
  done

  kubectl create secret generic "$secret_name" \
    --namespace "$namespace" \
    "${args[@]}" \
    --dry-run=client -o yaml |
    kubeseal --controller-namespace kube-system \
      --controller-name sealed-secrets-controller \
      -o yaml >"$output_path"
}

seal_env_file() {
  local namespace="$1"
  local secret_name="$2"
  local env_file="$3"
  local output_path="$4"

  kubectl create secret generic "$secret_name" \
    --namespace "$namespace" \
    --from-env-file="$env_file" \
    --dry-run=client -o yaml |
    kubeseal --controller-namespace kube-system \
      --controller-name sealed-secrets-controller \
      -o yaml >"$output_path"
}

seal_file() {
  local namespace="$1"
  local secret_name="$2"
  local key_name="$3"
  local file_path="$4"
  local output_path="$5"

  kubectl create secret generic "$secret_name" \
    --namespace "$namespace" \
    --from-file="$key_name=$file_path" \
    --dry-run=client -o yaml |
    kubeseal --controller-namespace kube-system \
      --controller-name sealed-secrets-controller \
      -o yaml >"$output_path"
}

load_minio_creds() {
  if [[ -z "$MINIO_ROOT_USER_VALUE" ]]; then
    MINIO_ROOT_USER_VALUE="${MINIO_ROOT_USER:-}"
  fi
  if [[ -z "$MINIO_ROOT_PASSWORD_VALUE" ]]; then
    MINIO_ROOT_PASSWORD_VALUE="${MINIO_ROOT_PASSWORD:-}"
  fi

  if [[ -z "$MINIO_ROOT_USER_VALUE" && -f "$LOCAL_SECRETS_DIR/minio/root-user.txt" ]]; then
    MINIO_ROOT_USER_VALUE="$(<"$LOCAL_SECRETS_DIR/minio/root-user.txt")"
  fi
  if [[ -z "$MINIO_ROOT_PASSWORD_VALUE" && -f "$LOCAL_SECRETS_DIR/minio/root-password.txt" ]]; then
    MINIO_ROOT_PASSWORD_VALUE="$(<"$LOCAL_SECRETS_DIR/minio/root-password.txt")"
  fi

  if [[ -z "$MINIO_ROOT_USER_VALUE" || -z "$MINIO_ROOT_PASSWORD_VALUE" ]]; then
    die "MinIO credentials are required for this component. Run 'minio' first or export MINIO_ROOT_USER/MINIO_ROOT_PASSWORD."
  fi
}

generate_minio() {
  echo -n "MinIO... "
  MINIO_ROOT_USER_VALUE="${MINIO_ROOT_USER:-minioadmin-$(random_hex 4)}"
  MINIO_ROOT_PASSWORD_VALUE="${MINIO_ROOT_PASSWORD:-minio-$(random_hex 12)}"

  write_text "$LOCAL_SECRETS_DIR/minio/root-user.txt" "$MINIO_ROOT_USER_VALUE"
  write_text "$LOCAL_SECRETS_DIR/minio/root-password.txt" "$MINIO_ROOT_PASSWORD_VALUE"

  seal_literals "minio" "minio-credentials" \
    "$INFRA_SECRETS_DIR/minio/minio-credentials-secret.yaml" \
    "rootUser=$MINIO_ROOT_USER_VALUE" \
    "rootPassword=$MINIO_ROOT_PASSWORD_VALUE"
  echo "done"
}

generate_grafana() {
  echo -n "Grafana... "
  local grafana_admin_user="${GRAFANA_ADMIN_USER:-admin}"
  local grafana_admin_password="${GRAFANA_ADMIN_PASSWORD:-grafana-$(random_hex 16)}"

  write_text "$LOCAL_SECRETS_DIR/monitoring/admin-user.txt" "$grafana_admin_user"
  write_text "$LOCAL_SECRETS_DIR/monitoring/admin-password.txt" "$grafana_admin_password"

  seal_literals "monitoring" "monitoring-grafana" \
    "$INFRA_SECRETS_DIR/monitoring/grafana-admin-secret.yaml" \
    "admin-user=$grafana_admin_user" \
    "admin-password=$grafana_admin_password"
  echo "done"
}

generate_velero() {
  echo -n "Velero... "
  load_minio_creds

  local velero_access_key_id="${VELERO_ACCESS_KEY_ID:-$MINIO_ROOT_USER_VALUE}"
  local velero_secret_access_key="${VELERO_SECRET_ACCESS_KEY:-$MINIO_ROOT_PASSWORD_VALUE}"
  local velero_cloud_file="$LOCAL_SECRETS_DIR/velero/cloud"

  cat >"$velero_cloud_file" <<EOF
[default]
aws_access_key_id=$velero_access_key_id
aws_secret_access_key=$velero_secret_access_key
EOF

  seal_file "velero" "velero-credentials" "cloud" \
    "$velero_cloud_file" \
    "$INFRA_SECRETS_DIR/velero/velero-credentials-secret.yaml"
  echo "done"
}

generate_invenio() {
  echo -n "Invenio... "
  load_minio_creds

  local db_host="${INVENIO_DB_HOST:-invenio-postgresql.invenio.svc.cluster.local}"
  local db_port="${INVENIO_DB_PORT:-5432}"
  local db_name="${INVENIO_DB_NAME:-invenio}"
  local db_user="${INVENIO_DB_USER:-invenio}"
  local db_password="${INVENIO_DB_PASSWORD:-$(random_hex 16)}"
  local redis_host="${INVENIO_REDIS_HOST:-invenio-redis.invenio.svc.cluster.local}"
  local redis_port="${INVENIO_REDIS_PORT:-6379}"
  local redis_password="${INVENIO_REDIS_PASSWORD:-}"
  local search_url="${INVENIO_OPENSEARCH_URL:-http://invenio-search.invenio.svc.cluster.local:9200}"
  local s3_access_key_id="${INVENIO_S3_ACCESS_KEY_ID:-$MINIO_ROOT_USER_VALUE}"
  local s3_secret_access_key="${INVENIO_S3_SECRET_ACCESS_KEY:-$MINIO_ROOT_PASSWORD_VALUE}"
  local secret_key="${INVENIO_SECRET_KEY:-$(random_hex 32)}"
  local sqlalchemy_uri="${INVENIO_SQLALCHEMY_DATABASE_URI:-postgresql+psycopg2://${db_user}:${db_password}@${db_host}:${db_port}/${db_name}}"
  local cache_redis_url="${INVENIO_CACHE_REDIS_URL:-}"

  if [[ -z "$cache_redis_url" ]]; then
    if [[ -n "$redis_password" ]]; then
      cache_redis_url="redis://:${redis_password}@${redis_host}:${redis_port}/0"
    else
      cache_redis_url="redis://${redis_host}:${redis_port}/0"
    fi
  fi

  local invenio_env_file="$LOCAL_SECRETS_DIR/invenio/invenio-app-secrets.env"
  cat >"$invenio_env_file" <<EOF
# Generated by scripts/generate-sealed-secrets.sh
SQLALCHEMY_DATABASE_URI=$sqlalchemy_uri
CACHE_REDIS_URL=$cache_redis_url
OPENSEARCH_URL=$search_url
S3_ACCESS_KEY_ID=$s3_access_key_id
S3_SECRET_ACCESS_KEY=$s3_secret_access_key
INVENIO_SECRET_KEY=$secret_key
EOF

  seal_env_file "invenio" "invenio-app-secrets" \
    "$invenio_env_file" \
    "$APPS_SECRETS_DIR/invenio/app-sealed-secret.yaml"
  echo "done"
}

generate_cloudflared() {
  echo -n "Cloudflared... "
  local tunnel_token="${CLOUDFLARE_TUNNEL_TOKEN:-}"
  if [[ -z "$tunnel_token" ]]; then
    echo "skipped (set CLOUDFLARE_TUNNEL_TOKEN to seal this secret)"
    return 0
  fi

  write_text "$LOCAL_SECRETS_DIR/cloudflared/tunnel-token.txt" "$tunnel_token"

  seal_literals "kube-system" "cloudflared-credentials" \
    "$EXTERNAL_LB_SECRETS_DIR/cloudflared-credentials-secret.yaml" \
    "tunnel-token=$tunnel_token"
  echo "done"
}

main() {
  local want_minio=0
  local want_grafana=0
  local want_velero=0
  local want_invenio=0
  local want_cloudflared=0
  local component

  if [[ $# -eq 0 ]]; then
    set -- all
  fi

  for component in "$@"; do
    case "$component" in
      -h|--help)
        usage
        exit 0
        ;;
      all)
        want_minio=1
        want_grafana=1
        want_velero=1
        want_invenio=1
        want_cloudflared=1
        ;;
      minio|grafana|velero|invenio|cloudflared)
        case "$component" in
          minio) want_minio=1 ;;
          grafana) want_grafana=1 ;;
          velero) want_velero=1 ;;
          invenio) want_invenio=1 ;;
          cloudflared) want_cloudflared=1 ;;
        esac
        ;;
      *)
        die "Unknown component: $component"
        ;;
    esac
  done

  ensure_cluster_access
  ensure_dirs

  echo "Generating sealed secrets..."

  if [[ "$want_minio" -eq 1 ]]; then
    generate_minio
  fi
  if [[ "$want_grafana" -eq 1 ]]; then
    generate_grafana
  fi
  if [[ "$want_velero" -eq 1 ]]; then
    generate_velero
  fi
  if [[ "$want_invenio" -eq 1 ]]; then
    generate_invenio
  fi
  if [[ "$want_cloudflared" -eq 1 ]]; then
    generate_cloudflared
  fi

  echo ""
  echo "Generated sealed secret files:"
  [[ "$want_minio" -eq 1 ]] && echo "  - $INFRA_SECRETS_DIR/minio/minio-credentials-secret.yaml"
  [[ "$want_grafana" -eq 1 ]] && echo "  - $INFRA_SECRETS_DIR/monitoring/grafana-admin-secret.yaml"
  [[ "$want_velero" -eq 1 ]] && echo "  - $INFRA_SECRETS_DIR/velero/velero-credentials-secret.yaml"
  [[ "$want_invenio" -eq 1 ]] && echo "  - $APPS_SECRETS_DIR/invenio/app-sealed-secret.yaml"
  [[ "$want_cloudflared" -eq 1 ]] && echo "  - $EXTERNAL_LB_SECRETS_DIR/cloudflared-credentials-secret.yaml"
  echo ""
  echo "Plaintext files were written under: $LOCAL_SECRETS_DIR/"
  echo "Commit only the sealed YAML files; keep $LOCAL_SECRETS_DIR/ untracked."
}

main "$@"
