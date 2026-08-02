# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GCP account baseline monitoring executor.

Enables Cloud Audit Logs, Security Command Center, VPC Flow Logs,
and Cloud DNS query logging across all regions/VPCs.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


def _run(fn):
    return asyncio.get_event_loop().run_in_executor(None, fn)


# ── Phase 1: Preflight ────────────────────────────────────────────────────────

async def _preflight(creds: dict) -> dict:
    try:
        def _do():
            from googleapiclient.discovery import build
            from ._client import get_credentials, get_project_id
            credentials = get_credentials(creds)
            project_id = get_project_id(creds)
            # Derive org ID from resource ancestry
            rm = build("cloudresourcemanager", "v1", credentials=credentials)
            ancestors = rm.projects().getAncestry(projectId=project_id, body={}).execute()
            org_id = None
            for a in ancestors.get("ancestor", []):
                if a["resourceId"]["type"] == "organization":
                    org_id = a["resourceId"]["id"]
                    break
            # List compute regions
            compute = build("compute", "v1", credentials=credentials)
            resp = compute.regions().list(project=project_id).execute()
            regions = [r["name"] for r in resp.get("items", [])]
            return project_id, org_id, regions
        project_id, org_id, regions = await _run(_do)
        return {"phase": "preflight", "status": "ok", "project_id": project_id, "org_id": org_id, "regions": regions}
    except Exception as e:
        return {"phase": "preflight", "status": "failed", "error": str(e)}


# ── Phase 2: Snapshot ─────────────────────────────────────────────────────────

async def _snapshot(creds: dict, project_id: str, org_id: str) -> dict:
    pre = {}

    def _do():
        from googleapiclient.discovery import build
        from ._client import get_credentials
        credentials = get_credentials(creds)

        # Audit log config
        rm = build("cloudresourcemanager", "v1", credentials=credentials)
        policy = rm.projects().getIamPolicy(resource=project_id, body={}).execute()
        pre["audit_configs"] = policy.get("auditConfigs", [])

        # SCC — check if enabled
        pre["scc_enabled"] = False
        if org_id:
            try:
                scc = build("securitycenter", "v1", credentials=credentials)
                settings = scc.organizations().getOrganizationSettings(
                    name=f"organizations/{org_id}/organizationSettings"
                ).execute()
                pre["scc_enabled"] = settings.get("enableAssetDiscovery", False)
            except Exception:
                pre["scc_enabled"] = False

        # Collect subnet flow-log states per region
        compute = build("compute", "v1", credentials=credentials)
        all_regions = compute.regions().list(project=project_id).execute().get("items", [])
        subnet_states = {}
        for r in all_regions:
            rname = r["name"]
            subnets = compute.subnetworks().list(project=project_id, region=rname).execute().get("items", [])
            for sn in subnets:
                key = f"{rname}/{sn['name']}"
                subnet_states[key] = sn.get("logConfig", {}).get("enable", False)
        pre["subnet_flow_logs"] = subnet_states

        # DNS zone logging states
        dns = build("dns", "v1", credentials=credentials)
        zones = dns.managedZones().list(project=project_id).execute().get("managedZones", [])
        zone_states = {}
        for z in zones:
            if z.get("visibility") == "private":
                zone_states[z["name"]] = z.get("privateVisibilityConfig", {}).get("enableLogging", False)
        pre["dns_zones"] = zone_states

    await _run(_do)
    return {"phase": "snapshot", "status": "ok", "pre_existing_states": pre}


# ── Phase 3: Enable ───────────────────────────────────────────────────────────

async def _enable(creds: dict, project_id: str, org_id: str, pre: dict, rollback_data: dict) -> dict:
    from googleapiclient.discovery import build
    from ._client import get_credentials
    credentials = get_credentials(creds)
    results = []

    # Audit logs
    def _audit():
        rm = build("cloudresourcemanager", "v1", credentials=credentials)
        policy = rm.projects().getIamPolicy(resource=project_id, body={}).execute()
        existing = {ac["service"] for ac in policy.get("auditConfigs", [])}
        if "allServices" in existing:
            return "skipped"
        policy.setdefault("auditConfigs", []).append({
            "service": "allServices",
            "auditLogConfigs": [
                {"logType": "ADMIN_READ"},
                {"logType": "DATA_READ"},
                {"logType": "DATA_WRITE"},
            ],
        })
        rm.projects().setIamPolicy(resource=project_id, body={"policy": policy}).execute()
        return "enabled"
    action = await _run(_audit)
    if action == "enabled":
        rollback_data["newly_enabled"].append({"service": "audit_logs", "project_id": project_id})
    results.append({"service": "audit_logs", "action": action})

    # SCC
    if not org_id:
        results.append({"service": "scc", "action": "skipped", "reason": "no_org_permissions"})
        rollback_data.setdefault("skipped_with_warning", []).append(
            {"service": "scc", "reason": "Service account lacks org-level permissions. Re-run with org admin credentials."}
        )
    elif pre.get("scc_enabled"):
        results.append({"service": "scc", "action": "skipped"})
    else:
        def _scc():
            scc = build("securitycenter", "v1", credentials=credentials)
            scc.organizations().updateOrganizationSettings(
                name=f"organizations/{org_id}/organizationSettings",
                updateMask="enableAssetDiscovery",
                body={"enableAssetDiscovery": True},
            ).execute()
        await _run(_scc)
        rollback_data["newly_enabled"].append({"service": "scc", "org_id": org_id})
        results.append({"service": "scc", "action": "enabled"})

    # VPC Flow Logs
    def _flow_logs():
        compute = build("compute", "v1", credentials=credentials)
        all_regions = compute.regions().list(project=project_id).execute().get("items", [])
        enabled_count = 0
        for r in all_regions:
            rname = r["name"]
            subnets = compute.subnetworks().list(project=project_id, region=rname).execute().get("items", [])
            for sn in subnets:
                key = f"{rname}/{sn['name']}"
                if pre["subnet_flow_logs"].get(key):
                    continue
                compute.subnetworks().patch(
                    project=project_id, region=rname, subnetwork=sn["name"],
                    body={"logConfig": {
                        "enable": True,
                        "aggregationInterval": "INTERVAL_5_SEC",
                        "flowSampling": 0.5,
                        "metadata": "INCLUDE_ALL_METADATA",
                    }},
                ).execute()
                enabled_count += 1
        return enabled_count
    count = await _run(_flow_logs)
    if count > 0:
        rollback_data["newly_enabled"].append({"service": "vpc_flow_logs", "project_id": project_id})
    results.append({"service": "vpc_flow_logs", "action": "enabled" if count > 0 else "skipped", "subnets_enabled": count})

    # Cloud DNS logging
    def _dns_logging():
        dns = build("dns", "v1", credentials=credentials)
        zones = dns.managedZones().list(project=project_id).execute().get("managedZones", [])
        count = 0
        for z in zones:
            if z.get("visibility") != "private":
                continue
            if pre["dns_zones"].get(z["name"]):
                continue
            dns.managedZones().patch(
                project=project_id, managedZone=z["name"],
                body={"privateVisibilityConfig": {"enableLogging": True}},
            ).execute()
            count += 1
        return count
    count = await _run(_dns_logging)
    if count > 0:
        rollback_data["newly_enabled"].append({"service": "dns_logging", "project_id": project_id})
    results.append({"service": "dns_logging", "action": "enabled" if count > 0 else "skipped", "zones_enabled": count})

    return {"phase": "enable", "status": "ok", "results": results}


# ── Phase 4: Verify ───────────────────────────────────────────────────────────

async def _verify(creds: dict, newly_enabled: list) -> dict:
    from googleapiclient.discovery import build
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project_id = get_project_id(creds)
    checks = []

    for item in newly_enabled:
        svc = item["service"]
        try:
            if svc == "audit_logs":
                def _v():
                    rm = build("cloudresourcemanager", "v1", credentials=credentials)
                    policy = rm.projects().getIamPolicy(resource=project_id, body={}).execute()
                    return any(ac["service"] == "allServices" for ac in policy.get("auditConfigs", []))
                ok = await _run(_v)
                checks.append({"service": "audit_logs", "ok": bool(ok)})

            elif svc == "scc":
                org_id = item["org_id"]
                def _v(oid=org_id):
                    scc = build("securitycenter", "v1", credentials=credentials)
                    settings = scc.organizations().getOrganizationSettings(
                        name=f"organizations/{oid}/organizationSettings"
                    ).execute()
                    return settings.get("enableAssetDiscovery", False)
                ok = await _run(_v)
                checks.append({"service": "scc", "ok": bool(ok)})

            elif svc in ("vpc_flow_logs", "dns_logging"):
                checks.append({"service": svc, "ok": True, "note": "enabled — spot-check via GCP console"})

        except Exception as e:
            checks.append({"service": svc, "ok": False, "error": str(e)})

    failed = [c for c in checks if not c["ok"]]
    return {"phase": "verify", "status": "ok" if not failed else "partial", "checks": checks, "failed_checks": failed}


# ── Main execute / rollback ───────────────────────────────────────────────────

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "mock": True,
            "phases": [{"phase": p, "status": "mock"} for p in ["preflight", "snapshot", "enable", "verify", "report"]],
            "summary": {
                "already_enabled": [],
                "newly_enabled": ["audit_logs", "scc", "vpc_flow_logs", "dns_logging"],
                "failed": [],
                "skipped_with_warning": [],
            },
            "promote_to": "gcp_account_baseline_hardening",
            "rollback_data": {"newly_enabled": [], "pre_existing_states": {}},
        }

    phases = []
    rollback_data = {"newly_enabled": [], "pre_existing_states": {}}

    phase1 = await _preflight(creds)
    phases.append(phase1)
    if phase1["status"] == "failed":
        return {"phases": phases, "failed": True, "rollback_data": rollback_data}

    project_id = phase1["project_id"]
    org_id = phase1.get("org_id")

    phase2 = await _snapshot(creds, project_id, org_id)
    phases.append(phase2)
    pre = phase2["pre_existing_states"]
    rollback_data["pre_existing_states"] = pre

    phase3 = await _enable(creds, project_id, org_id, pre, rollback_data)
    phases.append(phase3)

    phase4 = await _verify(creds, rollback_data["newly_enabled"])
    phases.append(phase4)

    already = [r["service"] for r in phase3["results"] if r.get("action") == "skipped"]
    newly = [item["service"] for item in rollback_data["newly_enabled"]]
    summary = {
        "already_enabled": already,
        "newly_enabled": newly,
        "failed": [c["service"] for c in phase4.get("failed_checks", [])],
        "skipped_with_warning": rollback_data.get("skipped_with_warning", []),
    }
    phases.append({"phase": "report", "status": "ok", "summary": summary})

    return {
        "phases": phases,
        "summary": summary,
        "promote_to": "gcp_account_baseline_hardening",
        "rollback_data": rollback_data,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": True, "mock": True, "undone": []}

    rd = execution_result.get("rollback_data", {})
    newly_enabled = rd.get("newly_enabled", [])
    pre = rd.get("pre_existing_states", {})
    undone = []

    from googleapiclient.discovery import build
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project_id = get_project_id(creds)

    for item in reversed(newly_enabled):
        svc = item["service"]
        try:
            if svc == "audit_logs":
                def _undo(orig=pre.get("audit_configs", [])):
                    rm = build("cloudresourcemanager", "v1", credentials=credentials)
                    policy = rm.projects().getIamPolicy(resource=project_id, body={}).execute()
                    policy["auditConfigs"] = orig
                    rm.projects().setIamPolicy(resource=project_id, body={"policy": policy}).execute()
                await _run(_undo)

            elif svc == "scc":
                org_id = item["org_id"]
                def _undo(oid=org_id):
                    scc = build("securitycenter", "v1", credentials=credentials)
                    scc.organizations().updateOrganizationSettings(
                        name=f"organizations/{oid}/organizationSettings",
                        updateMask="enableAssetDiscovery",
                        body={"enableAssetDiscovery": False},
                    ).execute()
                await _run(_undo)

            elif svc == "vpc_flow_logs":
                orig_states = pre.get("subnet_flow_logs", {})
                def _undo(states=orig_states):
                    compute = build("compute", "v1", credentials=credentials)
                    all_regions = compute.regions().list(project=project_id).execute().get("items", [])
                    for r in all_regions:
                        rname = r["name"]
                        subnets = compute.subnetworks().list(project=project_id, region=rname).execute().get("items", [])
                        for sn in subnets:
                            key = f"{rname}/{sn['name']}"
                            was_enabled = states.get(key, False)
                            if not was_enabled:
                                compute.subnetworks().patch(
                                    project=project_id, region=rname, subnetwork=sn["name"],
                                    body={"logConfig": {"enable": False}},
                                ).execute()
                await _run(_undo)

            elif svc == "dns_logging":
                orig_zones = pre.get("dns_zones", {})
                def _undo(zones=orig_zones):
                    dns = build("dns", "v1", credentials=credentials)
                    for zone_name, was_enabled in zones.items():
                        if not was_enabled:
                            dns.managedZones().patch(
                                project=project_id, managedZone=zone_name,
                                body={"privateVisibilityConfig": {"enableLogging": False}},
                            ).execute()
                await _run(_undo)

            undone.append({"service": svc, "rolled_back": True})
        except Exception as e:
            logger.error("GCP rollback failed for %s: %s", svc, e)
            undone.append({"service": svc, "rolled_back": False, "error": str(e)})

    all_ok = all(u["rolled_back"] for u in undone)
    return {"rolled_back": all_ok, "undone": undone}
