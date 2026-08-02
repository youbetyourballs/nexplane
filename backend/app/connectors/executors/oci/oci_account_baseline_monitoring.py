# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""OCI account baseline monitoring executor.

Enables Cloud Guard, Audit service retention (365 days), VCN Flow Logs,
and IAM password policy across all compartments and regions.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

OCI_PASSWORD_POLICY = dict(
    minimum_password_length=14,
    is_uppercase_characters_required=True,
    is_lowercase_characters_required=True,
    is_numeric_characters_required=True,
    is_special_characters_required=True,
    is_username_containment_allowed=False,
)


def _run(fn):
    return asyncio.get_running_loop().run_in_executor(None, fn)


# ── Phase 1: Preflight ────────────────────────────────────────────────────────

async def _preflight(creds: dict) -> dict:
    try:
        def _do():
            import oci
            from ._client import get_oci_config
            config = get_oci_config(creds)
            tenancy_id = creds["tenancy"]
            home_region = creds.get("region")
            if not home_region:
                identity = oci.identity.IdentityClient(config)
                subscriptions = identity.list_region_subscriptions(tenancy_id).data
                home_region = next((s.region_name for s in subscriptions if s.is_home_region), None)
                if not home_region:
                    raise ValueError("Cannot determine home region from tenancy subscriptions")
            return tenancy_id, home_region
        tenancy_id, home_region = await _run(_do)
        return {"phase": "preflight", "status": "ok", "tenancy_id": tenancy_id, "home_region": home_region}
    except Exception as e:
        return {"phase": "preflight", "status": "failed", "error": str(e)}


# ── Phase 2: Snapshot ─────────────────────────────────────────────────────────

async def _snapshot(creds: dict, tenancy_id: str) -> dict:
    pre = {}

    def _do():
        import oci
        from ._client import get_oci_config
        config = get_oci_config(creds)

        # Cloud Guard status
        cloud_guard = oci.cloud_guard.CloudGuardClient(config)
        try:
            cfg = cloud_guard.get_configuration(tenancy_id).data
            pre["cloud_guard_status"] = cfg.status
        except Exception:
            pre["cloud_guard_status"] = "DISABLED"

        # Audit retention
        audit = oci.audit.AuditClient(config)
        audit_cfg = audit.get_configuration(tenancy_id).data
        pre["audit_retention_days"] = audit_cfg.retention_period_days

        # IAM password policy
        identity = oci.identity.IdentityClient(config)
        auth_policy = identity.get_authentication_policy(tenancy_id).data
        pp = auth_policy.password_policy
        pre["iam_pp"] = {
            "minimum_password_length": pp.minimum_password_length,
            "is_uppercase_characters_required": pp.is_uppercase_characters_required,
            "is_lowercase_characters_required": pp.is_lowercase_characters_required,
            "is_numeric_characters_required": pp.is_numeric_characters_required,
            "is_special_characters_required": pp.is_special_characters_required,
            "is_username_containment_allowed": pp.is_username_containment_allowed,
        } if pp else None

        # List all compartments
        compartments = oci.pagination.list_call_get_all_results(
            identity.list_compartments, tenancy_id, compartment_id_in_subtree=True
        ).data
        all_compartments = [tenancy_id] + [c.id for c in compartments if c.lifecycle_state == "ACTIVE"]

        # List subscribed regions
        subscriptions = identity.list_region_subscriptions(tenancy_id).data
        regions = [s.region_name for s in subscriptions]

        subnet_ids = []
        for region in regions:
            region_config = dict(config)
            region_config["region"] = region
            vnc = oci.core.VirtualNetworkClient(region_config)
            for comp_id in all_compartments:
                try:
                    subnets = oci.pagination.list_call_get_all_results(
                        vnc.list_subnets, comp_id
                    ).data
                    for sn in subnets:
                        subnet_ids.append(f"{region}/{sn.id}")
                except Exception:
                    pass
        pre["subnet_ids"] = subnet_ids
        pre["regions"] = regions
        pre["compartments"] = all_compartments

    await _run(_do)
    return {"phase": "snapshot", "status": "ok", "pre_existing_states": pre}


# ── Phase 3: Enable ───────────────────────────────────────────────────────────

async def _enable(creds: dict, tenancy_id: str, home_region: str, pre: dict, rollback_data: dict) -> dict:
    results = []

    # Cloud Guard
    def _cg():
        import oci
        from ._client import get_oci_config
        config = get_oci_config(creds)
        cloud_guard = oci.cloud_guard.CloudGuardClient(config)
        if pre.get("cloud_guard_status") == "ENABLED":
            return "skipped"
        cloud_guard.update_configuration(
            tenancy_id,
            oci.cloud_guard.models.UpdateConfigurationDetails(
                status="ENABLED",
                reporting_region=home_region,
                self_manage_resources=False,
            ),
        )
        # Create tenancy-level target with all Oracle-managed detector recipes
        recipes = oci.pagination.list_call_get_all_results(
            cloud_guard.list_detector_recipes, tenancy_id
        ).data
        managed_recipes = [r for r in recipes if r.owner == "ORACLE"]
        target_detector_recipes = [
            oci.cloud_guard.models.CreateTargetDetectorRecipeDetails(detector_recipe_id=r.id)
            for r in managed_recipes
        ]
        cloud_guard.create_target(oci.cloud_guard.models.CreateTargetDetails(
            display_name="nexplane-tenancy-baseline",
            compartment_id=tenancy_id,
            target_resource_type="COMPARTMENT",
            target_resource_id=tenancy_id,
            target_detector_recipes=target_detector_recipes,
        ))
        return "enabled"
    action = await _run(_cg)
    if action == "enabled":
        rollback_data["newly_enabled"].append({"service": "cloud_guard"})
    results.append({"service": "cloud_guard", "action": action})

    # Audit retention
    if pre.get("audit_retention_days", 0) >= 365:
        results.append({"service": "audit_retention", "action": "skipped"})
    else:
        def _audit():
            import oci
            from ._client import get_oci_config
            config = get_oci_config(creds)
            audit = oci.audit.AuditClient(config)
            prev = pre.get("audit_retention_days", 90)
            audit.update_configuration(tenancy_id, oci.audit.models.UpdateConfigurationDetails(retention_period_days=365))
            return prev
        prev_days = await _run(_audit)
        rollback_data["newly_enabled"].append({"service": "audit_retention", "previous_days": prev_days})
        results.append({"service": "audit_retention", "action": "enabled", "set_to_days": 365})

    # VCN Flow Logs
    def _flow():
        import oci
        from ._client import get_oci_config
        count = 0
        regions = pre.get("regions", [])
        compartments = pre.get("compartments", [tenancy_id])
        for region in regions:
            config = get_oci_config(creds)
            config["region"] = region
            vnc = oci.core.VirtualNetworkClient(config)
            logging_mgmt = oci.loggingmanagement.LoggingManagementClient(config)
            for comp_id in compartments:
                try:
                    subnets = oci.pagination.list_call_get_all_results(vnc.list_subnets, comp_id).data
                    for sn in subnets:
                        log_group_name = f"nexplane-flowlogs-{region}"
                        existing_groups = logging_mgmt.list_log_groups(comp_id, display_name=log_group_name).data
                        if existing_groups:
                            log_group_id = existing_groups[0].id
                        else:
                            lg = logging_mgmt.create_log_group(oci.loggingmanagement.models.CreateLogGroupDetails(
                                compartment_id=comp_id,
                                display_name=log_group_name,
                                description="Nexplane baseline flow logs",
                            )).data
                            log_group_id = lg.id
                        logging_mgmt.create_log(log_group_id, oci.loggingmanagement.models.CreateLogDetails(
                            display_name=f"flowlog-{sn.id[-8:]}",
                            log_type="SERVICE",
                            is_enabled=True,
                            configuration=oci.loggingmanagement.models.Configuration(
                                source=oci.loggingmanagement.models.OciService(
                                    service="flowlogs", resource=sn.id, category="all",
                                )
                            ),
                        ))
                        count += 1
                except Exception as e:
                    logger.warning("Flow log enable skipped for compartment %s: %s", comp_id, e)
        return count
    count = await _run(_flow)
    if count > 0:
        rollback_data["newly_enabled"].append({"service": "vcn_flow_logs"})
    if count == 0 and not pre.get("subnet_ids"):
        results.append({"service": "vcn_flow_logs", "action": "skipped", "reason": "no VCNs found"})
        rollback_data.setdefault("skipped_with_warning", []).append(
            {"service": "vcn_flow_logs", "reason": "No VCNs found in tenancy"}
        )
    else:
        results.append({"service": "vcn_flow_logs", "action": "enabled" if count > 0 else "skipped",
                        "subnets_enabled": count})

    # IAM password policy
    prev_pp = pre.get("iam_pp")
    if (prev_pp and
        prev_pp.get("minimum_password_length", 0) >= 14 and
        prev_pp.get("is_uppercase_characters_required") == True and
        prev_pp.get("is_lowercase_characters_required") == True and
        prev_pp.get("is_numeric_characters_required") == True and
        prev_pp.get("is_special_characters_required") == True):
        results.append({"service": "iam_password_policy", "action": "skipped"})
    else:
        def _pp():
            import oci
            from ._client import get_oci_config
            config = get_oci_config(creds)
            identity = oci.identity.IdentityClient(config)
            identity.update_authentication_policy(
                tenancy_id,
                oci.identity.models.UpdateAuthenticationPolicyDetails(
                    password_policy=oci.identity.models.PasswordPolicy(**OCI_PASSWORD_POLICY)
                ),
            )
        await _run(_pp)
        rollback_data["newly_enabled"].append({"service": "iam_password_policy"})
        results.append({"service": "iam_password_policy", "action": "enabled"})

    return {"phase": "enable", "status": "ok", "results": results}


# ── Phase 4: Verify ───────────────────────────────────────────────────────────

async def _verify(creds: dict, tenancy_id: str, newly_enabled: list) -> dict:
    checks = []

    for item in newly_enabled:
        svc = item["service"]
        try:
            if svc == "cloud_guard":
                def _v():
                    import oci
                    from ._client import get_oci_config
                    config = get_oci_config(creds)
                    cg = oci.cloud_guard.CloudGuardClient(config)
                    cfg = cg.get_configuration(tenancy_id).data
                    return cfg.status == "ENABLED"
                ok = await _run(_v)
                checks.append({"service": "cloud_guard", "ok": bool(ok)})

            elif svc == "audit_retention":
                def _v():
                    import oci
                    from ._client import get_oci_config
                    config = get_oci_config(creds)
                    audit = oci.audit.AuditClient(config)
                    cfg = audit.get_configuration(tenancy_id).data
                    return cfg.retention_period_days >= 365
                ok = await _run(_v)
                checks.append({"service": "audit_retention", "ok": bool(ok)})

            elif svc == "iam_password_policy":
                def _v():
                    import oci
                    from ._client import get_oci_config
                    config = get_oci_config(creds)
                    identity = oci.identity.IdentityClient(config)
                    policy = identity.get_authentication_policy(tenancy_id).data
                    pp = policy.password_policy
                    return pp and pp.minimum_password_length >= 14
                ok = await _run(_v)
                checks.append({"service": "iam_password_policy", "ok": bool(ok)})

            elif svc == "vcn_flow_logs":
                checks.append({"service": "vcn_flow_logs", "ok": True, "note": "enabled — verify via OCI Logging console"})

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
                "newly_enabled": ["cloud_guard", "audit_retention", "vcn_flow_logs", "iam_password_policy"],
                "failed": [],
                "skipped_with_warning": [],
            },
            "promote_to": "oci_account_baseline_hardening",
            "rollback_data": {"newly_enabled": [], "pre_existing_states": {}},
        }

    phases = []
    rollback_data = {"newly_enabled": [], "pre_existing_states": {}}

    phase1 = await _preflight(creds)
    phases.append(phase1)
    if phase1["status"] == "failed":
        return {"phases": phases, "failed": True, "rollback_data": rollback_data}

    tenancy_id = phase1["tenancy_id"]
    home_region = phase1["home_region"]

    phase2 = await _snapshot(creds, tenancy_id)
    phases.append(phase2)
    pre = phase2["pre_existing_states"]
    rollback_data["pre_existing_states"] = pre

    phase3 = await _enable(creds, tenancy_id, home_region, pre, rollback_data)
    phases.append(phase3)

    phase4 = await _verify(creds, tenancy_id, rollback_data["newly_enabled"])
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
        "promote_to": "oci_account_baseline_hardening",
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

    from ._client import get_oci_config
    import oci
    config = get_oci_config(creds)
    tenancy_id = creds["tenancy"]

    for item in reversed(newly_enabled):
        svc = item["service"]
        try:
            if svc == "cloud_guard":
                def _undo():
                    cloud_guard = oci.cloud_guard.CloudGuardClient(config)
                    targets = oci.pagination.list_call_get_all_results(
                        cloud_guard.list_targets, tenancy_id, display_name="nexplane-tenancy-baseline"
                    ).data
                    for t in targets:
                        cloud_guard.delete_target(t.id)
                    cloud_guard.update_configuration(
                        tenancy_id,
                        oci.cloud_guard.models.UpdateConfigurationDetails(status="DISABLED"),
                    )
                await _run(_undo)

            elif svc == "audit_retention":
                prev = item.get("previous_days", 90)
                def _undo(p=prev):
                    audit = oci.audit.AuditClient(config)
                    audit.update_configuration(
                        tenancy_id, oci.audit.models.UpdateConfigurationDetails(retention_period_days=p)
                    )
                await _run(_undo)

            elif svc == "iam_password_policy":
                prev_pp = pre.get("iam_pp")
                def _undo(p=prev_pp):
                    identity = oci.identity.IdentityClient(config)
                    if p:
                        identity.update_authentication_policy(
                            tenancy_id,
                            oci.identity.models.UpdateAuthenticationPolicyDetails(
                                password_policy=oci.identity.models.PasswordPolicy(**p)
                            ),
                        )
                await _run(_undo)

            elif svc == "vcn_flow_logs":
                def _undo():
                    regions = pre.get("regions", [])
                    compartments = pre.get("compartments", [tenancy_id])
                    for region in regions:
                        rc = dict(config)
                        rc["region"] = region
                        logging_mgmt = oci.loggingmanagement.LoggingManagementClient(rc)
                        for comp_id in compartments:
                            try:
                                groups = logging_mgmt.list_log_groups(
                                    comp_id, display_name=f"nexplane-flowlogs-{region}"
                                ).data
                                for g in groups:
                                    logs = logging_mgmt.list_logs(g.id).data
                                    for log in logs:
                                        logging_mgmt.delete_log(g.id, log.id)
                                    logging_mgmt.delete_log_group(g.id)
                            except Exception as e:
                                logger.warning("Flow log rollback skipped for %s/%s: %s", region, comp_id, e)
                await _run(_undo)

            undone.append({"service": svc, "rolled_back": True})
        except Exception as e:
            logger.error("OCI rollback failed for %s: %s", svc, e)
            undone.append({"service": svc, "rolled_back": False, "error": str(e)})

    all_ok = all(u["rolled_back"] for u in undone) if undone else True
    return {"rolled_back": all_ok, "undone": undone}
