# Traefik Ingress Controller

Traefik v3.6.11 — the ingress layer that sits between Cloudflare Tunnel and internal services.

## Management

Traefik is managed via **Helm** with versioned values in `values.yaml`. This file is the source of truth.

### Deploy / Update

```bash
helm upgrade traefik traefik/traefik \
  -n traefik \
  --create-namespace \
  -f k8s/infra/traefik/values.yaml
```

### Current Configuration

| Setting | Value |
|---------|-------|
| Chart | traefik v39.0.6 |
| App Version | v3.6.11 |
| Image | docker.io/traefik:v3.6.11 |
| Replicas | 1 |
| Service Type | NodePort |
| Watched Namespaces (CRD) | traefik, argocd |
| Watched Namespaces (Ingress) | traefik, argocd |
| Default IngressClass | traefik |

## Architecture

```
Cloudflare Tunnel → traefik:8000 (web entryPoint)
                           │
                           ├─ IngressRoute: argocd.vityasy.me → argocd-server:8080
                           └─ IngressRoute: *.vityasy.me → (future services)
```

## Key Configuration Notes

- **`providers.kubernetesCRD.namespaces`**: Controls which namespaces Traefik scans for IngressRoute resources. Add new namespaces here when deploying new services with IngressRoutes.
- **`providers.kubernetesIngress.namespaces`**: Controls which namespaces Traefik scans for standard Kubernetes Ingress resources.
- **`service.type: NodePort`**: Required for RKE2/Rancher clusters where no external LoadBalancer is available. Cloudflare Tunnel connects directly to the NodePort.

## Troubleshooting

```bash
# Check Traefik logs
kubectl logs -n traefik -l app.kubernetes.io/name=traefik --tail=50

# Verify IngressRoutes are discovered
kubectl get ingressroute -A

# Check Traefik dashboard (port-forward)
kubectl port-forward -n traefik deploy/traefik 9000:9000
# Open http://localhost:9000/dashboard/
```
