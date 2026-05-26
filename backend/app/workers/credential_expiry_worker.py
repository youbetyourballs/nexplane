"""Daily job that checks TLS cert expiry and IAM key age, creates findings."""
from __future__ import annotations
import asyncio
import logging
import ssl
import socket
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.asset import Asset

logger = logging.getLogger(__name__)

# SLA tiers in days
SLA_CRITICAL = 7   # auto-CR
SLA_HIGH = 14      # escalate
SLA_MEDIUM = 30    # warning


VAULT_LEASE_WARN_HOURS = 24


async def _check_vault_leases(db) -> None:
    """Check Vault dynamic secret leases approaching expiry."""
    from app.connectors.executors.hashicorp_vault._client import VaultClient

    vault_connectors = await _get_connectors_by_type(db, "hashicorp_vault")
    for connector in vault_connectors:
        try:
            client = VaultClient.from_connector(connector)
            leases = client.list_leases()
            for lease in leases:
                ttl_hours = lease["ttl"] / 3600
                if ttl_hours < VAULT_LEASE_WARN_HOURS:
                    if lease["renewable"]:
                        client.renew_lease(lease["lease_id"])
                        logger.info("Renewed Vault lease %s", lease["lease_id"])
                    else:
                        await _create_expiry_finding(
                            db, None, "vault_lease",
                            f"Vault lease {lease['lease_id']} expires in {ttl_hours:.1f}h and cannot be renewed (max TTL reached)",
                            int(ttl_hours),
                        )
        except Exception as e:
            logger.debug("Vault lease check failed for connector %s: %s", connector.id, e)


async def _get_connectors_by_type(db, connector_type: str) -> list:
    """Return all connectors of the given type."""
    from app.models.connector import Connector
    result = await db.execute(select(Connector).where(Connector.connector_type == connector_type))
    return result.scalars().all()


SSH_KEY_MAX_AGE_DAYS = 365
ACME_RENEW_DAYS = 30


def _key_age_days(added_date_str) -> int | None:
    if not added_date_str:
        return None
    try:
        added = datetime.strptime(added_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - added).days
    except ValueError:
        return None


async def _run_ssh_authorized_keys_audit(asset, db) -> list[dict]:
    """Call the SSH authorized_keys_audit executor for this asset."""
    if not asset.connector_id:
        return []
    from app.models.connector import Connector
    connector = await db.get(Connector, asset.connector_id)
    if not connector or connector.connector_type != "ssh":
        return []
    from app.connectors.executors.ssh.authorized_keys_audit import execute as audit_execute
    try:
        result = await audit_execute({}, [str(asset.id)], connector)
        return result.get("keys", [])
    except Exception as e:
        logger.debug("SSH authorized_keys audit failed for asset %s: %s", asset.id, e)
        return []


async def _check_ssh_key_age(db) -> None:
    """Probe SSH authorized_keys age on all SSH-managed assets."""
    result = await db.execute(
        select(Asset).where(Asset.asset_type.in_(["server"]))
    )
    assets = result.scalars().all()

    for asset in assets:
        keys = await _run_ssh_authorized_keys_audit(asset, db)
        for key in keys:
            age_days = _key_age_days(key.get("added_date"))
            if age_days and age_days >= SSH_KEY_MAX_AGE_DAYS:
                label = key.get("comment") or key["key_fingerprint"][:16]
                await _create_expiry_finding(
                    db, asset, "ssh_authorized_key",
                    f"SSH authorized key '{label}' on {asset.name} is {age_days} days old",
                    SSH_KEY_MAX_AGE_DAYS - age_days,
                )


async def _check_step_ca_certs(db) -> None:
    """Auto-renew step-CA certs via ACME before they expire."""
    step_ca_connectors = await _get_connectors_by_type(db, "step_ca")
    for connector in step_ca_connectors:
        try:
            from app.connectors.executors.step_ca._client import StepCAClient
            client = StepCAClient.from_connector(connector)
            certs = client.list_certificates()
            for cert in certs:
                if not cert["expiry"]:
                    continue
                days_left = (cert["expiry"] - datetime.now(timezone.utc)).days
                if days_left <= ACME_RENEW_DAYS:
                    try:
                        client.renew_certificate(cert["serial"])
                        logger.info(
                            "Auto-renewed step-CA cert %s (was %dd from expiry)", cert["serial"], days_left
                        )
                    except Exception as renew_err:
                        await _create_expiry_finding(
                            db, None, "tls_certificate",
                            f"step-CA cert {cert['serial']} ({cert.get('subject', '')}) expires in {days_left}d — auto-renewal failed: {renew_err}",
                            days_left,
                        )
        except Exception as e:
            logger.debug("step-CA cert check failed for connector %s: %s", connector.id, e)


async def check_credential_expiry() -> None:
    """Main entry point called by APScheduler daily."""
    async with AsyncSessionLocal() as db:
        await _check_tls_certs(db)
        await _check_iam_key_age(db)
        await _check_vault_leases(db)
        await _check_ssh_key_age(db)
        await _check_step_ca_certs(db)


async def _check_tls_certs(db) -> None:
    """Probe TLS on port 443 for all known server/load_balancer assets."""
    result = await db.execute(
        select(Asset).where(Asset.asset_type.in_(["server", "load_balancer"]))
    )
    assets = result.scalars().all()

    for asset in assets:
        hostname = (
            asset.asset_metadata.get("public_ip")
            or asset.asset_metadata.get("private_ip")
            or asset.asset_metadata.get("dns_name")
        ) if asset.asset_metadata else None
        if not hostname:
            continue
        try:
            expiry = await asyncio.get_event_loop().run_in_executor(
                None, _get_cert_expiry, hostname, 443
            )
            if expiry:
                days_left = (expiry - datetime.now(timezone.utc)).days
                if days_left <= SLA_MEDIUM:
                    await _create_expiry_finding(
                        db, asset, "tls_certificate",
                        f"TLS certificate on {hostname} expires in {days_left} days ({expiry.date()})",
                        days_left,
                    )
        except Exception as e:
            logger.debug(f"TLS probe failed for {hostname}: {e}")


def _get_cert_expiry(hostname: str, port: int, timeout: int = 5) -> datetime | None:
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                if not cert:
                    return None
                expiry_str = cert.get("notAfter", "")
                return datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def _discover_api_key_consumers(db, key_id: str, key_type: str) -> list[dict]:
    """Search asset metadata for references to this key ID (best-effort)."""
    from app.models.asset import Asset
    from sqlalchemy import select

    consumers = []
    result = await db.execute(select(Asset))
    for asset in result.scalars():
        meta = asset.asset_metadata or {}
        for field in ["env_vars", "secrets_refs", "iam_role_arns", "service_account"]:
            if key_id in str(meta.get(field, "")):
                consumers.append({
                    "asset_id": str(asset.id),
                    "asset_name": asset.name,
                    "config_path": field,
                })
    return consumers


def _list_old_keys(creds: dict) -> list[tuple[str, str, int]]:
    """Sync: return (username, key_id, age_days) for IAM keys >= 90 days old.

    Runs in an executor so it does not block the async event loop.
    Uses connector credentials, not ambient boto3 environment.
    """
    import boto3
    iam = boto3.client(
        "iam",
        aws_access_key_id=creds["aws_access_key_id"],
        aws_secret_access_key=creds["aws_secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    results = []
    for page in iam.get_paginator("list_users").paginate():
        for user in page["Users"]:
            for key in iam.list_access_keys(UserName=user["UserName"])["AccessKeyMetadata"]:
                if key["Status"] != "Active":
                    continue
                age = (datetime.now(timezone.utc) - key["CreateDate"]).days
                if age >= 90:
                    results.append((user["UserName"], key["AccessKeyId"], age))
    return results


async def _check_iam_key_age(db) -> None:
    """Check IAM access keys older than 90 days, per registered AWS connector."""
    loop = asyncio.get_event_loop()
    aws_connectors = await _get_connectors_by_type(db, "aws")
    for connector in aws_connectors:
        try:
            creds = connector.credentials or {}
            if not creds.get("aws_access_key_id"):
                continue
            old_keys = await loop.run_in_executor(None, _list_old_keys, creds)
            for username, key_id, age_days in old_keys:
                consumers = await _discover_api_key_consumers(db, key_id, "aws_iam_key")
                await _create_expiry_finding(
                    db, None, "iam_access_key",
                    f"IAM key {key_id[:8]}... for {username} is {age_days} days old"
                    f" (policy: rotate every 90 days)",
                    90 - age_days,
                    consumers=consumers,
                )
        except Exception as e:
            logger.debug("IAM key age check failed for connector %s: %s", connector.id, e)


async def _create_expiry_finding(db, asset, credential_type: str, message: str, days_left: int, *, consumers: list[dict] | None = None) -> None:
    """Create a finding for this expiry condition."""
    try:
        from app.models.vulnerability import VulnerabilityFinding
        import uuid

        # Determine severity
        if days_left <= SLA_CRITICAL:
            severity = "critical"
        elif days_left <= SLA_HIGH:
            severity = "high"
        else:
            severity = "medium"

        org_id = asset.organization_id if asset else uuid.UUID("00000000-0000-0000-0000-000000000000")

        finding = VulnerabilityFinding(
            id=uuid.uuid4(),
            organization_id=org_id,
            asset_id=asset.id if asset else None,
            scanner="credential_expiry_monitor",
            scanner_finding_id=f"EXPIRY-{credential_type.upper()}-{abs(days_left)}D-{uuid.uuid4().hex[:8]}",
            source="credential_expiry_monitor",
            finding_type="misconfiguration",
            severity=severity,
            cve_id=None,
            title=message,
            description=message,
            remediation_hint=f"Renew {credential_type} immediately — expires in {days_left} days.",
            status="open",
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        db.add(finding)

        # Auto-create emergency CR for critical expiry
        if days_left <= SLA_CRITICAL and asset:
            from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel
            cr = ChangeRequest(
                id=uuid.uuid4(),
                organization_id=asset.organization_id,
                # requester_id omitted — system-generated, set a sentinel zero UUID
                requester_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                title=f"[AUTO] Renew {credential_type} expiring in {days_left}d — {message[:60]}",
                description=f"Auto-generated emergency CR by credential_expiry_monitor. {message}",
                change_type=(
                    ChangeType.rotate_ssh_keys
                    if credential_type == "tls_certificate"
                    else ChangeType.rotate_api_key
                ),
                target_asset_ids=[str(asset.id)],
                desired_outcome={"credential_type": credential_type, "days_left": days_left, "consumers": consumers or []},
                risk_level=RiskLevel.critical,
                priority="emergency",
                emergency_reason=f"Credential expiry in {days_left} days — auto-generated by monitor",
                status=ChangeRequestStatus.draft,
            )
            db.add(cr)
            logger.info(f"Auto-generated emergency CR for {credential_type} expiry: {asset.id}")

        await db.commit()
    except Exception as e:
        logger.warning(f"Failed to create expiry finding: {e}")
        await db.rollback()
