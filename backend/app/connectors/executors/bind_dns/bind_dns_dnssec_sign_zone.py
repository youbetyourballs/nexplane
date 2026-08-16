# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""BIND DNS DNSSEC sign zone executor.

Signs a BIND zone using dnssec-keygen + dnssec-signzone via nexplane_agent.
Steps run as agent jobs on the DNS server over SSH:
  1. Create key directory if absent
  2. Generate KSK (key signing key) with dnssec-keygen -f KSK
  3. Generate ZSK (zone signing key) with dnssec-keygen
  4. Run dnssec-signzone to produce {zone_file}.signed
  5. Update named.conf zone file path to the signed file
  6. named-checkconf to validate
  7. rndc reload to apply

Rollback: update named.conf back to the original zone file and rndc reload.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def dispatch_agent_job(command: str, connector, timeout_seconds: int = 120) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _dispatch
    return await _dispatch(
        command=command,
        parameters={},
        asset_ids=[],
        connector=connector,
        timeout_seconds=timeout_seconds,
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    zone = parameters["zone"].rstrip(".") + "."
    zone_file = parameters["zone_file_path"]
    key_dir = parameters.get("key_directory", "/var/named/keys")
    named_conf = parameters.get("named_conf_path", "/etc/named.conf")
    dry_run = parameters.get("dry_run", False)
    signed_zone_file = zone_file + ".signed"

    if dry_run:
        return {"status": "dry_run", "zone": zone}

    # 1. Create key directory
    r = await dispatch_agent_job(f"mkdir -p {key_dir} && chmod 700 {key_dir}", connector)
    if r.get("status") != "success":
        raise RuntimeError(f"Failed to create key directory: {r.get('output')}")

    # 2. Generate KSK
    ksk_cmd = f"cd {key_dir} && dnssec-keygen -a ECDSAP256SHA256 -b 256 -n ZONE -f KSK {zone}"
    r = await dispatch_agent_job(ksk_cmd, connector)
    if r.get("status") != "success":
        raise RuntimeError(f"dnssec-keygen KSK failed: {r.get('output')}")
    ksk_name = r.get("output", "").strip().splitlines()[-1].strip()
    logger.info("bind_dns_dnssec_sign_zone: KSK generated: %s", ksk_name)

    # 3. Generate ZSK
    zsk_cmd = f"cd {key_dir} && dnssec-keygen -a ECDSAP256SHA256 -b 256 -n ZONE {zone}"
    r = await dispatch_agent_job(zsk_cmd, connector)
    if r.get("status") != "success":
        raise RuntimeError(f"dnssec-keygen ZSK failed: {r.get('output')}")
    zsk_name = r.get("output", "").strip().splitlines()[-1].strip()
    logger.info("bind_dns_dnssec_sign_zone: ZSK generated: %s", zsk_name)

    # 4. Sign the zone
    sign_cmd = (
        f"cd {key_dir} && dnssec-signzone -A -3 $(head -c 500 /dev/urandom | sha1sum | cut -b 1-16) "
        f"-N INCREMENT -o {zone} -t -d {key_dir} {zone_file} "
        f"{key_dir}/{ksk_name}.key {key_dir}/{zsk_name}.key"
    )
    r = await dispatch_agent_job(sign_cmd, connector, timeout_seconds=300)
    if r.get("status") != "success":
        raise RuntimeError(f"dnssec-signzone failed: {r.get('output')}")
    sign_output = r.get("output", "")

    # Extract DS records from signzone output
    ds_records = [line.strip() for line in sign_output.splitlines() if " DS " in line]

    # 5. Update named.conf zone file pointer to signed file
    sed_cmd = (
        f"sed -i 's|file \"{zone_file}\"|file \"{signed_zone_file}\"|g' {named_conf}"
    )
    r = await dispatch_agent_job(sed_cmd, connector)
    if r.get("status") != "success":
        raise RuntimeError(f"Failed to update named.conf: {r.get('output')}")

    # 6. Validate config
    r = await dispatch_agent_job("named-checkconf", connector)
    if r.get("status") != "success":
        # Revert named.conf before raising
        revert_cmd = f"sed -i 's|file \"{signed_zone_file}\"|file \"{zone_file}\"|g' {named_conf}"
        await dispatch_agent_job(revert_cmd, connector)
        raise RuntimeError(f"named-checkconf failed after signing: {r.get('output')}")

    # 7. Reload named
    r = await dispatch_agent_job("rndc reload", connector)
    if r.get("status") != "success":
        raise RuntimeError(f"rndc reload failed: {r.get('output')}")
    logger.info("bind_dns_dnssec_sign_zone: zone %s signed and reloaded", zone)

    return {
        "status": "signed",
        "zone": zone,
        "zone_file_path": zone_file,
        "signed_zone_file": signed_zone_file,
        "key_directory": key_dir,
        "ksk_name": ksk_name,
        "zsk_name": zsk_name,
        "ds_records": ds_records,
        "named_conf_path": named_conf,
        "signed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    zone = execution_result.get("zone", parameters.get("zone", ""))
    zone_file = execution_result.get("zone_file_path", parameters.get("zone_file_path", ""))
    signed_zone_file = execution_result.get("signed_zone_file", zone_file + ".signed")
    named_conf = execution_result.get("named_conf_path", parameters.get("named_conf_path", "/etc/named.conf"))

    # Revert named.conf to original unsigned zone file
    revert_cmd = f"sed -i 's|file \"{signed_zone_file}\"|file \"{zone_file}\"|g' {named_conf}"
    r = await dispatch_agent_job(revert_cmd, connector)
    if r.get("status") != "success":
        logger.error("bind_dns_dnssec_sign_zone rollback: named.conf revert failed: %s", r.get("output"))

    # Validate and reload
    await dispatch_agent_job("named-checkconf", connector)
    r = await dispatch_agent_job("rndc reload", connector)
    logger.info("bind_dns_dnssec_sign_zone rollback: zone %s reverted to unsigned", zone)

    return {
        "rolled_back": True,
        "zone": zone,
        "zone_file_path": zone_file,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
