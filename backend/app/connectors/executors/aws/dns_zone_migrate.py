# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


def _r53(creds):
    from ._client import get_boto3_client
    return get_boto3_client(creds, "route53")


def _wait_insync(client, change_id):
    for _ in range(60):
        resp = client.get_change(Id=change_id)
        if resp["ChangeInfo"]["Status"] == "INSYNC":
            return
        time.sleep(5)
    raise TimeoutError(f"Route53 change {change_id} did not reach INSYNC after 300s")


def _list_all_rrsets(client, zone_id):
    rrsets = []
    kwargs = {"HostedZoneId": zone_id, "MaxItems": "300"}
    while True:
        resp = client.list_resource_record_sets(**kwargs)
        rrsets.extend(resp["ResourceRecordSets"])
        if not resp["IsTruncated"]:
            break
        kwargs["StartRecordName"] = resp["NextRecordName"]
        kwargs["StartRecordType"] = resp["NextRecordType"]
        if resp.get("NextRecordIdentifier"):
            kwargs["StartRecordIdentifier"] = resp["NextRecordIdentifier"]
    return rrsets


def _get_zone_info(client, zone_id):
    resp = client.get_hosted_zone(Id=zone_id)
    zone = resp["HostedZone"]
    name = zone["Name"]
    is_private = zone.get("Config", {}).get("PrivateZone", False)
    ns = resp.get("DelegationSet", {}).get("NameServers", [])
    return name, is_private, ns


def _phase_preflight(client, source_zone_id, target_zone_id):
    source_name, source_private, _ = _get_zone_info(client, source_zone_id)
    target_name, target_private, target_ns = _get_zone_info(client, target_zone_id)
    rrsets = _list_all_rrsets(client, source_zone_id)
    return {
        "phase": "preflight",
        "status": "ok",
        "message": f"Source zone {source_name} has {len(rrsets)} record sets; target zone {target_name} NS: {target_ns}",
        "source_name": source_name,
        "source_private": source_private,
        "target_name": target_name,
        "rrsets": rrsets,
        "target_ns": target_ns,
    }


def _phase_lower_ttl(client, source_zone_id, rrsets, ttl_lower_value):
    original_ttls = {}
    changes = []
    for rrset in rrsets:
        rtype = rrset["Type"]
        if rtype in ("NS", "SOA") and rrset["Name"] == rrset.get("Name"):
            pass
        current_ttl = rrset.get("TTL")
        if current_ttl is None:
            continue
        key = f"{rrset['Name']}:{rtype}"
        original_ttls[key] = current_ttl
        if current_ttl <= ttl_lower_value:
            continue
        new_rrset = dict(rrset)
        new_rrset["TTL"] = ttl_lower_value
        changes.append({"Action": "UPSERT", "ResourceRecordSet": new_rrset})

    if changes:
        resp = client.change_resource_record_sets(
            HostedZoneId=source_zone_id,
            ChangeBatch={"Changes": changes},
        )
        _wait_insync(client, resp["ChangeInfo"]["Id"])

    return {
        "phase": "lower_ttl",
        "status": "ok",
        "message": f"Lowered TTL to {ttl_lower_value}s on {len(changes)} record sets",
        "original_ttls": original_ttls,
        "changes_applied": len(changes),
    }


def _phase_switch_ns(client, source_zone_id, source_name, original_ns_rrset, target_ns):
    new_ns_records = [{"Value": ns if ns.endswith(".") else ns + "."} for ns in target_ns]
    change = {
        "Action": "UPSERT",
        "ResourceRecordSet": {
            "Name": source_name,
            "Type": "NS",
            "TTL": original_ns_rrset.get("TTL", 172800),
            "ResourceRecords": new_ns_records,
        },
    }
    resp = client.change_resource_record_sets(
        HostedZoneId=source_zone_id,
        ChangeBatch={"Changes": [change]},
    )
    _wait_insync(client, resp["ChangeInfo"]["Id"])
    return {
        "phase": "switch_ns",
        "status": "ok",
        "message": f"NS records updated to {target_ns}",
    }


def _phase_verify(zone_name, target_ns, verify_resolvers):
    import dns.resolver
    bare_name = zone_name.rstrip(".")
    target_ns_bare = {ns.rstrip(".").lower() for ns in target_ns}
    results = {}
    failed = []
    for resolver_ip in verify_resolvers:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [resolver_ip]
        resolver.timeout = 5
        resolver.lifetime = 10
        try:
            answers = resolver.resolve(bare_name, "NS")
            seen = {str(r).rstrip(".").lower() for r in answers}
            matched = bool(seen & target_ns_bare)
            results[resolver_ip] = {"ns": list(seen), "matched": matched}
            if not matched:
                failed.append(resolver_ip)
        except Exception as exc:
            results[resolver_ip] = {"error": str(exc), "matched": False}
            failed.append(resolver_ip)

    status = "ok" if not failed else "partial"
    return {
        "phase": "verify",
        "status": status,
        "message": f"Verification complete; {len(verify_resolvers) - len(failed)}/{len(verify_resolvers)} resolvers confirmed new NS",
        "results": results,
        "failed_resolvers": failed,
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    source_zone_id = parameters["source_zone_id"]
    target_zone_id = parameters["target_zone_id"]
    ttl_lower_value = int(parameters.get("ttl_lower_value", 60))
    propagation_wait = int(parameters.get("propagation_wait_seconds", 120))
    verify_resolvers = parameters.get("verify_resolvers", ["8.8.8.8", "1.1.1.1", "9.9.9.9"])

    loop = asyncio.get_event_loop()
    client = _r53(creds)

    preflight = await loop.run_in_executor(None, _phase_preflight, client, source_zone_id, target_zone_id)
    rrsets = preflight.pop("rrsets")
    source_name = preflight["source_name"]
    source_private = preflight.pop("source_private", False)
    target_ns = preflight["target_ns"]

    original_ns_rrset = next((r for r in rrsets if r["Type"] == "NS" and r["Name"].rstrip(".") == source_name.rstrip(".")), None)
    original_ns_values = [rec["Value"] for rec in original_ns_rrset.get("ResourceRecords", [])] if original_ns_rrset else []

    lower_result = await loop.run_in_executor(None, _phase_lower_ttl, client, source_zone_id, rrsets, ttl_lower_value)
    original_ttls = lower_result["original_ttls"]

    await asyncio.sleep(propagation_wait)
    wait_phase = {"phase": "wait_propagation", "status": "ok", "message": f"Waited {propagation_wait}s for TTL propagation"}

    switch_result = await loop.run_in_executor(None, _phase_switch_ns, client, source_zone_id, source_name, original_ns_rrset or {}, target_ns)

    if source_private:
        verify_result = {
            "phase": "verify",
            "status": "ok",
            "message": "Private hosted zone — external DNS verification skipped; NS switch confirmed via Route53 API",
        }
    else:
        verify_result = await loop.run_in_executor(None, _phase_verify, source_name, target_ns, verify_resolvers)

    return {
        "phases": [preflight, lower_result, wait_phase, switch_result, verify_result],
        "rollback_data": {
            "source_zone_id": source_zone_id,
            "source_name": source_name,
            "original_ns_values": original_ns_values,
            "original_ns_ttl": original_ns_rrset.get("TTL", 172800) if original_ns_rrset else 172800,
            "original_ttls": original_ttls,
            "rrsets_snapshot": [
                {"Name": r["Name"], "Type": r["Type"], "TTL": r.get("TTL"), "ResourceRecords": r.get("ResourceRecords", [])}
                for r in rrsets if r.get("TTL") is not None
            ],
        },
        "summary": {
            "source_zone_id": source_zone_id,
            "target_zone_id": target_zone_id,
            "ns_switched_to": target_ns,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    rd = execution_result.get("rollback_data", {})
    source_zone_id = rd.get("source_zone_id") or parameters.get("source_zone_id")
    original_ns_values = rd.get("original_ns_values", [])
    original_ns_ttl = rd.get("original_ns_ttl", 172800)
    original_ttls = rd.get("original_ttls", {})
    rrsets_snapshot = rd.get("rrsets_snapshot", [])
    source_name = rd.get("source_name", "")

    creds = getattr(connector, "credentials", {})
    loop = asyncio.get_event_loop()
    client = _r53(creds)

    phases = []

    if original_ns_values:
        def _restore_ns():
            ns_records = [{"Value": v if v.endswith(".") else v + "."} for v in original_ns_values]
            resp = client.change_resource_record_sets(
                HostedZoneId=source_zone_id,
                ChangeBatch={"Changes": [{
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": source_name,
                        "Type": "NS",
                        "TTL": original_ns_ttl,
                        "ResourceRecords": ns_records,
                    },
                }]},
            )
            _wait_insync(client, resp["ChangeInfo"]["Id"])

        await loop.run_in_executor(None, _restore_ns)
        phases.append({"phase": "restore_ns", "status": "ok", "message": f"NS restored to {original_ns_values}"})

    if original_ttls and rrsets_snapshot:
        def _restore_ttls():
            changes = []
            for rrset in rrsets_snapshot:
                key = f"{rrset['Name']}:{rrset['Type']}"
                orig_ttl = original_ttls.get(key)
                if orig_ttl is None:
                    continue
                new_rrset = dict(rrset)
                new_rrset["TTL"] = orig_ttl
                changes.append({"Action": "UPSERT", "ResourceRecordSet": new_rrset})
            if changes:
                resp = client.change_resource_record_sets(
                    HostedZoneId=source_zone_id,
                    ChangeBatch={"Changes": changes},
                )
                _wait_insync(client, resp["ChangeInfo"]["Id"])
            return len(changes)

        n = await loop.run_in_executor(None, _restore_ttls)
        phases.append({"phase": "restore_ttls", "status": "ok", "message": f"Restored original TTLs on {n} record sets"})

    return {
        "rolled_back": True,
        "phases": phases,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
