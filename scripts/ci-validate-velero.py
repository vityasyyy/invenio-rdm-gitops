#!/usr/bin/env python3
import json
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


# Two untainted workers; the control-plane node is tainted CriticalAddonsOnly.
SCHEDULABLE_NODES = 2


def _cpu_millis(value):
    text = str(value)
    if text.endswith("m"):
        return int(text[:-1])
    return int(float(text) * 1000)


def _memory_mib(value):
    text = str(value)
    for suffix, factor in (("Gi", 1024), ("Mi", 1), ("Ki", 1 / 1024)):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * factor)
    return int(text) // (1024 * 1024)


def _resources(entry):
    """(request cpu/mem, limit cpu/mem) in millicores and MiB."""
    resources = entry or {}
    requests = resources.get("requests") or {}
    limits = resources.get("limits") or {}
    return (
        _cpu_millis(requests.get("cpu", 0)),
        _memory_mib(requests.get("memory", 0)),
        _cpu_millis(limits.get("cpu", 0)),
        _memory_mib(limits.get("memory", 0)),
    )


def _flat_resources(entry):
    """Mover and maintenance configurations use flat *Request/*Limit keys."""
    entry = entry or {}
    return (
        _cpu_millis(entry.get("cpuRequest", 0)),
        _memory_mib(entry.get("memoryRequest", 0)),
        _cpu_millis(entry.get("cpuLimit", 0)),
        _memory_mib(entry.get("memoryLimit", 0)),
    )


def _effective_pod(containers, init_containers):
    """Kubernetes pod-level resources: max(sum of containers, max of init)."""
    totals = [sum(item[index] for item in containers) for index in range(4)]
    for index in range(4):
        for item in init_containers:
            totals[index] = max(totals[index], item[index])
    return tuple(totals)


def resource_budget(root):
    """Worst-case simultaneous Velero footprint vs velero-quota.

    Components: server (+1 rollout surge, Deployment maxSurge rounds up),
    2 node-agent pods, 1 active + 1 prepared mover per schedulable node
    (globalConfig and prepareQueueLength are read as per node), and one
    repository-maintenance job per scheduled namespace (9 repositories live;
    the namespace count is the conservative upper bound). Init-container
    effective requests come from the namespace LimitRange defaults.
    """
    root = Path(root)
    values = yaml.safe_load((root / "k8s/infra/velero/values.yaml").read_text()) or {}
    config = yaml.safe_load((root / "k8s/infra/velero/node-agent-config.yaml").read_text()) or {}
    settings = json.loads((config.get("data") or {})["node-agent-config.json"])
    schedule = yaml.safe_load((root / "k8s/infra/velero/backup-schedule.yaml").read_text()) or {}
    quota = (
        yaml.safe_load(
            (root / "k8s/infra/security/resource-quotas/velero-quota.yaml").read_text()
        )
        or {}
    ).get("spec", {}).get("hard", {})
    limit_range = yaml.safe_load((root / "k8s/infra/velero/limit-range.yaml").read_text()) or {}
    limit_range_entry = ((limit_range.get("spec") or {}).get("limits") or [{}])[0]
    defaults, max_defaults = (
        limit_range_entry.get("defaultRequest") or {},
        limit_range_entry.get("default") or {},
    )

    server = _effective_pod(
        [_resources(values.get("resources"))],
        [
            (
                _cpu_millis(defaults.get("cpu", 0)),
                _memory_mib(defaults.get("memory", 0)),
                _cpu_millis(max_defaults.get("cpu", 0)),
                _memory_mib(max_defaults.get("memory", 0)),
            )
        ],
    )
    agent = _resources((values.get("nodeAgent") or {}).get("resources"))
    mover = _flat_resources(settings.get("podResources"))
    global_maintenance = (
        (
            (
                (values.get("configuration") or {}).get("repositoryMaintenanceJob") or {}
            ).get("repositoryConfigData") or {}
        ).get("global") or {}
    )
    maintenance = _flat_resources(global_maintenance.get("podResources"))
    concurrency = settings.get("loadConcurrency") or {}
    movers_per_node = int(concurrency.get("globalConfig") or 0) + int(
        concurrency.get("prepareQueueLength") or 0
    )
    mover_count = SCHEDULABLE_NODES * movers_per_node
    namespaces = ((schedule.get("spec") or {}).get("template") or {}).get(
        "includedNamespaces"
    ) or []
    maintenance_count = len(namespaces)
    server_count = 2  # one replica plus the maxSurge pod during rollout

    budget = {
        "pods": server_count + 2 + mover_count + maintenance_count,
        "requests.cpu": server[0] * server_count
        + agent[0] * 2
        + mover[0] * mover_count
        + maintenance[0] * maintenance_count,
        "requests.memory": server[1] * server_count
        + agent[1] * 2
        + mover[1] * mover_count
        + maintenance[1] * maintenance_count,
        "limits.cpu": server[2] * server_count
        + agent[2] * 2
        + mover[2] * mover_count
        + maintenance[2] * maintenance_count,
        "limits.memory": server[3] * server_count
        + agent[3] * 2
        + mover[3] * mover_count
        + maintenance[3] * maintenance_count,
    }
    hard = {
        "pods": int(quota.get("pods", 0) or 0),
        "requests.cpu": _cpu_millis(quota.get("requests.cpu", 0) or 0),
        "requests.memory": _memory_mib(quota.get("requests.memory", 0) or 0),
        "limits.cpu": _cpu_millis(quota.get("limits.cpu", 0) or 0),
        "limits.memory": _memory_mib(quota.get("limits.memory", 0) or 0),
    }
    return budget, hard


def validate_resource_budget(root, errors):
    """Fail when the bounded worst case does not fit velero-quota."""
    root = Path(root)
    values = yaml.safe_load((root / "k8s/infra/velero/values.yaml").read_text()) or {}
    if not (values.get("nodeAgent") or {}).get("resources"):
        errors.append("nodeAgent.resources must be pinned so daemonset pods are uniform")
    global_maintenance = (
        (
            (
                (values.get("configuration") or {}).get("repositoryMaintenanceJob") or {}
            ).get("repositoryConfigData") or {}
        ).get("global") or {}
    )
    pod_resources = global_maintenance.get("podResources") or {}
    if not pod_resources:
        errors.append("values.yaml must pin repository maintenance podResources")
    else:
        if pod_resources.get("memoryRequest") != pod_resources.get("memoryLimit"):
            errors.append(
                "repository maintenance memoryRequest must equal memoryLimit"
            )
        if global_maintenance.get("keepLatestMaintenanceJobs") != 3:
            errors.append("repository maintenance history must stay bounded at 3 jobs")

    config = yaml.safe_load((root / "k8s/infra/velero/node-agent-config.yaml").read_text()) or {}
    settings = json.loads((config.get("data") or {})["node-agent-config.json"])
    mover_resources = settings.get("podResources") or {}
    if mover_resources.get("memoryRequest") != mover_resources.get("memoryLimit"):
        errors.append("data-mover memoryRequest must equal memoryLimit")

    budget, hard = resource_budget(root)
    for key, value in budget.items():
        if value > hard.get(key, 0):
            errors.append(
                f"velero-quota {key} is too small: worst case {value} exceeds hard {hard[key]}"
            )


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


def _pod_annotation(path, kind_name, key):
    """Return the pod-template annotation from a workload manifest, or None."""
    try:
        documents = list(yaml.safe_load_all(Path(path).read_text()))
    except (OSError, yaml.YAMLError):
        return None
    for document in documents:
        if not isinstance(document, dict) or document.get("kind") != kind_name:
            continue
        template = ((document.get("spec") or {}).get("template") or {})
        annotations = ((template.get("metadata") or {}).get("annotations") or {})
        if key in annotations:
            return annotations[key]
    return None


def validate_backup_scope(root, errors):
    """Opt-in allowlist: durable volumes only, no temp or self-backup volumes."""
    root = Path(root)
    expected = {
        ("k8s/apps/invenio/invenio-deployment.yaml", "Deployment"): {"data", "archive"},
        ("k8s/apps/invenio-deps/redis/manifests/redis.yaml", "Deployment"): {"data"},
    }
    for (relative_path, kind_name), volumes in expected.items():
        path = root / relative_path
        if not path.exists():
            errors.append(f"{relative_path} is required for backup scope validation")
            continue
        value = _pod_annotation(path, kind_name, "backup.velero.io/backup-volumes")
        if value is None:
            errors.append(f"{relative_path} must opt in to file-system backup")
            continue
        selected = {item.strip() for item in value.split(",") if item.strip()}
        if selected != volumes:
            errors.append(
                f"{relative_path} must back up exactly {sorted(volumes)}, found {sorted(selected)}"
            )
        if "tmp" in selected:
            errors.append(f"{relative_path} must not back up scratch volumes")

    grafana_values = root / "k8s/infra/monitoring/values.yaml"
    if not grafana_values.exists():
        errors.append("k8s/infra/monitoring/values.yaml is required for backup scope validation")
    else:
        values = yaml.safe_load(grafana_values.read_text()) or {}
        annotations = ((values.get("grafana") or {}).get("podAnnotations") or {})
        selected = str(annotations.get("backup.velero.io/backup-volumes", ""))
        if {item.strip() for item in selected.split(",") if item.strip()} != {"storage"}:
            errors.append("Grafana must opt in to backing up only its storage volume")

    schedule_path = root / "k8s/infra/velero/backup-schedule.yaml"
    if not schedule_path.exists():
        errors.append("backup-schedule.yaml is required for backup scope validation")
    else:
        template = (yaml.safe_load(schedule_path.read_text()) or {}).get("spec", {}).get(
            "template", {}
        )
        if template.get("defaultVolumesToFsBackup") is not False:
            errors.append("backup-schedule.yaml must use opt-in file-system backup")
        if template.get("snapshotVolumes") is not False:
            errors.append("backup-schedule.yaml must not request volume snapshots")

    config_path = root / "k8s/infra/velero/node-agent-config.yaml"
    if not config_path.exists():
        errors.append("node-agent-config.yaml is required for data-mover sizing")
        return
    document = yaml.safe_load(config_path.read_text()) or {}
    payload = (document.get("data") or {}).get("node-agent-config.json")
    if not payload:
        errors.append("node-agent-config.yaml must carry node-agent-config.json")
        return
    try:
        settings = json.loads(payload)
    except ValueError as error:
        errors.append(f"node-agent-config.json is not valid JSON: {error}")
        return
    resources = settings.get("podResources") or {}
    if resources.get("memoryLimit") != "1Gi":
        errors.append("data-mover memoryLimit must be the reviewed 1Gi value")
    if resources.get("cpuLimit") != "250m":
        errors.append("data-mover cpuLimit must stay at the 250m LimitRange default")
    concurrency = settings.get("loadConcurrency") or {}
    if concurrency.get("globalConfig") != 1:
        errors.append("data-mover concurrency must be bounded to 1 per node")
    if concurrency.get("prepareQueueLength") != 1:
        errors.append("prepared data-mover queue length must be bounded to 1")

    kustomization_path = root / "k8s/infra/velero/kustomization.yaml"
    if not kustomization_path.exists():
        errors.append("Velero kustomization is required for data-mover sizing")
    else:
        resources_list = (yaml.safe_load(kustomization_path.read_text()) or {}).get(
            "resources"
        ) or []
        if "node-agent-config.yaml" not in resources_list:
            errors.append("Velero kustomization must include node-agent-config.yaml")

    values_path = root / "k8s/infra/velero/values.yaml"
    if not values_path.exists():
        errors.append("Velero values are required for data-mover sizing")
    else:
        extra_args = ((yaml.safe_load(values_path.read_text()) or {}).get("nodeAgent") or {}).get(
            "extraArgs"
        ) or []
        if "--node-agent-configmap=node-agent-config" not in extra_args:
            errors.append("node-agent must be wired to node-agent-config")


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

    try:
        validate_backup_scope(root, errors)
    except (OSError, yaml.YAMLError) as error:
        errors.append(f"Velero backup scope: {error}")

    try:
        validate_resource_budget(root, errors)
    except (OSError, yaml.YAMLError, ValueError, KeyError) as error:
        errors.append(f"Velero resource budget: {error}")

    return errors


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    errors = validate(root)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"FAILED: {len(errors)} Velero ownership violation(s)")
        return 1
    print("OK: Velero exclusions are scoped, Schedule is Git-managed, the AWS plugin matches the Velero series, and the worst-case resource budget fits velero-quota")
    return 0


if __name__ == "__main__":
    sys.exit(main())
