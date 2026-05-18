# BIND DNS Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a BIND / RFC 2136 DNS connector with catalog, client, four executors, and a smoke test phase (BIND_DNS) that provisions BIND9 on EC2, tests all four actions, and caches the instance as an AMI.

**Architecture:** Follows the exact executor-per-file pattern used by `ldap` — one `_client.py` with shared helpers, one file per action. The smoke phase is split into two parts: `_run_bind_dns_tests()` (cloud-agnostic: registers connector, runs 5 CRs, cleans up) and `run_phase_bind_dns()` (decides whether to auto-provision BIND9 on AWS EC2 with AMI caching, or use a pre-existing server passed via `--bind-server-ip`). This makes the phase usable from GCP, Azure, and on-prem environments without any AWS dependency.

**Tech Stack:** Python, `dnspython>=2.4` (already in requirements.txt), boto3/SSM for smoke infra, BIND9 on Amazon Linux 2023.

---

## File Map

| File | Create/Modify | Purpose |
|------|--------------|---------|
| `backend/app/connectors/catalog/bind_dns.json` | Create | Connector catalog definition |
| `backend/app/connectors/executors/bind_dns/__init__.py` | Create | Empty package marker |
| `backend/app/connectors/executors/bind_dns/_client.py` | Create | TSIG keyring + update helpers |
| `backend/app/connectors/executors/bind_dns/list_zone.py` | Create | AXFR zone transfer executor |
| `backend/app/connectors/executors/bind_dns/create_record.py` | Create | RFC 2136 add executor |
| `backend/app/connectors/executors/bind_dns/delete_record.py` | Create | RFC 2136 delete executor |
| `backend/app/connectors/executors/bind_dns/check_record.py` | Create | Resolver query executor |
| `backend/tests/smoke/test_aws_live.py` | Modify | Add `_run_bind_dns_tests` + `run_phase_bind_dns` + wire into `main()` with `--bind-server-ip` args |

---

### Task 1: Catalog JSON

**Files:**
- Create: `backend/app/connectors/catalog/bind_dns.json`

- [ ] **Step 1: Write the catalog file**

```json
{
  "connector_type": "bind_dns",
  "display_name": "BIND / RFC 2136 DNS",
  "credential_fields": [
    {"name": "server", "label": "DNS Server Hostname/IP", "type": "string", "required": true},
    {"name": "port", "label": "Port", "type": "string", "required": false, "default": "53"},
    {"name": "zone", "label": "Default Zone", "type": "string", "required": true, "description": "e.g. corp.example.com"},
    {"name": "tsig_key_name", "label": "TSIG Key Name", "type": "string", "required": false, "description": "Required for authenticated updates"},
    {"name": "tsig_key_secret", "label": "TSIG Key Secret (base64)", "type": "password", "required": false},
    {"name": "tsig_algorithm", "label": "TSIG Algorithm", "type": "string", "required": false, "default": "hmac-sha256"}
  ],
  "actions": [
    {
      "action_id": "list_zone",
      "generic_action": "list_dns_records",
      "action_type": "ingest",
      "display_name": "List Zone Records",
      "description": "Lists all records in the DNS zone via AXFR zone transfer",
      "produces_asset_types": [],
      "applicable_asset_types": [],
      "executor": "bind_dns.list_zone",
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "none"
    },
    {
      "action_id": "create_record",
      "generic_action": "create_dns_record",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Create DNS Record",
      "description": "Creates an A, CNAME, or TXT record via RFC 2136 dynamic update",
      "applicable_asset_types": [],
      "parameters": [
        {"name": "record_name", "type": "string", "required": true, "description": "FQDN or relative name"},
        {"name": "record_type", "type": "string", "required": true, "description": "A, CNAME, TXT, MX"},
        {"name": "value", "type": "string", "required": true},
        {"name": "ttl", "type": "integer", "required": false, "default": 300},
        {"name": "zone", "type": "string", "required": false, "description": "Override default zone from credentials"}
      ],
      "executor": "bind_dns.create_record",
      "rollback_action": "delete_record",
      "estimated_duration_seconds": 5,
      "blast_radius_hint": "dns_change"
    },
    {
      "action_id": "delete_record",
      "generic_action": "delete_dns_record",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Delete DNS Record",
      "description": "Deletes a DNS record via RFC 2136 dynamic update",
      "applicable_asset_types": [],
      "parameters": [
        {"name": "record_name", "type": "string", "required": true},
        {"name": "record_type", "type": "string", "required": true},
        {"name": "zone", "type": "string", "required": false}
      ],
      "executor": "bind_dns.delete_record",
      "estimated_duration_seconds": 5,
      "blast_radius_hint": "dns_change"
    },
    {
      "action_id": "check_record",
      "generic_action": "check_dns_record",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Check DNS Record",
      "description": "Queries a specific record and returns its current value",
      "applicable_asset_types": [],
      "parameters": [
        {"name": "record_name", "type": "string", "required": true},
        {"name": "record_type", "type": "string", "required": false, "default": "A"}
      ],
      "executor": "bind_dns.check_record",
      "rollback_action": null,
      "estimated_duration_seconds": 5,
      "blast_radius_hint": "none"
    }
  ]
}
```

- [ ] **Step 2: Verify JSON is valid**

```bash
python -m json.tool backend/app/connectors/catalog/bind_dns.json
```
Expected: JSON pretty-printed with no errors.

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/catalog/bind_dns.json
git commit -m "feat: add bind_dns connector catalog"
```

---

### Task 2: Package init + client

**Files:**
- Create: `backend/app/connectors/executors/bind_dns/__init__.py`
- Create: `backend/app/connectors/executors/bind_dns/_client.py`

- [ ] **Step 1: Create empty `__init__.py`**

Content: empty file (zero bytes).

- [ ] **Step 2: Write `_client.py`**

```python
"""BIND/RFC-2136 DNS client helpers using dnspython."""
from __future__ import annotations
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.tsig
import dns.tsigkeyring
import dns.update


def get_keyring(creds: dict):
    """Build TSIG keyring from credentials. Returns (keyring, key_name) or (None, None)."""
    key_name = creds.get("tsig_key_name", "").strip()
    key_secret = creds.get("tsig_key_secret", "").strip()
    if not key_name or not key_secret:
        return None, None
    algorithm_map = {
        "hmac-sha256": dns.tsig.HMAC_SHA256,
        "hmac-sha512": dns.tsig.HMAC_SHA512,
        "hmac-md5": dns.tsig.HMAC_MD5,
    }
    algorithm = algorithm_map.get(
        creds.get("tsig_algorithm", "hmac-sha256"), dns.tsig.HMAC_SHA256
    )
    keyring = dns.tsigkeyring.from_text({key_name: key_secret})
    return keyring, key_name


def make_update(zone: str, creds: dict) -> dns.update.UpdateMessage:
    """Return a prepared UpdateMessage for the given zone, with TSIG if configured."""
    keyring, key_name = get_keyring(creds)
    update = dns.update.UpdateMessage(zone, keyring=keyring, keyname=key_name)
    return update


def send_update(update: dns.update.UpdateMessage, server: str, port: int) -> dns.message.Message:
    """Send a DNS UPDATE via TCP and raise on non-NOERROR rcode."""
    response = dns.query.tcp(update, server, port=port, timeout=10)
    rcode = response.rcode()
    if rcode != dns.rcode.NOERROR:
        raise RuntimeError(f"DNS update failed: {dns.rcode.to_text(rcode)}")
    return response


def _resolve_zone(parameters: dict, creds: dict) -> str:
    """Return zone from parameters (override) or connector credentials."""
    return (parameters.get("zone") or creds.get("zone") or "").strip()
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/bind_dns/__init__.py backend/app/connectors/executors/bind_dns/_client.py
git commit -m "feat: bind_dns executor package + client helpers"
```

---

### Task 3: list_zone executor

**Files:**
- Create: `backend/app/connectors/executors/bind_dns/list_zone.py`

- [ ] **Step 1: Write `list_zone.py`**

```python
"""List all records in a DNS zone via AXFR zone transfer."""
from __future__ import annotations
import dns.query
import dns.rdatatype
import dns.zone

from ._client import _resolve_zone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = _resolve_zone(parameters, creds)

    if not server or not zone:
        # No credentials — return mock so the executor can be called in tests without infra.
        return {"zone": "mock.local", "records": [], "count": 0}

    zone_obj = dns.zone.from_xfr(
        dns.query.xfr(server, zone, port=port, timeout=30)
    )
    records: list[dict] = []
    for name, node in zone_obj.nodes.items():
        for rdataset in node.rdatasets:
            for rdata in rdataset:
                records.append({
                    "name": str(name),
                    "type": dns.rdatatype.to_text(rdataset.rdtype),
                    "value": str(rdata),
                    "ttl": rdataset.ttl,
                })
    return {"zone": zone, "records": records, "count": len(records)}
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/connectors/executors/bind_dns/list_zone.py
git commit -m "feat: bind_dns list_zone executor (AXFR)"
```

---

### Task 4: create_record executor

**Files:**
- Create: `backend/app/connectors/executors/bind_dns/create_record.py`

- [ ] **Step 1: Write `create_record.py`**

```python
"""Create a DNS record via RFC 2136 dynamic update."""
from __future__ import annotations
from datetime import datetime, timezone

import dns.rdatatype

from ._client import _resolve_zone, make_update, send_update


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = _resolve_zone(parameters, creds)

    record_name: str = parameters["record_name"].strip()
    record_type: str = parameters["record_type"].strip().upper()
    value: str = parameters["value"].strip()
    ttl: int = int(parameters.get("ttl") or 300)

    if not server or not zone:
        return {
            "record_name": record_name, "record_type": record_type,
            "value": value, "ttl": ttl, "zone": zone or "mock.local",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "skipped", "reason": "no_dns_credentials",
        }

    rdtype = dns.rdatatype.from_text(record_type)
    update = make_update(zone, creds)
    # Prereq: record must not already exist (idempotency guard)
    update.absent(record_name, rdtype)
    update.add(record_name, ttl, rdtype, value)
    send_update(update, server, port)

    return {
        "record_name": record_name,
        "record_type": record_type,
        "value": value,
        "ttl": ttl,
        "zone": zone,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback a create by deleting the record that was just added."""
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = execution_result.get("zone") or _resolve_zone(parameters, creds)
    record_name = execution_result.get("record_name") or parameters.get("record_name", "")
    record_type = execution_result.get("record_type") or parameters.get("record_type", "A")

    if not server or not zone:
        return {"rolled_back": False, "reason": "no_dns_credentials"}

    rdtype = dns.rdatatype.from_text(record_type.upper())
    update = make_update(zone, creds)
    update.delete(record_name, rdtype)
    send_update(update, server, port)
    return {"rolled_back": True, "record_name": record_name, "record_type": record_type, "zone": zone}
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/connectors/executors/bind_dns/create_record.py
git commit -m "feat: bind_dns create_record executor + rollback"
```

---

### Task 5: delete_record executor

**Files:**
- Create: `backend/app/connectors/executors/bind_dns/delete_record.py`

- [ ] **Step 1: Write `delete_record.py`**

```python
"""Delete a DNS record via RFC 2136 dynamic update."""
from __future__ import annotations
from datetime import datetime, timezone

import dns.rdatatype
import dns.resolver

from ._client import _resolve_zone, make_update, send_update


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = _resolve_zone(parameters, creds)

    record_name: str = parameters["record_name"].strip()
    record_type: str = parameters["record_type"].strip().upper()

    if not server or not zone:
        return {
            "record_name": record_name, "record_type": record_type,
            "zone": zone or "mock.local", "previous_value": None,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "status": "skipped", "reason": "no_dns_credentials",
        }

    # Query current value before deleting (needed for rollback re-creation).
    fqdn = record_name if record_name.endswith(".") else f"{record_name}.{zone}"
    previous_value: str | None = None
    try:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [server]
        resolver.port = port
        answers = resolver.resolve(fqdn, record_type)
        previous_value = str(answers[0])
    except dns.resolver.NXDOMAIN:
        pass
    except Exception:
        pass

    rdtype = dns.rdatatype.from_text(record_type)
    update = make_update(zone, creds)
    update.delete(record_name, rdtype)
    send_update(update, server, port)

    return {
        "record_name": record_name,
        "record_type": record_type,
        "previous_value": previous_value,
        "zone": zone,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback a delete by re-creating the record with the captured previous value."""
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = execution_result.get("zone") or _resolve_zone(parameters, creds)
    record_name = execution_result.get("record_name") or parameters.get("record_name", "")
    record_type = execution_result.get("record_type") or parameters.get("record_type", "A")
    previous_value = execution_result.get("previous_value")

    if not server or not zone:
        return {"rolled_back": False, "reason": "no_dns_credentials"}
    if not previous_value:
        return {"rolled_back": False, "reason": "no_previous_value_captured"}

    rdtype = dns.rdatatype.from_text(record_type.upper())
    update = make_update(zone, creds)
    update.add(record_name, 300, rdtype, previous_value)
    send_update(update, server, port)
    return {
        "rolled_back": True,
        "record_name": record_name,
        "record_type": record_type,
        "value": previous_value,
        "zone": zone,
    }
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/connectors/executors/bind_dns/delete_record.py
git commit -m "feat: bind_dns delete_record executor + rollback"
```

---

### Task 6: check_record executor

**Files:**
- Create: `backend/app/connectors/executors/bind_dns/check_record.py`

- [ ] **Step 1: Write `check_record.py`**

```python
"""Query a specific DNS record and return its current value(s)."""
from __future__ import annotations

import dns.rdatatype
import dns.resolver

from ._client import _resolve_zone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = _resolve_zone(parameters, creds)

    record_name: str = parameters["record_name"].strip()
    record_type: str = (parameters.get("record_type") or "A").strip().upper()

    if not server:
        return {
            "record_name": record_name, "record_type": record_type,
            "values": [], "ttl": None, "exists": False,
            "status": "skipped", "reason": "no_dns_credentials",
        }

    fqdn = record_name if record_name.endswith(".") else (
        f"{record_name}.{zone}" if zone else record_name
    )

    resolver = dns.resolver.Resolver()
    resolver.nameservers = [server]
    resolver.port = port

    try:
        answers = resolver.resolve(fqdn, record_type)
        return {
            "record_name": record_name,
            "record_type": record_type,
            "values": [str(r) for r in answers],
            "ttl": answers.rrset.ttl if answers.rrset else None,
            "exists": True,
        }
    except dns.resolver.NXDOMAIN:
        return {
            "record_name": record_name,
            "record_type": record_type,
            "values": [],
            "ttl": None,
            "exists": False,
        }
    except dns.resolver.NoAnswer:
        return {
            "record_name": record_name,
            "record_type": record_type,
            "values": [],
            "ttl": None,
            "exists": False,
        }
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/connectors/executors/bind_dns/check_record.py
git commit -m "feat: bind_dns check_record executor"
```

---

### Task 7: Smoke phase BIND_DNS

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

This task adds two functions immediately before the `if __name__ == "__main__":` line:

1. `_run_bind_dns_tests(client, bind_server_ip, tsig_key_name, tsig_key_secret, instance_id_for_cleanup)` — cloud-agnostic test logic
2. `run_phase_bind_dns(client, cloud_account_id, bind_server_ip, tsig_key_name, tsig_key_secret)` — decides whether to auto-provision or use an external server

Then it adds three argparse args and updates the `main()` dispatch.

- [ ] **Step 1: Add `_run_bind_dns_tests` helper function**

Insert the following function immediately before the `if __name__ == "__main__":` line at the end of the file:

```python
def _run_bind_dns_tests(
    client: NexplaneClient,
    bind_server_ip: str,
    tsig_key_name: str,
    tsig_key_secret: str,
    instance_id_for_cleanup: Optional[str] = None,
) -> None:
    """Cloud-agnostic BIND DNS test sequence.

    Registers a bind_dns connector pointing at bind_server_ip, runs the
    5-step CR sequence (list_zone, create_record, check_record, delete_record,
    check_record), asserts each result, then deletes the connector.

    instance_id_for_cleanup: EC2 instance ID to terminate in the finally block.
    Pass None when the server was pre-provisioned externally (GCP/Azure/on-prem).
    """
    import time as _time

    connector_id: str = ""
    ec2_for_cleanup = _get_aws_boto3_client("ec2") if instance_id_for_cleanup else None

    try:
        # ------------------------------------------------------------------
        # Register bind_dns connector
        # ------------------------------------------------------------------
        log("BIND_DNS: registering bind_dns connector...")
        suffix = (instance_id_for_cleanup or bind_server_ip)[-8:].replace(".", "-")
        conn_resp = client.post("/connectors", json={
            "connector_type": "bind_dns",
            "name": f"nexplane-smoke-bind-{suffix}",
            "credentials": {
                "server": bind_server_ip,
                "port": "53",
                "zone": "smoke.nexplane.local",
                "tsig_key_name": tsig_key_name,
                "tsig_key_secret": tsig_key_secret,
                "tsig_algorithm": "hmac-sha256",
            },
        })
        connector_id = conn_resp.get("id") or conn_resp.get("connector_id")
        log(f"BIND_DNS: connector created — id={connector_id}")

        # ------------------------------------------------------------------
        # Step 1: list_zone — verify zone is queryable via AXFR
        # ------------------------------------------------------------------
        log("BIND_DNS: step 1 — list_zone")
        cr = client.run_cr(
            "[BIND_DNS] list_zone", "list_zone", connector_id,
            {"zone": "smoke.nexplane.local"},
        )
        result = client.get_cr_step_result(cr)
        assert result.get("zone") == "smoke.nexplane.local", (
            f"BIND_DNS: list_zone zone mismatch: {result}"
        )
        log(f"BIND_DNS: list_zone passed — {result.get('count', 0)} records")

        # ------------------------------------------------------------------
        # Step 2: create_record — A 10.0.0.42
        # ------------------------------------------------------------------
        log("BIND_DNS: step 2 — create_record nexplane-test A 10.0.0.42")
        cr2 = client.run_cr(
            "[BIND_DNS] create_record", "create_record", connector_id,
            {
                "record_name": "nexplane-test",
                "record_type": "A",
                "value": "10.0.0.42",
                "ttl": 60,
                "zone": "smoke.nexplane.local",
            },
        )
        result2 = client.get_cr_step_result(cr2)
        assert result2.get("record_name") == "nexplane-test", (
            f"BIND_DNS: create_record result unexpected: {result2}"
        )
        log("BIND_DNS: create_record passed")

        # ------------------------------------------------------------------
        # Step 3: check_record — verify A = 10.0.0.42
        # ------------------------------------------------------------------
        log("BIND_DNS: step 3 — check_record (expect 10.0.0.42)")
        cr3 = client.run_cr(
            "[BIND_DNS] check_record (after create)", "check_record", connector_id,
            {"record_name": "nexplane-test", "record_type": "A"},
        )
        result3 = client.get_cr_step_result(cr3)
        assert result3.get("exists") is True, (
            f"BIND_DNS: record should exist after create: {result3}"
        )
        assert "10.0.0.42" in result3.get("values", []), (
            f"BIND_DNS: expected 10.0.0.42 in values: {result3}"
        )
        log(f"BIND_DNS: check_record passed — values={result3.get('values')}")

        # ------------------------------------------------------------------
        # Step 4: delete_record
        # ------------------------------------------------------------------
        log("BIND_DNS: step 4 — delete_record nexplane-test A")
        cr4 = client.run_cr(
            "[BIND_DNS] delete_record", "delete_record", connector_id,
            {
                "record_name": "nexplane-test",
                "record_type": "A",
                "zone": "smoke.nexplane.local",
            },
        )
        result4 = client.get_cr_step_result(cr4)
        assert result4.get("record_name") == "nexplane-test", (
            f"BIND_DNS: delete_record result unexpected: {result4}"
        )
        log(f"BIND_DNS: delete_record passed — previous_value={result4.get('previous_value')}")

        # ------------------------------------------------------------------
        # Step 5: check_record — verify exists: False
        # ------------------------------------------------------------------
        log("BIND_DNS: step 5 — check_record (expect exists: False)")
        cr5 = client.run_cr(
            "[BIND_DNS] check_record (after delete)", "check_record", connector_id,
            {"record_name": "nexplane-test", "record_type": "A"},
        )
        result5 = client.get_cr_step_result(cr5)
        assert result5.get("exists") is False, (
            f"BIND_DNS: record should not exist after delete: {result5}"
        )
        log("BIND_DNS: check_record (post-delete) passed — exists=False")

        log("BIND_DNS: all steps passed")

    finally:
        # Delete connector
        if connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{connector_id}")
                log(f"BIND_DNS: deleted connector {connector_id}")
            except Exception as _ce:
                log(f"BIND_DNS: could not delete connector: {_ce}")
        # Terminate EC2 instance only if we launched it (not for external servers)
        if instance_id_for_cleanup and ec2_for_cleanup:
            try:
                ec2_for_cleanup.terminate_instances(InstanceIds=[instance_id_for_cleanup])
                log(f"BIND_DNS: terminated instance {instance_id_for_cleanup}")
            except Exception as _te:
                log(f"BIND_DNS: could not terminate instance: {_te}")
```

- [ ] **Step 2: Add `run_phase_bind_dns` function**

Insert immediately after `_run_bind_dns_tests` (still before `if __name__ == "__main__":`):

```python
# COST: ~$0.01/run (t3.small × 15min) when auto-provisioning on AWS — AMI cached after first run
def run_phase_bind_dns(
    client: NexplaneClient,
    cloud_account_id: str,
    bind_server_ip: str = "",
    bind_tsig_key_name: str = "",
    bind_tsig_key_secret: str = "",
) -> None:
    """Phase BIND_DNS: BIND9 / RFC 2136 DNS connector smoke test.

    If bind_server_ip is provided (non-empty) — skip all AWS infrastructure and
    call _run_bind_dns_tests directly. Use this path when the BIND server is
    pre-existing on GCP, Azure, or on-prem.

    If bind_server_ip is empty — launch a BIND9 server on AWS EC2 (t3.small,
    AMI-cached in SSM at /nexplane/smoke-amis/bind-dns/<hash[:8]>), extract
    TSIG credentials via SSM, call _run_bind_dns_tests, then terminate.
    """
    import hashlib
    import time as _time

    print("\n[Phase BIND_DNS] BIND9 / RFC 2136 DNS connector smoke test")

    # ------------------------------------------------------------------
    # PATH A: external server provided (GCP / Azure / on-prem)
    # ------------------------------------------------------------------
    if bind_server_ip:
        if not bind_tsig_key_name or not bind_tsig_key_secret:
            fail("[BIND_DNS] --bind-server-ip requires --bind-tsig-key-name and --bind-tsig-key-secret")
        log(f"BIND_DNS: using external server {bind_server_ip} (no AWS infra)")
        _run_bind_dns_tests(
            client,
            bind_server_ip=bind_server_ip,
            tsig_key_name=bind_tsig_key_name,
            tsig_key_secret=bind_tsig_key_secret,
            instance_id_for_cleanup=None,
        )
        return

    # ------------------------------------------------------------------
    # PATH B: auto-provision BIND9 on AWS EC2
    # ------------------------------------------------------------------
    ec2_client = _get_aws_boto3_client("ec2")
    ssm_boto = _get_aws_boto3_client("ssm")
    if not ec2_client or not ssm_boto:
        fail("[BIND_DNS] AWS credentials required for auto-provision (ec2 + ssm). "
             "Pass --bind-server-ip to use an external BIND server instead.")

    setup_script = r"""#!/bin/bash
set -e
dnf install -y bind bind-utils

mkdir -p /etc/named
tsig-keygen nexplane-smoke-key > /etc/named/nexplane-smoke.key
chmod 640 /etc/named/nexplane-smoke.key
chown root:named /etc/named/nexplane-smoke.key

PRIVATE_IP=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)
mkdir -p /var/named
cat > /var/named/smoke.nexplane.local.zone <<ZONEOF
\$ORIGIN smoke.nexplane.local.
\$TTL 300
@  IN  SOA  ns1.smoke.nexplane.local. admin.smoke.nexplane.local. (
   2026051701 3600 900 604800 300 )
@  IN  NS   ns1.smoke.nexplane.local.
ns1 IN  A   ${PRIVATE_IP}
ZONEOF
chown named:named /var/named/smoke.nexplane.local.zone
chmod 640 /var/named/smoke.nexplane.local.zone

cat > /etc/named.conf <<NAMEDEOF
options {
    directory "/var/named";
    recursion no;
    allow-query { any; };
    allow-transfer { none; };
    listen-on { any; };
    listen-on-v6 { none; };
};

include "/etc/named/nexplane-smoke.key";

zone "smoke.nexplane.local" {
    type master;
    file "/var/named/smoke.nexplane.local.zone";
    allow-update { key "nexplane-smoke-key"; };
    allow-transfer { any; };
};
NAMEDEOF

systemctl enable named
systemctl start named
sleep 2
systemctl is-active named
echo "BIND_READY"
"""
    setup_hash = hashlib.md5(setup_script.encode()).hexdigest()

    try:
        from run_on_ec2 import get_or_create_smoke_ami
    except ImportError:
        get_or_create_smoke_ami = None

    iam_client = _get_aws_boto3_client("iam")
    vpc_resp = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpc_resp:
        fail("[BIND_DNS] No default VPC found")
    vpc_id = vpc_resp[0]["VpcId"]
    subnets = ec2_client.describe_subnets(Filters=[{"Name": "vpcId", "Values": [vpc_id]}])["Subnets"]
    try:
        _offerings = ec2_client.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": ["t3.small"]}],
        )["InstanceTypeOfferings"]
        _supported_azs = {o["Location"] for o in _offerings}
        subnets = [s for s in subnets if s.get("AvailabilityZone") in _supported_azs] or subnets
    except Exception:
        pass
    subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
    subnet_id = subnets[0]["SubnetId"]

    AL2023_AMI = "ami-0953476d60561c955"

    launch_kwargs: dict = {
        "ImageId": AL2023_AMI,
        "InstanceType": "t3.small",
        "MinCount": 1, "MaxCount": 1,
        "TagSpecifications": [{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-bind-dns"},
            {"Key": "nexplane-smoke", "Value": "true"},
        ]}],
        "NetworkInterfaces": [{"DeviceIndex": 0, "SubnetId": subnet_id,
                                "AssociatePublicIpAddress": True}],
    }
    if iam_client:
        try:
            profiles = iam_client.list_instance_profiles(MaxItems=50)["InstanceProfiles"]
            for p in profiles:
                for r in p.get("Roles", []):
                    attached = iam_client.list_attached_role_policies(RoleName=r["RoleName"])["AttachedPolicies"]
                    if any("SSM" in pol["PolicyName"] or "SSM" in pol["PolicyArn"] for pol in attached):
                        launch_kwargs["IamInstanceProfile"] = {"Name": p["InstanceProfileName"]}
                        break
                if "IamInstanceProfile" in launch_kwargs:
                    break
        except Exception:
            pass

    instance_id: str = ""
    launched_fresh = False

    # Check for cached AMI
    cached_ami_id = None
    try:
        param = ssm_boto.get_parameter(Name=f"/nexplane/smoke-amis/bind-dns/{setup_hash[:8]}")
        cached_ami_id = param["Parameter"]["Value"].strip()
        log(f"BIND_DNS: found cached AMI {cached_ami_id} — launching from cache")
    except Exception:
        pass

    if cached_ami_id:
        launch_kwargs["ImageId"] = cached_ami_id
        resp = ec2_client.run_instances(**launch_kwargs)
        instance_id = resp["Instances"][0]["InstanceId"]
        log(f"BIND_DNS: launched from cached AMI: {instance_id}")
        # Wait for SSM availability
        ec2_client.get_waiter("instance_status_ok").wait(InstanceIds=[instance_id])
        deadline = _time.time() + 300
        while _time.time() < deadline:
            info = ssm_boto.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            if (info["InstanceInformationList"]
                    and info["InstanceInformationList"][0]["PingStatus"] == "Online"):
                break
            _time.sleep(10)
        else:
            fail("[BIND_DNS] Cached-AMI instance never came online in SSM")
    else:
        launched_fresh = True
        log("BIND_DNS: no cached AMI — launching fresh instance and installing BIND9...")
        resp = ec2_client.run_instances(**launch_kwargs)
        instance_id = resp["Instances"][0]["InstanceId"]
        log(f"BIND_DNS: instance {instance_id} — waiting for status OK...")
        ec2_client.get_waiter("instance_status_ok").wait(InstanceIds=[instance_id])

        log("BIND_DNS: waiting for SSM agent...")
        deadline = _time.time() + 300
        while _time.time() < deadline:
            info = ssm_boto.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            if (info["InstanceInformationList"]
                    and info["InstanceInformationList"][0]["PingStatus"] == "Online"):
                break
            _time.sleep(10)
        else:
            fail("[BIND_DNS] Instance never came online in SSM")

        log("BIND_DNS: running BIND9 setup script...")
        cmd_resp = ssm_boto.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [setup_script]},
            TimeoutSeconds=300,
        )
        cmd_id = cmd_resp["Command"]["CommandId"]
        deadline2 = _time.time() + 300
        while _time.time() < deadline2:
            _time.sleep(8)
            inv = ssm_boto.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
                if inv["Status"] != "Success":
                    # Terminate on setup failure before raising
                    try:
                        ec2_client.terminate_instances(InstanceIds=[instance_id])
                    except Exception:
                        pass
                    fail(f"[BIND_DNS] BIND9 setup failed: {inv.get('StandardErrorContent', '')}")
                break
            print(".", end="", flush=True)
        else:
            fail("[BIND_DNS] BIND9 setup timed out")

        # Cache instance as AMI for future runs
        if get_or_create_smoke_ami:
            try:
                get_or_create_smoke_ami(ssm_boto, ec2_client, instance_id, "bind-dns", setup_hash)
            except Exception as _ami_e:
                log(f"BIND_DNS: AMI caching skipped (non-fatal): {_ami_e}")

    # Get private IP
    inst_desc = ec2_client.describe_instances(InstanceIds=[instance_id])
    private_ip = inst_desc["Reservations"][0]["Instances"][0].get("PrivateIpAddress", "")
    if not private_ip:
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
        except Exception:
            pass
        fail("[BIND_DNS] Could not get private IP of BIND instance")
    log(f"BIND_DNS: BIND9 running at {private_ip}:53")

    # Read TSIG secret from key file via SSM
    read_key_resp = ssm_boto.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [
            "awk '/secret/ {gsub(/[\";\\ ]/,\"\",$2); print $2}' /etc/named/nexplane-smoke.key"
        ]},
        TimeoutSeconds=30,
    )
    read_cmd_id = read_key_resp["Command"]["CommandId"]
    tsig_secret_b64 = ""
    deadline3 = _time.time() + 60
    while _time.time() < deadline3:
        _time.sleep(5)
        key_inv = ssm_boto.get_command_invocation(
            CommandId=read_cmd_id, InstanceId=instance_id
        )
        if key_inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            tsig_secret_b64 = key_inv.get("StandardOutputContent", "").strip()
            break
    if not tsig_secret_b64:
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
        except Exception:
            pass
        fail("[BIND_DNS] Could not read TSIG key secret from instance")
    log(f"BIND_DNS: TSIG key secret retrieved ({len(tsig_secret_b64)} chars)")

    # Delegate the actual CR sequence to the cloud-agnostic helper.
    # Pass instance_id so the helper terminates it in its finally block.
    _run_bind_dns_tests(
        client,
        bind_server_ip=private_ip,
        tsig_key_name="nexplane-smoke-key",
        tsig_key_secret=tsig_secret_b64,
        instance_id_for_cleanup=instance_id,
    )
```

- [ ] **Step 3: Wire `BIND_DNS` into `main()` dispatch**

In `main()`, after the `if "AD_DC_INTEGRITY" in phases:` block (before the `print("\n" + "=" * 60)` success line), add:

```python
        if "BIND_DNS" in phases:
            run_phase_bind_dns(
                client, cloud_account_id,
                bind_server_ip=args.bind_server_ip,
                bind_tsig_key_name=args.bind_tsig_key_name,
                bind_tsig_key_secret=args.bind_tsig_key_secret,
            )
```

- [ ] **Step 4: Add three argparse arguments**

In `main()`, after the existing `parser.add_argument("--tailscale-auth-key", ...)` line (find the block of `parser.add_argument` calls), add:

```python
    parser.add_argument("--bind-server-ip", default="",
                        help="Pre-existing BIND server IP (GCP/Azure/on-prem). "
                             "When set, skips AWS EC2 provisioning.")
    parser.add_argument("--bind-tsig-key-name", default="",
                        help="TSIG key name for --bind-server-ip (required when --bind-server-ip is set)")
    parser.add_argument("--bind-tsig-key-secret", default="",
                        help="TSIG key secret (base64) for --bind-server-ip")
```

- [ ] **Step 5: Add `BIND_DNS` to `--phases` help text**

In the `parser.add_argument("--phases", ...)` call, append to the help string (after the last existing phase entry):

```
"BIND_DNS=BIND9/RFC-2136 DNS connector: list_zone/create_record/check_record/delete_record; "
"auto-provisions t3.small on AWS (AMI cached) or uses --bind-server-ip for external servers. "
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: smoke phase BIND_DNS — cloud-agnostic BIND9/RFC-2136 connector smoke test"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Covered by |
|-----------------|-----------|
| `bind_dns.json` catalog with all 6 credential fields | Task 1 |
| `list_zone` action with AXFR | Task 3 |
| `create_record` with absent prereq + rollback | Task 4 |
| `delete_record` capturing previous_value + rollback | Task 5 |
| `check_record` returns exists + values + ttl | Task 6 |
| `__init__.py` package marker | Task 2 |
| `_client.py` with get_keyring, make_update, send_update | Task 2 |
| Mock path when no credentials | Tasks 3, 4, 5, 6 |
| `_run_bind_dns_tests` cloud-agnostic helper (connector register + 5 CRs + cleanup) | Task 7 Step 1 |
| `run_phase_bind_dns` with external-server path (bind_server_ip non-empty) | Task 7 Step 2 |
| `run_phase_bind_dns` with AWS auto-provision path (bind_server_ip empty) | Task 7 Step 2 |
| Smoke: SSM AMI cache check | Task 7 Step 2 |
| Smoke: fresh EC2 + BIND9 install | Task 7 Step 2 |
| Smoke: TSIG secret read via SSM | Task 7 Step 2 |
| Smoke: AMI snapshot | Task 7 Step 2 |
| Smoke: connector registration + 5-step CR sequence | Task 7 Step 1 |
| Smoke: finally cleanup connector + terminate | Task 7 Step 1 |
| `BIND_DNS` wired in `main()` with `bind_server_ip/tsig` args | Task 7 Step 3 |
| `--bind-server-ip`, `--bind-tsig-key-name`, `--bind-tsig-key-secret` argparse args | Task 7 Step 4 |
| `BIND_DNS` in `--phases` help text | Task 7 Step 5 |
| `dnspython>=2.4` in requirements.txt | Already present — no change needed |
| COST comment | Task 7 Step 2 |

### Placeholder scan
No TBD, TODO, or "similar to" references. All code blocks are complete.

### Type consistency
- `_resolve_zone` defined in `_client.py`, imported identically in all 4 executors.
- `make_update` / `send_update` defined in `_client.py`, used only in `create_record.py` and `delete_record.py`.
- `connector.credentials` access pattern matches `ldap` pattern throughout.
- `client.get_cr_step_result(cr)` matches existing smoke test usage.
- `client.post("/connectors", json={...})` matches `run_phase_ldap_rotate` pattern exactly.
- `_run_bind_dns_tests(client, bind_server_ip, tsig_key_name, tsig_key_secret, instance_id_for_cleanup)` signature is used consistently in both call sites inside `run_phase_bind_dns`.

### Edge cases noted
1. **TSIG-less AXFR**: `list_zone` does not attach TSIG to the XFR query. BIND9 by default allows AXFR without TSIG; if a zone restricts AXFR to authenticated clients only, the list_zone call will fail with `FormError`. The smoke setup uses `allow-transfer { any; }` to avoid this.
2. **Relative vs FQDN record names**: The `create_record` `absent`/`add` calls pass `record_name` as given to dnspython's `UpdateMessage`; dnspython treats non-dot-terminated names as relative to the zone origin. The smoke test passes `"nexplane-test"` (relative), which is correct.
3. **AMI-cached instance cleanup**: When launched from a cached AMI, `instance_id_for_cleanup` is still passed to `_run_bind_dns_tests`, which terminates the instance after each run. This is correct — AMI caching saves BIND9 setup time but the instance itself is always created fresh and terminated after testing.
4. **External-server path (`bind_server_ip` non-empty)**: `instance_id_for_cleanup=None` is passed, so `_run_bind_dns_tests` does not attempt to terminate any EC2 instance.
5. **dnspython already in requirements**: `dnspython>=2.4.0` appears on line 55 of `requirements.txt` — no change needed.
