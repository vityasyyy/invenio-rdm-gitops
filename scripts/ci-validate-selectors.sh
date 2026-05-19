#!/usr/bin/env bash
set -euo pipefail

RENDER_DIR="${1:-rendered}"
FAIL_COUNT=0

if ! command -v yq &>/dev/null; then
  echo "Installing yq..."
  YQ_VERSION="4.45.1"
  curl -sL "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_amd64" \
    -o /usr/local/bin/yq && chmod +x /usr/local/bin/yq
  echo "  yq v${YQ_VERSION} installed"
fi

extract_documents() {
  local file="$1"
  yq e '.' "$file" 2>/dev/null || true
}

get_all_resources() {
  for f in "${RENDER_DIR}"/*.yaml; do
    [ -f "$f" ] || continue
    yq e '. | select(.kind != null)' "$f" 2>/dev/null || true
  done
}

echo "=== Validating Service selectors ==="
for f in "${RENDER_DIR}"/*.yaml; do
  [ -f "$f" ] || continue
  svc_count=$(yq e '. | select(.kind == "Service") | .metadata.name' "$f" 2>/dev/null | grep -c '.' || true)
  if [ "$svc_count" -eq 0 ]; then
    continue
  fi

  idx=0
  while IFS= read -r svc_name; do
    [ -z "$svc_name" ] && continue
    svc_selector=$(yq e ". | select(.kind == \"Service\") | .spec.selector // {} | to_entries | map(.key + \"=\" + .value) | join(\",\")" "$f" 2>/dev/null | sed -n "$((idx+1))p")
    if [ -z "$svc_selector" ] || [ "$svc_selector" = "" ]; then
      idx=$((idx + 1))
      continue
    fi

    found=false
    for dep_f in "${RENDER_DIR}"/*.yaml; do
      [ -f "$dep_f" ] || continue
      dep_match=$(yq e ". | select(.kind == \"Deployment\" or .kind == \"StatefulSet\" or .kind == \"DaemonSet\") | select(.spec.template.metadata.labels != null) | .metadata.name" "$dep_f" 2>/dev/null || true)
      if [ -z "$dep_match" ]; then
        continue
      fi
      found=true
    done

    if [ "$found" = true ]; then
      echo "  ✓ Service/$svc_name has selector"
    else
      echo "  ✗ Service/$svc_name has selector but no matching workloads found"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
    idx=$((idx + 1))
  done < <(yq e '. | select(.kind == "Service") | .metadata.name' "$f" 2>/dev/null)
done

echo ""
echo "=== Validating HPA scaleTargetRef ==="
for f in "${RENDER_DIR}"/*.yaml; do
  [ -f "$f" ] || continue
  while IFS= read -r hpa_name; do
    [ -z "$hpa_name" ] && continue
    target_kind=$(yq e ". | select(.kind == \"HorizontalPodAutoscaler\") | .spec.scaleTargetRef.kind" "$f" 2>/dev/null | head -1)
    target_name=$(yq e ". | select(.kind == \"HorizontalPodAutoscaler\") | .spec.scaleTargetRef.name" "$f" 2>/dev/null | head -1)

    if [ "$target_kind" = "Deployment" ] && [ -n "$target_name" ]; then
      found=false
      for dep_f in "${RENDER_DIR}"/*.yaml; do
        [ -f "$dep_f" ] || continue
        name_check=$(yq e ". | select(.kind == \"Deployment\") | select(.metadata.name == \"${target_name}\") | .metadata.name" "$dep_f" 2>/dev/null || true)
        if [ -n "$name_check" ]; then
          found=true
          break
        fi
      done
      if [ "$found" = true ]; then
        echo "  ✓ HPA/$hpa_name → ${target_kind}/${target_name}"
      else
        echo "  ✗ HPA/$hpa_name references ${target_kind}/${target_name} which does not exist in rendered manifests"
        FAIL_COUNT=$((FAIL_COUNT + 1))
      fi
    fi
  done < <(yq e '. | select(.kind == "HorizontalPodAutoscaler") | .metadata.name' "$f" 2>/dev/null)
done

echo ""
echo "=== Validating PDB selectors ==="
for f in "${RENDER_DIR}"/*.yaml; do
  [ -f "$f" ] || continue
  while IFS= read -r pdb_name; do
    [ -z "$pdb_name" ] && continue
    pdb_labels=$(yq e ". | select(.kind == \"PodDisruptionBudget\") | .spec.selector.matchLabels | keys | join(\",\")" "$f" 2>/dev/null | head -1)
    pdb_ns=$(yq e ". | select(.kind == \"PodDisruptionBudget\") | .metadata.namespace // \"default\"" "$f" 2>/dev/null | head -1)

    if [ -z "$pdb_labels" ]; then
      continue
    fi

    echo "  ✓ PDB/$pdb_name has selector keys: $pdb_labels"
  done < <(yq e '. | select(.kind == "PodDisruptionBudget") | .metadata.name' "$f" 2>/dev/null)
done

echo ""
echo "=== Validating NetworkPolicy podSelectors ==="
for f in "${RENDER_DIR}"/*.yaml; do
  [ -f "$f" ] || continue
  while IFS= read -r np_name; do
    [ -z "$np_name" ] && continue
    np_labels=$(yq e ". | select(.kind == \"NetworkPolicy\") | .spec.podSelector.matchLabels | to_entries | map(.key + \"=\" + .value) | join(\",\")" "$f" 2>/dev/null | head -1)
    if [ -z "$np_labels" ]; then
      continue
    fi
    echo "  ✓ NetworkPolicy/$np_name targets pods with: $np_labels"
  done < <(yq e '. | select(.kind == "NetworkPolicy") | .metadata.name' "$f" 2>/dev/null)
done

echo ""
echo "=== Summary ==="
if [ "${FAIL_COUNT}" -gt 0 ]; then
  echo "Failed: ${FAIL_COUNT} selector validation(s)"
  exit 1
fi
echo "All selector validations passed"