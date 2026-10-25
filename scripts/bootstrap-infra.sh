#!/bin/bash
# Cold-start only: install ArgoCD, then hand child reconciliation to the root app.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "$#" -ne 0 ]; then
  echo "Usage: $0 (clean cluster only; reset is not supported)" >&2
  exit 1
fi

if ! command -v kubectl &>/dev/null; then
  echo "ERROR: kubectl not found." >&2
  exit 1
fi
if ! python3 -c 'import yaml' 2>/dev/null; then
  echo "ERROR: Python 3 with PyYAML is required to split CRDs from the rendered installation." >&2
  exit 1
fi

kubectl cluster-info >/dev/null
# --ignore-not-found distinguishes absence from authorization/connection failures.
EXISTING_NAMESPACE=$(kubectl get namespace argocd --ignore-not-found -o name)
if [ -n "$EXISTING_NAMESPACE" ]; then
  echo "ERROR: argocd namespace exists; bootstrap will not overwrite or delete it." >&2
  echo "Inspect the existing installation and use the reviewed GitOps repair procedure." >&2
  exit 1
fi

kubectl wait --for=condition=Ready nodes --all --timeout=120s
# Render the authoritative installation once before mutating the cluster.
RENDER_DIR=$(mktemp -d)
trap 'rm -f "$RENDER_DIR/rendered.yaml" "$RENDER_DIR/crds.yaml" "$RENDER_DIR/resources.yaml"; rmdir "$RENDER_DIR"' EXIT
kubectl kustomize "$REPO_ROOT/k8s/infra/argocd/" > "$RENDER_DIR/rendered.yaml"
python3 - "$RENDER_DIR" <<'PY'
import sys
from pathlib import Path
import yaml

folder = Path(sys.argv[1])
objects = [obj for obj in yaml.safe_load_all((folder / "rendered.yaml").read_text()) if obj]
crds = [obj for obj in objects if obj["kind"] == "CustomResourceDefinition"]
resources = [obj for obj in objects if obj["kind"] != "CustomResourceDefinition"]
if not crds or not resources:
    raise SystemExit("ERROR: rendered ArgoCD installation must contain CRDs and resources")
for name, documents in (("crds", crds), ("resources", resources)):
    (folder / f"{name}.yaml").write_text(yaml.safe_dump_all(documents))
PY
kubectl create namespace argocd
kubectl apply -f "$RENDER_DIR/crds.yaml"
kubectl wait --for=condition=Established crd/applications.argoproj.io \
  crd/appprojects.argoproj.io --timeout=120s
kubectl apply -f "$RENDER_DIR/resources.yaml"
kubectl wait --for=condition=Available deployment/argocd-server \
  deployment/argocd-repo-server -n argocd --timeout=180s
kubectl rollout status statefulset/argocd-application-controller \
  -n argocd --timeout=180s

# Projects are included in the ArgoCD kustomization, not argocd/projects.
kubectl get appproject infra invenio -n argocd -o name
kubectl apply -f "$REPO_ROOT/argocd/root.yaml"
kubectl get application apps -n argocd -o name

echo "ArgoCD bootstrap submitted; child reconciliation is asynchronous."
echo "Verify Applications and run ./scripts/verify-infra.sh before claiming readiness."
echo "Create the tunnel token separately with ./external-lb/scripts/bootstrap-dev.sh."
echo "Admin credentials are not read or printed by this script."
