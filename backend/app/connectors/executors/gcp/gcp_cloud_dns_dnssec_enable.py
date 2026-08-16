# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GCP Cloud DNS DNSSEC enable executor.

Enables DNSSEC signing on a Cloud DNS managed zone. GCP auto-manages
KSK and ZSK — no external KMS required.

Rollback: patch zone dnssecConfig.state back to "off".
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    zone_name = parameters["zone_name"]
    dry_run = parameters.get("dry_run", False)

    def _get_zone():
        from googleapiclient.discovery import build
        from app.connectors.executors.gcp._client import get_credentials, get_project_id
        credentials = get_credentials(creds)
        project_id = get_project_id(creds)
        dns = build("dns", "v1", credentials=credentials)
        zone = dns.managedZones().get(project=project_id, managedZone=zone_name).execute()
        return zone, project_id, credentials

    zone, project_id, credentials = await _run(_get_zone)
    current_state = zone.get("dnssecConfig", {}).get("state", "off")

    if current_state == "on":
        def _get_keys():
            from googleapiclient.discovery import build
            dns = build("dns", "v1", credentials=credentials)
            return dns.dnsKeys().list(project=project_id, managedZone=zone_name).execute()
        keys_resp = await _run(_get_keys)
        ds_records = _extract_ds_records(keys_resp.get("dnsKeys", []))
        logger.info("gcp_cloud_dns_dnssec_enable: zone %s already signed — no-op", zone_name)
        return {
            "status": "signed",
            "already_enabled": True,
            "zone_name": zone_name,
            "ds_records": ds_records,
            "enabled_at": datetime.now(timezone.utc).isoformat(),
        }

    if dry_run:
        return {"status": "dry_run", "zone_name": zone_name}

    def _patch_enable():
        from googleapiclient.discovery import build
        dns = build("dns", "v1", credentials=credentials)
        return dns.managedZones().patch(
            project=project_id,
            managedZone=zone_name,
            body={"dnssecConfig": {"state": "on"}},
        ).execute()

    await _run(_patch_enable)
    logger.info("gcp_cloud_dns_dnssec_enable: DNSSEC enabled for zone %s", zone_name)

    # Poll until state == "on" and KSK available (up to 120s)
    deadline = time.time() + 120
    ds_records = []
    while time.time() < deadline:
        await asyncio.sleep(5)

        def _poll():
            from googleapiclient.discovery import build
            dns = build("dns", "v1", credentials=credentials)
            z = dns.managedZones().get(project=project_id, managedZone=zone_name).execute()
            keys = dns.dnsKeys().list(project=project_id, managedZone=zone_name).execute()
            return z, keys

        polled_zone, polled_keys = await _run(_poll)
        if polled_zone.get("dnssecConfig", {}).get("state") == "on":
            ds_records = _extract_ds_records(polled_keys.get("dnsKeys", []))
            break
    else:
        raise TimeoutError(f"GCP Cloud DNS zone {zone_name} did not reach DNSSEC state 'on' within 120s")

    return {
        "status": "signed",
        "already_enabled": False,
        "zone_name": zone_name,
        "ds_records": ds_records,
        "enabled_at": datetime.now(timezone.utc).isoformat(),
    }


def _extract_ds_records(dns_keys: list) -> list:
    records = []
    for key in dns_keys:
        if key.get("type") == "keySigning":
            for digest in key.get("dsDigests", []):
                records.append({
                    "key_tag": key.get("keyTag"),
                    "algorithm": key.get("algorithm"),
                    "digest_type": digest.get("type"),
                    "digest": digest.get("digest"),
                })
    return records


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    if execution_result.get("already_enabled"):
        return {"rolled_back": False, "reason": "DNSSEC was already enabled before execution — nothing to undo"}

    creds = connector.credentials
    zone_name = parameters["zone_name"]

    def _patch_disable():
        from googleapiclient.discovery import build
        from app.connectors.executors.gcp._client import get_credentials, get_project_id
        credentials = get_credentials(creds)
        project_id = get_project_id(creds)
        dns = build("dns", "v1", credentials=credentials)
        return dns.managedZones().patch(
            project=project_id,
            managedZone=zone_name,
            body={"dnssecConfig": {"state": "off"}},
        ).execute()

    await _run(_patch_disable)
    logger.info("gcp_cloud_dns_dnssec_enable rollback: DNSSEC disabled for zone %s", zone_name)

    return {
        "rolled_back": True,
        "zone_name": zone_name,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
