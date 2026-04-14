#!/bin/bash
# =============================================================================
# Infrastructure Bootstrap - ArgoCD only
# Run ONCE on a clean cluster to install ArgoCD.
# After this, ArgoCD syncs ALL infrastructure automatically:
#   Wave -5: Security policies (NetworkPolicies, LimitRanges, ResourceQuotas, PodSecurityAdmission)
#   Wave -4: Traefik + Cloudflare Tunnel DaemonSet
#   Wave -3: Sealed Secrets controller + ArgoCD self-management
#   Wave -2: Cert-manager
#   Wave  2: Velero (backups)
#   Wave  5: Monitoring (Prometheus/Grafana)
#
# After bootstrap, run:
#   1. ./external-lb/scripts/bootstrap-dev.sh  (creates tunnel token secret)
#   2. ./scripts/verify-infra.sh               (verify everything is healthy)
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

FORCE_RESET=false

# Parse command line arguments
for arg in "$@"; do
  case $arg in
  --force-reset)
    FORCE_RESET=true
    shift
    ;;
  *)
    echo "Unknown argument: $arg"
    echo "Usage: $0 [--force-reset]"
    echo "  --force-reset  Remove existing ArgoCD installation without prompting"
    exit 1
    ;;
  esac
done

echo "============================================"
echo "Infrastructure Bootstrap - ArgoCD Only"
echo "============================================"
if [ "$FORCE_RESET" = true ]; then
  echo "*** FORCE RESET MODE ***"
fi
echo ""

# --- Check prerequisites ---
if ! command -v kubectl &>/dev/null; then
  echo "ERROR: kubectl not found. Please install kubectl first."
  exit 1
fi

echo "==> Verifying cluster access"
if ! kubectl cluster-info &>/dev/null; then
  echo "ERROR: Cannot connect to Kubernetes cluster. Check your kubeconfig."
  exit 1
fi
echo "    Cluster is reachable."

NODES=$(kubectl get nodes --no-headers 2>/dev/null | grep -c 'Ready' || echo "0")
if [ "$NODES" -lt 1 ]; then
  echo "ERROR: No ready nodes found in cluster."
  exit 1
fi
echo "    Found $NODES ready node(s)."

# --- Cleanup function ---
cleanup_existing_installation() {
  echo ""
  echo "==> Cleaning up existing ArgoCD installation..."

  # Delete ArgoCD namespace
  if kubectl get namespace argocd &>/dev/null; then
    echo "    Deleting ArgoCD namespace..."
    kubectl delete namespace argocd --cascade=orphan --force --grace-period=0 2>/dev/null || true
    # Wait for namespace to be fully deleted
    echo "    Waiting for ArgoCD namespace to be removed..."
    for _ in $(seq 1 30); do
      if ! kubectl get namespace argocd &>/dev/null; then
        break
      fi
      sleep 2
    done
    if kubectl get namespace argocd &>/dev/null; then
      echo "    WARNING: ArgoCD namespace still exists, force removing..."
      kubectl patch namespace argocd -p '{"metadata":{"finalizers":[]}}' --type=merge 2>/dev/null || true
      sleep 5
    fi
    echo "    ArgoCD namespace removed."
  fi

  echo ""
}

# --- Check for existing installation ---
HAS_EXISTING_INSTALL=false
if kubectl get namespace argocd &>/dev/null; then
  HAS_EXISTING_INSTALL=true
fi

if [ "$HAS_EXISTING_INSTALL" = true ]; then
  if [ "$FORCE_RESET" = true ]; then
    echo ""
    echo "WARNING: Existing installation detected. Force-reset mode enabled."
    cleanup_existing_installation
  else
    echo ""
    echo "WARNING: ArgoCD namespace already exists."
    echo "  This script will OVERWRITE the ArgoCD installation."
    read -p "Continue? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      exit 0
    fi
    cleanup_existing_installation
  fi
fi

# --- Install ArgoCD via Kustomize ---
echo ""
echo "==> Installing ArgoCD (kustomize)"
echo "    Source: k8s/infra/argocd/"

kubectl apply -k "$REPO_ROOT/k8s/infra/argocd/"

echo "    Waiting for ArgoCD server to start (up to 120s)..."
kubectl wait --for=condition=available \
  deployment/argocd-server \
  -n argocd \
  --timeout=120s 2>/dev/null || true

ARGO_PODS=$(kubectl get pods -n argocd -l app.kubernetes.io/name=argocd-server --no-headers 2>/dev/null | awk '{print $1}')
if [ -n "$ARGO_PODS" ]; then
  echo "    ArgoCD server pod: $ARGO_PODS"
fi

echo "    ArgoCD installed successfully."

# --- Get ArgoCD admin password ---
echo ""
echo "==> ArgoCD admin credentials"
ARGOCD_POD=$(kubectl get pods -n argocd -l app.kubernetes.io/name=argocd-server -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -n "$ARGOCD_POD" ]; then
  ADMIN_PASS=$(kubectl get secret -n argocd argocd-initial-admin-secret \
    -o jsonpath="{.data.password}" 2>/dev/null | base64 -d 2>/dev/null || echo "")

  if [ -z "$ADMIN_PASS" ]; then
    ADMIN_PASS=$(kubectl exec -n argocd "$ARGOCD_POD" -- \
      argocd admin initial-password 2>/dev/null | tail -1 | tr -d '[:space:]' || echo "")
  fi

  if [ -z "$ADMIN_PASS" ]; then
    ADMIN_PASS=$(kubectl exec -n argocd "$ARGOCD_POD" -- \
      argocd initial-password 2>/dev/null | tail -1 | tr -d '[:space:]' || echo "")
  fi

  if [ -n "$ADMIN_PASS" ]; then
    echo "    Username: admin"
    echo "    Password: $ADMIN_PASS"
    echo ""
    echo "    IMPORTANT: Change the default password after first login:"
    echo "    argocd account update-password"
  else
    echo "    WARNING: Could not retrieve ArgoCD admin password automatically."
    echo ""
    echo "    Get the password manually with:"
    echo "    kubectl get secret -n argocd argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
    echo ""
    echo "    Username: admin"
  fi
fi

# --- Apply the infra project (so ArgoCD has a project to sync apps into) ---
echo ""
echo "==> Applying ArgoCD project (infra)"
kubectl apply -f "$REPO_ROOT/argocd/projects/infra-project.yaml"

# --- Summary ---
echo ""
echo "============================================"
echo "ArgoCD Bootstrap Complete!"
echo "============================================"
echo ""
echo "ArgoCD will now automatically sync all infrastructure apps:"
echo ""
echo "  Wave -5: security-policies  (NetworkPolicies, LimitRanges, ResourceQuotas)"
echo "  Wave -4: traefik, cloudflared"
echo "  Wave -3: sealed-secrets, argocd-self"
echo "  Wave -2: cert-manager"
echo "  Wave  2: velero"
echo "  Wave  5: monitoring"
echo ""
echo "Next steps:"
echo "  1. Create cloudflared tunnel token: ./external-lb/scripts/bootstrap-dev.sh"
echo "     (This creates the tunnel-token secret that cloudflared needs)"
echo "  2. Verify infra is healthy: ./scripts/verify-infra.sh"
echo "  3. Log into ArgoCD: https://argocd.vityasy.me"
echo "     (after tunnel is active)"
echo ""
