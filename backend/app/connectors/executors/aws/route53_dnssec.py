# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS Route53 DNSSEC Enable executor.

Enables DNSSEC signing on a Route53 public hosted zone by:
  1. Resolving or creating a KMS key in us-east-1 (ECC_NIST_P256 / SIGN_VERIFY)
  2. Creating a Key Signing Key (KSK) attached to that KMS key
  3. Calling enable_hosted_zone_dnssec
  4. Polling until status == SIGNING and extracting the DS record

Rollback:
  1. disable_hosted_zone_dnssec
  2. Deactivate KSK
  3. Delete KSK
  4. Schedule KMS key deletion (7-day pending window) if Nexplane created it
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

# KMS for Route53 DNSSEC must be us-east-1
_KMS_REGION = "us-east-1"
_DNSSEC_PRINCIPAL = "dnssec-route53.amazonaws.com"


def _boto(connector, service, region=None):
    from app.connectors.executors.aws.reference_scan import _boto_client
    return _boto_client(service, connector, region or "us-east-1")


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


# ---------------------------------------------------------------------------
# KMS helpers
# ---------------------------------------------------------------------------

def _find_existing_key(kms, zone_id: str):
    """Search for a KMS key tagged nexplane-dnssec-{zone_id}."""
    paginator = kms.get_paginator("list_keys")
    for page in paginator.paginate():
        for key_meta in page["Keys"]:
            key_id = key_meta["KeyId"]
            try:
                tags_resp = kms.list_resource_tags(KeyId=key_id)
                tags = {t["TagKey"]: t["TagValue"] for t in tags_resp.get("Tags", [])}
                if (
                    tags.get("nexplane-dnssec-zone") == zone_id
                    and tags.get("ManagedBy") == "nexplane"
                ):
                    desc = kms.describe_key(KeyId=key_id)
                    state = desc["KeyMetadata"]["KeyState"]
                    if state == "Enabled":
                        return key_id, desc["KeyMetadata"]["Arn"]
            except Exception:
                continue
    return None, None


def _create_dnssec_key(kms, zone_id: str, zone_name: str) -> tuple[str, str]:
    """Create ECC_NIST_P256 KMS key with Route53 DNSSEC key policy."""
    account_id = kms.meta.client_id if hasattr(kms.meta, "client_id") else None
    # Build key policy that allows Route53 DNSSEC to use it
    key_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Enable IAM User Permissions",
                "Effect": "Allow",
                "Principal": {"AWS": "*"},
                "Action": "kms:*",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "kms:CallerAccount": "${aws:PrincipalAccount}"
                    }
                }
            },
            {
                "Sid": "Allow Route53 DNSSEC Service",
                "Effect": "Allow",
                "Principal": {"Service": _DNSSEC_PRINCIPAL},
                "Action": [
                    "kms:DescribeKey",
                    "kms:GetPublicKey",
                    "kms:Sign"
                ],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "aws:SourceAccount": "${aws:PrincipalAccount}"
                    }
                }
            }
        ]
    })

    resp = kms.create_key(
        Description=f"Nexplane DNSSEC key for zone {zone_name} ({zone_id})",
        KeyUsage="SIGN_VERIFY",
        KeySpec="ECC_NIST_P256",
        Policy=key_policy,
    )
    key_id = resp["KeyMetadata"]["KeyId"]
    key_arn = resp["KeyMetadata"]["Arn"]

    kms.tag_resource(
        KeyId=key_id,
        Tags=[
            {"TagKey": "ManagedBy", "TagValue": "nexplane"},
            {"TagKey": "nexplane-dnssec-zone", "TagValue": zone_id},
            {"TagKey": "Purpose", "TagValue": "route53-dnssec"},
        ],
    )
    return key_id, key_arn


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    zone_id = parameters["zone_id"]
    kms_key_id_param = parameters.get("kms_key_id", "auto")
    ksk_name = parameters.get("ksk_name", "nexplane-ksk")
    algorithm = parameters.get("key_signing_algorithm", "ECDSAP256SHA256")
    dry_run = parameters.get("dry_run", False)

    r53 = _boto(connector, "route53")
    kms = _boto(connector, "kms", _KMS_REGION)

    # ------------------------------------------------------------------
    # Preflight: zone exists?
    # ------------------------------------------------------------------
    zone_resp = await _run(lambda: r53.get_hosted_zone(Id=zone_id))
    zone_name = zone_resp["HostedZone"]["Name"]
    logger.info("route53_dnssec_enable: zone %s (%s)", zone_id, zone_name)

    # Check existing DNSSEC status
    dnssec_resp = await _run(lambda: r53.get_dnssec(HostedZoneId=zone_id))
    current_status = dnssec_resp.get("Status", {}).get("ServeSignature", "NOT_SIGNING")
    if current_status == "SIGNING":
        # Already enabled — return current state without touching anything
        ksks = dnssec_resp.get("KeySigningKeys", [])
        ds_record = ksks[0].get("DSRecord", "") if ksks else ""
        key_tag = ksks[0].get("KeyTag", 0) if ksks else 0
        ksk_n = ksks[0].get("Name", "") if ksks else ""
        logger.info("route53_dnssec_enable: zone already SIGNING — no-op")
        return {
            "status": "signing",
            "already_enabled": True,
            "zone_id": zone_id,
            "zone_name": zone_name,
            "ksk_name": ksk_n,
            "kms_key_arn": ksks[0].get("KmsArn", "") if ksks else "",
            "kms_key_created_by_nexplane": False,
            "ds_record": ds_record,
            "key_tag": key_tag,
            "registrar_instructions": (
                f"Add the following DS record at your registrar for {zone_name}:\n{ds_record}"
            ),
            "enabled_at": datetime.now(timezone.utc).isoformat(),
        }

    if dry_run:
        logger.info("route53_dnssec_enable: dry_run=True — skipping KMS/KSK creation")
        return {"status": "dry_run", "zone_id": zone_id, "zone_name": zone_name}

    # ------------------------------------------------------------------
    # Resolve or create KMS key
    # ------------------------------------------------------------------
    kms_key_created = False
    if kms_key_id_param == "auto":
        existing_id, existing_arn = await _run(lambda: _find_existing_key(kms, zone_id))
        if existing_id:
            kms_key_id = existing_id
            kms_key_arn = existing_arn
            logger.info("route53_dnssec_enable: reusing existing KMS key %s", kms_key_arn)
        else:
            kms_key_id, kms_key_arn = await _run(lambda: _create_dnssec_key(kms, zone_id, zone_name))
            kms_key_created = True
            logger.info("route53_dnssec_enable: created KMS key %s", kms_key_arn)
    else:
        kms_key_id = kms_key_id_param
        resp = await _run(lambda: kms.describe_key(KeyId=kms_key_id))
        kms_key_arn = resp["KeyMetadata"]["Arn"]
        logger.info("route53_dnssec_enable: using provided KMS key %s", kms_key_arn)

    # ------------------------------------------------------------------
    # Create KSK
    # ------------------------------------------------------------------
    caller_ref = f"nexplane-{zone_id}-{int(time.time())}"
    await _run(lambda: r53.create_key_signing_key(
        CallerReference=caller_ref,
        HostedZoneId=zone_id,
        KeyManagementServiceArn=kms_key_arn,
        Name=ksk_name,
        Status="ACTIVE",
    ))
    logger.info("route53_dnssec_enable: KSK %s created", ksk_name)

    # ------------------------------------------------------------------
    # Enable DNSSEC signing
    # ------------------------------------------------------------------
    await _run(lambda: r53.enable_hosted_zone_dnssec(HostedZoneId=zone_id))
    logger.info("route53_dnssec_enable: enable_hosted_zone_dnssec called")

    # ------------------------------------------------------------------
    # Poll until SIGNING (up to 300s)
    # ------------------------------------------------------------------
    deadline = time.time() + 300
    ds_record = ""
    key_tag = 0
    while time.time() < deadline:
        await asyncio.sleep(10)
        status_resp = await _run(lambda: r53.get_dnssec(HostedZoneId=zone_id))
        sig_status = status_resp.get("Status", {}).get("ServeSignature", "")
        logger.info("route53_dnssec_enable: polling status=%s", sig_status)
        if sig_status == "SIGNING":
            ksks = status_resp.get("KeySigningKeys", [])
            if ksks:
                ds_record = ksks[0].get("DSRecord", "")
                key_tag = ksks[0].get("KeyTag", 0)
            break
    else:
        raise TimeoutError(
            f"Route53 DNSSEC for zone {zone_id} did not reach SIGNING within 300s"
        )

    enabled_at = datetime.now(timezone.utc).isoformat()
    registrar_instructions = (
        f"Add the following DS record at your registrar for {zone_name}:\n{ds_record}"
    )

    return {
        "status": "signing",
        "already_enabled": False,
        "zone_id": zone_id,
        "zone_name": zone_name,
        "ksk_name": ksk_name,
        "kms_key_arn": kms_key_arn,
        "kms_key_created_by_nexplane": kms_key_created,
        "ds_record": ds_record,
        "key_tag": key_tag,
        "registrar_instructions": registrar_instructions,
        "enabled_at": enabled_at,
    }


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    zone_id = execution_result.get("zone_id") or parameters["zone_id"]
    ksk_name = execution_result.get("ksk_name", parameters.get("ksk_name", "nexplane-ksk"))
    kms_key_arn = execution_result.get("kms_key_arn", "")
    kms_key_created = execution_result.get("kms_key_created_by_nexplane", False)

    r53 = _boto(connector, "route53")
    kms = _boto(connector, "kms", _KMS_REGION)

    errors = []

    # 1. Disable DNSSEC signing
    try:
        await _run(lambda: r53.disable_hosted_zone_dnssec(HostedZoneId=zone_id))
        logger.info("route53_dnssec_enable rollback: disabled DNSSEC for %s", zone_id)
    except r53.exceptions.DNSSECNotFound:
        logger.info("route53_dnssec_enable rollback: DNSSEC already disabled")
    except Exception as exc:
        logger.error("route53_dnssec_enable rollback: disable_hosted_zone_dnssec failed: %s", exc)
        errors.append(str(exc))

    # 2. Deactivate KSK
    if ksk_name:
        try:
            await _run(lambda: r53.update_key_signing_key(
                HostedZoneId=zone_id,
                Name=ksk_name,
                Status="INACTIVE",
            ))
            logger.info("route53_dnssec_enable rollback: KSK %s deactivated", ksk_name)
        except Exception as exc:
            logger.warning("route53_dnssec_enable rollback: deactivate KSK: %s", exc)

        # 3. Delete KSK
        try:
            await _run(lambda: r53.delete_key_signing_key(
                HostedZoneId=zone_id,
                Name=ksk_name,
            ))
            logger.info("route53_dnssec_enable rollback: KSK %s deleted", ksk_name)
        except Exception as exc:
            logger.warning("route53_dnssec_enable rollback: delete KSK: %s", exc)

    # 4. Schedule KMS key deletion if Nexplane created it
    if kms_key_created and kms_key_arn:
        try:
            await _run(lambda: kms.schedule_key_deletion(
                KeyId=kms_key_arn,
                PendingWindowInDays=7,
            ))
            logger.info(
                "route53_dnssec_enable rollback: KMS key %s scheduled for deletion in 7 days",
                kms_key_arn,
            )
        except Exception as exc:
            logger.warning("route53_dnssec_enable rollback: schedule_key_deletion: %s", exc)

    return {
        "rolled_back": True,
        "zone_id": zone_id,
        "ksk_name": ksk_name,
        "kms_key_deletion_scheduled": kms_key_created,
        "errors": errors,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
