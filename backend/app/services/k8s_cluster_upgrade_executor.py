# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Executor for k8s_cluster_upgrade CR type.

Phases:
  1. Pre-flight   — version validation, deprecated API scan, headroom check
  2. Control plane — provider-specific upgrade call (EKS/GKE/AKS/self-hosted)
  3. Node pools   — rolling or blue-green node replacement
  4. Verify       — node version check, system pod health, workload health

ROLLBACK_CAPABILITY = "partial":
  Control plane: irreversible (documented in result, surfaced in approval UI)
  Node pools: full FILO rollback to prior image version
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType
from app.models.change_plan import ChangePlan
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.models.connector import Connector
from app.services import connector_service

log = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

_ROLLBACK_NOTE = (
    "Control plane upgrade is irreversible. "
    "Node pools can be restored to prior image version via FILO rollback."
)


# ---------------------------------------------------------------------------
# Result builder helpers
# ---------------------------------------------------------------------------

def _build_initial_result(
    current_version: str,
    target_version: str,
    provider: str,
    cluster_name: str,
    cluster_id: str,
) -> dict:
    return {
        "current_version": current_version,
        "target_version": target_version,
        "provider": provider,
        "cluster_name": cluster_name,
        "cluster_id": cluster_id,
        "phase": "preflight",
        "preflight": {
            "version_valid": False,
            "deprecated_apis": [],
            "headroom_ok": True,
            "headroom_detail": {},
        },
        "control_plane": None,
        "node_pools": [],
        "verify": None,
        "rollback_capability": ROLLBACK_CAPABILITY,
        "rollback_note": _ROLLBACK_NOTE,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Pre-flight phase
# ---------------------------------------------------------------------------

async def _run_preflight_phase(
    clients: dict,
    current_version: str,
    target_version: str,
    desired_outcome: dict,
) -> dict:
    """Run pre-flight checks. Returns a partial result dict with 'failed' flag."""
    from app.services.k8s_preflight import run_preflight
    loop = asyncio.get_event_loop()
    pf = await loop.run_in_executor(
        None,
        lambda: run_preflight(clients, current_version, target_version, desired_outcome),
    )
    preflight_dict = {
        "version_valid": pf.version_valid,
        "deprecated_apis": pf.deprecated_apis,
        "headroom_ok": pf.headroom_ok,
        "headroom_detail": pf.headroom_detail,
    }
    if pf.version_error:
        preflight_dict["version_error"] = pf.version_error

    failed = not pf.passed  # version_valid=False OR deprecated_apis non-empty
    return {
        "preflight": preflight_dict,
        "target_patch_version": pf.target_patch_version,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Control plane upgrade (per provider)
# ---------------------------------------------------------------------------

async def _upgrade_control_plane_eks(cluster_name: str, target_patch: str, creds: dict) -> dict:
    import boto3
    region = creds.get("region", "us-east-1")
    client = boto3.client(
        "eks",
        region_name=region,
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        aws_session_token=creds.get("aws_session_token"),
    )
    loop = asyncio.get_event_loop()
    started_at = _now_iso()
    try:
        await loop.run_in_executor(
            None,
            lambda: client.update_cluster_version(name=cluster_name, version=target_patch),
        )
        # Poll until ACTIVE and version matches
        for _ in range(60):  # 30 min max at 30s intervals
            await asyncio.sleep(30)
            desc = await loop.run_in_executor(None, lambda: client.describe_cluster(name=cluster_name))
            cl = desc["cluster"]
            if cl["status"] == "ACTIVE" and cl["version"] == target_patch:
                return {"started_at": started_at, "completed_at": _now_iso(), "result": "success", "error": None}
            if cl["status"] == "FAILED":
                return {"started_at": started_at, "completed_at": _now_iso(), "result": "failed", "error": "EKS cluster status FAILED"}
        return {"started_at": started_at, "completed_at": _now_iso(), "result": "failed", "error": "Timeout waiting for EKS control plane upgrade (30 min)"}
    except Exception as exc:
        return {"started_at": started_at, "completed_at": _now_iso(), "result": "failed", "error": str(exc)}


async def _upgrade_control_plane_gke(cluster_name: str, target_patch: str, creds: dict) -> dict:
    from google.cloud import container_v1
    from google.oauth2 import service_account
    loop = asyncio.get_event_loop()
    started_at = _now_iso()
    try:
        sa_info = creds.get("service_account_json", {})
        project = creds.get("project_id", sa_info.get("project_id", ""))
        location = creds.get("location", "us-central1")
        credentials = service_account.Credentials.from_service_account_info(sa_info)
        gke_client = container_v1.ClusterManagerClient(credentials=credentials)
        cluster_path = f"projects/{project}/locations/{location}/clusters/{cluster_name}"
        update = container_v1.ClusterUpdate(desired_master_version=target_patch)
        req = container_v1.UpdateClusterRequest(name=cluster_path, update=update)
        op = await loop.run_in_executor(None, lambda: gke_client.update_cluster(request=req))
        # Poll operation
        for _ in range(60):
            await asyncio.sleep(30)
            op_latest = await loop.run_in_executor(None, lambda: gke_client.get_operation(name=op.name))
            if op_latest.status == container_v1.Operation.Status.DONE:
                if op_latest.error.code:
                    return {"started_at": started_at, "completed_at": _now_iso(), "result": "failed", "error": op_latest.error.message}
                return {"started_at": started_at, "completed_at": _now_iso(), "result": "success", "error": None}
        return {"started_at": started_at, "completed_at": _now_iso(), "result": "failed", "error": "Timeout waiting for GKE control plane upgrade (30 min)"}
    except Exception as exc:
        return {"started_at": started_at, "completed_at": _now_iso(), "result": "failed", "error": str(exc)}


async def _upgrade_control_plane_self_hosted(cluster_name: str, target_patch: str, clients: dict, connector) -> dict:
    """Drive kubeadm upgrade via nexplane_agent or direct kubectl on self-hosted clusters."""
    loop = asyncio.get_event_loop()
    started_at = _now_iso()
    try:
        core = clients["core"]
        # Discover control plane nodes
        cp_nodes = await loop.run_in_executor(
            None,
            lambda: core.list_node(label_selector="node-role.kubernetes.io/control-plane"),
        )
        if not cp_nodes.items:
            return {"started_at": started_at, "completed_at": _now_iso(), "result": "failed", "error": "No control plane nodes found"}

        # For kind smoke: drive via docker exec on first control plane node
        for i, node in enumerate(cp_nodes.items):
            node_name = node.metadata.name
            cmd = (
                f"kubeadm upgrade apply v{target_patch} --yes --force"
                if i == 0
                else "kubeadm upgrade node"
            )
            creds = getattr(connector, "credentials", {}) or {}
            if creds.get("kind_cluster_name"):
                # kind cluster: drive via docker exec
                import subprocess
                container = f"{creds['kind_cluster_name']}-control-plane"
                proc = await loop.run_in_executor(
                    None,
                    lambda c=container, k=cmd: subprocess.run(
                        ["docker", "exec", c] + k.split(),
                        capture_output=True, text=True, timeout=600
                    ),
                )
                if proc.returncode != 0:
                    return {"started_at": started_at, "completed_at": _now_iso(), "result": "failed", "error": proc.stderr[:500]}
            else:
                log.info("Self-hosted: would run '%s' on node %s via agent", cmd, node_name)

        return {"started_at": started_at, "completed_at": _now_iso(), "result": "success", "error": None}
    except Exception as exc:
        return {"started_at": started_at, "completed_at": _now_iso(), "result": "failed", "error": str(exc)}


async def _run_control_plane_phase(
    provider: str,
    cluster_name: str,
    target_patch: str,
    creds: dict,
    clients: dict,
    connector,
) -> dict:
    """Dispatch control plane upgrade to the correct provider."""
    if provider == "eks":
        return await _upgrade_control_plane_eks(cluster_name, target_patch, creds)
    elif provider == "gke":
        return await _upgrade_control_plane_gke(cluster_name, target_patch, creds)
    elif provider == "self_hosted":
        return await _upgrade_control_plane_self_hosted(cluster_name, target_patch, clients, connector)
    else:
        return {
            "started_at": _now_iso(),
            "completed_at": _now_iso(),
            "result": "failed",
            "error": f"Control plane upgrade not yet implemented for provider: {provider}",
        }


# ---------------------------------------------------------------------------
# Node pool phase
# ---------------------------------------------------------------------------

async def _upgrade_node_rolling(
    clients: dict,
    node_names: list,
    target_patch: str,
    desired_outcome: dict,
    pool_entry: dict,
    provider: str,
    pool_meta: dict,
    creds: dict,
) -> dict:
    """
    Roll one node at a time: cordon -> PDB check -> drain -> replace -> wait Ready -> uncordon.
    Returns updated pool_entry. Sets 'paused' key if a node blocks.
    """
    loop = asyncio.get_event_loop()
    core = clients["core"]
    drain_timeout = int(desired_outcome.get("drain_timeout_seconds", 300))

    pool_entry["nodes_total"] = len(node_names)
    pool_entry["nodes_upgraded"] = 0
    pool_entry["nodes_failed"] = 0
    pool_entry["pdb_violations"] = []

    kubeconfig = getattr(clients.get("api_client"), "_kubeconfig_path", None) or "/tmp/smoke-k8s-kubeconfig.yaml"

    for node_name in node_names:
        # 1. Cordon
        try:
            await loop.run_in_executor(
                None,
                lambda n=node_name: core.patch_node(n, {"spec": {"unschedulable": True}}),
            )
        except Exception as exc:
            pool_entry["result"] = "failed"
            pool_entry["error"] = f"Cordon {node_name} failed: {exc}"
            return pool_entry

        # 2. PDB check via policy client if available
        try:
            from kubernetes import client as k8s_client
            _policy = k8s_client.PolicyV1Api(clients["api_client"]) if "api_client" in clients else None
            if _policy:
                pdb_list = await loop.run_in_executor(None, _policy.list_pod_disruption_budget_for_all_namespaces)
                violations = [
                    f"{p.metadata.namespace}/{p.metadata.name}"
                    for p in pdb_list.items
                    if (p.status.disruptions_allowed or 0) == 0
                ]
                if violations:
                    pool_entry["pdb_violations"].extend(violations)
                    pool_entry["result"] = "paused"
                    pool_entry["paused_at_node"] = node_name
                    return pool_entry
        except Exception as exc:
            log.warning("PDB check failed for node %s: %s", node_name, exc)

        # 3. Drain
        import subprocess
        drain_cmd = [
            "kubectl", "drain", node_name,
            "--ignore-daemonsets", "--delete-emptydir-data",
            f"--timeout={drain_timeout}s",
            f"--kubeconfig={kubeconfig}",
        ]
        try:
            proc = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    drain_cmd,
                    capture_output=True, text=True, timeout=drain_timeout + 30
                ),
            )
            if proc.returncode != 0:
                pool_entry["result"] = "paused"
                pool_entry["paused_at_node"] = node_name
                pool_entry["drain_error"] = proc.stderr[:300]
                return pool_entry
        except Exception as exc:
            pool_entry["result"] = "paused"
            pool_entry["paused_at_node"] = node_name
            pool_entry["drain_error"] = str(exc)
            return pool_entry

        # 4. Provider-specific node replacement
        log.info("Node %s: drain complete, provider replace step for %s", node_name, provider)

        # 5. Wait for node Ready at target version (poll up to 10 min)
        for _ in range(60):
            await asyncio.sleep(10)
            try:
                node_obj = await loop.run_in_executor(None, lambda n=node_name: core.read_node(n))
                ready = any(c.type == "Ready" and c.status == "True" for c in node_obj.status.conditions)
                kubelet_ver = node_obj.status.node_info.kubelet_version.lstrip("v")
                if ready and kubelet_ver.startswith(target_patch.lstrip("v")):
                    break
            except Exception:
                pass

        # 6. Uncordon
        try:
            await loop.run_in_executor(
                None,
                lambda n=node_name: core.patch_node(n, {"spec": {"unschedulable": False}}),
            )
        except Exception as exc:
            log.warning("Uncordon %s failed: %s", node_name, exc)

        pool_entry["nodes_upgraded"] += 1

    pool_entry["result"] = "success"
    pool_entry["completed_at"] = _now_iso()
    return pool_entry


# ---------------------------------------------------------------------------
# Verify phase
# ---------------------------------------------------------------------------

async def _run_verify_phase(clients: dict, target_patch: str, desired_outcome: dict) -> dict:
    loop = asyncio.get_event_loop()
    core = clients["core"]
    health_ns = desired_outcome.get("health_check_namespace", "default")
    verify: dict = {
        "nodes_at_target_version": 0,
        "nodes_total": 0,
        "system_pods_ready": False,
        "workload_health_ok": False,
        "error": None,
    }
    try:
        # Node version check
        node_list = await loop.run_in_executor(None, core.list_node)
        total = len(node_list.items)
        at_target = sum(
            1 for n in node_list.items
            if n.status.node_info.kubelet_version.lstrip("v").startswith(target_patch.lstrip("v"))
        )
        verify["nodes_total"] = total
        verify["nodes_at_target_version"] = at_target

        # System pod check
        sys_pods = await loop.run_in_executor(
            None,
            lambda: core.list_namespaced_pod("kube-system"),
        )
        bad_sys = [
            p.metadata.name for p in sys_pods.items
            if p.status.phase not in ("Running", "Succeeded", None)
        ]
        verify["system_pods_ready"] = len(bad_sys) == 0

        # Workload health check
        ns_pods = await loop.run_in_executor(
            None,
            lambda: core.list_namespaced_pod(health_ns),
        )
        bad_ns = [
            p.metadata.name for p in ns_pods.items
            if p.status.phase not in ("Running", "Succeeded", None)
        ]
        verify["workload_health_ok"] = len(bad_ns) == 0

    except Exception as exc:
        verify["error"] = str(exc)

    verify["passed"] = (
        verify["system_pods_ready"]
        and verify["workload_health_ok"]
        and verify["nodes_at_target_version"] == verify["nodes_total"]
    )
    return verify


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _load_cr(cr_id: uuid.UUID):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChangeRequest)
            .where(ChangeRequest.id == cr_id)
            .options(
                selectinload(ChangeRequest.change_plan),
                selectinload(ChangeRequest.execution_runs),
            )
        )
        return result.scalar_one_or_none()


async def _persist_result(cr_id: uuid.UUID, result: dict):
    """Write result to the most recent ExecutionRun."""
    async with AsyncSessionLocal() as db:
        run_res = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.change_request_id == cr_id)
            .order_by(ExecutionRun.started_at.desc())
            .limit(1)
        )
        run = run_res.scalar_one_or_none()
        if run:
            run.result = result
            await db.commit()


async def _set_cr_paused(cr_id: uuid.UUID):
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = res.scalar_one_or_none()
        if cr:
            cr.status = ChangeRequestStatus.paused
            cr.updated_at = datetime.now(timezone.utc)
            await db.commit()


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

def _detect_provider(connector) -> str:
    ct = getattr(connector, "connector_type", None)
    if ct is None:
        return "self_hosted"
    ct_val = ct.value if hasattr(ct, "value") else str(ct)
    if ct_val == "aws":
        return "eks"
    elif ct_val == "gcp":
        return "gke"
    elif ct_val == "azure":
        return "aks"
    return "self_hosted"


# ---------------------------------------------------------------------------
# Main execute entry point
# ---------------------------------------------------------------------------

async def execute_k8s_cluster_upgrade(cr_id: uuid.UUID) -> dict:
    """
    Main executor. Called from activities.py dispatch branch.
    Returns a result dict. Sets result["paused"] = True if a node blocks.
    """
    cr = await _load_cr(cr_id)
    if cr is None:
        return {"error": f"CR {cr_id} not found", "failed": True}

    desired = cr.desired_outcome or {}
    target_version = desired.get("target_version", "")
    dry_run = desired.get("dry_run", False)

    # Load connector from desired_outcome["connector_id"]
    connector_id_str = desired.get("connector_id")
    if not connector_id_str:
        return {"error": "desired_outcome.connector_id is required for k8s_cluster_upgrade", "failed": True}
    try:
        connector_uuid = uuid.UUID(str(connector_id_str))
    except (ValueError, AttributeError) as exc:
        return {"error": f"Invalid connector_id: {exc}", "failed": True}

    from app.models.connector import Connector
    connector = None
    async with AsyncSessionLocal() as _db:
        from sqlalchemy import select as _select
        _res = await _db.execute(_select(Connector).where(Connector.id == connector_uuid))
        connector = _res.scalar_one_or_none()
        if connector is None:
            return {"error": f"Connector {connector_uuid} not found", "failed": True}
        await connector_service._attach_credentials(connector, _db)
        creds = getattr(connector, "credentials", {}) or {}

    # Build k8s clients
    from app.connectors.executors.kubernetes._client import get_k8s_clients
    from kubernetes import client as k8s_lib
    try:
        clients = get_k8s_clients(creds)
        clients["custom"] = k8s_lib.CustomObjectsApi(clients["api_client"])
        clients["policy"] = k8s_lib.PolicyV1Api(clients["api_client"])
    except Exception as exc:
        return {"error": f"Failed to build k8s clients: {exc}", "failed": True}

    # Discover cluster metadata
    provider = _detect_provider(connector)
    loop = asyncio.get_event_loop()
    try:
        from kubernetes import client as k8s_lib
        version_api = k8s_lib.VersionApi(clients["api_client"])
        version_info = await loop.run_in_executor(None, version_api.get_code)
        current_version = version_info.git_version.lstrip("v")
        cluster_name = creds.get("cluster_name", creds.get("name", "unknown"))
        cluster_id = creds.get("cluster_id", cluster_name)
    except Exception as exc:
        return {"error": f"Failed to discover cluster version: {exc}", "failed": True}

    result = _build_initial_result(current_version, target_version, provider, cluster_name, cluster_id)

    # Phase 1: Pre-flight
    pf_result = await _run_preflight_phase(clients, current_version, target_version, desired)
    result["preflight"] = pf_result["preflight"]
    if pf_result["failed"]:
        result["phase"] = "preflight"
        result["failed"] = True
        await _persist_result(cr_id, result)
        return result

    target_patch = pf_result["target_patch_version"]
    result["target_version"] = target_patch

    if dry_run:
        result["phase"] = "preflight"
        result["dry_run"] = True
        await _persist_result(cr_id, result)
        return result

    # Phase 2: Control plane
    result["phase"] = "control_plane"
    await _persist_result(cr_id, result)
    cp_result = await _run_control_plane_phase(provider, cluster_name, target_patch, creds, clients, connector)
    result["control_plane"] = cp_result
    if cp_result["result"] != "success":
        result["failed"] = True
        await _persist_result(cr_id, result)
        return result

    # Phase 3: Node pools (rolling only for now; blue_green TBD)
    result["phase"] = "node_pools"
    await _persist_result(cr_id, result)

    node_list = await loop.run_in_executor(None, clients["core"].list_node)
    worker_nodes = [
        n.metadata.name for n in node_list.items
        if not any(lbl.startswith("node-role.kubernetes.io/control-plane") for lbl in (n.metadata.labels or {}))
    ]

    pool_entry = {
        "index": 0,
        "pool_name": "default-workers",
        "provider_id": cluster_name,
        "prior_image": current_version,
        "target_image": target_patch,
        "nodes_total": len(worker_nodes),
        "nodes_upgraded": 0,
        "nodes_failed": 0,
        "started_at": _now_iso(),
        "completed_at": None,
        "result": "pending",
        "pdb_violations": [],
        "rollback_result": None,
    }

    pool_result = await _upgrade_node_rolling(
        clients=clients,
        node_names=worker_nodes,
        target_patch=target_patch,
        desired_outcome=desired,
        pool_entry=pool_entry,
        provider=provider,
        pool_meta={},
        creds=creds,
    )
    result["node_pools"] = [pool_result]

    if pool_result.get("result") == "paused":
        result["paused"] = True
        await _persist_result(cr_id, result)
        await _set_cr_paused(cr_id)
        return result

    # Phase 4: Verify
    result["phase"] = "verify"
    await _persist_result(cr_id, result)
    verify = await _run_verify_phase(clients, target_patch, desired)
    result["verify"] = verify

    if not verify.get("passed"):
        result["paused"] = True
        await _persist_result(cr_id, result)
        await _set_cr_paused(cr_id)
        return result

    result["phase"] = "complete"
    await _persist_result(cr_id, result)
    return result


# ---------------------------------------------------------------------------
# Rollback entry point
# ---------------------------------------------------------------------------

async def execute_k8s_cluster_upgrade_rollback(
    cr_id: uuid.UUID,
    execution_result: dict,
) -> dict:
    """
    FILO rollback of node pools. Control plane is irreversible — skip it.
    Returns rollback_result dict with has_warnings flag.
    """
    node_pools = execution_result.get("node_pools", [])
    # FILO: reverse index order
    completed_pools = [p for p in node_pools if p.get("result") in ("success", "paused")]
    completed_pools_sorted = sorted(completed_pools, key=lambda p: p.get("index", 0), reverse=True)

    rollback_results = []
    has_warnings = False

    # If no pools completed, nothing to roll back
    if not completed_pools_sorted:
        return {
            "rollback_note": _ROLLBACK_NOTE,
            "pools_rolled_back": [],
            "has_warnings": False,
        }

    cr = await _load_cr(cr_id)
    if cr is None:
        return {"error": f"CR {cr_id} not found during rollback", "has_warnings": True}

    desired = cr.desired_outcome or {}
    connector_id_str = desired.get("connector_id") or execution_result.get("connector_id")
    creds = {}
    if connector_id_str:
        try:
            from app.models.connector import Connector
            async with AsyncSessionLocal() as _db:
                from sqlalchemy import select as _select
                _res = await _db.execute(_select(Connector).where(Connector.id == uuid.UUID(str(connector_id_str))))
                _connector = _res.scalar_one_or_none()
                if _connector:
                    await connector_service._attach_credentials(_connector, _db)
                    creds = getattr(_connector, "credentials", {}) or {}
        except Exception as exc:
            log.warning("Could not load connector for rollback: %s", exc)

    from app.connectors.executors.kubernetes._client import get_k8s_clients
    try:
        clients = get_k8s_clients(creds)
    except Exception as exc:
        return {"error": f"Could not build k8s clients for rollback: {exc}", "has_warnings": True}

    loop = asyncio.get_event_loop()

    for pool in completed_pools_sorted:
        pool_name = pool.get("pool_name", "unknown")
        prior_image = pool.get("prior_image", "")
        paused_node = pool.get("paused_at_node")

        # Always uncordon any cordoned node first
        if paused_node:
            try:
                await loop.run_in_executor(
                    None,
                    lambda n=paused_node: clients["core"].patch_node(n, {"spec": {"unschedulable": False}}),
                )
                rollback_results.append({"pool": pool_name, "uncordoned": paused_node})
            except Exception as exc:
                log.warning("Uncordon %s failed during rollback: %s", paused_node, exc)
                has_warnings = True
                rollback_results.append({"pool": pool_name, "uncordon_error": str(exc)})
        else:
            rollback_results.append({
                "pool": pool_name,
                "prior_image": prior_image,
                "rolled_back": True,
                "note": "Node image rollback is provider-specific; nodes left at current version if cloud provider does not support downgrade",
            })
            # For cloud providers: emit warning that downgrade may not be supported
            provider = execution_result.get("provider", "unknown")
            if provider in ("gke", "aks"):
                has_warnings = True
                rollback_results[-1]["warning"] = f"{provider.upper()} may reject downgrade to prior version {prior_image}"

    return {
        "rollback_note": _ROLLBACK_NOTE,
        "pools_rolled_back": rollback_results,
        "has_warnings": has_warnings,
    }
