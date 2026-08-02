# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Azure account baseline monitoring executor.

Enables Microsoft Defender for Cloud (all resource types), Defender for DNS,
Activity Log diagnostic settings (Log Analytics workspace), and Entra ID
security defaults (skipped if Conditional Access policies exist).
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

DEFENDER_RESOURCE_TYPES = [
    "VirtualMachines", "SqlServers", "AppServices", "StorageAccounts",
    "KubernetesService", "ContainerRegistry", "KeyVaults", "Arm", "Dns",
]


def _run(fn):
    return asyncio.get_running_loop().run_in_executor(None, fn)


# ── Phase 1: Preflight ────────────────────────────────────────────────────────

async def _preflight(creds: dict) -> dict:
    try:
        def _do():
            from ._client import get_credential
            get_credential(creds)  # validates credentials
            sub_id = creds["subscription_id"]
            tenant_id = creds["tenant_id"]
            return sub_id, tenant_id
        sub_id, tenant_id = await _run(_do)
        return {"phase": "preflight", "status": "ok", "subscription_id": sub_id,
                "tenant_id": tenant_id}
    except Exception as e:
        return {"phase": "preflight", "status": "failed", "error": str(e)}


# ── Phase 2: Snapshot ─────────────────────────────────────────────────────────

async def _snapshot(creds: dict, sub_id: str, tenant_id: str) -> dict:
    pre = {}

    def _do():
        from ._client import get_credential
        credential = get_credential(creds)
        from azure.mgmt.security import SecurityCenter
        security = SecurityCenter(credential, sub_id)

        defender_tiers = {}
        for rt in DEFENDER_RESOURCE_TYPES:
            try:
                pricing = security.pricings.get(pricing_name=rt)
                defender_tiers[rt] = pricing.pricing_tier
            except Exception:
                defender_tiers[rt] = "Free"
        pre["defender_tiers"] = defender_tiers

        from azure.mgmt.monitor import MonitorManagementClient
        monitor = MonitorManagementClient(credential, sub_id)
        resource_uri = f"/subscriptions/{sub_id}"
        diag_settings = list(monitor.diagnostic_settings.list(resource_uri=resource_uri))
        pre["diagnostic_settings"] = [d.name for d in diag_settings]

        import requests
        token = credential.get_token("https://graph.microsoft.com/.default").token
        r = requests.get(
            "https://graph.microsoft.com/v1.0/policies/identitySecurityDefaultsEnforcementPolicy",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        pre["security_defaults_enabled"] = r.json().get("isEnabled", False) if r.ok else None

        r = requests.get(
            "https://graph.microsoft.com/v1.0/identity/conditionalAccess/policies",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        pre["has_ca_policies"] = bool(r.json().get("value")) if r.ok else False

    await _run(_do)
    return {"phase": "snapshot", "status": "ok", "pre_existing_states": pre}


# ── Phase 3: Enable ───────────────────────────────────────────────────────────

async def _enable(creds: dict, sub_id: str, tenant_id: str, pre: dict, rollback_data: dict) -> dict:
    results = []

    def _do():
        from ._client import get_credential
        credential = get_credential(creds)
        from azure.mgmt.security import SecurityCenter
        from azure.mgmt.security.models import Pricing
        security = SecurityCenter(credential, sub_id)

        upgraded = []
        already_enabled = []
        skipped_with_warning = []
        for rt in DEFENDER_RESOURCE_TYPES:
            try:
                existing = security.pricings.get(pricing_name=rt)
                if existing.pricing_tier == "Standard":
                    already_enabled.append(rt)
                else:
                    security.pricings.update(pricing_name=rt, pricing=Pricing(pricing_tier="Standard"))
                    upgraded.append(rt)
            except Exception as e:
                skipped_with_warning.append({"service": f"defender:{rt}", "reason": str(e)[:100]})
        return upgraded, already_enabled, skipped_with_warning

    upgraded, already_enabled_types, defender_warnings = await _run(_do)
    if upgraded:
        rollback_data["newly_enabled"].append({"service": "defender", "upgraded_types": upgraded})
    if defender_warnings:
        rollback_data.setdefault("skipped_with_warning", []).extend(defender_warnings)
    results.append({
        "service": "defender",
        "action": "enabled" if upgraded else "skipped",
        "types": upgraded,
        "already_enabled": already_enabled_types,
        "skipped_with_warning": defender_warnings,
    })

    if "nexplane-baseline" not in pre.get("diagnostic_settings", []):
        def _diag():
            from ._client import get_credential
            credential = get_credential(creds)
            from azure.mgmt.loganalytics import LogAnalyticsManagementClient
            from azure.mgmt.loganalytics.models import Workspace
            from azure.mgmt.monitor import MonitorManagementClient
            from azure.mgmt.monitor.models import DiagnosticSettingsResource, LogSettings

            la = LogAnalyticsManagementClient(credential, sub_id)
            workspace_name = f"nexplane-logs-{sub_id}"
            rg_name = "nexplane-monitoring"
            from azure.mgmt.resource import ResourceManagementClient
            rm = ResourceManagementClient(credential, sub_id)
            rm.resource_groups.create_or_update(rg_name, {"location": "eastus"})
            workspace = la.workspaces.begin_create_or_update(
                rg_name, workspace_name,
                Workspace(location="eastus", sku={"name": "PerGB2018"}, retention_in_days=90),
            ).result()
            workspace_id = workspace.id

            monitor = MonitorManagementClient(credential, sub_id)
            categories = ["Administrative", "Security", "ServiceHealth", "Alert", "Recommendation",
                          "Policy", "Autoscale", "ResourceHealth"]
            monitor.diagnostic_settings.create_or_update(
                resource_uri=f"/subscriptions/{sub_id}",
                name="nexplane-baseline",
                parameters=DiagnosticSettingsResource(
                    workspace_id=workspace_id,
                    logs=[LogSettings(category=cat, enabled=True) for cat in categories],
                ),
            )
            return workspace_id

        workspace_id = await _run(_diag)
        rollback_data["newly_enabled"].append({"service": "diagnostic_settings", "workspace_id": workspace_id,
                                                "rg": "nexplane-monitoring", "workspace_name": f"nexplane-logs-{sub_id}"})
        results.append({"service": "diagnostic_settings", "action": "enabled"})
    else:
        results.append({"service": "diagnostic_settings", "action": "skipped"})

    if pre.get("has_ca_policies"):
        results.append({"service": "security_defaults", "action": "skipped",
                        "reason": "Conditional Access policies already in place"})
        rollback_data.setdefault("skipped_with_warning", []).append({
            "service": "security_defaults",
            "reason": "Conditional Access policies detected. Security defaults would conflict. Operators with CA policies already have equivalent controls.",
        })
    elif pre.get("security_defaults_enabled"):
        results.append({"service": "security_defaults", "action": "skipped"})
    else:
        def _sd():
            from ._client import get_credential
            credential = get_credential(creds)
            import requests
            token = credential.get_token("https://graph.microsoft.com/.default").token
            r = requests.patch(
                "https://graph.microsoft.com/v1.0/policies/identitySecurityDefaultsEnforcementPolicy",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"isEnabled": True},
                timeout=30,
            )
            r.raise_for_status()
        await _run(_sd)
        rollback_data["newly_enabled"].append({"service": "security_defaults"})
        results.append({"service": "security_defaults", "action": "enabled"})

    return {"phase": "enable", "status": "ok", "results": results}


# ── Phase 4: Verify ───────────────────────────────────────────────────────────

async def _verify(creds: dict, sub_id: str, newly_enabled: list) -> dict:
    checks = []

    for item in newly_enabled:
        svc = item["service"]
        try:
            if svc == "defender":
                def _v(types=item["upgraded_types"]):
                    from ._client import get_credential
                    credential = get_credential(creds)
                    from azure.mgmt.security import SecurityCenter
                    security = SecurityCenter(credential, sub_id)
                    return all(
                        security.pricings.get(pricing_name=rt).pricing_tier == "Standard"
                        for rt in types
                    )
                ok = await _run(_v)
                checks.append({"service": "defender", "ok": bool(ok)})

            elif svc == "diagnostic_settings":
                def _v():
                    from ._client import get_credential
                    credential = get_credential(creds)
                    from azure.mgmt.monitor import MonitorManagementClient
                    monitor = MonitorManagementClient(credential, sub_id)
                    settings = list(monitor.diagnostic_settings.list(resource_uri=f"/subscriptions/{sub_id}"))
                    return any(s.name == "nexplane-baseline" for s in settings)
                ok = await _run(_v)
                checks.append({"service": "diagnostic_settings", "ok": bool(ok)})

            elif svc == "security_defaults":
                def _v():
                    from ._client import get_credential
                    credential = get_credential(creds)
                    import requests
                    token = credential.get_token("https://graph.microsoft.com/.default").token
                    r = requests.get(
                        "https://graph.microsoft.com/v1.0/policies/identitySecurityDefaultsEnforcementPolicy",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=30,
                    )
                    return r.ok and r.json().get("isEnabled") is True
                ok = await _run(_v)
                checks.append({"service": "security_defaults", "ok": bool(ok)})

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
                "newly_enabled": ["defender", "diagnostic_settings", "security_defaults"],
                "failed": [],
                "skipped_with_warning": [],
            },
            "promote_to": "azure_account_baseline_hardening",
            "rollback_data": {"newly_enabled": [], "pre_existing_states": {}},
        }

    phases = []
    rollback_data = {"newly_enabled": [], "pre_existing_states": {}}

    phase1 = await _preflight(creds)
    phases.append(phase1)
    if phase1["status"] == "failed":
        return {"phases": phases, "failed": True, "rollback_data": rollback_data}

    sub_id = phase1["subscription_id"]
    tenant_id = phase1["tenant_id"]

    phase2 = await _snapshot(creds, sub_id, tenant_id)
    phases.append(phase2)
    pre = phase2["pre_existing_states"]
    rollback_data["pre_existing_states"] = pre

    phase3 = await _enable(creds, sub_id, tenant_id, pre, rollback_data)
    phases.append(phase3)

    phase4 = await _verify(creds, sub_id, rollback_data["newly_enabled"])
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
        "promote_to": "azure_account_baseline_hardening",
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

    from ._client import get_credential
    credential = get_credential(creds)
    sub_id = creds["subscription_id"]

    for item in reversed(newly_enabled):
        svc = item["service"]
        try:
            if svc == "defender":
                def _undo(types=item["upgraded_types"], prev_tiers=pre.get("defender_tiers", {})):
                    from azure.mgmt.security import SecurityCenter
                    from azure.mgmt.security.models import Pricing
                    security = SecurityCenter(credential, sub_id)
                    for rt in types:
                        prev = prev_tiers.get(rt, "Free")
                        security.pricings.update(pricing_name=rt, pricing=Pricing(pricing_tier=prev))
                await _run(_undo)

            elif svc == "diagnostic_settings":
                def _undo(rg=item["rg"], ws=item["workspace_name"]):
                    from azure.mgmt.monitor import MonitorManagementClient
                    from azure.mgmt.loganalytics import LogAnalyticsManagementClient
                    monitor = MonitorManagementClient(credential, sub_id)
                    monitor.diagnostic_settings.delete(resource_uri=f"/subscriptions/{sub_id}", name="nexplane-baseline")
                    la = LogAnalyticsManagementClient(credential, sub_id)
                    la.workspaces.begin_delete(rg, ws).result()
                await _run(_undo)

            elif svc == "security_defaults":
                def _undo():
                    import requests
                    token = credential.get_token("https://graph.microsoft.com/.default").token
                    r = requests.patch(
                        "https://graph.microsoft.com/v1.0/policies/identitySecurityDefaultsEnforcementPolicy",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        json={"isEnabled": False},
                        timeout=30,
                    )
                    r.raise_for_status()
                await _run(_undo)

            undone.append({"service": svc, "rolled_back": True})
        except Exception as e:
            logger.error("Azure rollback failed for %s: %s", svc, e)
            undone.append({"service": svc, "rolled_back": False, "error": str(e)})

    all_ok = all(u["rolled_back"] for u in undone)
    return {"rolled_back": all_ok, "undone": undone}
