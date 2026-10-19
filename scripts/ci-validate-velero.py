#!/usr/bin/env python3
import re
import sys
from pathlib import Path

import yaml


EXPECTED_EXCLUSION = {
    "apiGroups": ["velero.io"],
    "kinds": ["Backup", "Restore"],
    "clusters": ["https://kubernetes.default.svc", "https://10.43.0.1:443"],
}

# Velero chart versions ship a fixed Velero server line. Keep the mapping
# explicit so a chart bump cannot silently pair an unsupported AWS plugin.
VELERO_CHART_SERIES = {
    "11.4.0": "1.17",
}
AWS_PLUGIN_SERIES = {
    "1.17": "1.13",
}


def _series(version):
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.\d+)?", str(version))
    return f"{match.group(1)}.{match.group(2)}" if match else None


def validate_plugin_pair(root, errors):
    app = yaml.safe_load((root / "argocd/apps/velero.yaml").read_text()) or {}
    sources = ((app.get("spec") or {}).get("sources") or [])
    chart_versions = [
        source.get("targetRevision")
        for source in sources
        if isinstance(source, dict) and source.get("chart") == "velero"
    ]
    if len(chart_versions) != 1:
        errors.append("argocd/apps/velero.yaml must reference exactly one Velero chart")
        return
    velero_series = VELERO_CHART_SERIES.get(chart_versions[0])
    if not velero_series:
        errors.append(
            f"Velero chart {chart_versions[0]} has no declared plugin compatibility mapping"
        )
        return

    values = yaml.safe_load((root / "k8s/infra/velero/values.yaml").read_text()) or {}
    plugins = [
        container
        for container in values.get("initContainers") or []
        if isinstance(container, dict)
        and container.get("name") == "velero-plugin-for-aws"
    ]
    if len(plugins) != 1:
        errors.append("Velero values must declare exactly one velero-plugin-for-aws init container")
        return
    image = str(plugins[0].get("image") or "")
    repository, separator, tag = image.rpartition(":")
    if not separator or repository != "velero/velero-plugin-for-aws":
        errors.append(
            "Velero AWS plugin image must be pinned as velero/velero-plugin-for-aws:<version>"
        )
        return
    plugin_series = _series(tag)
    expected = AWS_PLUGIN_SERIES[velero_series]
    if plugin_series != expected:
        errors.append(
            f"Velero {velero_series}.x requires AWS plugin {expected}.x, found {tag}"
        )


def validate(root):
    errors = []

    def load(relative_path):
        try:
            return yaml.safe_load((root / relative_path).read_text()) or {}
        except (OSError, yaml.YAMLError) as error:
            errors.append(f"{relative_path}: {error}")
            return {}

    argocd = load("k8s/infra/argocd/patches/argocd-cm.yaml")
    if (
        argocd.get("kind") != "ConfigMap"
        or (argocd.get("metadata") or {}).get("name") != "argocd-cm"
    ):
        errors.append("ArgoCD settings must be in the argocd-cm ConfigMap")
    try:
        exclusions = yaml.safe_load(
            (argocd.get("data") or {}).get("resource.exclusions", "[]")
        ) or []
    except yaml.YAMLError as error:
        exclusions = []
        errors.append(f"argocd-cm resource.exclusions: {error}")
    if not isinstance(exclusions, list):
        exclusions = []
    if exclusions.count(EXPECTED_EXCLUSION) != 1:
        errors.append(
            "argocd-cm resource.exclusions must include exactly one local velero.io Backup/Restore rule"
        )
    for rule in exclusions:
        if not isinstance(rule, dict):
            errors.append("argocd-cm resource.exclusions entries must be mappings")
            continue
        groups = rule.get("apiGroups")
        kinds = rule.get("kinds")
        if any(
            not isinstance(patterns, list)
            or not patterns
            or any(
                not isinstance(pattern, str)
                or not pattern
                or any(token in pattern for token in "*?[")
                for pattern in patterns
            )
            for patterns in (groups, kinds)
        ):
            errors.append(
                "argocd-cm resource.exclusions must not wildcard apiGroups or kinds"
            )
            continue
        if (
            rule != EXPECTED_EXCLUSION
            and "velero.io" in groups
            and {"Backup", "Restore", "Schedule"}.intersection(kinds)
        ):
            errors.append(
                "argocd-cm may not exclude Velero Backup, Restore, or Schedule outside the scoped rule"
            )

    kustomization = load("k8s/infra/velero/kustomization.yaml")
    if "backup-schedule.yaml" not in (kustomization.get("resources") or []):
        errors.append("Velero kustomization must keep backup-schedule.yaml Git-managed")
    schedule = load("k8s/infra/velero/backup-schedule.yaml")
    if schedule.get("apiVersion") != "velero.io/v1" or schedule.get("kind") != "Schedule":
        errors.append("backup-schedule.yaml must remain a velero.io/v1 Schedule")

    try:
        validate_plugin_pair(root, errors)
    except (OSError, yaml.YAMLError) as error:
        errors.append(f"Velero plugin compatibility: {error}")

    return errors


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    errors = validate(root)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"FAILED: {len(errors)} Velero ownership violation(s)")
        return 1
    print("OK: Velero exclusions are scoped, Schedule is Git-managed, and the AWS plugin matches the Velero series")
    return 0


if __name__ == "__main__":
    sys.exit(main())
