# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""BIND DNS DNSSEC sign zone executor.

Signs a BIND zone using dnssec-keygen + dnssec-signzone via nexplane_agent run_command.
Steps run on the DNS server via agent job dispatch:
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
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_SAFE_PATH = re.compile(r'^[/A-Za-z0-9._\-]+$')
_SAFE_ZONE = re.compile(r'^[A-Za-z0-9.\-]+\.?$')


def _validate_params(zone, zone_file_path, key_directory, named_conf_path):
    if not _SAFE_ZONE.match(zone):
        raise ValueError(f"zone contains invalid characters: {zone!r}")
    for name, path in [("zone_file_path", zone_file_path), ("key_directory", key_directory), ("named_conf_path", named_conf_path)]:
        if not _SAFE_PATH.match(path):
            raise ValueError(f"{name} contains invalid characters: {path!r}")


async def _run(shell_cmd: str, asset_ids: list, timeout_seconds: int = 120) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _dispatch
    return await _dispatch(
        command="run_command",
        parameters={"command": shell_cmd, "timeout": float(timeout_seconds)},
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds + 30,
    )


def _check(r: dict, label: str):
    if r.get("exit_code", 0) != 0:
        raise RuntimeError(f"{label} failed (exit_code={r.get('exit_code')}): {r.get('output', '')}")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    zone = parameters["zone"].rstrip(".") + "."
    zone_file = parameters["zone_file_path"]
    key_dir = parameters.get("key_directory", "/var/named/keys")
    named_conf = parameters.get("named_conf_path", "/etc/named.conf")
    dry_run = parameters.get("dry_run", False)
    _validate_params(zone, zone_file, key_dir, named_conf)
    signed_zone_file = zone_file + ".signed"

    if dry_run:
        return {"status": "dry_run", "zone": zone}

    # 1. Create key directory
    _check(await _run(f"mkdir -p {key_dir} && chmod 700 {key_dir}", asset_ids), "mkdir key_dir")

    # 2. Generate KSK
    r = await _run(f"cd {key_dir} && dnssec-keygen -a ECDSAP256SHA256 -n ZONE -f KSK {zone}", asset_ids)
    _check(r, "dnssec-keygen KSK")
    raw_ksk_name = r.get("output", "").strip().splitlines()[-1].strip()
    ksk_name = re.sub(r"[^A-Za-z0-9._+\-]", "", raw_ksk_name)
    logger.info("bind_dns_dnssec_sign_zone: KSK generated: %s", ksk_name)

    # 3. Generate ZSK
    r = await _run(f"cd {key_dir} && dnssec-keygen -a ECDSAP256SHA256 -n ZONE {zone}", asset_ids)
    _check(r, "dnssec-keygen ZSK")
    raw_zsk_name = r.get("output", "").strip().splitlines()[-1].strip()
    zsk_name = re.sub(r"[^A-Za-z0-9._+\-]", "", raw_zsk_name)
    logger.info("bind_dns_dnssec_sign_zone: ZSK generated: %s", zsk_name)

    # 4. Sign the zone
    sign_cmd = (
        f"cd {key_dir} && dnssec-signzone -A -3 $(head -c 500 /dev/urandom | sha1sum | cut -b 1-16) "
        f"-N INCREMENT -o {zone} -t -d {key_dir} {zone_file} "
        f"{key_dir}/{ksk_name}.key {key_dir}/{zsk_name}.key"
    )
    r = await _run(sign_cmd, asset_ids, timeout_seconds=300)
    _check(r, "dnssec-signzone")
    sign_output = r.get("output", "")
    ds_records = [line.strip() for line in sign_output.splitlines() if " DS " in line]

    # 5. Update named.conf zone file pointer to signed file
    sed_cmd = f"sed -i 's|file \"{zone_file}\"|file \"{signed_zone_file}\"|g' {named_conf}"
    _check(await _run(sed_cmd, asset_ids), "update named.conf")

    # 6. Validate config
    r = await _run("named-checkconf", asset_ids)
    if r.get("exit_code", 0) != 0:
        revert_cmd = f"sed -i 's|file \"{signed_zone_file}\"|file \"{zone_file}\"|g' {named_conf}"
        await _run(revert_cmd, asset_ids)
        raise RuntimeError(f"named-checkconf failed: {r.get('output', '')}")

    # 7. Reload named
    _check(await _run("rndc reload", asset_ids), "rndc reload")
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
        "_asset_ids": list(asset_ids),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    zone = execution_result.get("zone", parameters.get("zone", ""))
    zone_file = execution_result.get("zone_file_path", parameters.get("zone_file_path", ""))
    signed_zone_file = execution_result.get("signed_zone_file", zone_file + ".signed")
    named_conf = execution_result.get("named_conf_path", parameters.get("named_conf_path", "/etc/named.conf"))
    asset_ids = execution_result.get("_asset_ids", [])

    revert_cmd = f"sed -i 's|file \"{signed_zone_file}\"|file \"{zone_file}\"|g' {named_conf}"
    r = await _run(revert_cmd, asset_ids)
    if r.get("exit_code", 0) != 0:
        logger.error("bind_dns_dnssec_sign_zone rollback: named.conf revert failed: %s", r.get("output"))

    await _run("named-checkconf", asset_ids)
    await _run("rndc reload", asset_ids)
    logger.info("bind_dns_dnssec_sign_zone rollback: zone %s reverted to unsigned", zone)

    return {
        "rolled_back": True,
        "zone": zone,
        "zone_file_path": zone_file,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
