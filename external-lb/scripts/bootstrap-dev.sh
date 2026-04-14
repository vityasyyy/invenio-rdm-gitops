#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_DIR="$REPO_ROOT/external-lb/terraform/envs/dev"
K8S_DIR="$REPO_ROOT/external-lb/k8s"

echo "==> Loading environment variables from .env"
if [ -f "$ENV_DIR/.env" ]; then
  set -a
  source "$ENV_DIR/.env"
  set +a
else
  echo "ERROR: .env file not found at $ENV_DIR/.env"
  echo "Copy .env.example to .env and fill in your values"
  exit 1
fi

echo "==> Validating required variables"
: "${TF_VAR_cloudflare_api_token:?missing - set in .env}"
: "${TF_VAR_account_id:?missing - set in .env}"
: "${TF_VAR_zone_id:?missing - set in .env}"

echo "==> Checking prerequisites"
if ! command -v jq &>/dev/null; then
  echo "ERROR: jq not found. Please install jq (brew install jq on macOS)"
  exit 1
fi

if ! command -v kubectl &>/dev/null; then
  echo "ERROR: kubectl not found. Please install kubectl"
  exit 1
fi

if ! kubectl cluster-info &>/dev/null; then
  echo "ERROR: Cannot connect to Kubernetes cluster. Check your kubeconfig."
  exit 1
fi

echo "==> Running Terraform apply"
cd "$ENV_DIR"
terraform init -input=false
terraform apply -auto-approve

echo "==> Fetching tunnel credentials from Cloudflare API"
TUNNEL_ID=$(terraform output -raw tunnel_id 2>/dev/null)
if [ -z "$TUNNEL_ID" ]; then
  echo "ERROR: Could not get tunnel ID from Terraform state"
  exit 1
fi

echo "    Tunnel ID: $TUNNEL_ID"
echo "    Fetching tunnel token from Cloudflare API..."

TUNNEL_RESPONSE=$(curl -s "https://api.cloudflare.com/client/v4/accounts/${TF_VAR_account_id}/cfd_tunnel/${TUNNEL_ID}/token" \
  -H "Authorization: Bearer ${TF_VAR_cloudflare_api_token}" \
  -H "Content-Type: application/json")

if [ -z "$TUNNEL_RESPONSE" ]; then
  echo "ERROR: Empty response from Cloudflare API"
  exit 1
fi

SUCCESS=$(echo "$TUNNEL_RESPONSE" | jq -r '.success')
if [ "$SUCCESS" != "true" ]; then
  echo "ERROR: Cloudflare API returned an error:"
  echo "$TUNNEL_RESPONSE" | jq . 2>/dev/null || echo "$TUNNEL_RESPONSE"
  exit 1
fi

TUNNEL_TOKEN=$(echo "$TUNNEL_RESPONSE" | jq -r '.result // empty')
if [ -z "$TUNNEL_TOKEN" ]; then
  echo "ERROR: No token found in tunnel response"
  echo "Response:"
  echo "$TUNNEL_RESPONSE" | jq .
  exit 1
fi

echo "    Token retrieved successfully."

echo "==> Creating cloudflared credentials secret in kube-system"

kubectl create secret generic cloudflared-credentials \
  -n kube-system \
  --from-literal=tunnel-id="$TUNNEL_ID" \
  --from-literal=tunnel-token="$TUNNEL_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

# Verify the secret was created
SECRET_CHECK=$(kubectl get secret -n kube-system cloudflared-credentials -o jsonpath='{.data.tunnel-token}' 2>/dev/null || echo "")
if [ -z "$SECRET_CHECK" ]; then
  echo "ERROR: Failed to create cloudflared-credentials secret"
  exit 1
fi

echo "    Secret created and verified."

echo "==> Cloudflared DaemonSet is managed by ArgoCD (argocd/apps/cloudflared.yaml)"
echo "    ArgoCD will deploy the DaemonSet once it syncs."
echo ""
echo "==> Waiting for cloudflared pods to start (up to 180s)..."
CLOUDFLARED_WAIT_RETRIES=36
CLOUDFLARED_WAIT_COUNT=0
CLOUDFLARED_READY=false

while [ $CLOUDFLARED_WAIT_COUNT -lt $CLOUDFLARED_WAIT_RETRIES ]; do
  sleep 5
  CLOUDFLARED_WAIT_COUNT=$((CLOUDFLARED_WAIT_COUNT + 1))

  # Check if any pod is ready
  READY_PODS=$(kubectl get pods -n kube-system -l app=cloudflared --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d ' ')

  if [ "$READY_PODS" -gt 0 ]; then
    CLOUDFLARED_READY=true
    echo "    Found $READY_PODS running pod(s)."
    break
  fi

  # Show progress every 15 seconds
  if [ $((CLOUDFLARED_WAIT_COUNT % 3)) -eq 0 ]; then
    POD_STATUS=$(kubectl get pods -n kube-system -l app=cloudflared --no-headers 2>/dev/null || echo "  No pods found yet")
    echo "    Waiting... ($((CLOUDFLARED_WAIT_COUNT * 5))s)"
    echo "    $POD_STATUS"
  fi
done

if [ "$CLOUDFLARED_READY" = true ]; then
  echo "    cloudflared DaemonSet deployed successfully."
  echo ""
  echo "    Pods:"
  kubectl get pods -n kube-system -l app=cloudflared
else
  echo ""
  echo "    WARNING: cloudflared pods did not become ready within 180s."
  echo ""
  echo "    Troubleshooting steps:"
  echo "      1. Check pod status: kubectl get pods -n kube-system -l app=cloudflared"
  echo "      2. Check pod logs: kubectl logs -n kube-system -l app=cloudflared --tail=50"
  echo "      3. Check credentials secret: kubectl get secret -n kube-system cloudflared-credentials -o jsonpath='{.data.tunnel-token}' | base64 -d"
  echo "      4. Check ArgoCD sync: argocd app get cloudflared"
  echo ""
  echo "    Common issues:"
  echo "      - Image pull failure: Check if cloudflare/cloudflared:2025.2.0 is accessible"
  echo "      - Invalid credentials: Re-run this script to regenerate"
  echo "      - Node not ready: kubectl get nodes"
  echo "      - ArgoCD not synced yet: argocd app sync cloudflared"
  exit 1
fi

echo ""
echo "==> Tunnel deployment complete"
echo "    ArgoCD will be accessible at: https://argocd.vityasy.me"
echo ""
echo "==> To verify tunnel is active (wait ~2 min for DNS propagation):"
echo "    curl -v https://argocd.vityasy.me"
echo ""
