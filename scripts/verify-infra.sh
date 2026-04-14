#!/bin/bash
# =============================================================================
# Infrastructure Verification Script
# Verifies that ArgoCD, Sealed Secrets, and Cloudflare Tunnel are working
# =============================================================================

echo "========================================="
echo "Infrastructure Verification"
echo "========================================="
echo ""

PASS=0
FAIL=0

check() {
  local desc="$1"
  local cmd="$2"

  if eval "$cmd" >/dev/null 2>&1; then
    echo "✅ $desc"
    PASS=$((PASS + 1))
  else
    echo "❌ $desc"
    FAIL=$((FAIL + 1))
  fi
}

warn() {
  local desc="$1"
  echo "⚠️  $desc"
}

echo "--- Cluster Health ---"
check "Cluster is reachable" "kubectl cluster-info"
NODE_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")
echo "    Found $NODE_COUNT node(s)"

echo ""
echo "--- Storage Class ---"
check "StorageClass btd-nfs exists" "kubectl get storageclass btd-nfs"
RECLAIM=$(kubectl get storageclass btd-nfs -o jsonpath='{.reclaimPolicy}' 2>/dev/null || echo "Unknown")
if [ "$RECLAIM" = "Retain" ]; then
  echo "✅ StorageClass reclaimPolicy is Retain (safe for data protection)"
  PASS=$((PASS + 1))
else
  echo "⚠️  StorageClass reclaimPolicy is '$RECLAIM' (should be Retain for safety)"
fi

echo ""
echo "--- Traefik ---"
check "Traefik namespace exists" "kubectl get ns traefik"
check "Traefik service exists" "kubectl get svc -n traefik traefik"

echo ""
echo "--- ArgoCD ---"
check "ArgoCD namespace exists" "kubectl get ns argocd"
check "ArgoCD pods are running" "kubectl get pods -n argocd -l app.kubernetes.io/name=argocd-server | grep -q Running"
check "ArgoCD server deployment is ready" "kubectl get deployment -n argocd argocd-server -o jsonpath='{.status.readyReplicas}' | grep -q '1'"
check "ArgoCD repo server is running" "kubectl get deployment -n argocd argocd-repo-server -o jsonpath='{.status.readyReplicas}' | grep -q '1'"

echo ""
echo "--- Sealed Secrets ---"
check "Sealed Secrets CRD exists" "kubectl get crd sealedsecrets.bitnami.com"
check "Sealed Secrets controller pod is running" "kubectl get pods -n kube-system -l name=sealed-secrets-controller | grep -q Running"
check "Sealed Secrets controller is ready" "kubectl get pods -n kube-system -l name=sealed-secrets-controller -o jsonpath='{.items[0].status.containerStatuses[0].ready}' | grep -q true"
SEALED_KEY=$(kubectl get secret -n kube-system \
  -l sealedsecrets.bitnami.com/sealed-secrets-key=active \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [ -n "$SEALED_KEY" ]; then
  echo "✅ Sealed Secrets key secret exists"
  PASS=$((PASS + 1))
else
  echo "❌ Sealed Secrets key secret not found (controller may not be ready)"
  FAIL=$((FAIL + 1))
fi

echo ""
echo "--- Cloudflare Tunnel ---"
check "Cloudflared DaemonSet exists" "kubectl get ds -n kube-system cloudflared"
CLOUDFLARED_PODS=$(kubectl get pods -n kube-system -l app=cloudflared --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")
if [ "$CLOUDFLARED_PODS" -gt 0 ]; then
  echo "✅ Cloudflared pods running: $CLOUDFLARED_PODS"
  PASS=$((PASS + 1))
  READY_PODS=$(kubectl get pods -n kube-system -l app=cloudflared --no-headers 2>/dev/null | grep Running | wc -l | tr -d ' ' || echo "0")
  echo "    Ready: $READY_PODS / $CLOUDFLARED_PODS"
else
  echo "❌ No cloudflared pods found"
  FAIL=$((FAIL + 1))
fi

check "Cloudflared credentials secret exists" "kubectl get secret -n kube-system cloudflared-credentials"

echo ""
echo "--- External Connectivity ---"

# Test ArgoCD access via tunnel
echo "Testing ArgoCD access via https://argocd.vityasy.me ..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://argocd.vityasy.me 2>/dev/null || echo "000")

case "$HTTP_CODE" in
200)
  echo "✅ ArgoCD UI is accessible (HTTP 200)"
  PASS=$((PASS + 1))
  ;;
301 | 302)
  echo "⚠️  ArgoCD is redirecting (HTTP $HTTP_CODE) - tunnel may not be active yet"
  FAIL=$((FAIL + 1))
  ;;
404)
  echo "⚠️  ArgoCD UI returns 404 - check Traefik IngressRoute configuration"
  FAIL=$((FAIL + 1))
  ;;
000)
  echo "❌ Cannot connect to argocd.vityasy.me - check DNS and tunnel"
  FAIL=$((FAIL + 1))
  ;;
*)
  echo "⚠️  Unexpected HTTP code: $HTTP_CODE"
  FAIL=$((FAIL + 1))
  ;;
esac

echo ""
echo "--- ArgoCD Application Status ---"

# Check if ArgoCD API is accessible locally (via kubectl port-forward)
if kubectl get svc -n argocd argocd-server 2>/dev/null | grep -q "ClusterIP"; then
  echo "✅ ArgoCD server service exists"
  PASS=$((PASS + 1))
else
  echo "⚠️  ArgoCD server service not found or is not ClusterIP type"
fi

# Try to get app list from ArgoCD (requires argocd CLI)
if command -v argocd &>/dev/null; then
  echo ""
  echo "Fetching ArgoCD application list..."
  if argocd app list &>/dev/null; then
    echo "✅ ArgoCD API is accessible"
    PASS=$((PASS + 1))
    argocd app list 2>/dev/null || echo "    (requires authentication)"
  else
    warn "ArgoCD CLI installed but cannot connect (may need login)"
  fi
else
  echo "ℹ️  argocd CLI not installed (optional - install with: brew install argocd)"
fi

echo ""
echo "========================================="
echo "Results: $PASS passed, $FAIL failed"
echo "========================================="

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "⚠️  Some checks failed. Review the output above."
  echo ""
  echo "Common issues:"
  echo "  - ArgoCD pods not ready: kubectl get pods -n argocd"
  echo "  - Sealed Secrets not running: kubectl get pods -n kube-system -l app.kubernetes.io/name=sealed-secrets-controller"
  echo "  - Tunnel not connecting: kubectl get pods -n kube-system -l app=cloudflared"
  echo "  - DNS not resolving: wait a few minutes for propagation"
  echo ""
  echo "To start fresh, run: ./scripts/bootstrap-infra.sh --force-reset"
  exit 1
else
  echo ""
  echo "✅ All infrastructure checks passed!"
  echo ""
  echo "Your infrastructure is ready for application deployment."
  echo ""
  echo "Next steps:"
  echo "  1. Log into ArgoCD: https://argocd.vityasy.me"
  echo "  2. Change the default admin password: argocd account update-password"
  echo "  3. Create ArgoCD Applications for your workloads"
  exit 0
fi
