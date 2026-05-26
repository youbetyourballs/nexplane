# Credential Lifecycle Phase 2 — Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend the existing `credential_expiry_worker` with four new probes — SSH key age enforcement, Vault lease renewal, API key rotation with consumer discovery, and step-CA ACME certificate renewal — so that every major credential class Nexplane manages has automated expiry detection and remediation.

**Architecture:** Each new check is a standalone `async def _check_*()` function added to `credential_expiry_worker.py`, registered in `check_credential_expiry()`. They follow the same pattern: probe → create `VulnerabilityFinding` → auto-generate emergency CR on critical expiry. Consumer discovery is a new utility layer queried before generating rotation CRs so the CR's `desired_outcome` includes the full consumer list.

**Tech Stack:** FastAPI APScheduler, SQLAlchemy async, SSH connector, HashiCorp Vault connector, step-CA connector, ACME protocol (existing step-CA executor)

---

## Section 1: SSH Key Age Enforcement

### What it does

Scans all SSH-type assets for authorized keys older than a configured threshold (default: 365 days). Creates a finding per stale key. Orphan detection: keys with no auth events in the asset's audit log for >90 days are flagged as orphan-suspected.

### Implementation

```python
SSH_KEY_MAX_AGE_DAYS = 365
SSH_KEY_ORPHAN_DAYS = 90

async def _check_ssh_key_age(db) -> None:
    """Probe SSH authorized_keys age on all SSH-managed assets."""
    result = await db.execute(
        select(Asset).where(Asset.asset_type.in_(["server", "ec2_instance"]))
    )
    assets = result.scalars().all()

    for asset in assets:
        # Use existing SSH connector to run authorized_keys_audit agent command
        # Returns: [{"key_fingerprint": str, "comment": str, "added_date": str|None}]
        keys = await _run_ssh_authorized_keys_audit(asset)
        for key in keys:
            age_days = _key_age_days(key.get("added_date"))
            if age_days and age_days >= SSH_KEY_MAX_AGE_DAYS:
                await _create_expiry_finding(
                    db, asset, "ssh_authorized_key",
                    f"SSH authorized key '{key.get('comment', key['key_fingerprint'][:16])}' on {asset.name} is {age_days} days old",
                    SSH_KEY_MAX_AGE_DAYS - age_days,  # negative = overdue
                )
```

`_run_ssh_authorized_keys_audit` calls the existing `authorized_keys_audit` Go agent command via the SSH connector's `run_command` interface. Returns parsed JSON output.

### CR type for rotation

When auto-generating a CR for stale SSH keys, use `ChangeType.rotate_ssh_keys` (already exists). The CR's `desired_outcome` includes the key fingerprint and the asset ID.

---

## Section 2: Vault Lease Renewal

### What it does

Queries the Vault connector for all dynamic secret leases that will expire within the warning window. Renews leases with remaining TTL > 0. Creates a finding for leases that cannot be renewed (e.g., max TTL reached).

### Implementation

```python
VAULT_LEASE_WARN_HOURS = 24  # warn if lease expires < 24h

async def _check_vault_leases(db) -> None:
    """Check Vault dynamic secret leases approaching expiry."""
    from app.connectors.hashicorp_vault._client import VaultClient

    vault_connectors = await _get_connectors_by_type(db, "hashicorp_vault")
    for connector in vault_connectors:
        try:
            client = VaultClient.from_connector(connector)
            leases = client.list_leases()  # GET /sys/leases/lookup returns lease IDs + TTLs
            for lease in leases:
                ttl_hours = lease["ttl"] / 3600
                if ttl_hours < VAULT_LEASE_WARN_HOURS:
                    if lease["renewable"]:
                        # Attempt renewal
                        client.renew_lease(lease["lease_id"])
                        logger.info(f"Renewed Vault lease {lease['lease_id']}")
                    else:
                        # Cannot renew — create finding
                        await _create_expiry_finding(
                            db, None, "vault_lease",
                            f"Vault lease {lease['lease_id']} expires in {ttl_hours:.1f}h and cannot be renewed (max TTL reached)",
                            int(ttl_hours),
                        )
        except Exception as e:
            logger.debug(f"Vault lease check failed for connector {connector.id}: {e}")
```

The `VaultClient` already exists at `backend/app/connectors/hashicorp_vault/_client.py`. Add `list_leases()` and `renew_lease()` methods using Vault's `/v1/sys/leases/lookup` and `/v1/sys/leases/renew` endpoints.

---

## Section 3: API Key Rotation with Consumer Discovery

### What it does

Before generating a rotation CR for an API key (AWS IAM, GCP service account, Azure client secret), discover all services that reference the key in their configuration. Include the consumer list in the CR's `desired_outcome` so the executor can update consumer configs after generating the new key.

### Consumer discovery strategy

Discovery is best-effort: query asset metadata for known config patterns.

```python
async def _discover_api_key_consumers(db, key_id: str, key_type: str) -> list[dict]:
    """
    Search asset metadata for references to this key ID.
    Returns: [{"asset_id": str, "asset_name": str, "config_path": str}]
    """
    consumers = []
    result = await db.execute(select(Asset))
    for asset in result.scalars():
        meta = asset.asset_metadata or {}
        # Check common metadata fields where keys appear
        for field in ["env_vars", "secrets_refs", "iam_role_arns", "service_account"]:
            if key_id in str(meta.get(field, "")):
                consumers.append({
                    "asset_id": str(asset.id),
                    "asset_name": asset.name,
                    "config_path": field,
                })
    return consumers
```

### Integration with `_check_iam_key_age`

Extend the existing IAM key age check:

```python
consumers = await _discover_api_key_consumers(db, key["AccessKeyId"], "aws_iam_key")
desired_outcome = {
    "credential_type": "iam_access_key",
    "access_key_id": key["AccessKeyId"],
    "username": user["UserName"],
    "consumers": consumers,  # NEW: list of assets that use this key
}
```

The `rotate_api_key` executor (already exists) reads `desired_outcome["consumers"]` and after generating the new key, dispatches `ssm_command` sub-steps to update consumer configs where possible. If it cannot auto-update, it logs the consumer list to the CR's execution result for operator follow-up.

---

## Section 4: step-CA ACME Certificate Renewal

### What it does

Checks TLS certificates issued by step-CA connectors. When a cert is within 30 days of expiry, triggers an ACME renewal via the step-CA connector instead of creating a manual CR.

### Implementation

```python
ACME_RENEW_DAYS = 30

async def _check_step_ca_certs(db) -> None:
    """Auto-renew step-CA certs via ACME before they expire."""
    from app.connectors.step_ca._client import StepCAClient

    step_ca_connectors = await _get_connectors_by_type(db, "step_ca")
    for connector in step_ca_connectors:
        try:
            client = StepCAClient.from_connector(connector)
            certs = client.list_certificates()  # GET /1.0/ssh/certificates or /1.0/x509/certificates
            for cert in certs:
                days_left = (cert["expiry"] - datetime.now(timezone.utc)).days
                if days_left <= ACME_RENEW_DAYS:
                    try:
                        client.renew_certificate(cert["serial"])
                        logger.info(f"Auto-renewed step-CA cert {cert['serial']} (was {days_left}d from expiry)")
                    except Exception as renew_err:
                        # Renewal failed — create finding + emergency CR
                        await _create_expiry_finding(
                            db, None, "tls_certificate",
                            f"step-CA cert {cert['serial']} ({cert.get('subject', '')}) expires in {days_left}d — auto-renewal failed: {renew_err}",
                            days_left,
                        )
        except Exception as e:
            logger.debug(f"step-CA cert check failed for connector {connector.id}: {e}")
```

`StepCAClient` already exists at `backend/app/connectors/step_ca/_client.py`. Add `list_certificates()` and `renew_certificate()` methods.

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `backend/app/workers/credential_expiry_worker.py` | Add `_check_ssh_key_age`, `_check_vault_leases`, `_check_step_ca_certs`; extend `_check_iam_key_age` with consumer discovery; register all in `check_credential_expiry()` |
| `backend/app/connectors/hashicorp_vault/_client.py` | Add `list_leases()`, `renew_lease()` methods |
| `backend/app/connectors/step_ca/_client.py` | Add `list_certificates()`, `renew_certificate()` methods |
| `backend/app/workers/credential_expiry_worker.py` | Add `_discover_api_key_consumers()` utility |
| `backend/tests/test_credential_expiry_worker.py` | Unit tests for all four new checks |

---

## Testing Strategy

Unit tests mock the connector clients and verify: findings are created with correct severity, leases are renewed when renewable, consumer list is included in IAM rotation CR `desired_outcome`, ACME renewal is attempted before creating a finding. No live connector calls.

Smoke: the existing VULN_REMEDIATION phase doesn't cover expiry workers. Add a new `CREDENTIAL_EXPIRY` smoke phase that: provisions a Vault dynamic secret with a short TTL (60s), waits 30s, runs `check_credential_expiry()` directly, verifies the lease was renewed or a finding was created.
