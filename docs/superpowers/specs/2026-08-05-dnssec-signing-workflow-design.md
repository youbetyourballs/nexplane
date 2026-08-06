# DNSSEC Signing Workflow Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a `route53_dnssec_enable` CR type that enables DNSSEC signing on an existing Route 53 hosted zone, polls until the zone reaches SIGNING status, and surfaces the DS record values the operator must submit to their domain registrar. Includes a companion `route53_dnssec_disable` rollback path.

**Architecture:** AWS-connector executor (not nexplane_agent — no host access needed, pure API calls). Flow: preflight (zone exists, KMS key accessible or auto-create) → enable DNSSEC (Route53 API) → poll until SIGNING → output DS record artifact → rollback (DisableHostedZoneDNSSEC + delete KSK). DNSSEC DS record submission to the registrar is always a manual step regardless of registrar; Nexplane surfaces the values as execution_result output and a human-readable instruction block.

**GCP Cloud DNS DNSSEC, Azure DNS (no DNSSEC support), and OCI DNS DNSSEC go on the backlog.** Azure DNS does not support DNSSEC as of 2026.

**Tech Stack:** Python asyncio executor, boto3 Route53 + KMS, pytest smoke using existing AWS connector credentials.

## Global Constraints

- Executor lives in `backend/app/connectors/executors/aws/route53_dnssec.py`
- `desired_outcome` is the only parameter channel
- ChangeType enum + DB migration + change_type_definition JSON + catalog entry required
- KMS key for DNSSEC must be in `us-east-1` (Route53 DNSSEC requirement — Route53 is a global service that uses us-east-1 KMS)
- `EnableHostedZoneDNSSEC` requires the KMS key policy to grant Route53 usage; executor must add this policy if creating a new key
- ROLLBACK_CAPABILITY = `"full"` — `DisableHostedZoneDNSSEC` is always available
- Smoke test uses an existing Route53 zone (create one if none exists in the test account); clean up after

---

## CR Type: `route53_dnssec_enable`

**Files:**
- Create: `backend/app/connectors/executors/aws/route53_dnssec.py`
- Create: `backend/app/connectors/change_type_definitions/route53_dnssec_enable.json`
- Modify: `backend/app/models/change_request.py` — add `route53_dnssec_enable` near Route53 types
- Modify: `backend/app/connectors/catalog/aws.json` — add catalog entry
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_route53_dnssec.py`

**Parameters (all in `desired_outcome`):**
- `zone_id`: Route53 hosted zone ID e.g. `"Z1234567890ABC"` (required)
- `kms_key_id`: ARN of existing KMS key in us-east-1, or `"auto"` to create a new one (default `"auto"`)
- `key_signing_algorithm`: `"ECDSAP256SHA256"` | `"ECDSAP384SHA384"` | `"RSA_2048_SHA256"` (default `"ECDSAP256SHA256"`)
- `ksk_name`: name for the Key Signing Key (default `"nexplane-ksk"`)
- `dry_run`: bool

**Executor flow:**

```python
ROLLBACK_CAPABILITY = "full"

async def execute(parameters, asset_ids, connector):
    zone_id = parameters["zone_id"]
    kms_key_id = parameters.get("kms_key_id", "auto")
    algorithm = parameters.get("key_signing_algorithm", "ECDSAP256SHA256")
    ksk_name = parameters.get("ksk_name", "nexplane-ksk")
    dry_run = parameters.get("dry_run", False)
```

1. **Preflight:**
   - `route53.get_hosted_zone(Id=zone_id)` — zone exists, record zone name
   - Check current DNSSEC status: `route53.get_dnssec(HostedZoneId=zone_id)` — if `Status.ServeSignature == "SIGNING"`, return early with `{status: "already_enabled", ...}`
   - If `kms_key_id == "auto"`: check for existing KMS key tagged `nexplane-dnssec-<zone_id>`; create if missing
   - Verify KMS key policy grants Route53 `kms:DescribeKey`, `kms:GetPublicKey`, `kms:Sign` for `dnssec-route53.amazonaws.com`; update policy if missing

2. **Snapshot:**
   - Record current DNSSEC status (disabled or partial)
   - Store `kms_key_id` (whether existing or newly created) in execution_result for rollback

3. **Enable DNSSEC:**
   - `route53.create_key_signing_key(HostedZoneId=zone_id, KeyManagementServiceArn=kms_key_arn, Name=ksk_name, Status="ACTIVE")`
   - `route53.enable_hosted_zone_dnssec(HostedZoneId=zone_id)`

4. **Poll until SIGNING:**
   - Poll `route53.get_dnssec(HostedZoneId=zone_id)` every 10s up to 5 min
   - Terminal states: `Status.ServeSignature == "SIGNING"` (success) or `Status.ServeSignature == "FAILED"` (fail)
   - If FAILED: capture `Status.StatusMessage` and return error

5. **Extract DS record:**
   - From `get_dnssec()` response, `KeySigningKeys[0]` contains `DSRecord` (the DS record string ready for registrar submission) and `DigestAlgorithmMnemonic`, `DigestValue`, `KeyTag`, `DigestAlgorithmType`
   - Format as human-readable instruction

6. **Return:**
```python
{
    "status": "signing",
    "zone_id": zone_id,
    "zone_name": zone_name,
    "ksk_name": ksk_name,
    "kms_key_arn": kms_key_arn,
    "ds_record": ds_record_string,      # e.g. "12345 13 2 AABBCCDD..."
    "key_tag": key_tag,
    "digest_algorithm": "SHA-256",
    "digest_value": digest_hex,
    "registrar_instructions": (
        f"Add the following DS record at your domain registrar for {zone_name}:\n"
        f"  Key Tag: {key_tag}\n"
        f"  Algorithm: 13 (ECDSA P-256 with SHA-256)\n"
        f"  Digest Type: 2 (SHA-256)\n"
        f"  Digest: {digest_hex}\n"
        "This record delegates DNSSEC trust from the parent zone to this zone."
    ),
    "enabled_at": datetime.now(timezone.utc).isoformat(),
}
```

**Rollback:**
```python
async def rollback(parameters, asset_ids, connector, execution_result):
    zone_id = parameters["zone_id"]
    ksk_name = execution_result.get("ksk_name", "nexplane-ksk")
    kms_key_arn = execution_result.get("kms_key_arn")
    created_key = execution_result.get("kms_key_created_by_nexplane", False)

    # 1. Disable DNSSEC signing
    route53.disable_hosted_zone_dnssec(HostedZoneId=zone_id)
    # 2. Deactivate + delete KSK
    route53.update_key_signing_key(HostedZoneId=zone_id, Name=ksk_name, Status="INACTIVE")
    route53.delete_key_signing_key(HostedZoneId=zone_id, Name=ksk_name)
    # 3. If we created the KMS key, schedule deletion (7-day minimum pending window)
    if created_key and kms_key_arn:
        kms.schedule_key_deletion(KeyId=kms_key_arn, PendingWindowInDays=7)
    return {"rolled_back": True, "zone_id": zone_id}
```

**change_type_definition:**
```json
{
  "change_type": "route53_dnssec_enable",
  "display_name": "Route53 DNSSEC Enable",
  "steps": [{"generic_action": "route53_dnssec_enable", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "route53_dnssec_enable",
  "rollback_connector_type": "aws"
}
```

**Catalog entry in `aws.json`:**
```json
{
  "action_id": "route53_dnssec_enable",
  "generic_action": "route53_dnssec_enable",
  "action_type": "change",
  "execution_tier": 2,
  "display_name": "Enable DNSSEC on Route53 Zone",
  "description": "Enables DNSSEC signing on a Route53 hosted zone using a KMS-backed Key Signing Key. Surfaces DS record values for registrar submission.",
  "applicable_asset_types": ["cloud_account", "dns_zone"],
  "parameters": [
    {"name": "zone_id", "type": "string", "required": true},
    {"name": "kms_key_id", "type": "string", "required": false, "default": "auto"},
    {"name": "key_signing_algorithm", "type": "string", "required": false, "default": "ECDSAP256SHA256"},
    {"name": "ksk_name", "type": "string", "required": false, "default": "nexplane-ksk"}
  ],
  "executor": "aws.route53_dnssec",
  "rollback_strategy": "executor",
  "estimated_duration_seconds": 120,
  "safety_notes": ["Requires KMS key in us-east-1", "DS record must be submitted to registrar manually after execution"],
  "smoke_verified": false
}
```

**Smoke test:**

Phase 1: Setup
- Get AWS creds from DB
- Create a fresh Route53 hosted zone for the smoke test (`smoke-dnssec-<uuid>.example.com`) — private zone is fine (DNSSEC works on public and private zones)
- Register asset in platform

Phase 2: Execute
- CR lifecycle: `route53_dnssec_enable` with `kms_key_id: "auto"`, `zone_id: <smoke_zone_id>`
- Assert `status == "signing"`
- Assert `ds_record` is a non-empty string
- Assert `registrar_instructions` contains the zone name

Phase 3: Rollback
- Trigger rollback
- Poll until CR `rolled_back`
- Assert `route53.get_dnssec(zone_id)` `Status.ServeSignature != "SIGNING"`

Phase 4: Teardown
- Delete hosted zone
- Note: KMS key enters 7-day pending deletion (minimum allowed); cannot be immediately deleted

---

## Backlog additions (cross-cloud DNSSEC parity)

- **GCP Cloud DNS DNSSEC** — `gcp.cloud_dns_dnssec_enable`: `dns.managedZones().patch` with `dnssecConfig.state = "on"`; ZSK and KSK are auto-managed by Google
- **OCI DNS DNSSEC** — `oci.dns_dnssec_enable`: OCI DNS supports DNSSEC via `UpdateZone` API with `dnssecConfig`
- **Azure DNS** — does NOT support DNSSEC; mark as N/A in backlog
- **BIND DNS DNSSEC** — `bind_dns.dnssec_sign_zone`: `dnssec-keygen` + `dnssec-signzone`; already has bind_dns executor directory — add as future task
