#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RENDER_DIR="${REPO_ROOT}/rendered"
FAIL_COUNT=0

install_tools() {
  if ! command -v kustomize &>/dev/null; then
    echo "Installing kustomize..."
    KUSTOMIZE_VERSION="5.6.0"
    curl -sL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" \
      | tar xz -C /usr/local/bin
    echo "  kustomize v${KUSTOMIZE_VERSION} installed"
  fi

  if ! command -v helm &>/dev/null; then
    echo "Installing helm..."
    curl -sL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    echo "  helm installed"
  fi
}

install_tools

rm -rf "${RENDER_DIR}"
mkdir -p "${RENDER_DIR}"

render_kustomize() {
  local src="$1"
  local dst_name
  dst_name="$(echo "${src}" | tr '/' '_')"
  local dst="${RENDER_DIR}/${dst_name}.yaml"

  local full_src="${REPO_ROOT}/${src}"
  if [ ! -f "${full_src}/kustomization.yaml" ]; then
    echo "✗ kustomize: ${src} (no kustomization.yaml found)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return
  fi

  if kustomize build "${full_src}" > "${dst}" 2>/dev/null; then
    echo "✓ kustomize: ${src}"
  else
    echo "✗ kustomize: ${src}"
    rm -f "${dst}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

render_helm() {
  local name="$1"
  local chart="$2"
  local repo="$3"
  local version="$4"
  local namespace="$5"
  local values="$6"
  local dst="${RENDER_DIR}/helm_${name}.yaml"

  local values_args=()
  if [ -n "${values}" ]; then
    if [ -f "${REPO_ROOT}/${values}" ]; then
      values_args=(-f "${REPO_ROOT}/${values}")
    else
      local tmp_values
      tmp_values="$(mktemp)"
      echo "${values}" > "${tmp_values}"
      values_args=(-f "${tmp_values}")
    fi
  fi

  if helm template "${name}" "${chart}" --repo "${repo}" --version "${version}" --namespace "${namespace}" "${values_args[@]}" > "${dst}" 2>/dev/null; then
    echo "✓ helm: ${name}"
  else
    echo "✗ helm: ${name}"
    rm -f "${dst}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

add_helm_repos() {
  helm repo add traefik https://traefik.github.io/charts 2>/dev/null || true
  helm repo add minio https://charts.min.io/ 2>/dev/null || true
  helm repo add velero https://vmware-tanzu.github.io/helm-charts 2>/dev/null || true
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>/dev/null || true
  helm repo add grafana https://grafana.github.io/helm-charts 2>/dev/null || true
  helm repo add cloudnative-pg https://cloudnative-pg.github.io/charts 2>/dev/null || true
  helm repo add opensearch https://opensearch-project.github.io/helm-charts/ 2>/dev/null || true
  helm repo update 2>/dev/null || true
}

echo "=== Rendering Kustomize directories ==="

render_kustomize "k8s/infra/argocd"
render_kustomize "k8s/infra/argocd-image-updater"
render_kustomize "k8s/infra/minio"
render_kustomize "k8s/infra/monitoring"
render_kustomize "k8s/infra/sealed-secrets"
render_kustomize "k8s/infra/security"
render_kustomize "k8s/infra/velero"
render_kustomize "k8s/apps/invenio"
render_kustomize "k8s/apps/invenio-deps/postgresql"
render_kustomize "k8s/apps/invenio-deps/opensearch/manifests"
render_kustomize "k8s/apps/invenio-deps/redis/manifests"
render_kustomize "external-lb/k8s"

echo ""
echo "=== Rendering Helm charts ==="

add_helm_repos

render_helm "traefik" "traefik" "https://traefik.github.io/charts" "v39.0.6" "traefik" "k8s/infra/traefik/values.yaml"

render_helm "minio" "minio" "https://charts.min.io/" "5.4.0" "minio" "k8s/infra/minio/values.yaml"

render_helm "velero" "velero" "https://vmware-tanzu.github.io/helm-charts" "11.4.0" "velero" "k8s/infra/velero/values.yaml"

render_helm "monitoring" "kube-prometheus-stack" "https://prometheus-community.github.io/helm-charts" "69.6.0" "monitoring" "k8s/infra/monitoring/values.yaml"

render_helm "loki" "loki" "https://grafana.github.io/helm-charts" "6.24.0" "monitoring" "k8s/infra/loki/values.yaml"

CLOUDNATIVE_PG_VALUES="replicaCount: 1
resources:
  limits:
    cpu: 500m
    memory: 256Mi
  requests:
    cpu: 100m
    memory: 128Mi
webhook:
  livenessProbe:
    initialDelaySeconds: 60
  readinessProbe:
    initialDelaySeconds: 30"
render_helm "cloudnative-pg" "cloudnative-pg" "https://cloudnative-pg.github.io/charts" "0.23.0" "database" "${CLOUDNATIVE_PG_VALUES}"

OPENSEARCH_VALUES="clusterName: opensearch-cluster
nodeGroup: master
replicas: 1
singleNode: true
config:
  opensearch.yml: |
    cluster.name: opensearch-cluster
    network.host: 0.0.0.0
    plugins.security.disabled: true
resources:
  limits:
    cpu: 1000m
    memory: 2Gi
  requests:
    cpu: 250m
    memory: 512Mi
persistence:
  enabled: true
  storageClass: btd-nfs
  size: 10Gi
securityContext:
  runAsUser: 1000
  runAsGroup: 1000
roles:
  - master
  - ingest
  - data
metricsExporter:
  enabled: true
  serviceMonitor:
    enabled: true
    namespace: monitoring
    labels:
      release: monitoring"
render_helm "opensearch" "opensearch" "https://opensearch-project.github.io/helm-charts/" "2.32.0" "search" "${OPENSEARCH_VALUES}"

echo ""
echo "=== Summary ==="
TOTAL=$(ls -1 "${RENDER_DIR}"/*.yaml 2>/dev/null | wc -l | tr -d ' ')
echo "Rendered: ${TOTAL} manifests to ${RENDER_DIR}/"

if [ "${FAIL_COUNT}" -gt 0 ]; then
  echo "Failed: ${FAIL_COUNT} renders"
  exit 1
fi

echo "All renders succeeded"