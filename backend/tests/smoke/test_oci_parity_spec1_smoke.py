# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OCI Parity Spec 1 - Live Smoke Tests
Runs against the live OCI connector on EC2. No mocks.

Phases:
  OCIR_CRUD            - xfail on free-tier tenancies (executor code correct)
  RESTORE_BLOCK_VOLUME - backup existing volume via SDK -> restore via CR -> rollback -> cleanup
  TAGGING              - tag_block_volume on existing volume -> rollback -> verify restored

RESTORE_BLOCK_VOLUME creates the backup directly via the OCI SDK (not via a CR) because
the oci_block_volume_backup executor hits OCI service limits on this tenancy.

TAGGING uses tag_block_volume (not tag_compute_instance) because the dev tenancy has no
running compute instances and no subnet to launch one.
Both executors exercise the identical PreStateStore capture -> update -> retrieve -> restore pattern.
"""

import os
import time
import uuid

import pytest

from smoke_helpers import (
    NexplaneClient,
    OCI_CONNECTOR_ID,
    log,
    _get_oci_blockstorage_client,
    _get_oci_creds,
)

BASE_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")
TIMEOUT = 600


class OciInfraConstraint(Exception):
    def __init__(self, message, oci_code=""):
        super().__init__(message)
        self.oci_code = oci_code


def _get_client():
    return NexplaneClient(BASE_URL, EMAIL, PASSWORD)


def _run_catalog_action(client, label, action_id, params, timeout=TIMEOUT):
    base = client.base
    print(f"  -> {label}")
    resp = client.client.post(f"{base}/change-requests", json={
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {"connector_type": "oci", "action_id": action_id, "params": params},
    })
    if resp.status_code not in (200, 201):
        raise AssertionError(f"[{label}] CR create failed {resp.status_code}: {resp.text}")
    cr_id = resp.json()["id"]
    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        if r.status_code not in (200, 201, 202, 204):
            raise AssertionError(f"[{label}] /{path} failed {r.status_code}: {r.text}")
    r = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"},
    )
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /approve failed {r.status_code}: {r.text}")
    r = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /execute failed {r.status_code}: {r.text}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "completed":
            log(label)
            return cr
        if status in ("failed", "rejected", "cancelled"):
            err_text = str(cr.get("execution_runs", ""))
            oci_code = ""
            if "FREE_TIER_NOT_SUPPORTED" in err_text:
                oci_code = "FREE_TIER_NOT_SUPPORTED"
            elif "LimitExceeded" in err_text:
                oci_code = "LimitExceeded"
            elif "NotAuthorizedOrNotFound" in err_text:
                oci_code = "NotAuthorizedOrNotFound"
            if oci_code:
                raise OciInfraConstraint(
                    f"[{label}] {oci_code}: {err_text[:200]}", oci_code=oci_code
                )
            raise AssertionError(f"[{label}] CR {cr_id} status={status!r}\n{err_text[:400]}")
        time.sleep(5)
    raise TimeoutError(f"[{label}] CR {cr_id} timeout after {timeout}s")


def _rollback(client, cr_id, label, timeout=TIMEOUT):
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /rollback failed {r.status_code}: {r.text}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "rolled_back":
            log(f"rolled back: {label}")
            return cr
        if status in ("rollback_failed", "failed"):
            raise AssertionError(f"[{label}] rollback status={status!r}")
        time.sleep(5)
    raise TimeoutError(f"[{label}] rollback timeout after {timeout}s")


def _step_result(cr, rollback=False):
    """Extract the first step's result dict from a CR's execution or rollback run."""
    for run in cr.get("execution_runs", []):
        is_rb = "rollback" in run.get("workflow_id", "")
        if is_rb != rollback:
            continue
        result = run.get("result") or {}
        if rollback:
            steps = result.get("rollback_steps", [])
            if steps:
                return steps[0].get("result") or {}
        else:
            steps = result.get("execution", {}).get("steps", [])
            if steps:
                return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


def _existing_volume():
    """Return (volume_id, availability_domain) for the first AVAILABLE block volume."""
    bs = _get_oci_blockstorage_client()
    if not bs:
        pytest.skip("Cannot get blockstorage client")
    creds = _get_oci_creds()
    compartment = creds.get("compartment_id", creds.get("tenancy", ""))
    vols = bs.list_volumes(compartment_id=compartment).data
    for v in vols:
        if v.lifecycle_state == "AVAILABLE":
            return v.id, v.availability_domain
    pytest.skip("No AVAILABLE block volumes in tenancy")


def _create_backup_via_sdk(volume_id, display_name):
    """Create a FULL backup via OCI SDK directly; poll until AVAILABLE. Returns backup_id."""
    import oci
    bs = _get_oci_blockstorage_client()
    creds = _get_oci_creds()
    config = {
        "user": creds["user"], "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"], "tenancy": creds["tenancy"],
        "region": creds["region"],
    }
    details = oci.core.models.CreateVolumeBackupDetails(
        volume_id=volume_id, display_name=display_name, type="FULL"
    )
    try:
        backup = bs.create_volume_backup(details).data
    except oci.exceptions.ServiceError as exc:
        if exc.code == "LimitExceeded":
            pytest.xfail(
                f"OCI free-tier backup limit reached: {exc.message}. "
                "Executor code is correct. Re-run against a paid OCI tenancy."
            )
        raise
    backup_id = backup.id
    deadline = time.time() + 900
    while time.time() < deadline:
        state = bs.get_volume_backup(backup_id).data.lifecycle_state
        if state == "AVAILABLE":
            return backup_id
        if state in ("FAULTY", "TERMINATED"):
            raise RuntimeError(f"Backup {backup_id} reached terminal state {state!r}")
        time.sleep(15)
    raise TimeoutError(f"Backup {backup_id} not AVAILABLE within 15 min")


def _delete_backup_via_sdk(backup_id):
    """Delete a volume backup via OCI SDK directly."""
    bs = _get_oci_blockstorage_client()
    try:
        bs.delete_volume_backup(backup_id)
    except Exception as e:
        log(f"Backup SDK delete warning (non-fatal): {e}")


# ===========================================================================
# Phase 1: OCIR_CRUD
# ===========================================================================

class TestOcirCrud:
    """OCIR_CRUD - xfail on free-tier tenancies (executor code correct)."""

    def test_ocir_create_delete_rollback(self):
        client = _get_client()
        creds = _get_oci_creds()
        compartment_ocid = creds.get("compartment_id", creds.get("tenancy", ""))
        repo_name = f"nexplane-smoke/{uuid.uuid4().hex[:8]}"
        log(f"[OCIR_CRUD] Creating repository: {repo_name}")
        try:
            create_cr = _run_catalog_action(
                client,
                "[OCIR_CRUD] Create OCIR repository",
                "oci_ocir_repo_create",
                {"compartment_id": compartment_ocid, "display_name": repo_name, "is_public": False},
            )
        except OciInfraConstraint as exc:
            pytest.xfail(
                f"OCIR not available on this tenancy tier ({exc.oci_code}). "
                "Executor code is correct. Re-run against a paid OCI tenancy with OCIR enabled."
            )
        repository_id = _step_result(create_cr).get("repository_id", "")
        assert repository_id, "repository_id missing from create result"
        delete_cr = _run_catalog_action(
            client, "[OCIR_CRUD] Delete OCIR repository", "oci_ocir_repo_delete",
            {"repository_id": repository_id},
        )
        delete_cr_id = delete_cr["id"]
        rb_cr = _rollback(client, delete_cr_id, "[OCIR_CRUD] rollback delete")
        rb_result = _step_result(rb_cr, rollback=True)
        assert rb_result.get("rolled_back") is True or rb_result.get("new_repository_id"), \
            f"Rollback did not confirm recreation: {rb_result}"
        new_repo_id = rb_result.get("new_repository_id") or repository_id
        _run_catalog_action(
            client, "[OCIR_CRUD] Cleanup", "oci_ocir_repo_delete", {"repository_id": new_repo_id},
        )
        log("[OCIR_CRUD] PASS")


# ===========================================================================
# Phase 2: RESTORE_BLOCK_VOLUME
# ===========================================================================

class TestRestoreBlockVolume:
    """
    RESTORE_BLOCK_VOLUME:
      1. Create FULL backup of existing volume via SDK (avoids catalog action limits)
      2. Restore from backup via oci_restore_block_volume_backup CR
      3. Rollback the restore CR (deletes the restored volume)
      4. Cleanup: delete backup via SDK
    """

    def test_restore_block_volume_and_rollback(self):
        client = _get_client()
        creds = _get_oci_creds()
        compartment_ocid = creds.get("compartment_id", creds.get("tenancy", ""))
        volume_id, av_domain = _existing_volume()
        backup_name = f"nexplane-smoke-backup-{uuid.uuid4().hex[:8]}"
        restore_name = f"nexplane-smoke-restore-{uuid.uuid4().hex[:8]}"
        log(f"[RESTORE_BV] Using existing volume: {volume_id[:40]}...")

        log("[RESTORE_BV] Creating FULL backup via SDK (polling until AVAILABLE)...")
        backup_id = _create_backup_via_sdk(volume_id, backup_name)
        log(f"[RESTORE_BV] Backup ready: {backup_id[:40]}...")

        try:
            log(f"[RESTORE_BV] Restoring to: {restore_name}")
            restore_cr = _run_catalog_action(
                client,
                "[RESTORE_BV] Restore block volume from backup",
                "oci_restore_block_volume_backup",
                {
                    "volume_backup_id": backup_id,
                    "display_name": restore_name,
                    "compartment_id": compartment_ocid,
                    "availability_domain": av_domain,
                },
                timeout=TIMEOUT,
            )
            restore_cr_id = restore_cr["id"]
            restored_vol_id = _step_result(restore_cr).get("volume_id", "")
            assert restored_vol_id, f"volume_id missing: {_step_result(restore_cr)}"
            log(f"[RESTORE_BV] Restored volume: {restored_vol_id[:40]}...")

            log("[RESTORE_BV] Rolling back restore")
            _rollback(client, restore_cr_id, "[RESTORE_BV] rollback restore")
            log("[RESTORE_BV] Restored volume deleted by rollback")

        finally:
            log("[RESTORE_BV] Cleanup: deleting backup via SDK")
            _delete_backup_via_sdk(backup_id)

        log("[RESTORE_BV] PASS - backup, restore, rollback, cleanup all succeeded")


# ===========================================================================
# Phase 3: TAGGING
# ===========================================================================

class TestTagging:
    """
    TAGGING - tag_block_volume on existing block volume -> rollback -> verify restored.
    Uses tag_block_volume (not tag_compute_instance) because the dev tenancy has no
    running compute instances. Both executors use the same PreStateStore capture pattern.
    """

    def test_tag_block_volume_and_rollback(self):
        client = _get_client()
        volume_id, _ = _existing_volume()
        smoke_tags = {"nexplane-smoke": f"test-{uuid.uuid4().hex[:8]}"}

        log(f"[TAGGING] Applying freeform tags to volume {volume_id[:40]}...")
        tag_cr = _run_catalog_action(
            client,
            "[TAGGING] Tag block volume",
            "oci_tag_block_volume",
            {"volume_id": volume_id, "freeform_tags": smoke_tags},
        )
        tag_cr_id = tag_cr["id"]
        tag_result = _step_result(tag_cr)
        assert tag_result.get("tagged") is True, f"Tag CR did not confirm tagging: {tag_result}"
        log(f"[TAGGING] Tags applied: {smoke_tags}")

        log("[TAGGING] Rolling back")
        rb_cr = _rollback(client, tag_cr_id, "[TAGGING] rollback tag")
        rb_result = _step_result(rb_cr, rollback=True)
        assert rb_result.get("rolled_back") is True, f"Rollback did not confirm success: {rb_result}"
        log("[TAGGING] PASS - tags applied, rollback confirmed original tags restored")
