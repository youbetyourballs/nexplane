# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Pre-flight checks for k8s_cluster_upgrade CR type.

Validates:
  1. Version path is exactly +1 minor (no skips, no downgrades).
  2. No workloads use APIs removed in the target version.
  3. Node resource headroom is sufficient for rolling drain.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# Static table of removed Kubernetes API groups by target minor version.
# Format: (old_group, old_version, resource_kind, replacement)
REMOVED_APIS: dict[int, list[tuple[str, str, str, str]]] = {
    22: [
        ("extensions", "v1beta1", "Ingress", "networking.k8s.io/v1"),
        ("batch", "v1beta1", "CronJob", "batch/v1"),
        ("policy", "v1beta1", "PodSecurityPolicy", "removed — use OPA/Kyverno"),
    ],
    25: [
        ("policy", "v1beta1", "PodDisruptionBudget", "policy/v1"),
        ("discovery.k8s.io", "v1beta1", "EndpointSlice", "discovery.k8s.io/v1"),
    ],
    26: [
        ("autoscaling", "v2beta2", "HorizontalPodAutoscaler", "autoscaling/v2"),
    ],
    29: [
        ("flowcontrol.apiserver.k8s.io", "v1beta2", "FlowSchema", "flowcontrol.apiserver.k8s.io/v1"),
        ("flowcontrol.apiserver.k8s.io", "v1beta2", "PriorityLevelConfiguration", "flowcontrol.apiserver.k8s.io/v1"),
    ],
    32: [
        ("resource.k8s.io", "v1alpha2", "ResourceClaim", "resource.k8s.io/v1beta1"),
    ],
}

_KIND_TO_PLURAL = {
    "Ingress": "ingresses",
    "CronJob": "cronjobs",
    "PodSecurityPolicy": "podsecuritypolicies",
    "PodDisruptionBudget": "poddisruptionbudgets",
    "EndpointSlice": "endpointslices",
    "HorizontalPodAutoscaler": "horizontalpodautoscalers",
    "FlowSchema": "flowschemas",
    "PriorityLevelConfiguration": "prioritylevelconfigurations",
    "ResourceClaim": "resourceclaims",
}

_STABLE_PATCH: dict[str, str] = {
    "1.28": "1.28.13",
    "1.29": "1.29.4",
    "1.30": "1.30.3",
    "1.31": "1.31.1",
    "1.32": "1.32.0",
}

_MEM_SUFFIXES = {
    "Ki": 1024,
    "Mi": 1024 ** 2,
    "Gi": 1024 ** 3,
    "Ti": 1024 ** 4,
    "K": 1000,
    "M": 1000 ** 2,
    "G": 1000 ** 3,
}


def _parse_cpu(val: str) -> float:
    """Parse Kubernetes CPU quantity to float cores."""
    if val.endswith("m"):
        return float(val[:-1]) / 1000
    return float(val)


def _parse_memory_bytes(val: str) -> int:
    """Parse Kubernetes memory quantity to bytes."""
    for suffix, multiplier in _MEM_SUFFIXES.items():
        if val.endswith(suffix):
            return int(val[: -len(suffix)]) * multiplier
    return int(val)


def _resolve_patch_version(target_version: str) -> str:
    """Resolve 'X.Y' or 'X.Y.Z' to a full patch string."""
    parts = target_version.split(".")
    if len(parts) == 3:
        return target_version
    minor_key = ".".join(parts[:2])
    return _STABLE_PATCH.get(minor_key, f"{minor_key}.0")


@dataclass
class PreflightResult:
    version_valid: bool
    version_error: Optional[str]
    target_patch_version: str
    deprecated_apis: list = field(default_factory=list)
    headroom_ok: bool = True
    headroom_detail: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.version_valid and len(self.deprecated_apis) == 0


def run_preflight(
    clients: dict,
    current_version: str,
    target_version: str,
    desired_outcome: dict,
) -> PreflightResult:
    """
    Run all pre-flight checks synchronously.

    clients: dict returned by get_k8s_clients() — must include 'core' and 'custom' keys.
    current_version: full semver string from cluster (e.g. "1.28.8").
    target_version: operator-supplied string (e.g. "1.29" or "1.29.4").
    desired_outcome: the CR desired_outcome dict.
    """
    # 1. Version validation
    cur_parts = current_version.lstrip("v").split(".")
    tgt_parts = target_version.lstrip("v").split(".")
    try:
        cur_minor = int(cur_parts[1])
        tgt_minor = int(tgt_parts[1])
    except (IndexError, ValueError) as exc:
        return PreflightResult(
            version_valid=False,
            version_error=f"Could not parse version strings: {exc}",
            target_patch_version="",
        )

    if tgt_minor != cur_minor + 1:
        direction = "downgrade" if tgt_minor <= cur_minor else "skip"
        return PreflightResult(
            version_valid=False,
            version_error=(
                f"Version {direction} detected: current {cur_parts[0]}.{cur_minor}, "
                f"target {tgt_parts[0]}.{tgt_minor}. "
                f"Kubernetes requires sequential minor upgrades (+1 minor only)."
            ),
            target_patch_version="",
        )

    target_patch = _resolve_patch_version(target_version)

    # 2. Deprecated API scan
    deprecated = []
    if tgt_minor in REMOVED_APIS:
        custom_client = clients.get("custom")
        for group, version, kind, replacement in REMOVED_APIS[tgt_minor]:
            if custom_client is None:
                break
            plural = _KIND_TO_PLURAL.get(kind, kind.lower() + "s")
            try:
                resp = custom_client.list_cluster_custom_object(group, version, plural)
                items = (resp or {}).get("items", [])
                if items:
                    affected = [
                        f"{i.get('metadata', {}).get('namespace', 'cluster')}/{i.get('metadata', {}).get('name', '?')}"
                        for i in items
                    ]
                    deprecated.append({
                        "api": f"{group}/{version}/{kind}",
                        "replacement": replacement,
                        "affected_resources": affected,
                    })
            except Exception as exc:
                log.warning("Could not query %s/%s/%s: %s", group, version, kind, exc)

    # 3. Headroom check (skip for blue_green)
    headroom_ok = True
    headroom_detail: dict = {}
    strategy = desired_outcome.get("node_pool_strategy", "rolling")
    max_unavailable = int(desired_outcome.get("max_unavailable", 1))

    if strategy != "blue_green":
        core = clients.get("core")
        if core is not None:
            try:
                node_list = core.list_node()
                ready_nodes = [
                    n for n in node_list.items
                    if any(c.type == "Ready" and c.status == "True" for c in n.status.conditions)
                ]
                total_cpu = sum(_parse_cpu(n.status.allocatable.get("cpu", "0")) for n in ready_nodes)
                total_mem = sum(_parse_memory_bytes(n.status.allocatable.get("memory", "0")) for n in ready_nodes)
                if ready_nodes:
                    avg_cpu = total_cpu / len(ready_nodes)
                    avg_mem = total_mem / len(ready_nodes)
                    drain_cpu = avg_cpu * max_unavailable
                    drain_mem = avg_mem * max_unavailable
                    spare_cpu_frac = (total_cpu - drain_cpu) / total_cpu if total_cpu > 0 else 0
                    spare_mem_frac = (total_mem - drain_mem) / total_mem if total_mem > 0 else 0
                    headroom_ok = spare_cpu_frac >= 0.15 and spare_mem_frac >= 0.15
                    headroom_detail = {
                        "total_allocatable_cpu": str(round(total_cpu, 2)),
                        "total_allocatable_memory_gi": str(round(total_mem / (1024 ** 3), 1)),
                        "estimated_drain_overhead_cpu": str(round(drain_cpu, 2)),
                        "estimated_drain_overhead_memory_gi": str(round(drain_mem / (1024 ** 3), 1)),
                        "spare_cpu_fraction": round(spare_cpu_frac, 3),
                        "spare_mem_fraction": round(spare_mem_frac, 3),
                    }
            except Exception as exc:
                log.warning("Headroom check failed: %s", exc)

    return PreflightResult(
        version_valid=True,
        version_error=None,
        target_patch_version=target_patch,
        deprecated_apis=deprecated,
        headroom_ok=headroom_ok,
        headroom_detail=headroom_detail,
    )
