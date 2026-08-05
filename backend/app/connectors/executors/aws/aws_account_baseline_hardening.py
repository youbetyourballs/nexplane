# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

PASSWORD_POLICY = dict(
    MinimumPasswordLength=14,
    RequireSymbols=True,
    RequireNumbers=True,
    RequireUppercaseCharacters=True,
    RequireLowercaseCharacters=True,
    AllowUsersToChangePassword=True,
    MaxPasswordAge=90,
    PasswordReusePrevention=12,
)

_PP_SETTABLE_KEYS = set(PASSWORD_POLICY.keys())


def _c(connector, service, region="us-east-1"):
    from app.connectors.executors.aws.reference_scan import _boto_client
    return _boto_client(service, connector, region)


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def _preflight(connector) -> dict:
    try:
        def _do():
            sts = _c(connector, "sts")
            return sts.get_caller_identity()["Account"]
        account_id = await _run(_do)
        return {"phase": "preflight", "status": "ok", "account_id": account_id}
    except Exception as e:
        return {"phase": "preflight", "status": "error", "error": str(e)}


async def _snapshot(connector, account_id: str) -> dict:
    try:
        def _do():
            snap = {}

            iam = _c(connector, "iam")
            try:
                snap["password_policy"] = iam.get_account_password_policy()["PasswordPolicy"]
            except iam.exceptions.NoSuchEntityException:
                snap["password_policy"] = None
            except Exception:
                snap["password_policy"] = None

            s3ctrl = _c(connector, "s3control")
            try:
                snap["s3_block"] = s3ctrl.get_public_access_block(AccountId=account_id)["PublicAccessBlockConfiguration"]
                snap["s3_block_exists"] = True
            except Exception:
                snap["s3_block"] = None
                snap["s3_block_exists"] = False

            summary = iam.get_account_summary()["SummaryMap"]
            snap["root_mfa_enabled"] = bool(summary.get("AccountMFAEnabled", 0))

            ec2 = _c(connector, "ec2")
            vpcs = ec2.describe_vpcs()["Vpcs"]
            vpc_ids = [v["VpcId"] for v in vpcs]

            flow_logs_resp = ec2.describe_flow_logs(
                Filters=[{"Name": "resource-id", "Values": vpc_ids}]
            ) if vpc_ids else {"FlowLogs": []}
            covered_vpcs = {fl["ResourceId"] for fl in flow_logs_resp["FlowLogs"]}
            snap["vpcs"] = vpc_ids
            snap["vpcs_with_flow_logs"] = list(covered_vpcs)

            return snap

        snap = await _run(_do)
        return {"phase": "snapshot", "status": "ok", "snapshot": snap}
    except Exception as e:
        return {"phase": "snapshot", "status": "error", "error": str(e)}


async def _harden(connector, account_id: str, snapshot: dict, parameters: dict, rollback_data: dict) -> dict:
    newly_applied = []
    already_applied = []
    warnings = []
    failed = []

    enforce_pp = parameters.get("enforce_password_policy", True)
    enforce_s3 = parameters.get("enforce_s3_block_public", True)
    enforce_mfa_check = parameters.get("enforce_root_mfa_check", True)
    enable_flow_logs = parameters.get("enable_vpc_flow_logs", True)
    flow_logs_s3_arn = parameters.get("vpc_flow_logs_s3_arn")

    if enforce_pp:
        prev_pp = snapshot.get("password_policy")
        if prev_pp and prev_pp.get("MinimumPasswordLength", 0) >= 14:
            already_applied.append("iam_password_policy")
        else:
            try:
                def _apply_pp():
                    iam = _c(connector, "iam")
                    iam.update_account_password_policy(**PASSWORD_POLICY)
                await _run(_apply_pp)
                newly_applied.append("iam_password_policy")
                rollback_data["pre_password_policy"] = snapshot.get("password_policy")
            except Exception as e:
                failed.append({"control": "iam_password_policy", "error": str(e)})

    if enforce_s3:
        prev_s3 = snapshot.get("s3_block")
        if prev_s3 and all(prev_s3.values()):
            already_applied.append("s3_block_public_access")
        else:
            try:
                def _apply_s3(acct=account_id):
                    s3ctrl = _c(connector, "s3control")
                    s3ctrl.put_public_access_block(
                        AccountId=acct,
                        PublicAccessBlockConfiguration={
                            "BlockPublicAcls": True,
                            "IgnorePublicAcls": True,
                            "BlockPublicPolicy": True,
                            "RestrictPublicBuckets": True,
                        },
                    )
                await _run(_apply_s3)
                newly_applied.append("s3_block_public_access")
                rollback_data["pre_s3_block"] = snapshot.get("s3_block")
                rollback_data["pre_s3_block_exists"] = snapshot.get("s3_block_exists", False)
            except Exception as e:
                failed.append({"control": "s3_block_public_access", "error": str(e)})

    if enforce_mfa_check:
        if snapshot.get("root_mfa_enabled"):
            already_applied.append("root_mfa")
        else:
            warnings.append({
                "control": "root_mfa",
                "status": "warning",
                "message": "Root MFA not enabled — manual action required",
            })

    if enable_flow_logs:
        vpcs = snapshot.get("vpcs", [])
        covered = set(snapshot.get("vpcs_with_flow_logs", []))
        uncovered = [v for v in vpcs if v not in covered]

        if not uncovered:
            already_applied.append("vpc_flow_logs")
        elif not flow_logs_s3_arn:
            warnings.append({
                "control": "vpc_flow_logs",
                "status": "warning",
                "message": f"{len(uncovered)} VPC(s) lack flow logs but vpc_flow_logs_s3_arn not provided — skipped",
            })
        else:
            newly_applied_flow_logs = []
            for vpc_id in uncovered:
                try:
                    def _apply_fl(vid=vpc_id, arn=flow_logs_s3_arn):
                        ec2 = _c(connector, "ec2")
                        resp = ec2.create_flow_logs(
                            ResourceIds=[vid],
                            ResourceType="VPC",
                            TrafficType="ALL",
                            LogDestinationType="s3",
                            LogDestination=arn,
                        )
                        fl_ids = resp.get("FlowLogIds", [])
                        return fl_ids[0] if fl_ids else None
                    fl_id = await _run(_apply_fl)
                    if fl_id:
                        newly_applied_flow_logs.append({"vpc_id": vpc_id, "flow_log_id": fl_id})
                except Exception as e:
                    failed.append({"control": f"vpc_flow_logs:{vpc_id}", "error": str(e)})

            rollback_data["newly_applied_flow_logs"] = newly_applied_flow_logs
            if newly_applied_flow_logs:
                newly_applied.append(f"vpc_flow_logs:{len(newly_applied_flow_logs)}_vpcs")

    return {
        "phase": "harden",
        "status": "ok" if not failed else "partial",
        "newly_applied": newly_applied,
        "already_applied": already_applied,
        "warnings": warnings,
        "failed": failed,
    }


async def _verify(connector, account_id: str, newly_applied: list) -> dict:
    checks = []

    controls = {item.split(":")[0] for item in newly_applied}

    if "iam_password_policy" in controls:
        try:
            def _v():
                iam = _c(connector, "iam")
                pp = iam.get_account_password_policy()["PasswordPolicy"]
                return pp.get("MinimumPasswordLength", 0) >= 14
            ok = await _run(_v)
            checks.append({"control": "iam_password_policy", "ok": bool(ok)})
        except Exception as e:
            checks.append({"control": "iam_password_policy", "ok": False, "error": str(e)})

    if "s3_block_public_access" in controls:
        try:
            def _v(acct=account_id):
                s3ctrl = _c(connector, "s3control")
                pab = s3ctrl.get_public_access_block(AccountId=acct)["PublicAccessBlockConfiguration"]
                return all(pab.values())
            ok = await _run(_v)
            checks.append({"control": "s3_block_public_access", "ok": bool(ok)})
        except Exception as e:
            checks.append({"control": "s3_block_public_access", "ok": False, "error": str(e)})

    for item in newly_applied:
        if item.startswith("vpc_flow_logs:"):
            checks.append({"control": "vpc_flow_logs", "ok": True, "note": "created — not re-verified"})
            break

    failed = [c for c in checks if not c["ok"]]
    return {
        "phase": "verify",
        "status": "ok" if not failed else "partial",
        "checks": checks,
        "failed_checks": failed,
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "mock": True,
            "phases": [{"phase": p, "status": "mock"} for p in ["preflight", "snapshot", "harden", "verify"]],
            "summary": {"newly_applied": [], "already_applied": [], "warnings": [], "failed": []},
            "rollback_data": {
                "pre_password_policy": None,
                "pre_s3_block": None,
                "pre_s3_block_exists": False,
                "newly_applied_flow_logs": [],
            },
        }

    phases = []
    rollback_data = {
        "pre_password_policy": None,
        "pre_s3_block": None,
        "pre_s3_block_exists": False,
        "newly_applied_flow_logs": [],
    }

    phase1 = await _preflight(connector)
    phases.append(phase1)
    if phase1["status"] != "ok":
        return {"phases": phases, "failed": True, "rollback_data": rollback_data}

    account_id = phase1["account_id"]

    phase2 = await _snapshot(connector, account_id)
    phases.append(phase2)
    if phase2["status"] != "ok":
        return {"phases": phases, "failed": True, "rollback_data": rollback_data}

    snapshot = phase2["snapshot"]

    phase3 = await _harden(connector, account_id, snapshot, parameters, rollback_data)
    phases.append(phase3)

    phase4 = await _verify(connector, account_id, phase3["newly_applied"])
    phases.append(phase4)

    summary = {
        "newly_applied": phase3["newly_applied"],
        "already_applied": phase3["already_applied"],
        "warnings": phase3["warnings"],
        "failed": phase3["failed"] + [
            {"control": c["control"], "error": c.get("error", "verify failed")}
            for c in phase4.get("failed_checks", [])
        ],
    }

    return {
        "phases": phases,
        "summary": summary,
        "rollback_data": rollback_data,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": True, "mock": True, "undone": []}

    rd = execution_result.get("rollback_data", {})
    undone = []

    flow_logs = rd.get("newly_applied_flow_logs", [])
    if flow_logs:
        fl_ids = [fl["flow_log_id"] for fl in flow_logs if fl.get("flow_log_id")]
        if fl_ids:
            try:
                def _del_fl(ids=fl_ids):
                    ec2 = _c(connector, "ec2")
                    ec2.delete_flow_logs(FlowLogIds=ids)
                await _run(_del_fl)
                for fl in flow_logs:
                    undone.append({"control": "vpc_flow_logs", "vpc_id": fl["vpc_id"], "rolled_back": True})
            except Exception as e:
                logger.error("Rollback vpc_flow_logs failed: %s", e)
                for fl in flow_logs:
                    undone.append({"control": "vpc_flow_logs", "vpc_id": fl["vpc_id"], "rolled_back": False, "error": str(e)})

    if "pre_s3_block" in rd or "pre_s3_block_exists" in rd:
        try:
            pre_s3 = rd.get("pre_s3_block")
            pre_exists = rd.get("pre_s3_block_exists", False)
            phases = execution_result.get("phases", [])
            account_id = next((p.get("account_id") for p in phases if p.get("account_id")), None)
            def _restore_s3(acct=account_id):
                s3ctrl = _c(connector, "s3control")
                if not pre_exists:
                    try:
                        s3ctrl.delete_public_access_block(AccountId=acct)
                    except Exception:
                        pass
                elif pre_s3:
                    s3ctrl.put_public_access_block(
                        AccountId=acct,
                        PublicAccessBlockConfiguration=pre_s3,
                    )
            await _run(_restore_s3)
            undone.append({"control": "s3_block_public_access", "rolled_back": True})
        except Exception as e:
            logger.error("Rollback s3_block_public_access failed: %s", e)
            undone.append({"control": "s3_block_public_access", "rolled_back": False, "error": str(e)})

    if "pre_password_policy" in rd:
        try:
            prev_pp = rd.get("pre_password_policy")
            def _restore_pp(p=prev_pp):
                iam = _c(connector, "iam")
                if p is None:
                    iam.delete_account_password_policy()
                else:
                    iam.update_account_password_policy(**{k: v for k, v in p.items() if k in _PP_SETTABLE_KEYS})
            await _run(_restore_pp)
            undone.append({"control": "iam_password_policy", "rolled_back": True})
        except Exception as e:
            logger.error("Rollback iam_password_policy failed: %s", e)
            undone.append({"control": "iam_password_policy", "rolled_back": False, "error": str(e)})

    all_ok = all(u["rolled_back"] for u in undone)
    return {"rolled_back": all_ok, "undone": undone}
