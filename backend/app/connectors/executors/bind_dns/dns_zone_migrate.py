# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


def _make_tsig_key(creds):
    import dns.tsigkeyring
    import dns.tsig
    key_name = creds.get("tsig_key_name")
    key_secret = creds.get("tsig_key_secret")
    algorithm_name = creds.get("tsig_algorithm", "hmac-sha256")
    if not key_name or not key_secret:
        return None, None, None
    keyring = dns.tsigkeyring.from_text({key_name: key_secret})
    algorithm_map = {
        "hmac-sha256": dns.tsig.HMAC_SHA256,
        "hmac-sha512": dns.tsig.HMAC_SHA512,
        "hmac-md5": dns.tsig.HMAC_MD5,
    }
    algorithm = algorithm_map.get(algorithm_name.lower(), dns.tsig.HMAC_SHA256)
    return keyring, key_name, algorithm


def _send_update(server, port, zone_name, updates, keyring=None, keyname=None, algorithm=None):
    import dns.update
    import dns.query
    update = dns.update.Update(zone_name, keyring=keyring, keyname=keyname, keyalgorithm=algorithm)
    for op in updates:
        if op["op"] == "replace":
            update.replace(op["name"], op["ttl"], op["rdtype"], op["rdata"])
        elif op["op"] == "delete":
            update.delete(op["name"], op["rdtype"])
    response = dns.query.tcp(update, server, port=int(port), timeout=10)
    return response.rcode()


def _axfr_records(server, port, zone_name, keyring=None, keyname=None, algorithm=None):
    import dns.zone
    import dns.query
    z = dns.zone.from_xfr(dns.query.xfr(
        server, zone_name, port=int(port), timeout=30,
        keyring=keyring, keyname=keyname, keyalgorithm=algorithm,
    ))
    records = []
    for name, node in z.nodes.items():
        for rdataset in node.rdatasets:
            for rdata in rdataset:
                records.append({
                    "name": str(name),
                    "ttl": rdataset.ttl,
                    "rdtype": dns.rdatatype.to_text(rdataset.rdtype),
                    "rdata": rdata.to_text(),
                })
    return records


def _phase_preflight(creds, zone_name):
    server = creds["server"]
    port = creds.get("port", "53")
    keyring, keyname, algorithm = _make_tsig_key(creds)
    records = _axfr_records(server, port, zone_name, keyring, keyname, algorithm)
    ns_records = [r for r in records if r["rdtype"] == "NS" and r["name"] in ("@", zone_name.rstrip("."), "")]
    return {
        "phase": "preflight",
        "status": "ok",
        "message": f"Zone {zone_name} has {len(records)} records; current NS count: {len(ns_records)}",
        "records": records,
        "current_ns": ns_records,
    }


def _phase_lower_ttl(creds, zone_name, records, ttl_lower_value):
    server = creds["server"]
    port = creds.get("port", "53")
    keyring, keyname, algorithm = _make_tsig_key(creds)
    original_ttls = {}
    updates = []
    for rec in records:
        key = f"{rec['name']}:{rec['rdtype']}"
        if key not in original_ttls:
            original_ttls[key] = rec["ttl"]
        if rec["ttl"] <= ttl_lower_value:
            continue
        updates.append({
            "op": "replace",
            "name": rec["name"],
            "ttl": ttl_lower_value,
            "rdtype": rec["rdtype"],
            "rdata": rec["rdata"],
        })
    if updates:
        _send_update(server, port, zone_name, updates, keyring, keyname, algorithm)
    return {
        "phase": "lower_ttl",
        "status": "ok",
        "message": f"Lowered TTL to {ttl_lower_value}s on {len(updates)} records",
        "original_ttls": original_ttls,
    }


def _phase_switch_ns(creds, zone_name, target_nameservers):
    server = creds["server"]
    port = creds.get("port", "53")
    keyring, keyname, algorithm = _make_tsig_key(creds)
    updates = [{"op": "delete", "name": "@", "rdtype": "NS"}]
    for ns in target_nameservers:
        ns_fqdn = ns if ns.endswith(".") else ns + "."
        updates.append({"op": "replace", "name": "@", "ttl": 300, "rdtype": "NS", "rdata": ns_fqdn})
    _send_update(server, port, zone_name, updates, keyring, keyname, algorithm)
    return {
        "phase": "switch_ns",
        "status": "ok",
        "message": f"NS records switched to {target_nameservers}",
    }


def _phase_verify(zone_name, target_nameservers, verify_resolvers):
    import dns.resolver
    bare_name = zone_name.rstrip(".")
    target_ns_bare = {ns.rstrip(".").lower() for ns in target_nameservers}
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
    zone_name = parameters.get("source_zone") or creds.get("zone")
    target_nameservers = parameters["target_nameservers"]
    ttl_lower_value = int(parameters.get("ttl_lower_value", 60))
    propagation_wait = int(parameters.get("propagation_wait_seconds", 120))
    verify_resolvers = parameters.get("verify_resolvers", ["8.8.8.8", "1.1.1.1"])

    loop = asyncio.get_event_loop()

    preflight = await loop.run_in_executor(None, _phase_preflight, creds, zone_name)
    records = preflight.pop("records")
    current_ns = preflight.get("current_ns", [])

    lower_result = await loop.run_in_executor(None, _phase_lower_ttl, creds, zone_name, records, ttl_lower_value)
    original_ttls = lower_result["original_ttls"]

    await asyncio.sleep(propagation_wait)
    wait_phase = {"phase": "wait_propagation", "status": "ok", "message": f"Waited {propagation_wait}s for TTL propagation"}

    switch_result = await loop.run_in_executor(None, _phase_switch_ns, creds, zone_name, target_nameservers)

    verify_result = await loop.run_in_executor(None, _phase_verify, zone_name, target_nameservers, verify_resolvers)

    return {
        "phases": [preflight, lower_result, wait_phase, switch_result, verify_result],
        "rollback_data": {
            "zone_name": zone_name,
            "original_ns": [r["rdata"] for r in current_ns],
            "original_ns_ttl": current_ns[0]["ttl"] if current_ns else 300,
            "original_ttls": original_ttls,
            "records_snapshot": records,
        },
        "summary": {
            "zone_name": zone_name,
            "ns_switched_to": target_nameservers,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    rd = execution_result.get("rollback_data", {})
    creds = getattr(connector, "credentials", {})
    zone_name = rd.get("zone_name") or parameters.get("source_zone") or creds.get("zone")
    original_ns = rd.get("original_ns", [])
    original_ns_ttl = rd.get("original_ns_ttl", 300)
    original_ttls = rd.get("original_ttls", {})
    records_snapshot = rd.get("records_snapshot", [])

    loop = asyncio.get_event_loop()
    phases = []

    if original_ns:
        def _restore_ns():
            server = creds["server"]
            port = creds.get("port", "53")
            keyring, keyname, algorithm = _make_tsig_key(creds)
            updates = [{"op": "delete", "name": "@", "rdtype": "NS"}]
            for ns in original_ns:
                ns_fqdn = ns if ns.endswith(".") else ns + "."
                updates.append({"op": "replace", "name": "@", "ttl": original_ns_ttl, "rdtype": "NS", "rdata": ns_fqdn})
            _send_update(server, port, zone_name, updates, keyring, keyname, algorithm)

        await loop.run_in_executor(None, _restore_ns)
        phases.append({"phase": "restore_ns", "status": "ok", "message": f"NS restored to {original_ns}"})

    if original_ttls and records_snapshot:
        def _restore_ttls():
            server = creds["server"]
            port = creds.get("port", "53")
            keyring, keyname, algorithm = _make_tsig_key(creds)
            updates = []
            seen_keys = set()
            for rec in records_snapshot:
                key = f"{rec['name']}:{rec['rdtype']}"
                orig_ttl = original_ttls.get(key)
                if orig_ttl is None or key in seen_keys:
                    continue
                seen_keys.add(key)
                updates.append({
                    "op": "replace",
                    "name": rec["name"],
                    "ttl": orig_ttl,
                    "rdtype": rec["rdtype"],
                    "rdata": rec["rdata"],
                })
            if updates:
                _send_update(server, port, zone_name, updates, keyring, keyname, algorithm)
            return len(updates)

        n = await loop.run_in_executor(None, _restore_ttls)
        phases.append({"phase": "restore_ttls", "status": "ok", "message": f"Restored original TTLs on {n} records"})

    return {
        "rolled_back": True,
        "phases": phases,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
