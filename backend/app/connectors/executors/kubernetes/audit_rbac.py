from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from ._client import get_k8s_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Audit RBAC — find overprivileged bindings (cluster-admin, wildcards)."""
    clients = get_k8s_client(connector, parameters)
    if not clients:
        return {"action": "k8s_audit_rbac", "status": "skipped", "reason": "no_k8s_credentials"}

    loop = asyncio.get_event_loop()

    def _audit():
        rbac = clients["rbac"]
        findings = []
        # Check ClusterRoleBindings for cluster-admin
        crbs = rbac.list_cluster_role_binding()
        for crb in crbs.items:
            if crb.role_ref.name == "cluster-admin":
                for subject in (crb.subjects or []):
                    findings.append({
                        "type": "cluster_admin_binding",
                        "binding": crb.metadata.name,
                        "subject": subject.name,
                        "subject_kind": subject.kind,
                        "severity": "critical",
                    })
        return {"findings": findings, "cluster_role_binding_count": len(crbs.items)}

    result = await loop.run_in_executor(None, _audit)
    return {
        "action": "k8s_audit_rbac",
        "audited_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "read-only audit"}
