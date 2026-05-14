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


async def check_credential_expiry() -> None:
    """Main entry point called by APScheduler daily."""
    async with AsyncSessionLocal() as db:
        await _check_tls_certs(db)
        await _check_iam_key_age(db)


async def _check_tls_certs(db) -> None:
    """Probe TLS on port 443 for all known server/load_balancer assets."""
    result = await db.execute(
        select(Asset).where(Asset.asset_type.in_(["server", "ec2_instance", "load_balancer"]))
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


async def _check_iam_key_age(db) -> None:
    """Check IAM access keys older than 90 days."""
    try:
        import boto3
        iam = boto3.client("iam")
        paginator = iam.get_paginator("list_users")
        for page in paginator.paginate():
            for user in page["Users"]:
                keys = iam.list_access_keys(UserName=user["UserName"])["AccessKeyMetadata"]
                for key in keys:
                    if key["Status"] != "Active":
                        continue
                    age_days = (datetime.now(timezone.utc) - key["CreateDate"]).days
                    if age_days >= 90:
                        await _create_expiry_finding(
                            db, None, "iam_access_key",
                            f"IAM key {key['AccessKeyId'][:8]}... for {user['UserName']} is {age_days} days old (policy: rotate every 90 days)",
                            90 - age_days,  # negative = overdue
                        )
    except Exception as e:
        logger.debug(f"IAM key age check failed: {e}")


async def _create_expiry_finding(db, asset, credential_type: str, message: str, days_left: int) -> None:
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
                desired_outcome={"credential_type": credential_type, "days_left": days_left},
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
