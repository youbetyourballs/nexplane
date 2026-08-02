# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS account baseline monitoring executor.

Enables CloudTrail, GuardDuty, SecurityHub, AWS Config, S3 account-level
public access block, and IAM password policy across all regions.
"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"
TRAIL_NAME = "nexplane-baseline"
RECORDER_NAME = "nexplane-baseline"
CHANNEL_NAME = "nexplane-baseline"
PASSWORD_POLICY = dict(
    MinimumPasswordLength=14,
    RequireSymbols=True,
    RequireNumbers=True,
    RequireUppercaseCharacters=True,
    RequireLowercaseCharacters=True,
    MaxPasswordAge=90,
    PasswordReusePrevention=24,
    HardExpiry=False,
)


def _c(connector, service, region="us-east-1"):
    from app.connectors.executors.aws.reference_scan import _boto_client
    return _boto_client(service, connector, region)


async def _run(fn):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn)


# ── Phase 1: Preflight ────────────────────────────────────────────────────────

async def _preflight(connector) -> dict:
    try:
        def _do():
            sts = _c(connector, "sts")
            account_id = sts.get_caller_identity()["Account"]
            ec2 = _c(connector, "ec2", "us-east-1")
            resp = ec2.describe_regions(Filters=[{
                "Name": "opt-in-status",
                "Values": ["opt-in-not-required", "opted-in"],
            }])
            regions = [r["RegionName"] for r in resp["Regions"]]
            return account_id, regions
        account_id, regions = await _run(_do)
        return {"phase": "preflight", "status": "ok", "account_id": account_id, "regions": regions}
    except Exception as e:
        return {"phase": "preflight", "status": "failed", "error": str(e)}


# ── Phase 2: Snapshot ─────────────────────────────────────────────────────────

async def _snapshot(connector, account_id: str, regions: list) -> dict:
    pre = {}

    def _account_level():
        # CloudTrail
        ct = _c(connector, "cloudtrail", "us-east-1")
        trails = ct.describe_trails(includeShadowTrails=False).get("trailList", [])
        pre["cloudtrail"] = next(
            (t["TrailARN"] for t in trails if t.get("IsMultiRegionTrail") and t["Name"] != TRAIL_NAME), None
        )
        # S3 account public access block
        s3ctrl = _c(connector, "s3control", "us-east-1")
        try:
            pre["s3_block"] = s3ctrl.get_public_access_block(AccountId=account_id)["PublicAccessBlockConfiguration"]
        except Exception:
            pre["s3_block"] = None
        # IAM password policy
        iam = _c(connector, "iam", "us-east-1")
        try:
            pre["iam_pp"] = iam.get_account_password_policy()["PasswordPolicy"]
        except Exception:
            pre["iam_pp"] = None

    await _run(_account_level)

    per_region = {}
    for region in regions:
        def _region_snap(r=region):
            snap = {}
            gd = _c(connector, "guardduty", r)
            detectors = gd.list_detectors().get("DetectorIds", [])
            snap["guardduty"] = detectors[0] if detectors else None
            sh = _c(connector, "securityhub", r)
            try:
                sh.describe_hub()
                snap["securityhub"] = True
            except Exception:
                snap["securityhub"] = False
            cfg = _c(connector, "config", r)
            recorders = cfg.describe_configuration_recorders().get("ConfigurationRecorders", [])
            snap["config"] = recorders[0]["name"] if recorders else None
            return snap
        per_region[region] = await _run(_region_snap)

    pre["regions"] = per_region
    return {"phase": "snapshot", "status": "ok", "pre_existing_states": pre}


# ── Phase 3: Enable ───────────────────────────────────────────────────────────

async def _enable(connector, account_id: str, regions: list, pre: dict, rollback_data: dict) -> dict:
    results = []

    # CloudTrail
    if pre.get("cloudtrail"):
        results.append({"service": "cloudtrail", "action": "skipped"})
    else:
        bucket = f"nexplane-cloudtrail-{account_id}"
        def _ct(b=bucket, acct=account_id):
            s3 = _c(connector, "s3", "us-east-1")
            try:
                s3.create_bucket(Bucket=b)
            except Exception:
                pass
            s3.put_bucket_policy(Bucket=b, Policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {"Sid": "AWSCloudTrailAclCheck", "Effect": "Allow",
                     "Principal": {"Service": "cloudtrail.amazonaws.com"},
                     "Action": "s3:GetBucketAcl", "Resource": f"arn:aws:s3:::{b}"},
                    {"Sid": "AWSCloudTrailWrite", "Effect": "Allow",
                     "Principal": {"Service": "cloudtrail.amazonaws.com"},
                     "Action": "s3:PutObject",
                     "Resource": f"arn:aws:s3:::{b}/AWSLogs/{acct}/*",
                     "Condition": {"StringEquals": {"s3:x-amz-acl": "bucket-owner-full-control"}}},
                ],
            }))
            ct = _c(connector, "cloudtrail", "us-east-1")
            ct.create_trail(
                Name=TRAIL_NAME, S3BucketName=b, IsMultiRegionTrail=True,
                EnableLogFileValidation=True, IncludeGlobalServiceEvents=True,
            )
            ct.start_logging(Name=TRAIL_NAME)
        await _run(_ct)
        rollback_data["newly_enabled"].append({"service": "cloudtrail", "bucket": bucket})
        results.append({"service": "cloudtrail", "action": "enabled", "bucket": bucket})

    # S3 account public access block
    prev_s3 = pre.get("s3_block")
    if prev_s3 and all(prev_s3.values()):
        results.append({"service": "s3_account_public_access_block", "action": "skipped"})
    else:
        def _s3b(acct=account_id):
            s3ctrl = _c(connector, "s3control", "us-east-1")
            s3ctrl.put_public_access_block(AccountId=acct, PublicAccessBlockConfiguration={
                "BlockPublicAcls": True, "IgnorePublicAcls": True,
                "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
            })
        await _run(_s3b)
        rollback_data["newly_enabled"].append({"service": "s3_account_public_access_block", "account_id": account_id})
        results.append({"service": "s3_account_public_access_block", "action": "enabled"})

    # IAM password policy
    prev_pp = pre.get("iam_pp")
    if prev_pp and prev_pp.get("MinimumPasswordLength", 0) >= 14:
        results.append({"service": "iam_password_policy", "action": "skipped"})
    else:
        def _iam():
            iam = _c(connector, "iam", "us-east-1")
            iam.update_account_password_policy(**PASSWORD_POLICY)
        await _run(_iam)
        rollback_data["newly_enabled"].append({"service": "iam_password_policy"})
        results.append({"service": "iam_password_policy", "action": "enabled"})

    # Per-region: GuardDuty, SecurityHub, Config
    for region in regions:
        pre_r = pre["regions"].get(region, {})

        if pre_r.get("guardduty"):
            results.append({"service": f"guardduty:{region}", "action": "skipped"})
        else:
            def _gd(r=region):
                gd = _c(connector, "guardduty", r)
                resp = gd.create_detector(Enable=True, FindingPublishingFrequency="FIFTEEN_MINUTES")
                return resp["DetectorId"]
            detector_id = await _run(_gd)
            rollback_data["newly_enabled"].append({"service": "guardduty", "region": region, "detector_id": detector_id})
            results.append({"service": f"guardduty:{region}", "action": "enabled"})

        if pre_r.get("securityhub"):
            results.append({"service": f"securityhub:{region}", "action": "skipped"})
        else:
            def _sh(r=region):
                sh = _c(connector, "securityhub", r)
                sh.enable_security_hub(EnableDefaultStandards=False)
                sh.batch_enable_standards(StandardsSubscriptionRequests=[{
                    "StandardsArn": "arn:aws:securityhub:::ruleset/cis-aws-foundations-benchmark/v/1.2.0"
                }])
            await _run(_sh)
            rollback_data["newly_enabled"].append({"service": "securityhub", "region": region})
            results.append({"service": f"securityhub:{region}", "action": "enabled"})

        if pre_r.get("config"):
            results.append({"service": f"config:{region}", "action": "skipped"})
        else:
            cfg_bucket = f"nexplane-config-{account_id}-{region}"
            def _cfg(r=region, b=cfg_bucket, acct=account_id):
                s3 = _c(connector, "s3", "us-east-1")
                try:
                    if r != "us-east-1":
                        s3.create_bucket(Bucket=b, CreateBucketConfiguration={"LocationConstraint": r})
                    else:
                        s3.create_bucket(Bucket=b)
                except Exception:
                    pass
                cfg = _c(connector, "config", r)
                cfg.put_configuration_recorder(ConfigurationRecorder={
                    "name": RECORDER_NAME,
                    "roleARN": f"arn:aws:iam::{acct}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig",
                    "recordingGroup": {"allSupported": True, "includeGlobalResourceTypes": r == "us-east-1"},
                })
                cfg.put_delivery_channel(DeliveryChannel={"name": CHANNEL_NAME, "s3BucketName": b})
                cfg.start_configuration_recorder(ConfigurationRecorderName=RECORDER_NAME)
            await _run(_cfg)
            rollback_data["newly_enabled"].append({"service": "config", "region": region, "bucket": cfg_bucket})
            results.append({"service": f"config:{region}", "action": "enabled"})

    return {"phase": "enable", "status": "ok", "results": results}


# ── Phase 4: Verify ───────────────────────────────────────────────────────────

async def _verify(connector, account_id: str, newly_enabled: list) -> dict:
    checks = []
    for item in newly_enabled:
        svc = item["service"]
        region = item.get("region", "us-east-1")
        try:
            if svc == "cloudtrail":
                def _v():
                    ct = _c(connector, "cloudtrail", "us-east-1")
                    return ct.get_trail_status(Name=TRAIL_NAME)["IsLogging"]
                ok = await _run(_v)
                checks.append({"service": "cloudtrail", "ok": bool(ok)})

            elif svc == "s3_account_public_access_block":
                def _v(acct=account_id):
                    s3ctrl = _c(connector, "s3control", "us-east-1")
                    pab = s3ctrl.get_public_access_block(AccountId=acct)["PublicAccessBlockConfiguration"]
                    return all(pab.values())
                ok = await _run(_v)
                checks.append({"service": "s3_account_public_access_block", "ok": bool(ok)})

            elif svc == "iam_password_policy":
                def _v():
                    iam = _c(connector, "iam", "us-east-1")
                    pp = iam.get_account_password_policy()["PasswordPolicy"]
                    return pp.get("MinimumPasswordLength", 0) >= 14
                ok = await _run(_v)
                checks.append({"service": "iam_password_policy", "ok": bool(ok)})

            elif svc == "guardduty":
                did = item["detector_id"]
                def _v(r=region, d=did):
                    gd = _c(connector, "guardduty", r)
                    return gd.get_detector(DetectorId=d)["Status"] == "ENABLED"
                ok = await _run(_v)
                checks.append({"service": f"guardduty:{region}", "ok": bool(ok)})

            elif svc == "securityhub":
                def _v(r=region):
                    sh = _c(connector, "securityhub", r)
                    try:
                        sh.describe_hub()
                        return True
                    except Exception:
                        return False
                ok = await _run(_v)
                checks.append({"service": f"securityhub:{region}", "ok": bool(ok)})

            elif svc == "config":
                def _v(r=region):
                    cfg = _c(connector, "config", r)
                    statuses = cfg.describe_configuration_recorder_status().get("ConfigurationRecordersStatus", [])
                    return any(s.get("recording") for s in statuses)
                ok = await _run(_v)
                checks.append({"service": f"config:{region}", "ok": bool(ok)})

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
                "newly_enabled": ["cloudtrail", "guardduty", "securityhub", "config",
                                  "s3_account_public_access_block", "iam_password_policy"],
                "failed": [],
                "skipped_with_warning": [],
            },
            "promote_to": "aws_account_baseline_hardening",
            "rollback_data": {"newly_enabled": [], "pre_existing_states": {}},
        }

    phases = []
    rollback_data = {"newly_enabled": [], "pre_existing_states": {}}

    phase1 = await _preflight(connector)
    phases.append(phase1)
    if phase1["status"] == "failed":
        return {"phases": phases, "failed": True, "rollback_data": rollback_data}

    account_id = phase1["account_id"]
    regions = phase1["regions"]

    phase2 = await _snapshot(connector, account_id, regions)
    phases.append(phase2)
    pre = phase2["pre_existing_states"]
    rollback_data["pre_existing_states"] = pre

    phase3 = await _enable(connector, account_id, regions, pre, rollback_data)
    phases.append(phase3)

    phase4 = await _verify(connector, account_id, rollback_data["newly_enabled"])
    phases.append(phase4)

    already_enabled = [r["service"] for r in phase3["results"] if r.get("action") == "skipped"]
    newly = [
        item["service"] + (":" + item["region"] if item.get("region") else "")
        for item in rollback_data["newly_enabled"]
    ]
    summary = {
        "already_enabled": already_enabled,
        "newly_enabled": newly,
        "failed": [c["service"] for c in phase4.get("failed_checks", [])],
        "skipped_with_warning": [],
    }
    phases.append({"phase": "report", "status": "ok", "summary": summary})

    return {
        "phases": phases,
        "summary": summary,
        "promote_to": "aws_account_baseline_hardening",
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

    for item in reversed(newly_enabled):
        svc = item["service"]
        region = item.get("region", "us-east-1")
        try:
            if svc == "cloudtrail":
                bucket = item["bucket"]
                def _undo_ct(b=bucket):
                    ct = _c(connector, "cloudtrail", "us-east-1")
                    s3 = _c(connector, "s3", "us-east-1")
                    ct.stop_logging(Name=TRAIL_NAME)
                    ct.delete_trail(Name=TRAIL_NAME)
                    objs = s3.list_objects_v2(Bucket=b).get("Contents", [])
                    if objs:
                        s3.delete_objects(Bucket=b, Delete={"Objects": [{"Key": o["Key"]} for o in objs]})
                    s3.delete_bucket(Bucket=b)
                await _run(_undo_ct)

            elif svc == "s3_account_public_access_block":
                acct = item["account_id"]
                prev = pre.get("s3_block")
                def _undo_s3(a=acct, p=prev):
                    s3ctrl = _c(connector, "s3control", "us-east-1")
                    if p:
                        s3ctrl.put_public_access_block(AccountId=a, PublicAccessBlockConfiguration=p)
                    else:
                        s3ctrl.delete_public_access_block(AccountId=a)
                await _run(_undo_s3)

            elif svc == "iam_password_policy":
                prev = pre.get("iam_pp")
                def _undo_iam(p=prev):
                    iam = _c(connector, "iam", "us-east-1")
                    if p:
                        safe_keys = set(PASSWORD_POLICY.keys())
                        iam.update_account_password_policy(**{k: v for k, v in p.items() if k in safe_keys})
                    else:
                        iam.delete_account_password_policy()
                await _run(_undo_iam)

            elif svc == "guardduty":
                did = item["detector_id"]
                def _undo_gd(r=region, d=did):
                    gd = _c(connector, "guardduty", r)
                    gd.delete_detector(DetectorId=d)
                await _run(_undo_gd)

            elif svc == "securityhub":
                def _undo_sh(r=region):
                    sh = _c(connector, "securityhub", r)
                    sh.disable_security_hub()
                await _run(_undo_sh)

            elif svc == "config":
                bucket = item["bucket"]
                def _undo_cfg(r=region, b=bucket):
                    cfg = _c(connector, "config", r)
                    cfg.stop_configuration_recorder(ConfigurationRecorderName=RECORDER_NAME)
                    cfg.delete_delivery_channel(DeliveryChannelName=CHANNEL_NAME)
                    cfg.delete_configuration_recorder(ConfigurationRecorderName=RECORDER_NAME)
                    s3 = _c(connector, "s3", "us-east-1")
                    objs = s3.list_objects_v2(Bucket=b).get("Contents", [])
                    if objs:
                        s3.delete_objects(Bucket=b, Delete={"Objects": [{"Key": o["Key"]} for o in objs]})
                    s3.delete_bucket(Bucket=b)
                await _run(_undo_cfg)

            undone.append({"service": svc, "region": region, "rolled_back": True})
        except Exception as e:
            logger.error("Rollback failed for %s/%s: %s", svc, region, e)
            undone.append({"service": svc, "region": region, "rolled_back": False, "error": str(e)})

    all_ok = all(u["rolled_back"] for u in undone)
    return {"rolled_back": all_ok, "undone": undone}
