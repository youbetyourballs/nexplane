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


# ---------------------------------------------------------------------------
# Phase 4 — Update
# ---------------------------------------------------------------------------

async def _update_dependents(dependents: list, rotation_result: dict, db: AsyncSession) -> list:
    """Push new cert to each dependent. Returns updated list; sets update_result on each."""
    updated = []
    for dep in dependents:
        dep = dict(dep)
        try:
            connector = await _load_connector(dep["connector_type"], dep.get("connector_id"), db)
            if dep["type"] == "host":
                if dep["connector_type"] == "nexplane_agent":
                    from app.connectors.catalog_service import get_catalog_service
                    catalog = get_catalog_service()
                    mod = catalog.get_executor("nexplane_agent", "manage_tls_certificates")
                    result = await mod.execute(
                        {
                            "cert_pem": rotation_result["cert_pem"],
                            "key_pem": rotation_result["key_pem"],
                            "reload_command": dep.get("reload_command", "nginx -s reload"),
                        },
                        [dep.get("asset_id", "")],
                        connector,
                    )
                    dep["update_result"] = result
                else:
                    # SSM deploy path
                    from app.connectors.catalog_service import get_catalog_service
                    catalog = get_catalog_service()
                    mod = catalog.get_executor("step_ca", "rotate_certificate")
                    result = await mod.execute(
                        {
                            "subject": rotation_result["subject"],
                            "deploy_via_ssm": True,
                            "instance_id": dep.get("asset_id", ""),
                        },
                        [],
                        connector,
                    )
                    dep["update_result"] = result

            elif dep["type"] == "k8s_secret":
                from app.connectors.catalog_service import get_catalog_service
                catalog = get_catalog_service()
                mod = catalog.get_executor("kubernetes", "patch_secret")
                result = await mod.execute(
                    {
                        "namespace": dep.get("namespace", "default"),
                        "name": dep["name"],
                        "data": {"tls.crt": rotation_result["cert_pem"], "tls.key": rotation_result["key_pem"]},
                    },
                    [],
                    connector,
                )
                dep["update_result"] = result

            elif dep["type"] == "aws_secret":
                from app.connectors.catalog_service import get_catalog_service
                catalog = get_catalog_service()
                mod = catalog.get_executor("aws", "rotate_secrets_manager_secret")
                result = await mod.execute(
                    {
                        "secret_id": dep["secret_id"],
                        "new_value": rotation_result["cert_pem"],
                    },
                    [],
                    connector,
                )
                dep["update_result"] = result

            else:
                dep["update_result"] = {"success": False, "error": f"Unknown dependent type: {dep['type']}"}

        except Exception as exc:
            logger.error("Update failed for dependent %s: %s", dep.get("index"), exc)
            dep["update_result"] = {"success": False, "error": str(exc)}

        updated.append(dep)

    return updated


# ---------------------------------------------------------------------------
# Phase 5 — Verify
# ---------------------------------------------------------------------------

async def _verify_dependents(
    dependents: list,
    rotation_result: dict,
    verify_timeout_seconds: int,
    db: AsyncSession,
) -> list:
    """After waiting verify_timeout_seconds, probe each dependent."""
    import asyncio
    await asyncio.sleep(verify_timeout_seconds)

    updated = []
    for dep in dependents:
        dep = dict(dep)
        try:
            if dep["type"] == "host":
                pem = _tls_probe_pem(dep["host"], dep.get("port", 443))
                if pem:
                    served_fp = _pem_fingerprint(pem)
                    if served_fp == rotation_result["fingerprint"]:
                        dep["verify_result"] = {"success": True, "fingerprint": served_fp, "error": None}
                    else:
                        dep["verify_result"] = {
                            "success": False,
                            "fingerprint": served_fp,
                            "error": f"Fingerprint mismatch: got {served_fp}, expected {rotation_result['fingerprint']}",
                        }
                else:
                    dep["verify_result"] = {"success": False, "fingerprint": None, "error": "TLS probe returned no cert"}

            elif dep["type"] == "k8s_secret":
                connector = await _load_connector(dep["connector_type"], dep.get("connector_id"), db)
                from app.connectors.catalog_service import get_catalog_service
                catalog = get_catalog_service()
                mod = catalog.get_executor("kubernetes", "get_secret")
                result = await mod.execute(
                    {"namespace": dep.get("namespace", "default"), "name": dep["name"]},
                    [],
                    connector,
                )
                stored = (result.get("data") or {}).get("tls.crt") or result.get("value", "")
                if rotation_result["cert_pem"].strip() in (stored or "").strip():
                    dep["verify_result"] = {"success": True, "fingerprint": None, "error": None}
                else:
                    dep["verify_result"] = {"success": False, "fingerprint": None, "error": "Secret value does not match new cert PEM"}

            elif dep["type"] == "aws_secret":
                connector = await _load_connector(dep["connector_type"], dep.get("connector_id"), db)
                from app.connectors.catalog_service import get_catalog_service
                catalog = get_catalog_service()
                mod = catalog.get_executor("aws", "get_secret_value")
                result = await mod.execute({"secret_id": dep["secret_id"]}, [], connector)
                stored = result.get("secret_string") or result.get("value", "")
                if rotation_result["cert_pem"].strip() in (stored or "").strip():
                    dep["verify_result"] = {"success": True, "fingerprint": None, "error": None}
                else:
                    dep["verify_result"] = {"success": False, "fingerprint": None, "error": "Secret value does not match new cert PEM"}

            else:
                dep["verify_result"] = {"success": False, "fingerprint": None, "error": f"Unknown type: {dep['type']}"}

        except Exception as exc:
            logger.error("Verify failed for dependent %s: %s", dep.get("index"), exc)
            dep["verify_result"] = {"success": False, "fingerprint": None, "error": str(exc)}

        updated.append(dep)

    return updated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _persist(run, data: dict) -> None:
    """Write data to ExecutionRun.result in-place (caller must commit)."""
    if run:
        run.result = data


def _build_result(dependents: list, rotation_result: Optional[dict], phase: str, paused: bool) -> dict:
    has_warnings = any(
        d.get("verify_result") and not d["verify_result"].get("success")
        for d in dependents
    )
    return {
        "phase": phase,
        "dependents": dependents,
        "rotation_result": rotation_result,
        "rollback_strategy": None,
        "has_warnings": has_warnings,
        "paused": paused,
    }


def _update_succeeded(result: dict) -> bool:
    """Normalise success check across executors that return different key names.

    manage_tls_certificates returns {"applied": bool, ...} while most other
    executors return {"success": bool, ...}.  Check both so update failure
    detection is not silently masked by a missing key.
    """
    if result is None:
        return False
    if "success" in result:
        return bool(result["success"])
    if "applied" in result:
        return bool(result["applied"])
    return True  # unknown format — assume ok


def _merge_by_index(existing: list, new_items: list) -> list:
    """Merge two lists of dependents by index, new_items overriding existing."""
    merged = {d["index"]: d for d in existing}
    for d in new_items:
        merged[d["index"]] = d
    return [merged[k] for k in sorted(merged)]


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def execute_certificate_rotation(cr_id: uuid.UUID) -> dict:
    """Run all 5 phases for a certificate_rotation CR. Persists phase state after each step."""
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from app.models.execution_run import ExecutionRun, ExecutionStatus

    async with AsyncSessionLocal() as db:
        cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = cr_res.scalar_one()
        desired = cr.desired_outcome or {}

        run_res = await db.execute(
            select(ExecutionRun).where(
                ExecutionRun.change_request_id == cr_id,
                ExecutionRun.status.in_([ExecutionStatus.running, ExecutionStatus.pending]),
            ).order_by(ExecutionRun.started_at.desc()).limit(1)
        )
        run = run_res.scalar_one_or_none()
        if not run:
            run_res2 = await db.execute(
                select(ExecutionRun).where(ExecutionRun.change_request_id == cr_id)
                .order_by(ExecutionRun.started_at.desc()).limit(1)
            )
            run = run_res2.scalar_one_or_none()

        existing = (run.result or {}) if run else {}
        dependents = existing.get("dependents", [])
        rotation_result = existing.get("rotation_result")
        verify_timeout_seconds = desired.get("verify_timeout_seconds", 10)

        # Phase 1 — Scan (only if no dependents yet)
        if not dependents:
            logger.info("[cert-rotation] Phase 1: scan for dependents")
            dependents = await _scan_dependents(desired, cr.organization_id, db)
            _persist(run, {"phase": "scan", "dependents": dependents, "rotation_result": None})
            await db.commit()

        # Phase 2 — Snapshot (only if snapshots not yet taken)
        if dependents and not any(d.get("snapshot") is not None for d in dependents):
            logger.info("[cert-rotation] Phase 2: snapshot")
            dependents = await _snapshot_dependents(dependents, db, organization_id=cr.organization_id)
            _persist(run, {"phase": "snapshot", "dependents": dependents, "rotation_result": rotation_result})
            await db.commit()

        # Phase 3 — Rotate (only if not yet done)
        if not rotation_result:
            logger.info("[cert-rotation] Phase 3: rotate")
            step_ca_connector = await _load_step_ca_connector(cr.organization_id, db)
            rotation_result = await _rotate_certificate(desired, step_ca_connector)
            _persist(run, {"phase": "rotate", "dependents": dependents, "rotation_result": rotation_result})
            await db.commit()

        # Phase 4 — Update (process only dependents lacking update_result)
        pending_update = [d for d in dependents if not d.get("update_result")]
        if pending_update:
            logger.info("[cert-rotation] Phase 4: update (%d pending)", len(pending_update))
            updated = await _update_dependents(pending_update, rotation_result, db)
            dependents = _merge_by_index([d for d in dependents if d.get("update_result")], updated)
            _persist(run, {"phase": "update", "dependents": dependents, "rotation_result": rotation_result})
            await db.commit()

        # Check for update failures — unconditionally, before entering phase 5
        if any(
            d.get("update_result") and not _update_succeeded(d["update_result"])
            for d in dependents
        ):
            return _build_result(dependents, rotation_result, phase="update", paused=True)

        # Phase 5 — Verify (skip already-verified dependents on resume)
        if not all(d.get("verify_result") for d in dependents):
            logger.info("[cert-rotation] Phase 5: verify (timeout=%ss)", verify_timeout_seconds)
            pending = [d for d in dependents if not d.get("verify_result")]
            already = [d for d in dependents if d.get("verify_result")]
            verified = await _verify_dependents(pending, rotation_result, verify_timeout_seconds, db)
            dependents = _merge_by_index(already, verified)
            _persist(run, {"phase": "verify", "dependents": dependents, "rotation_result": rotation_result})
            await db.commit()

        failed = [d for d in dependents if not (d.get("verify_result") or {}).get("success")]
        paused = bool(failed)
        result = _build_result(dependents, rotation_result, phase="verify", paused=paused)

        if not paused:
            # Transition to completed
            cr.status = ChangeRequestStatus.completed
            cr.updated_at = datetime.now(timezone.utc)
            if run:
                run.status = ExecutionStatus.completed
                run.result = result
            await db.commit()

        return result


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def _cert_has_24h_remaining(pem: str) -> bool:
    """Return True if cert PEM has more than 24h until expiry."""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        cert = x509.load_pem_x509_certificate(pem.encode(), default_backend())
        try:
            expiry = cert.not_valid_after_utc
        except AttributeError:
            expiry = cert.not_valid_after.replace(tzinfo=timezone.utc)
        remaining = expiry - datetime.now(timezone.utc)
        return remaining.total_seconds() > 86400
    except Exception:
        return False


async def execute_certificate_rollback(cr_id: uuid.UUID, execution_result: dict) -> dict:
    """FILO unwind of all updated dependents."""
    from app.models.change_request import ChangeRequest

    async with AsyncSessionLocal() as db:
        cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = cr_res.scalar_one()
        desired = cr.desired_outcome or {}
        trigger_reason = desired.get("trigger_reason", "scheduled")

        dependents = execution_result.get("dependents", [])

        # Determine global rollback strategy
        if trigger_reason == "compromise":
            global_strategy = "reissue"
        else:
            # Try strategy B: find any type-A (host) dependent with a valid snapshot
            global_strategy = "reissue"
            for dep in dependents:
                if dep["type"] == "host" and dep.get("snapshot") and dep.get("snapshot_fingerprint"):
                    if _cert_has_24h_remaining(dep["snapshot"]):
                        global_strategy = "restore"
                        break

        rollback_steps = []
        # FILO: reverse index order
        for dep in sorted(dependents, key=lambda d: d["index"], reverse=True):
            step = {"index": dep["index"], "strategy": global_strategy, "rolled_back": False, "error": None}
            try:
                connector = await _load_connector(dep["connector_type"], dep.get("connector_id"), db)

                if global_strategy == "restore" and dep.get("snapshot"):
                    # Strategy B: restore snapshot
                    if dep["type"] == "host":
                        if dep.get("snapshot_key"):
                            # Full restore — both cert and key were captured
                            from app.connectors.catalog_service import get_catalog_service
                            catalog = get_catalog_service()
                            mod = catalog.get_executor("nexplane_agent", "manage_tls_certificates")
                            result = await mod.execute(
                                {
                                    "cert_pem": dep["snapshot"],
                                    "key_pem": dep["snapshot_key"],
                                    "reload_command": dep.get("reload_command", "nginx -s reload"),
                                },
                                [dep.get("asset_id", "")],
                                connector,
                            )
                            step["rollback_result"] = result
                            step["rolled_back"] = True
                        else:
                            # No private key snapshot available — re-issue a fresh cert instead.
                            # TLS probes can only capture the public cert, never the private key,
                            # so snapshot_key is always absent for host dependents discovered via
                            # _snapshot_dependents.  Fall back to strategy A (re-issue) for this
                            # specific dependent rather than supplying cert PEM as key PEM.
                            step["strategy"] = "reissue"
                            step_ca_connector = await _load_step_ca_connector(cr.organization_id, db)
                            new_rotation = await _rotate_certificate(desired, step_ca_connector)
                            fresh_dependents = await _update_dependents([dep], new_rotation, db)
                            fresh_dep = fresh_dependents[0]
                            update_ok = _update_succeeded(fresh_dep.get("update_result") or {})
                            step["rollback_result"] = fresh_dep.get("update_result")
                            step["rolled_back"] = update_ok
                            if not update_ok:
                                step["error"] = (fresh_dep.get("update_result") or {}).get(
                                    "error", "Re-issue fallback failed (no snapshot_key)"
                                )
                    elif dep["type"] == "k8s_secret":
                        from app.connectors.catalog_service import get_catalog_service
                        catalog = get_catalog_service()
                        mod = catalog.get_executor("kubernetes", "patch_secret")
                        result = await mod.execute(
                            {
                                "namespace": dep.get("namespace", "default"),
                                "name": dep["name"],
                                "data": dep["snapshot"],
                            },
                            [],
                            connector,
                        )
                        step["rollback_result"] = result
                        step["rolled_back"] = True
                    elif dep["type"] == "aws_secret":
                        from app.connectors.catalog_service import get_catalog_service
                        catalog = get_catalog_service()
                        mod = catalog.get_executor("aws", "rotate_secrets_manager_secret")
                        result = await mod.execute(
                            {"secret_id": dep["secret_id"], "new_value": dep["snapshot"]},
                            [],
                            connector,
                        )
                        step["rollback_result"] = result
                        step["rolled_back"] = True
                    else:
                        step["error"] = f"Cannot restore unknown type: {dep['type']}"
                else:
                    # Strategy A: re-issue fresh cert
                    step_ca_connector = await _load_step_ca_connector(cr.organization_id, db)
                    new_rotation = await _rotate_certificate(desired, step_ca_connector)
                    # Push fresh cert to dependent via same path as phase 4
                    fresh_dependents = await _update_dependents([dep], new_rotation, db)
                    fresh_dep = fresh_dependents[0]
                    update_ok = (fresh_dep.get("update_result") or {}).get("success", False)
                    step["rollback_result"] = fresh_dep.get("update_result")
                    step["rolled_back"] = update_ok
                    if not update_ok:
                        step["error"] = (fresh_dep.get("update_result") or {}).get("error", "Re-issue failed")

            except Exception as exc:
                logger.error("Rollback failed for dependent %s: %s", dep.get("index"), exc)
                step["error"] = str(exc)

            rollback_steps.append(step)

        all_rolled_back = all(s["rolled_back"] for s in rollback_steps)
        has_warnings = not all_rolled_back

        return {
            "rollback_steps": rollback_steps,
            "all_rolled_back": all_rolled_back,
            "has_warnings": has_warnings,
            "rollback_strategy": global_strategy,
        }
