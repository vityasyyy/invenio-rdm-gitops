# Traefik 404 Fix & Codebase Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the NetworkPolicy blocking Traefik from the K8s API server (causing all 404s), add missing default-deny ingress for the traefik namespace, consolidate duplicated Middlewares, remove duplicate AppProjects, fix docs drift, and verify security posture.

**Architecture:** The root cause is a NetworkPolicy port/IP mismatch that blocks Traefik's informers from reaching the Kubernetes API. The fix is a namespaceSelector-based egress rule (pre-DNAT). We also consolidate four duplicated security-header Middlewares into one shared Middleware using Traefik's cross-namespace reference feature, and clean up duplicated ArgoCD AppProject files.

**Tech Stack:** Kubernetes NetworkPolicy, Traefik CRD (IngressRoute, Middleware), ArgoCD AppProject, Kustomize

---

## File Structure

| Action | File | Purpose |
|--------|------|---------|
| Modify | `k8s/infra/security/traefik-full-egress.yaml` | Fix API server egress rule |
| Modify | `k8s/infra/security/network-policies/default-deny.yaml` | Add traefik namespace default-deny |
| Modify | `k8s/infra/traefik/values.yaml` | Enable cross-namespace middleware refs |
| Modify | `k8s/infra/traefik/kustomization.yaml` | Add new shared middleware resource |
| Create | `k8s/infra/traefik/shared-security-headers-middleware.yaml` | Shared security headers middleware |
| Modify | `k8s/apps/invenio/invenio-ingressroute.yaml` | Remove inline middleware, reference shared |
| Modify | `k8s/infra/argocd/argocd-ingress.yaml` | Reduce argocd-headers to CSP-only |
| Modify | `k8s/infra/minio/minio-console-ingressroute.yaml` | Remove inline middleware, reference shared |
| Modify | `k8s/infra/monitoring/grafana-ingress.yaml` | Reference shared middleware |
| Delete | `k8s/infra/monitoring/grafana-headers-middleware.yaml` | Replaced by shared middleware |
| Delete | `argocd/projects/infra-project.yaml` | Duplicate of k8s/infra/argocd/projects/ |
| Delete | `argocd/projects/invenio-project.yaml` | Duplicate of k8s/infra/argocd/projects/ |
| Modify | `k8s/infra/traefik/README.md` | Fix docs drift |
| Modify | `k8s/infra/monitoring/kustomization.yaml` | Remove deleted middleware resource |

---

### Task 1: Fix Traefik API server egress NetworkPolicy

**Files:**
- Modify: `k8s/infra/security/traefik-full-egress.yaml:37-42`

- [ ] **Step 1: Edit the NetworkPolicy to replace the broken ipBlock rule**

Replace lines 37-42 in `k8s/infra/security/traefik-full-egress.yaml`:

```yaml
    # BEFORE:
    - to:
        - ipBlock:
            cidr: 10.43.0.0/16
      ports:
        - protocol: TCP
          port: 6443

    # AFTER:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: TCP
          port: 443
```

This allows Traefik to reach the K8s API server via the pre-DNAT ClusterIP address `10.43.0.1:443`, which DNATs to the node at `10.17.117.41:6443`. The namespaceSelector-based rule is evaluated before DNAT, so it matches correctly.

- [ ] **Step 2: Commit the NetworkPolicy fix**

```bash
git add k8s/infra/security/traefik-full-egress.yaml
git commit -m "fix: correct Traefik egress NetworkPolicy to reach K8s API server

The previous ipBlock rule (10.43.0.0/16:6443) was incorrect because:
- The API server ClusterIP is at 10.43.0.1:443 (pre-DNAT)
- Post-DNAT, traffic goes to 10.17.117.41:6443 which is outside the CIDR

This caused all Traefik informers to timeout, resulting in 404 errors
for all routes. Replaced with namespaceSelector targeting kube-system:443.
"
```

---

### Task 2: Add default-deny-ingress for traefik namespace

**Files:**
- Modify: `k8s/infra/security/network-policies/default-deny.yaml`

- [ ] **Step 1: Add a default-deny-ingress policy for the traefik namespace**

Append the following to `k8s/infra/security/network-policies/default-deny.yaml` (after the velero entry):

```yaml
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: traefik
spec:
  podSelector: {}
  policyTypes:
    - Ingress
```

The existing `allow-cloudflared-to-traefik` NetworkPolicy explicitly allows cloudflared traffic, so this default-deny does not break legitimate access.

- [ ] **Step 2: Commit the default-deny policy**

```bash
git add k8s/infra/security/network-policies/default-deny.yaml
git commit -m "security: add default-deny-ingress for traefik namespace

Traefik namespace was the only watched namespace missing a default-deny
ingress policy. The existing allow-cloudflared-to-traefik policy explicitly
permits legitimate traffic."
```

---

### Task 3: Restart Traefik and verify routing

**Files:** None (operational)

- [ ] **Step 1: Apply the NetworkPolicy changes via ArgoCD or kubectl**

```bash
kubectl apply -f k8s/infra/security/traefik-full-egress.yaml
kubectl apply -k k8s/infra/security/
```

- [ ] **Step 2: Restart Traefik to force reconnection**

```bash
kubectl rollout restart -n traefik deployment/traefik
kubectl rollout status -n traefik deployment/traefik
```

- [ ] **Step 3: Verify Traefik can reach the API server**

```bash
kubectl logs -n traefik -l app.kubernetes.io/name=traefik --tail=20
```

Expected: No more `dial tcp 10.43.0.1:443: i/o timeout` errors.

- [ ] **Step 4: Verify IngressRoutes are discovered**

```bash
kubectl get ingressroute -A
```

Expected: All 5 IngressRoutes listed (invenio-ui, invenio-api, argocd-server, grafana, minio-console).

- [ ] **Step 5: Test each route resolves**

```bash
kubectl get middleware -A
```

Expected: All middlewares listed in their respective namespaces.

---

### Task 4: Enable cross-namespace Middleware references in Traefik

**Files:**
- Modify: `k8s/infra/traefik/values.yaml:58`

- [ ] **Step 1: Change allowCrossNamespace from false to true**

In `k8s/infra/traefik/values.yaml`, change line 58:

```yaml
# BEFORE:
    allowCrossNamespace: false

# AFTER:
    allowCrossNamespace: true
```

This allows IngressRoutes in any namespace to reference Middlewares in the `traefik` namespace.

- [ ] **Step 2: Commit the Traefik values change**

```bash
git add k8s/infra/traefik/values.yaml
git commit -m "feat: enable cross-namespace middleware references in Traefik

Allows IngressRoutes in watched namespaces to reference the shared
security-headers middleware in the traefik namespace, eliminating
per-namespace middleware duplication."
```

---

### Task 5: Create shared security headers Middleware

**Files:**
- Create: `k8s/infra/traefik/shared-security-headers-middleware.yaml`
- Modify: `k8s/infra/traefik/kustomization.yaml` (add new resource)

- [ ] **Step 1: Create the shared Middleware manifest**

Create `k8s/infra/traefik/shared-security-headers-middleware.yaml`:

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: security-headers
  namespace: traefik
  annotations:
    argocd.argoproj.io/sync-wave: "3"
spec:
  headers:
    frameDeny: true
    browserXssFilter: true
    contentTypeNosniff: true
    stsSeconds: 31536000
    stsIncludeSubdomains: true
    forceSTSHeader: true
```

- [ ] **Step 2: Add the new resource to the Traefik kustomization**

Check if `k8s/infra/traefik/` has a kustomization.yaml. If Traefik is managed via Helm (ArgoCD Application referencing values.yaml), the middleware needs to be in a separate ArgoCD Application or included via a kustomization that Helm references. Verify the deployment method first.

If the traefik directory uses only `values.yaml` (Helm-managed), then the shared middleware should be created as a standalone manifest applied by a separate ArgoCD Application, OR added to the `k8s/infra/security/` kustomization. The most appropriate place: create an ArgoCD Application or add it to the security kustomization which already manages NetworkPolicies in the traefik namespace.

**Action:** Add the Middleware to `k8s/infra/security/kustomization.yaml` as a new resource entry, since security resources for traefik namespace are already managed there.

```yaml
# In k8s/infra/security/kustomization.yaml, add:
  - shared-security-headers-middleware.yaml
```

**Important:** Move the file from `k8s/infra/traefik/` to `k8s/infra/security/` since it will be managed by the security kustomization:

Create `k8s/infra/security/shared-security-headers-middleware.yaml` instead.

- [ ] **Step 3: Commit the shared middleware**

```bash
git add k8s/infra/security/shared-security-headers-middleware.yaml k8s/infra/security/kustomization.yaml
git commit -m "feat: add shared security-headers middleware in traefik namespace

Consolidates duplicated security header configurations across all
IngressRoutes into a single shared Middleware that can be referenced
cross-namespace."
```

---

### Task 6: Update IngressRoutes to reference shared Middleware

**Files:**
- Modify: `k8s/apps/invenio/invenio-ingressroute.yaml`
- Modify: `k8s/infra/argocd/argocd-ingress.yaml`
- Modify: `k8s/infra/minio/minio-console-ingressroute.yaml`
- Modify: `k8s/infra/monitoring/grafana-ingress.yaml`

- [ ] **Step 1: Update invenio IngressRoutes**

In `k8s/apps/invenio/invenio-ingressroute.yaml`, remove the `invenio-headers` Middleware definition (lines 1-16) and update both IngressRoutes to reference the shared middleware. The full file becomes:

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: invenio-ui
  namespace: invenio
  annotations:
    argocd.argoproj.io/sync-wave: "4"
spec:
  entryPoints:
    - web
  routes:
    - match: Host(`invenio.vityasy.me`)
      kind: Rule
      middlewares:
        - name: security-headers
          namespace: traefik
      services:
        - name: invenio-web
          port: 8000
---
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: invenio-api
  namespace: invenio
  annotations:
    argocd.argoproj.io/sync-wave: "4"
spec:
  entryPoints:
    - web
  routes:
    - match: Host(`api-invenio.vityasy.me`)
      kind: Rule
      middlewares:
        - name: security-headers
          namespace: traefik
      services:
        - name: invenio-web
          port: 8000
```

- [ ] **Step 2: Update ArgoCD IngressRoute**

In `k8s/infra/argocd/argocd-ingress.yaml`, reduce `argocd-headers` to only the Custom CSP header and reference the shared middleware as a chain. Add a middleware chain that includes both:

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: argocd-headers
  namespace: argocd
spec:
  headers:
    contentSecurityPolicy: "frame-ancestors 'self'"
---
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: argocd-full-headers
  namespace: argocd
spec:
  chain:
    middlewares:
      - name: security-headers
        namespace: traefik
      - name: argocd-headers
        namespace: argocd
---
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: argocd-server
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "0"
spec:
  entryPoints:
    - web
  routes:
    - match: Host(`argocd.vityasy.me`) && HeadersRegexp(`Content-Type`, `^application/grpc-web`)
      kind: Rule
      priority: 10
      services:
        - name: argocd-server
          port: 8080
      middlewares:
        - name: argocd-full-headers
    - match: Host(`argocd.vityasy.me`)
      kind: Rule
      priority: 1
      services:
        - name: argocd-server
          port: 8080
      middlewares:
        - name: argocd-full-headers
```

- [ ] **Step 3: Update MinIO IngressRoute**

In `k8s/infra/minio/minio-console-ingressroute.yaml`, remove the inline `minio-console-headers` Middleware definition and reference the shared middleware. The file becomes:

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: minio-console
  namespace: minio
spec:
  entryPoints:
    - web
  routes:
    - match: Host(`minio-console.vityasy.me`)
      kind: Rule
      services:
        - name: minio-console
          port: 9001
      middlewares:
        - name: security-headers
          namespace: traefik
```

- [ ] **Step 4: Update Grafana IngressRoute**

In `k8s/infra/monitoring/grafana-ingress.yaml`, update the middleware reference to the shared one:

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: grafana
  namespace: monitoring
spec:
  entryPoints:
    - web
  routes:
    - match: Host(`grafana.vityasy.me`)
      kind: Rule
      middlewares:
        - name: security-headers
          namespace: traefik
      services:
        - name: monitoring-grafana
          port: 80
```

- [ ] **Step 5: Delete the standalone grafana-headers middleware**

Delete `k8s/infra/monitoring/grafana-headers-middleware.yaml` and remove it from `k8s/infra/monitoring/kustomization.yaml`.

- [ ] **Step 6: Commit all IngressRoute changes**

```bash
git add k8s/apps/invenio/invenio-ingressroute.yaml \
       k8s/infra/argocd/argocd-ingress.yaml \
       k8s/infra/minio/minio-console-ingressroute.yaml \
       k8s/infra/monitoring/grafana-ingress.yaml \
       k8s/infra/monitoring/grafana-headers-middleware.yaml \
       k8s/infra/monitoring/kustomization.yaml
git commit -m "refactor: consolidate Middlewares into shared security-headers

All IngressRoutes now reference the shared security-headers middleware
in the traefik namespace. ArgoCD uses a chain of shared + CSP-specific
middleware. Removed per-namespace duplicated Middleware definitions."
```

---

### Task 7: Remove duplicate ArgoCD AppProjects

**Files:**
- Delete: `argocd/projects/infra-project.yaml`
- Delete: `argocd/projects/invenio-project.yaml`

- [ ] **Step 1: Verify the files are identical**

```bash
diff argocd/projects/infra-project.yaml k8s/infra/argocd/projects/infra-project.yaml
diff argocd/projects/invenio-project.yaml k8s/infra/argocd/projects/invenio-project.yaml
```

Expected: No differences (files are identical).

- [ ] **Step 2: Delete the duplicate files**

```bash
rm argocd/projects/infra-project.yaml argocd/projects/invenio-project.yaml
```

- [ ] **Step 3: Check if argocd/projects/ is now empty and can be removed**

```bash
ls argocd/projects/
```

If empty, remove the directory. Also check if `argocd/` has other files or if there's a kustomization referencing these.

- [ ] **Step 4: Commit**

```bash
git add -A argocd/projects/
git commit -m "refactor: remove duplicate ArgoCD AppProject files

Kept k8s/infra/argocd/projects/ as the single source of truth."
```

---

### Task 8: Fix README docs drift

**Files:**
- Modify: `k8s/infra/traefik/README.md`

- [ ] **Step 1: Update the README table**

In `k8s/infra/traefik/README.md`, change the table:

```markdown
# BEFORE:
| Replicas | 1 |

# AFTER:
| Replicas | 2 |
```

```markdown
# BEFORE:
| Watched Namespaces (CRD) | traefik, argocd, monitoring, minio |
| Watched Namespaces (Ingress) | traefik, argocd, monitoring, minio |

# AFTER:
| Watched Namespaces (CRD) | traefik, argocd, monitoring, minio, invenio |
| Watched Namespaces (Ingress) | traefik, argocd, monitoring, minio, invenio |
```

- [ ] **Step 2: Update the architecture diagram**

Add the Invenio routes and a note about internal HTTP:

```markdown
## Architecture

```
Cloudflare Tunnel (handles TLS termination)
    ↓ (HTTP)
traefik:8000 (web entryPoint)
    │
    ├─ IngressRoute: invenio.vityasy.me → invenio-web:8000 (→ :5000)
    ├─ IngressRoute: api-invenio.vityasy.me → invenio-web:8000 (→ :5000)
    ├─ IngressRoute: argocd.vityasy.me → argocd-server:8080
    ├─ IngressRoute: grafana.vityasy.me → monitoring-grafana:80
    └─ IngressRoute: minio-console.vityasy.me → minio-console:9001
```

**Note:** All internal traffic (cloudflared → Traefik → backend services) is HTTP by design.
TLS is terminated at the Cloudflare edge. The `websecure` entrypoint exists but is not
used by current IngressRoutes.
```

- [ ] **Step 3: Commit**

```bash
git add k8s/infra/traefik/README.md
git commit -m "docs: fix Traefik README - replicas, namespaces, architecture

- Replicas: 1 → 2 (matching values.yaml)
- Added invenio to watched namespaces
- Added Invenio routes to architecture diagram
- Documented HTTP-only internal architecture"
```

---

### Task 9: Verify secrets not in git history

**Files:** None (verification only)

- [ ] **Step 1: Search git history for secrets**

```bash
git log --all --diff-filter=A --name-only --pretty=format: -- secrets/ | sort -u
```

Expected: Empty output (secrets never tracked).

- [ ] **Step 2: If secrets were found in history, rotate all credentials**

This would require regenerating sealed-secrets keys, rotating Cloudflare tunnel token, MinIO root credentials, Grafana admin password, and Velero credentials.

- [ ] **Step 3: Verify .gitignore covers secrets/**

```bash
git check-ignore -v secrets/grafana-admin-password.txt
```

Expected: Shows `.gitignore` rule matching.

---

### Task 10: Move sealed-secrets private key

**Files:**
- Move: `secrets/sealed-secrets-private.pem` → `~/.sealed-secrets/`
- Move: `secrets/sealed-secrets-public.pem` → `~/.sealed-secrets/`

- [ ] **Step 1: Create destination directory**

```bash
mkdir -p ~/.sealed-secrets
```

- [ ] **Step 2: Move the keys**

```bash
mv secrets/sealed-secrets-private.pem ~/.sealed-secrets/
mv secrets/sealed-secrets-public.pem ~/.sealed-secrets/
```

- [ ] **Step 3: Document the key location**

Add a note in a README or documentation about where sealed-secrets keys are stored for disaster recovery:

```markdown
## Sealed Secrets Keys

The sealed-secrets private and public keys are stored at `~/.sealed-secrets/` on the admin workstation.
These are **not** committed to the repository. For disaster recovery, these keys must be backed up separately.
```

This could go in the existing `docs/` directory or similar.

- [ ] **Step 4: Commit (if any tracked files changed)**

The `secrets/` directory is already gitignored, so no commit should be needed. But verify:

```bash
git status
```

Expected: No changes to tracked files.

---

### Task 11: Sync ArgoCD IngressRoute and final verification

**Files:** None (operational verification)

- [ ] **Step 1: Force ArgoCD sync all applications**

```bash
argocd app sync --force traefik
argocd app sync --force invenio
argocd app sync --force infra
```

Or via kubectl:

```bash
kubectl exec -n argocd deploy/argocd-applicationset-controller -- argocd app sync traefik invenio infra
```

- [ ] **Step 2: Verify all IngressRoutes are present in cluster**

```bash
kubectl get ingressroute -A
```

Expected: All 5+ routes (invenio-ui, invenio-api, argocd-server with 2 routes, grafana, minio-console).

- [ ] **Step 3: Verify all Middlewares are present**

```bash
kubectl get middleware -A
```

Expected: `security-headers` in traefik namespace, `argocd-headers` and `argocd-full-headers` in argocd namespace.

- [ ] **Step 4: Verify NetworkPolicy is applied**

```bash
kubectl get networkpolicy -n traefik
```

Expected: `allow-cloudflared-to-traefik`, `allow-traefik-egress`, `default-deny-ingress`.

- [ ] **Step 5: Test HTTP endpoints**

```bash
kubectl port-forward -n traefik deploy/traefik 8000:8000 &
curl -H "Host: invenio.vityasy.me" http://localhost:8000/
curl -H "Host: argocd.vityasy.me" http://localhost:8000/
curl -H "Host: grafana.vityasy.me" http://localhost:8000/
curl -H "Host: minio-console.vityasy.me" http://localhost:8000/
```

Expected: No 404 errors. Each should return a response from the backend service.

---

## Self-Review

1. **Spec coverage**: All 3 phases covered — critical fix (Task 1-3), security (Task 9-10), dedup (Task 4-8), verification (Task 11). No gaps.
2. **Placeholder scan**: No TBDs, TODOs, or "implement later". All steps have complete code.
3. **Type consistency**: All Middleware references use consistent `name` + `namespace` format. The `argocd-full-headers` chain references `security-headers` in `traefik` and `argocd-headers` in `argocd`.