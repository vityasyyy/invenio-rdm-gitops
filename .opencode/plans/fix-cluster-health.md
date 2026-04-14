# Plan: Fix CI Error & Complete ArgoCD Bootstrap

## Problem Summary

1. **CI Pipeline Broken**: `kustomize build k8s/infra/argocd/` fails with "must specify a target for JSON patch"
2. **ArgoCD Apps Not Applied**: No Application resources in cluster, so ArgoCD can't auto-sync anything
3. **Security Gaps**: ArgoCD components running without hardened security contexts
4. **Missing infra project**: Only `default` AppProject exists, not `infra`

## Root Cause: Patch File Format

All 8 JSON patch files use `- target:` (YAML list) instead of `target:` (YAML mapping).
The kustomize `patches` field with `path:` expects each external patch file to be a **single mapping document**, not a list.

## Changes Required

### 1. Fix 8 Patch Files (remove leading `- `)

**Files to change** (identical edit pattern — remove leading `- ` from line 1, fix indentation):

#### k8s/infra/argocd/patches/kustomize/security-context-server.yaml
```yaml
# BEFORE (broken):
- target:
    kind: Deployment
    name: argocd-server
  patch: |

# AFTER (fixed):
target:
  kind: Deployment
  name: argocd-server
patch: |
```

Same change for these 6 files:
- `k8s/infra/argocd/patches/kustomize/security-context-repo.yaml`
- `k8s/infra/argocd/patches/kustomize/security-context-controller.yaml`
- `k8s/infra/argocd/patches/kustomize/security-context-dex.yaml`
- `k8s/infra/argocd/patches/kustomize/security-context-redis.yaml`
- `k8s/infra/argocd/patches/kustomize/security-context-notifications.yaml`
- `k8s/infra/argocd/patches/kustomize/security-context-applicationset.yaml`

#### k8s/infra/sealed-secrets/patches/kustomize/security-context.yaml
Same fix: `- target:` → `target:` (remove leading `- `, fix indentation)

### 2. Verify kustomize builds pass

```bash
kustomize build k8s/infra/argocd/ > /dev/null
kustomize build k8s/infra/sealed-secrets/ > /dev/null
kustomize build k8s/infra/security/ > /dev/null
kustomize build external-lb/k8s/ > /dev/null
```

### 3. Apply ArgoCD infra project

```bash
kubectl apply -f argocd/projects/infra-project.yaml
```

### 4. Apply ArgoCD Application manifests

```bash
kubectl apply -f argocd/apps/
```

This creates 8 Application resources. ArgoCD will auto-sync them in wave order:
- Wave -5: security-policies (already applied manually, ArgoCD will adopt)
- Wave -4: traefik, cloudflared (already running, ArgoCD will adopt)
- Wave -3: sealed-secrets (already running), argocd-self (re-applies ArgoCD WITH security patches)
- Wave -2: cert-manager (NEW — will be installed)
- Wave  2: velero (NEW — will be installed)
- Wave  5: monitoring (NEW — will be installed)

### 5. Wait for sync & verify

```bash
# Watch app status
kubectl get applications -n argocd -w

# Run verification
./scripts/verify-infra.sh
```

### 6. Post-sync security verification

```bash
# Verify ArgoCD pods now have security contexts
kubectl get pods -n argocd -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.securityContext.runAsNonRoot}{"\n"}{end}'

# Verify all apps are Healthy/Synced
kubectl get applications -n argocd
```

## Expected Outcome

- CI pipeline passes
- All 8 ArgoCD apps sync and reach Healthy status
- ArgoCD components restart with hardened security contexts
- cert-manager, velero, and monitoring stack installed automatically
- Cluster fully secured with network policies, pod security admission, resource quotas, and hardened pod security contexts
