# InvenioRDM GitOps Codebase Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix critical issues, improve scalability, add observability, and harden the InvenioRDM GitOps infrastructure to production-grade standards.

**Architecture:** GitOps-first approach — all changes are YAML manifests or Helm values committed to the repo, synced by ArgoCD. The cluster has 3 nodes (1 control-plane at 10.17.117.41, 2 workers at 10.17.117.42/43) with a separate NFS server at 10.17.117.48 providing all persistent storage via NFS CSI driver (`btd-nfs` StorageClass, `nfs.csi.k8s.io` provisioner, path `/export/kube-btd/`).

**Tech Stack:** Kubernetes (RKE2 v1.32.6), ArgoCD v2.12, Kustomize, Helm, CloudNativePG, Sealed Secrets, Traefik, Velero, kube-prometheus-stack, OpenSearch, MinIO, Redis, InvenioRDM

---

## Diagnosed Cluster State

### Physical Infrastructure
- **3 nodes**: control-plane (10.17.117.41), worker-01 (10.17.117.42), worker-02 (10.17.117.43)
- **NFS server**: Separate machine at 10.17.117.48, export path `/export/kube-btd/`
- **Storage**: All PVCs use `btd-nfs` StorageClass provisioned by `nfs.csi.k8s.io`
- **All ArgoCD apps**: Synced + Healthy

### Issues Found in Live Cluster
1. **23 Released orphaned PVCs** (~350Gi+ wasted on NFS from deleted `ugm-dbrepo-dev` namespace)
2. **Kubelet proxy 502** on worker-01 (10.17.117.42) — can't exec/logs into pods on that node
3. **`debug-worker01` pod** in invenio namespace with 139 restarts (stale debugging pod)
4. **CNPG PodMonitor disabled** — PostgreSQL metrics not collected
5. **Velero backups working** — 4 weekly backups present (Apr 26, May 3, May 10, May 17)

---

## Phase 1: Critical Fixes (Security & Reliability)

### Task 1: Fix ArgoCD Sync Wave Ordering

The `invenio-bootstrap` app (wave 7) deploys ExternalName services and setup jobs that depend on PostgreSQL, Redis, and OpenSearch — which deploy at wave 8. This causes DNS resolution failures and setup job crashes on fresh deployments.

**Files:**
- Modify: `argocd/apps/invenio-bootstrap.yaml`
- Modify: `argocd/apps/invenio-postgresql.yaml`
- Modify: `argocd/apps/invenio-redis.yaml`
- Modify: `argocd/apps/invenio-opensearch.yaml`

- [ ] **Step 1: Move invenio dependencies to wave 7, invenio app to wave 9**

Edit `argocd/apps/invenio-postgresql.yaml` — change sync-wave annotation from `"8"` to `"7"`:
```yaml
annotations:
  argocd.argoproj.io/sync-wave: "7"
```

Edit `argocd/apps/invenio-redis.yaml` — change sync-wave from `"8"` to `"7"`:
```yaml
annotations:
  argocd.argoproj.io/sync-wave: "7"
```

Edit `argocd/apps/invenio-opensearch.yaml` — change sync-wave from `"8"` to `"7"`:
```yaml
annotations:
  argocd.argoproj.io/sync-wave: "7"
```

Edit `argocd/apps/invenio-bootstrap.yaml` — change sync-wave from `"7"` to `"9"`:
```yaml
annotations:
  argocd.argoproj.io/sync-wave: "9"
```

- [ ] **Step 2: Commit and verify**

```bash
git add argocd/apps/invenio-bootstrap.yaml argocd/apps/invenio-postgresql.yaml argocd/apps/invenio-redis.yaml argocd/apps/invenio-opensearch.yaml
git commit -m "fix(argocd): move invenio deps to wave 7, invenio app to wave 9"
```

---

### Task 2: Delete Orphaned and Conflicting Files

The `k8s/infra/namespaces/` directory contains 5 orphaned namespace files that conflict with active namespace definitions. Other orphaned files need removal too.

**Files to delete:**
- `k8s/infra/namespaces/invenio.yaml` (quota 8CPU/16Gi conflicts with active 12CPU/24Gi)
- `k8s/infra/namespaces/database.yaml` (duplicate)
- `k8s/infra/namespaces/redis.yaml` (duplicate)
- `k8s/infra/namespaces/search.yaml` (PSA `restricted` conflicts with active `baseline`)
- `k8s/infra/namespaces/minio.yaml` (PSA `restricted` conflicts with active `privileged`)
- `k8s/infra/namespaces/` directory (remove empty)
- `k8s/infra/monitoring/grafana-ingressroute.yaml` (duplicate of grafana-ingress.yaml)
- `k8s/infra/velero/backup-storage-location.yaml` (not in kustomization, logic in Helm values)
- `k8s/infra/cloudflared/` directory (empty, actual config at `external-lb/k8s/`)

- [ ] **Step 1: Delete orphaned namespace files**

```bash
git rm k8s/infra/namespaces/invenio.yaml k8s/infra/namespaces/database.yaml k8s/infra/namespaces/redis.yaml k8s/infra/namespaces/search.yaml k8s/infra/namespaces/minio.yaml
rmdir k8s/infra/namespaces/
```

- [ ] **Step 2: Delete duplicate and unused files**

```bash
git rm k8s/infra/monitoring/grafana-ingressroute.yaml
git rm k8s/infra/velero/backup-storage-location.yaml
rm -rf k8s/infra/cloudflared/
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove orphaned namespace files, duplicate ingress, unused velero config, empty cloudflared dir"
```

---

### Task 3: Add Health Probes to Celery Worker

The invenio-worker has zero health probes. If the Celery process hangs, Kubernetes won't restart it.

**Files:**
- Modify: `k8s/apps/invenio/invenio-worker-deployment.yaml`

- [ ] **Step 1: Add liveness and readiness probes**

Find the `worker` container spec in `k8s/apps/invenio/invenio-worker-deployment.yaml` and add after the existing `securityContext` block:

```yaml
livenessProbe:
  exec:
    command:
      - /bin/sh
      - -c
      - celery -A invenio_app.celery inspect ping -d celery@$(hostname) --timeout 10
  initialDelaySeconds: 60
  periodSeconds: 60
  timeoutSeconds: 15
  failureThreshold: 3
readinessProbe:
  exec:
    command:
      - /bin/sh
      - -c
      - celery -A invenio_app.celery inspect ping -d celery@$(hostname) --timeout 5
  initialDelaySeconds: 30
  periodSeconds: 30
  timeoutSeconds: 10
  failureThreshold: 3
```

- [ ] **Step 2: Commit**

```bash
git add k8s/apps/invenio/invenio-worker-deployment.yaml
git commit -m "fix(invenio): add liveness and readiness probes to celery worker"
```

---

### Task 4: Switch Invenio-web Probes from TCP to HTTP

TCP probes only check if a port is open, not that the app is serving. InvenioRDM has `/api/health`.

**Files:**
- Modify: `k8s/apps/invenio/invenio-deployment.yaml`

- [ ] **Step 1: Replace TCP probes with HTTP probes**

Find and replace the `startupProbe`, `livenessProbe`, and `readinessProbe` blocks in the `web` container:

```yaml
startupProbe:
  httpGet:
    path: /api/health
    port: 5000
  initialDelaySeconds: 10
  periodSeconds: 5
  failureThreshold: 30
livenessProbe:
  httpGet:
    path: /api/health
    port: 5000
  initialDelaySeconds: 10
  periodSeconds: 30
  timeoutSeconds: 5
  failureThreshold: 3
readinessProbe:
  httpGet:
    path: /api/health
    port: 5000
  initialDelaySeconds: 5
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3
```

- [ ] **Step 2: Commit**

```bash
git add k8s/apps/invenio/invenio-deployment.yaml
git commit -m "fix(invenio): switch web probes from TCP to HTTP /api/health endpoint"
```

---

### Task 5: Fix Alertmanager SMTP Configuration

Alerts won't fire — placeholder emails and empty SMTP auth.

**Files:**
- Modify: `k8s/infra/monitoring/values.yaml`
- Create: `k8s/infra/monitoring/alertmanager-smtp-secret.yaml`
- Modify: `k8s/infra/monitoring/kustomization.yaml`

- [ ] **Step 1: Update Alertmanager config with real SMTP structure**

Edit `k8s/infra/monitoring/values.yaml`. Replace the `alertmanager` section's `config` with properly structured config referencing a SealedSecret for the SMTP password:

```yaml
alertmanager:
  alertmanagerSpec:
    resources:
      requests:
        cpu: 50m
        memory: 64Mi
      limits:
        cpu: 200m
        memory: 128Mi
    storage:
      volumeClaimTemplate:
        spec:
          storageClassName: btd-nfs
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
  config:
    global:
      resolve_timeout: 5m
    route:
      group_by: ["alertname", "namespace", "service"]
      group_wait: 30s
      group_interval: 5m
      repeat_interval: 4h
      receiver: "null"
      routes:
        - match:
            alertname: Watchdog
          receiver: "null"
        - match:
            severity: critical
          receiver: "critical-email"
          continue: false
        - match:
            severity: warning
          receiver: "warning-email"
          continue: false
    receivers:
      - name: "null"
      - name: "critical-email"
        email_configs:
          - send_resolved: true
            to: "REPLACE_WITH_REAL_EMAIL@example.com"
            from: "alertmanager@vityasy.me"
            smarthost: "REPLACE_WITH_REAL_SMTP_HOST:587"
            auth_username: "alertmanager@vityasy.me"
            auth_password:
              name: alertmanager-smtp-secret
              key: password
            require_tls: true
      - name: "warning-email"
        email_configs:
          - send_resolved: true
            to: "REPLACE_WITH_REAL_EMAIL@example.com"
            from: "alertmanager@vityasy.me"
            smarthost: "REPLACE_WITH_REAL_SMTP_HOST:587"
            auth_username: "alertmanager@vityasy.me"
            auth_password:
              name: alertmanager-smtp-secret
              key: password
            require_tls: true
    inhibit_rules:
      - source_match:
          severity: "critical"
        target_match:
          severity: "warning"
        equal: ["alertname", "namespace", "service"]
```

**IMPORTANT:** Replace `REPLACE_WITH_REAL_EMAIL@example.com` and `REPLACE_WITH_REAL_SMTP_HOST` with actual values before deploying.

- [ ] **Step 2: Create SealedSecret placeholder**

Create `k8s/infra/monitoring/alertmanager-smtp-secret.yaml`:

```yaml
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: alertmanager-smtp-secret
  namespace: monitoring
  annotations:
    argocd.argoproj.io/sync-wave: "0"
spec:
  encryptedData:
    password: PLACEHOLDER_MUST_BE_REGENERATED_WITH_KUBESEAL
  template:
    metadata:
      name: alertmanager-smtp-secret
      namespace: monitoring
    type: Opaque
```

**Must be regenerated with real password using kubeseal before it will work.**

- [ ] **Step 3: Add SealedSecret to monitoring kustomization**

Edit `k8s/infra/monitoring/kustomization.yaml`. Add `alertmanager-smtp-secret.yaml` to the resources list after `grafana-admin-secret.yaml`.

- [ ] **Step 4: Commit**

```bash
git add k8s/infra/monitoring/values.yaml k8s/infra/monitoring/alertmanager-smtp-secret.yaml k8s/infra/monitoring/kustomization.yaml
git commit -m "fix(monitoring): configure alertmanager SMTP with sealed secret for credentials"
```

---

### Task 6: Deploy Loki + Promtail (Log Aggregation)

No centralized logging exists. Deploy Grafana Loki + Promtail to aggregate logs into the existing Grafana.

**Files:**
- Create: `argocd/apps/loki.yaml`
- Create: `k8s/infra/loki/values.yaml`
- Create: `k8s/infra/monitoring/loki-networkpolicy.yaml`
- Modify: `k8s/infra/monitoring/kustomization.yaml`
- Modify: `argocd/projects/infra-project.yaml`

- [ ] **Step 1: Create Loki ArgoCD application**

Create `argocd/apps/loki.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: loki
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "5"
spec:
  project: infra
  sources:
    - chart: loki
      repoURL: https://grafana.github.io/helm-charts
      targetRevision: "6.24.0"
      helm:
        valueFiles:
          - $values/k8s/infra/loki/values.yaml
    - repoURL: https://github.com/vityasyyy/invenio-rdm-gitops.git
      targetRevision: main
      ref: values
  destination:
    server: https://kubernetes.default.svc
    namespace: monitoring
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
      - PrunePropagationPolicy=foreground
```

- [ ] **Step 2: Create Loki Helm values**

Create `k8s/infra/loki/values.yaml`:

```yaml
loki:
  commonConfig:
    replication_factor: 1
  storage:
    type: filesystem
  storage_config:
    filesystem:
      directory: /loki/storage
  auth_enabled: false
  analytic:
    reporting_enabled: false

singleBinary:
  replicas: 1
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi
  persistence:
    enabled: true
    size: 10Gi
    storageClassName: btd-nfs

chunksCache:
  allocated_memory: 256Mi

resultsCache:
  allocated_memory: 256Mi

gateway:
  enabled: false

read:
  replicas: 0
write:
  replicas: 0
backend:
  replicas: 0

monitoring:
  selfMonitoring:
    enabled: false
    grafana:
      dashboards:
        enabled: false
  lokiCanary:
    enabled: false

test:
  enabled: false

promtail:
  enabled: true
  resources:
    requests:
      cpu: 50m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 128Mi
  tolerations:
    - key: node-role.kubernetes.io/control-plane
      effect: NoSchedule
      operator: Equal
    - key: node-role.kubernetes.io/etcd
      effect: NoSchedule
      operator: Equal
  extraArgs:
    - -config.expand-env=true
  config:
    clients:
      - url: http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/push
        external_labels:
          cluster: invenio-rdm
```

- [ ] **Step 3: Create Loki network policy**

Create `k8s/infra/monitoring/loki-networkpolicy.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: loki-allow-internal
  namespace: monitoring
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: loki
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
      ports:
        - port: 3100
          protocol: TCP
    - from:
        - podSelector: {}
      ports:
        - port: 3100
          protocol: TCP
  egress:
    - to:
        - namespaceSelector: {}
      ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
```

- [ ] **Step 4: Update monitoring kustomization**

Edit `k8s/infra/monitoring/kustomization.yaml`. Add `loki-networkpolicy.yaml` to the resources list.

- [ ] **Step 5: Add Grafana Helm repo to AppProject**

Edit `argocd/projects/infra-project.yaml`. Add `'https://grafana.github.io/helm-charts'` to `sourceRepos` list.

- [ ] **Step 6: Commit**

```bash
git add argocd/apps/loki.yaml k8s/infra/loki/values.yaml k8s/infra/monitoring/loki-networkpolicy.yaml k8s/infra/monitoring/kustomization.yaml argocd/projects/infra-project.yaml
git commit -m "feat(monitoring): add Loki + Promtail for centralized log aggregation"
```

---

### Task 7: Replace Hardcoded IPs in Network Policies

Two network policies have hardcoded IPs that break on cluster rebuild.

**Files:**
- Modify: `k8s/infra/security/traefik-full-egress.yaml`
- Modify: `k8s/infra/security/monitoring-egress.yaml`

- [ ] **Step 1: Replace `10.17.117.41/32` with service CIDR in Traefik egress**

Edit `k8s/infra/security/traefik-full-egress.yaml`. Find the ipBlock entry for `10.17.117.41/32` port 6443 and replace with the Kubernetes service CIDR:

Replace:
```yaml
- to:
    - ipBlock:
        cidr: 10.17.117.41/32
  ports:
    - port: 6443
      protocol: TCP
```

With:
```yaml
- to:
    - ipBlock:
        cidr: 10.43.0.0/16
  ports:
    - port: 6443
      protocol: TCP
```

- [ ] **Step 2: Replace `10.43.0.1/32` with service CIDR in monitoring egress**

Edit `k8s/infra/security/monitoring-egress.yaml`. Find the ipBlock entry for `10.43.0.1/32` port 443 and replace:

Replace:
```yaml
- to:
    - ipBlock:
        cidr: 10.43.0.1/32
  ports:
    - port: 443
      protocol: TCP
```

With:
```yaml
- to:
    - ipBlock:
        cidr: 10.43.0.0/16
  ports:
    - port: 443
      protocol: TCP
```

- [ ] **Step 3: Commit**

```bash
git add k8s/infra/security/traefik-full-egress.yaml k8s/infra/security/monitoring-egress.yaml
git commit -m "fix(security): replace hardcoded IPs with service CIDR in network policies"
```

---

## Phase 2: Scalability Improvements

### Task 8: Add HorizontalPodAutoscaler for Invenio

**Files:**
- Create: `k8s/apps/invenio/invenio-hpa.yaml`
- Modify: `k8s/apps/invenio/kustomization.yaml`
- Modify: `argocd/projects/infra-project.yaml`

- [ ] **Step 1: Create HPA manifests**

Create `k8s/apps/invenio/invenio-hpa.yaml`:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: invenio-web-hpa
  namespace: invenio
  annotations:
    argocd.argoproj.io/sync-wave: "4"
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: invenio-web
  minReplicas: 2
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: invenio-worker-hpa
  namespace: invenio
  annotations:
    argocd.argoproj.io/sync-wave: "4"
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: invenio-worker
  minReplicas: 2
  maxReplicas: 4
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

- [ ] **Step 2: Add HPA to invenio kustomization**

Edit `k8s/apps/invenio/kustomization.yaml`. Add `- invenio-hpa.yaml` to the resources list.

- [ ] **Step 3: Add HPA resource to AppProject**

Edit `argocd/projects/infra-project.yaml`. In the `namespaceResourceWhitelist`, add:
```yaml
  - group: 'autoscaling'
    kind: 'HorizontalPodAutoscaler'
```

- [ ] **Step 4: Commit**

```bash
git add k8s/apps/invenio/invenio-hpa.yaml k8s/apps/invenio/kustomization.yaml argocd/projects/infra-project.yaml
git commit -m "feat(invenio): add HPA for web and worker deployments"
```

---

### Task 9: Add PodDisruptionBudgets

**Files:**
- Create: `k8s/apps/invenio/invenio-pdb.yaml`
- Modify: `k8s/apps/invenio/kustomization.yaml`

- [ ] **Step 1: Create PDB manifests**

Create `k8s/apps/invenio/invenio-pdb.yaml`:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: invenio-web-pdb
  namespace: invenio
  annotations:
    argocd.argoproj.io/sync-wave: "4"
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: invenio-web
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: invenio-worker-pdb
  namespace: invenio
  annotations:
    argocd.argoproj.io/sync-wave: "4"
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: invenio-worker
```

- [ ] **Step 2: Add PDB to kustomization**

Edit `k8s/apps/invenio/kustomization.yaml`. Add `- invenio-pdb.yaml` to the resources list.

- [ ] **Step 3: Commit**

```bash
git add k8s/apps/invenio/invenio-pdb.yaml k8s/apps/invenio/kustomization.yaml
git commit -m "feat(invenio): add PodDisruptionBudgets for web and worker"
```

---

### Task 10: Scale PostgreSQL to 3 Replicas (HA)

**Files:**
- Modify: `k8s/apps/invenio-deps/postgresql/cluster.yaml`
- Modify: `k8s/apps/invenio-deps/postgresql/namespace.yaml` (if quota needs adjusting)

- [ ] **Step 1: Update CNPG cluster instances**

Edit `k8s/apps/invenio-deps/postgresql/cluster.yaml`. Change `instances: 1` to `instances: 3`.

- [ ] **Step 2: Verify database namespace quota is sufficient**

The current database namespace quota is 2 CPU / 4Gi requests and 4 CPU / 8Gi limits. With 3 PG pods (each ~250m/256Mi request), total requests would be ~750m/768Mi — currently under the 2 CPU/4Gi quota. Limits at ~1.5Gi/1.5Gi — under 4/8Gi. **No quota change needed.**

- [ ] **Step 3: Commit**

```bash
git add k8s/apps/invenio-deps/postgresql/cluster.yaml
git commit -m "feat(database): scale PostgreSQL to 3 instances for HA"
```

**IMPORTANT:** CNPG handles online scaling. Adding replicas streams WAL to new pods. Verify quota before deploying.

---

### Task 11: Remove Stale Debug Pod

Runtime operation only, no codebase changes.

- [ ] **Step 1: Delete the debug pod**

```bash
kubectl delete pod debug-worker01 -n invenio
```

---

## Phase 3: Maintainability Improvements

### Task 12: Split AppProject into infra and invenio

**Files:**
- Create: `argocd/projects/invenio-project.yaml`
- Create: `k8s/infra/argocd/invenio-project.yaml` (copy for ArgoCD self-management)
- Modify: `argocd/apps/invenio-bootstrap.yaml` (project: invenio)
- Modify: `argocd/apps/invenio-postgresql.yaml` (project: invenio)
- Modify: `argocd/apps/invenio-redis.yaml` (project: invenio)
- Modify: `argocd/apps/invenio-opensearch.yaml` (project: invenio)
- Modify: `k8s/infra/argocd/kustomization.yaml`
- Modify: `scripts/bootstrap-infra.sh`

- [ ] **Step 1: Create invenio AppProject at `argocd/projects/invenio-project.yaml`**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: invenio
  namespace: argocd
spec:
  description: "InvenioRDM application workloads"
  sourceRepos:
    - 'https://github.com/vityasyyy/invenio-rdm-gitops.git'
    - 'https://charts.cloudnative-pg.io'
    - 'https://opensearch-project.github.io/helm-charts'
  destinations:
    - namespace: invenio
      server: https://kubernetes.default.svc
    - namespace: database
      server: https://kubernetes.default.svc
    - namespace: redis
      server: https://kubernetes.default.svc
    - namespace: search
      server: https://kubernetes.default.svc
  clusterResourceWhitelist:
    - group: ''
      kind: Namespace
  namespaceResourceWhitelist:
    - group: 'apps'
      kind: 'Deployment'
    - group: 'apps'
      kind: 'Job'
    - group: 'batch'
      kind: 'Job'
    - group: ''
      kind: 'Service'
    - group: ''
      kind: 'ConfigMap'
    - group: ''
      kind: 'Secret'
    - group: 'bitnami.com'
      kind: 'SealedSecret'
    - group: ''
      kind: 'ServiceAccount'
    - group: 'networking.k8s.io'
      kind: 'NetworkPolicy'
    - group: 'traefik.io'
      kind: 'IngressRoute'
    - group: 'traefik.io'
      kind: 'Middleware'
    - group: 'postgresql.cnpg.io'
      kind: 'Cluster'
    - group: 'postgresql.cnpg.io'
      kind: 'ScheduledBackup'
    - group: 'autoscaling'
      kind: 'HorizontalPodAutoscaler'
    - group: 'policy'
      kind: 'PodDisruptionBudget'
    - group: 'monitoring.coreos.com'
      kind: 'ServiceMonitor'
  roles:
    - name: admin
      description: Admin access to invenio project
      policies:
        - "p, proj:invenio:admin, applications, get, invenio/*, allow"
        - "p, proj:invenio:admin, applications, create, invenio/*, allow"
        - "p, proj:invenio:admin, applications, update, invenio/*, allow"
        - "p, proj:invenio:admin, applications, delete, invenio/*, allow"
        - "p, proj:invenio:admin, applications, sync, invenio/*, allow"
        - "p, proj:invenio:admin, applications, override, invenio/*, allow"
      groups:
        - github:vityasyyy:admins
```

- [ ] **Step 2: Copy for ArgoCD self-management and add to kustomization**

Create `k8s/infra/argocd/invenio-project.yaml` with the same content as above.

Edit `k8s/infra/argocd/kustomization.yaml`. Add `invenio-project.yaml` to the resources list.

- [ ] **Step 3: Update invenio apps to use project `invenio`**

In each of these 4 files, change `project: infra` to `project: invenio`:
- `argocd/apps/invenio-bootstrap.yaml`
- `argocd/apps/invenio-postgresql.yaml`
- `argocd/apps/invenio-redis.yaml`
- `argocd/apps/invenio-opensearch.yaml`

- [ ] **Step 4: Add invenio project to bootstrap script**

Edit `scripts/bootstrap-infra.sh`. After the `kubectl apply -f argocd/projects/infra-project.yaml` line, add:
```bash
kubectl apply -f argocd/projects/invenio-project.yaml
```

- [ ] **Step 5: Commit**

```bash
git add argocd/projects/invenio-project.yaml k8s/infra/argocd/invenio-project.yaml k8s/infra/argocd/kustomization.yaml argocd/apps/invenio-bootstrap.yaml argocd/apps/invenio-postgresql.yaml argocd/apps/invenio-redis.yaml argocd/apps/invenio-opensearch.yaml scripts/bootstrap-infra.sh
git commit -m "feat(argocd): split AppProject into infra and invenio for least privilege"
```

---

### Task 13: Remove Unused Cert-Manager

Cert-Manager is installed but has no ClusterIssuers or Certificates. TLS is handled by Cloudflare.

**Files:**
- Delete: `argocd/apps/cert-manager.yaml`
- Delete: `k8s/infra/cert-manager/values.yaml`
- Delete: `k8s/infra/cert-manager/` directory
- Modify: `argocd/projects/infra-project.yaml` (remove jetstack repo)

- [ ] **Step 1: Remove cert-manager files**

```bash
git rm argocd/apps/cert-manager.yaml
git rm k8s/infra/cert-manager/values.yaml
rmdir k8s/infra/cert-manager/ 2>/dev/null || true
```

- [ ] **Step 2: Remove jetstack Helm repo from AppProject**

Edit `argocd/projects/infra-project.yaml`. Remove `'https://charts.jetstack.io'` from the `sourceRepos` list.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove unused cert-manager (TLS handled by Cloudflare)"
```

---

## Phase 4: Production Hardening

### Task 14: Tighten Database Namespace Egress Policy

The `database-allow-all-egress` policy allows all egress from the database namespace. Replace with specific rules.

**Files:**
- Modify: `k8s/infra/security/network-policies/invenio-netpol.yaml` (or wherever database-allow-all-egress is defined)

Let me check where this is actually defined. Based on earlier analysis, the database network policies are in `k8s/infra/security/network-policies/invenio-netpol.yaml`.

- [ ] **Step 1: Replace database-allow-all-egress with specific egress policy**

Find `database-allow-all-egress` in the network policy file and replace it with:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: database-allow-egress
  namespace: database
spec:
  podSelector:
    matchLabels: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
        - podSelector: {}
      ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: minio
      ports:
        - port: 9000
          protocol: TCP
```

This restricts database egress to DNS and MinIO (for WAL archiving) only.

- [ ] **Step 2: Commit**

```bash
git add k8s/infra/security/network-policies/invenio-netpol.yaml
git commit -m "fix(security): tighten database namespace egress policy"
```

---

### Task 15: Add Monitoring Alerts for Velero, PG, MinIO, OpenSearch

**Files:**
- Modify: `k8s/infra/monitoring/alerts.yaml`
- Modify: `k8s/apps/invenio-deps/postgresql/cluster.yaml` (enable PodMonitor)

- [ ] **Step 1: Add alert rules**

Edit `k8s/infra/monitoring/alerts.yaml`. Add after the existing `invenio.rules` group:

```yaml
- name: velero.rules
  rules:
    - alert: VeleroBackupFailed
      expr: velero_backup_attempt_total - velero_backup_success_total > 0
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "Velero backup failed"
        description: "Velero backup has failed attempts. Check velero backup logs."
    - alert: VeleroBackupStale
      expr: time() - velero_backup_timestamp > 8 * 24 * 3600
      for: 1h
      labels:
        severity: warning
      annotations:
        summary: "No Velero backup in over 8 days"
        description: "The last successful Velero backup was more than 8 days ago."

- name: postgresql.rules
  rules:
    - alert: PostgreSQLHighConnections
      expr: cnpg_collection_connections_total / cnpg_collection_connections_max > 0.8
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "PostgreSQL connection usage above 80%"
        description: "Cluster {{ $labels.cluster_name }} is using more than 80% of available connections."

- name: minio.rules
  rules:
    - alert: MinIOHighDiskUsage
      expr: minio_cluster_disk_free_bytes / minio_cluster_disk_total_bytes < 0.2
      for: 10m
      labels:
        severity: warning
      annotations:
        summary: "MinIO disk usage above 80%"
        description: "MinIO has less than 20% free disk space."

- name: opensearch.rules
  rules:
    - alert: OpenSearchClusterRed
      expr: opensearch_cluster_status == 2
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "OpenSearch cluster status is red"
        description: "OpenSearch cluster is in RED state. Some shards are unavailable."
```

- [ ] **Step 2: Enable CNPG PodMonitor**

Edit `k8s/apps/invenio-deps/postgresql/cluster.yaml`. Find `enablePodMonitor: false` and change to `enablePodMonitor: true`.

- [ ] **Step 3: Enable MinIO metrics ServiceMonitor**

Edit `k8s/infra/minio/values.yaml`. Add or update the metrics section:

```yaml
metrics:
  serviceMonitor:
    enabled: true
```

- [ ] **Step 4: Commit**

```bash
git add k8s/infra/monitoring/alerts.yaml k8s/apps/invenio-deps/postgresql/cluster.yaml k8s/infra/minio/values.yaml
git commit -m "feat(monitoring): add alerts for Velero/PG/MinIO/OpenSearch, enable CNPG PodMonitor"
```

---

### Task 16: Clean Up Orphaned Released PVCs

Runtime operation — no codebase changes.

- [ ] **Step 1: List and delete Released PVs**

```bash
kubectl get pv | grep Released
kubectl get pv -o json | jq -r '.items[] | select(.status.phase=="Released") | .metadata.name' | xargs kubectl delete pv
```

- [ ] **Step 2: Coordinate with NFS admin** to clean up orphaned subdirectories under `/export/kube-btd/` on 10.17.117.48.

---

### Task 17: Fix Kubelet Proxy 502 on Worker-01

Runtime/diagnostic operation — requires SSH to the worker node.

- [ ] **Step 1: SSH to worker-01 and diagnose**

```bash
ssh ubuntu-btd-kubernetes-worker-01
systemctl status rke2-agent
ss -tlnp | grep 10250
```

- [ ] **Step 2: Restart kubelet if needed**

```bash
systemctl restart rke2-agent
```

- [ ] **Step 3: Verify** by running `kubectl exec` against a pod on worker-01.

---

### Task 18: Add Off-site Backup Destination (Placeholder)

This requires an external S3 provider and credentials that don't exist yet.

**Files:**
- Modify: `k8s/infra/velero/values.yaml`
- Create: `k8s/infra/velero/external-backup-secret.yaml` (placeholder)
- Modify: `k8s/infra/velero/backup-schedule.yaml`
- Modify: `k8s/infra/velero/kustomization.yaml`

- [ ] **Step 1: Add external BackupStorageLocation to Velero values**

Edit `k8s/infra/velero/values.yaml`. Add a second BSL entry after the existing `default` one:

```yaml
    - name: external
      provider: aws
      bucket: REPLACE_WITH_YOUR_EXTERNAL_BUCKET_NAME
      config:
        region: auto
        s3ForcePathStyle: "true"
        s3Url: https://s3.REPLACE_WITH_YOUR_PROVIDER_ENDPOINT
      credential:
        name: external-backup-credentials
        namespace: velero
```

- [ ] **Step 2: Create SealedSecret placeholder**

Create `k8s/infra/velero/external-backup-secret.yaml`:

```yaml
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: external-backup-credentials
  namespace: velero
  annotations:
    argocd.argoproj.io/sync-wave: "1"
spec:
  encryptedData:
    aws: PLACEHOLDER_MUST_BE_REGENERATED_WITH_KUBESEAL
  template:
    metadata:
      name: external-backup-credentials
      namespace: velero
    type: Opaque
```

- [ ] **Step 3: Add external backup schedule**

Edit `k8s/infra/velero/backup-schedule.yaml`. Add after the existing schedule:

```yaml
---
apiVersion: velero.io/v1
kind: Schedule
metadata:
  name: weekly-infra-backup-external
  namespace: velero
  annotations:
    argocd.argoproj.io/sync-wave: "3"
spec:
  schedule: "0 5 * * 0"
  template:
    ttl: 720h
    storageLocation: external
    defaultVolumesToFsBackup: true
    snapshotVolumes: false
    includedNamespaces:
      - argocd
      - traefik
      - monitoring
      - minio
      - invenio
      - database
      - redis
      - search
      - velero
```

- [ ] **Step 4: Add secret to kustomization**

Edit `k8s/infra/velero/kustomization.yaml`. Add `external-backup-secret.yaml` to resources.

- [ ] **Step 5: Commit**

```bash
git add k8s/infra/velero/values.yaml k8s/infra/velero/external-backup-secret.yaml k8s/infra/velero/backup-schedule.yaml k8s/infra/velero/kustomization.yaml
git commit -m "feat(velero): add external S3 backup destination (credentials placeholder)"
```

**NOTE:** Will NOT work until SealedSecret is regenerated with real S3 credentials and bucket/endpoint values are filled in.

---

## Summary: Recommended Execution Order

| Order | Task | Priority | Effort |
|-------|------|----------|--------|
| 1 | Fix sync wave ordering | P1-Critical | Low |
| 2 | Delete orphaned/conflicting files | P1-Critical | Low |
| 3 | Add worker health probes | P1-Critical | Low |
| 4 | Switch web probes to HTTP | P1-Critical | Low |
| 5 | Fix Alertmanager SMTP | P1-Critical | Medium |
| 6 | Deploy Loki + Promtail | P1-Critical | Medium |
| 7 | Replace hardcoded IPs | P1-Critical | Low |
| 8 | Add HPA for Invenio | P2 | Low |
| 9 | Add PDBs for Invenio | P2 | Low |
| 10 | Scale PG to 3 replicas | P2 | Low |
| 11 | Remove debug pod | P1 | Trivial |
| 12 | Split AppProject | P3 | Medium |
| 13 | Remove cert-manager | P3 | Low |
| 14 | Tighten DB egress policy | P3 | Low |
| 15 | Add monitoring alerts | P4 | Medium |
| 16 | Clean orphaned PVCs | P2 | Trivial (needs NFS access) |
| 17 | Fix kubelet 502 | P2 | Medium (needs SSH) |
| 18 | Add off-site backup | P4 | Medium (needs S3 provider) |

Execute in order: 1 → 2 → 3 → 4 → 7 → 5 → 6 → 11 → 8 → 9 → 10 → 14 → 16 → 17 → 12 → 13 → 15 → 18