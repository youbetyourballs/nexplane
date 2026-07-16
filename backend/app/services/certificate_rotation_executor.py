# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Certificate rotation executor — orchestrates 5-phase TLS cert rotation campaign."""

import hashlib
import logging
import socket
import ssl
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 1 — Scan
# ---------------------------------------------------------------------------

async def _scan_dependents(desired: dict, organization_id: uuid.UUID, db: AsyncSession) -> list:
    """Query asset DB for hosts/secrets referencing the subject or SANs."""
    from app.models.asset import Asset, AssetType

    subject = desired.get("subject", "")
    san_list = desired.get("san") or [subject]
    search_terms = list({subject} | set(san_list))
    scope = desired.get("scan_scope") or []

    dependents = []
    idx = 0

    # Type-A: server assets whose name matches subject/SAN
    # Note: Asset model has no `hostname` field; `name` is the canonical identifier.
    if "nexplane_agent" in scope or "aws" in scope or not scope:
        res = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.asset_type == AssetType.server,
            )
        )
        servers = res.scalars().all()
        for asset in servers:
            hostname = asset.name or ""
            if any(t.lower() in hostname.lower() or hostname.lower() in t.lower() for t in search_terms):
                connector_type = getattr(asset, "connector_type", "nexplane_agent") or "nexplane_agent"
                connector_id = str(asset.connector_id or "")
                dependents.append({
                    "index": idx,
                    "type": "host",
                    "host": hostname,
                    "port": asset.asset_metadata.get("port", 443) if asset.asset_metadata else 443,
                    "connector_type": connector_type,
                    "connector_id": connector_id,
                    "asset_id": str(asset.id),
                    "snapshot": None,
                    "snapshot_fingerprint": None,
                    "update_result": None,
                    "verify_result": None,
                    "rollback_result": None,
                })
                idx += 1

    # Type-B: Kubernetes secret assets (cert-bearing K8s Secrets registered with
    # AssetType.k8s_secret; kubernetes_workload assets are NOT searched here).
    if "kubernetes" in scope or not scope:
        res = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.asset_type == AssetType.k8s_secret,
            )
        )
        k8s_secrets = res.scalars().all()
        for asset in k8s_secrets:
            name = asset.name or ""
            meta = asset.asset_metadata or {}
            labels = meta.get("labels", {}) or {}
            cert_subject = labels.get("cert-subject", "") or meta.get("cert_subject", "")
            if cert_subject and any(t.lower() in cert_subject.lower() for t in search_terms):
                dependents.append({
                    "index": idx,
                    "type": "k8s_secret",
                    "namespace": meta.get("namespace", "default"),
                    "name": name,
                    "connector_type": "kubernetes",
                    "connector_id": str(asset.connector_id or ""),
                    "asset_id": str(asset.id),
                    "snapshot": None,
                    "snapshot_fingerprint": None,
                    "update_result": None,
                    "verify_result": None,
                    "rollback_result": None,
                })
                idx += 1

    # Type-B: AWS Secrets Manager entries holding cert material, registered as
    # AssetType.aws_secret (distinct from storage_bucket / S3).
    if "aws" in scope or not scope:
        res = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.asset_type == AssetType.aws_secret,
            )
        )
        aws_secrets = res.scalars().all()
        for asset in aws_secrets:
            meta = asset.asset_metadata or {}
            cert_subject = meta.get("cert_subject", "")
            if cert_subject and any(t.lower() in cert_subject.lower() for t in search_terms):
                dependents.append({
                    "index": idx,
                    "type": "aws_secret",
                    "secret_id": asset.name or "",
                    "connector_type": "aws",
                    "connector_id": str(asset.connector_id or ""),
                    "asset_id": str(asset.id),
                    "snapshot": None,
                    "snapshot_fingerprint": None,
                    "update_result": None,
                    "verify_result": None,
                    "rollback_result": None,
                })
                idx += 1

    return dependents


# ---------------------------------------------------------------------------
# Phase 2 — Snapshot
# ---------------------------------------------------------------------------

def _tls_probe_pem(host: str, port: int, timeout: int = 10) -> Optional[str]:
    """Return current cert PEM from live TLS handshake, or None on failure."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert_der = ssock.getpeercert(binary_form=True)
                if not cert_der:
                    return None
                return ssl.DER_cert_to_PEM_cert(cert_der)
    except Exception as exc:
        logger.warning("TLS probe failed for %s:%s — %s", host, port, exc)
        return None


def _pem_fingerprint(pem: str) -> str:
    """Return SHA-256 fingerprint of PEM cert."""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        cert = x509.load_pem_x509_certificate(pem.encode(), default_backend())
        from cryptography.hazmat.primitives import hashes
        fp_bytes = cert.fingerprint(hashes.SHA256())
        return fp_bytes.hex()
    except Exception:
        # Fallback: hash the raw PEM bytes
        return hashlib.sha256(pem.encode()).hexdigest()


async def _snapshot_dependents(
    dependents: list,
    db: AsyncSession,
    organization_id: Optional[uuid.UUID] = None,
) -> list:
    """Populate snapshot + snapshot_fingerprint for each dependent."""
    updated = []
    for dep in dependents:
        dep = dict(dep)
        try:
            if dep["type"] == "host":
                pem = _tls_probe_pem(dep["host"], dep.get("port", 443))
                if pem:
                    dep["snapshot"] = pem
                    dep["snapshot_fingerprint"] = _pem_fingerprint(pem)
                else:
                    logger.warning("Snapshot failed for host %s — no cert returned", dep["host"])
                    dep["snapshot"] = None

            elif dep["type"] == "k8s_secret":
                connector = await _load_connector(
                    dep["connector_type"], dep.get("connector_id"), db, organization_id
                )
                if connector:
                    from app.connectors.catalog_service import get_catalog_service
                    catalog = get_catalog_service()
                    mod = catalog.get_executor("kubernetes", "get_secret")
                    result = await mod.execute(
                        {"namespace": dep.get("namespace", "default"), "name": dep["name"]},
                        [],
                        connector,
                    )
                    dep["snapshot"] = result.get("data") or result.get("value")
                else:
                    dep["snapshot"] = None

            elif dep["type"] == "aws_secret":
                connector = await _load_connector(
                    dep["connector_type"], dep.get("connector_id"), db, organization_id
                )
                if connector:
                    from app.connectors.catalog_service import get_catalog_service
                    catalog = get_catalog_service()
                    mod = catalog.get_executor("aws", "get_secret_value")
                    result = await mod.execute(
                        {"secret_id": dep["secret_id"]},
                        [],
                        connector,
                    )
                    dep["snapshot"] = result.get("secret_string") or result.get("value")
                else:
                    dep["snapshot"] = None

        except Exception as exc:
            logger.warning("Snapshot error for dependent %s: %s", dep.get("index"), exc)
            dep["snapshot"] = None

        updated.append(dep)
    return updated


# ---------------------------------------------------------------------------
# Phase 3 — Rotate
# ---------------------------------------------------------------------------

async def _rotate_certificate(desired: dict, connector) -> dict:
    """Issue new cert via StepCAClient and return rotation_result dict."""
    from app.connectors.executors.step_ca._client import get_step_ca_client

    client = get_step_ca_client(connector)
    subject = desired["subject"]
    san = desired.get("san") or [subject]
    not_after = desired.get("not_after", "720h")

    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = f"{tmpdir}/cert.pem"
        key_path = f"{tmpdir}/key.pem"

        # issue_certificate writes to files; we read them back.
        # san is always passed as a list; StepCAClient expands each entry into
        # a separate --san flag so all SANs appear in the issued certificate.
        client.issue_certificate(
            subject=subject,
            san=san,
            output_cert=cert_path,
            output_key=key_path,
            not_after=not_after,
        )

        with open(cert_path) as f:
            cert_pem = f.read()
        with open(key_path) as f:
            key_pem = f.read()

    fingerprint = _pem_fingerprint(cert_pem)
    return {
        "subject": subject,
        "fingerprint": fingerprint,
        "cert_pem": cert_pem,
        "key_pem": key_pem,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _load_connector(
    connector_type: str,
    connector_id: Optional[str],
    db: AsyncSession,
    organization_id: Optional[uuid.UUID] = None,
):
    """Load and credential-attach a connector by id (preferred) or by type.

    When ``organization_id`` is provided the fallback type-based lookup is
    scoped to that org so we never accidentally grab a connector from a
    different tenant.
    """
    from app.models.connector import Connector, ConnectorType
    from app.services.connector_service import _attach_credentials

    connector = None
    if connector_id:
        try:
            connector = await db.get(Connector, uuid.UUID(connector_id))
        except Exception:
            pass

    if not connector and connector_type:
        conditions = [Connector.connector_type == ConnectorType(connector_type)]
        if organization_id is not None:
            conditions.append(Connector.organization_id == organization_id)
        res = await db.execute(
            select(Connector).where(*conditions).limit(1)
        )
        connector = res.scalar_one_or_none()

    if connector:
        await _attach_credentials(connector, db)

    return connector


async def _load_step_ca_connector(organization_id: uuid.UUID, db: AsyncSession):
    """Load the step_ca connector for the given org."""
    from app.models.connector import Connector, ConnectorType
    from app.services.connector_service import _attach_credentials

    res = await db.execute(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.connector_type == ConnectorType("step_ca"),
        ).limit(1)
    )
    connector = res.scalar_one_or_none()
    if connector:
        await _attach_credentials(connector, db)
    return connector
