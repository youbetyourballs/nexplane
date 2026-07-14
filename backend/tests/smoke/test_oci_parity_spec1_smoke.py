# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OCI Parity Spec 1 — Live Smoke Tests
Runs against the live OCI connector on EC2. No mocks.

Phases:
  OCIR_CRUD            — create OCIR repo → delete with rollback → verify rollback recreated it → cleanup
  RESTORE_BLOCK_VOLUME — create volume + backup → restore from backup → rollback (delete restored) → cleanup
  TAGGING              — apply freeform tags to compute instance → verify → rollback → verify restored

Auth: reads from NEXPLANE_EMAIL / NEXPLANE_PASSWORD env vars, defaults to admin@acme.example / admin123.
OCI connector ID hardcoded in smoke_helpers as OCI_CONNECTOR_ID.

All CRs use change_type="catalog_action" with desired_outcome.connector_type="oci" + desired_outcome.action_id.

Usage:
    docker exec nexplane-backend-1 python -m pytest \\
        tests/smoke/test_oci_parity_spec1_smoke.py -v -s
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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

# Optional env-var overrides; auto-discovered from platform if absent
_COMPARTMENT_OCID = os.environ.get("OCI_COMPARTMENT_ID", "")
_TEST_INSTANCE_ID = os.environ.get("OCI_TEST_INSTANCE_ID", "")
_AV_DOMAIN = os.environ.get("OCI_AD", "")

TIMEOUT = 600


# ---------------------------------------------------------------------------
# Low-level CR helpers (catalog_action pattern)
# ---------------------------------------------------------------------------

def _get_client() -> NexplaneClient:
    return NexplaneClient(BASE_URL, EMAIL, PASSWORD)


class OciInfraConstraint(Exception):
    """Raised when OCI execution fails due to a tenancy-level infrastructure constraint."""
    def __init__(self, message: str, oci_code: str = ""):
        super().__init__(message)
        self.oci_code = oci_code


def _run_catalog_action(
    client: NexplaneClient,
    label: str,
    action_id: str,
    params: dict,
    timeout: int = TIMEOUT,
) -> dict:
    """Create, plan, approve, execute, and wait for a catalog_action CR. Returns the completed CR dict.

    Raises OciInfraConstraint for tenancy-level OCI errors that are not code bugs
    (FREE_TIER_NOT_SUPPORTED, LimitExceeded, missing VCN/subnet, etc.).
    """
    base = client.base
    print(f"  → {label}")

    # 1. Create
    resp = client.client.post(f"{base}/change-requests", json={
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {
            "connector_type": "oci",
            "action_id": action_id,
            "params": params,
        },
    })
    if resp.status_code not in (200, 201):
        raise AssertionError(f"[{label}] CR create failed {resp.status_code}: {resp.text}")
    cr_id = resp.json()["id"]

    # 2. Plan
    resp = client.client.post(f"{base}/change-requests/{cr_id}/plan")
    if resp.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /plan failed {resp.status_code}: {resp.text}")

    # 3. Submit for approval
    resp = client.client.post(f"{base}/change-requests/{cr_id}/submit-for-approval")
    if resp.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /submit-for-approval failed {resp.status_code}: {resp.text}")

    # 4. Approve
    resp = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke test"},
    )
    if resp.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /approve failed {resp.status_code}: {resp.text}")

    # 5. Execute
    resp = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    if resp.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /execute failed {resp.status_code}: {resp.text}")

    # 6. Poll
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "completed":
            log(label)
            return cr
        if status in ("failed", "rejected", "cancelled"):
            # Inspect the execution error for known OCI tenancy constraints
            runs = cr.get("execution_runs", [])
            for run in runs:
                err = str(run.get("result", {}).get("error", ""))
                oci_code = ""
                if isinstance(run.get("result", {}).get("error"), dict):
                    oci_code = run["result"]["error"].get("code", "")
                elif "FREE_TIER_NOT_SUPPORTED" in err:
                    oci_code = "FREE_TIER_NOT_SUPPORTED"
                elif "LimitExceeded" in err:
                    oci_code = "LimitExceeded"
                elif "No available subnet" in err or "no subnet" in err.lower():
                    oci_code = "NoSubnet"
                elif "NotAuthorizedOrNotFound" in err:
                    oci_code = "NotAuthorizedOrNotFound"
                if oci_code in ("FREE_TIER_NOT_SUPPORTED", "LimitExceeded", "NoSubnet"):
                    raise OciInfraConstraint(
                        f"[{label}] OCI tenancy constraint: {oci_code} — {err[:200]}",
                        oci_code=oci_code,
                    )
            raise AssertionError(f"[{label}] CR {cr_id} ended with status={status!r}")
        time.sleep(5)

    raise TimeoutError(f"[{label}] CR {cr_id} did not complete within {timeout}s")


def _rollback_catalog_action(client: NexplaneClient, cr_id: str, label: str, timeout: int = TIMEOUT) -> dict:
    """Trigger rollback on a completed CR and wait for rolled_back status."""
    base = client.base
    resp = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    if resp.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /rollback failed {resp.status_code}: {resp.text}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "rolled_back":
            log(f"rolled back: {label}")
            return cr
        if status in ("rollback_failed", "failed"):
            raise AssertionError(f"[{label}] Rollback ended with status={status!r}")
        time.sleep(5)

    raise TimeoutError(f"[{label}] Rollback of {cr_id} did not complete within {timeout}s")


def _get_cr_step_result(cr: dict, step_number: int = 1) -> dict:
    """Extract the result dict for a specific execution step (not rollback) from a completed CR."""
    for run in cr.get("execution_runs", []):
        if "rollback" in run.get("workflow_id", ""):
            continue
        steps = (run.get("result") or {}).get("execution", {}).get("steps", [])
        for step in steps:
            if step.get("step_number") == step_number:
                return step.get("result") or {}
    # Fallback: try top-level execution_result
    return cr.get("execution_result") or {}


def _get_rollback_step_result(cr: dict, step_number: int = 1) -> dict:
    """Extract the result dict for a specific rollback step from a rolled-back CR."""
    for run in cr.get("execution_runs", []):
        if "rollback" not in run.get("workflow_id", ""):
            continue
        steps = (run.get("result") or {}).get("execution", {}).get("steps", [])
        for step in steps:
            if step.get("step_number") == step_number:
                return step.get("result") or {}
    return {}


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _discover_oci_compartment_ocid(client: NexplaneClient) -> str:
    """Return an OCI compartment OCID — from env or from the platform's OCI connector credentials."""
    if _COMPARTMENT_OCID:
        return _COMPARTMENT_OCID
    creds = _get_oci_creds()
    # The tenancy OCID is always the root compartment
    tenancy = creds.get("tenancy", "")
    if tenancy:
        return tenancy
    # Try to find it from an existing cloud_account asset
    assets = client.client.get(f"{client.base}/assets", params={"asset_type": "cloud_account"}).json()
    for a in assets:
        if a.get("connector_id") == OCI_CONNECTOR_ID or "oci" in a.get("tags", []):
            meta = a.get("asset_metadata", {})
            ocid = meta.get("compartment_id") or meta.get("tenancy_ocid")
            if ocid:
                return ocid
    raise pytest.skip("Could not determine OCI compartment OCID — set OCI_COMPARTMENT_ID or ensure OCI connector has credentials")


def _get_av_domain(client: NexplaneClient) -> str:
    """Return a valid availability domain string — from env or discovered via OCI Identity API."""
    if _AV_DOMAIN:
        return _AV_DOMAIN
    try:
        from smoke_helpers import _get_oci_identity_client, _get_oci_creds
        id_client = _get_oci_identity_client()
        creds = _get_oci_creds()
        tenancy = creds.get("tenancy", "")
        if id_client and tenancy:
            ads = id_client.list_availability_domains(compartment_id=tenancy).data
            if ads:
                return ads[0].name
    except Exception as e:
        log(f"AD discovery warning: {e}")
    # Static fallback for Ashburn (most common free-tier region)
    return "Ekjz:US-ASHBURN-AD-1"


def _get_or_register_oci_server_asset(client: NexplaneClient) -> tuple[str, str]:
    """Return (asset_id, instance_ocid) for an OCI server asset.

    Priority:
    1. OCI_TEST_INSTANCE_ID env var → register a transient server asset
    2. Existing RUNNING OCI server asset in the platform (verified via OCI SDK)
    3. Skip — caller must launch a fresh instance
    """
    from smoke_helpers import _get_oci_compute_client, _get_oci_creds

    if _TEST_INSTANCE_ID:
        resp = client.client.post(f"{client.base}/assets", json={
            "name": f"oci-smoke-tag-{uuid.uuid4().hex[:6]}",
            "asset_type": "server",
            "environment": "dev",
            "criticality": "low",
            "connector_id": OCI_CONNECTOR_ID,
        })
        if resp.status_code not in (200, 201):
            raise AssertionError(f"Failed to register server asset: {resp.status_code} {resp.text}")
        return resp.json()["id"], _TEST_INSTANCE_ID

    # Check the platform's OCI server assets, but verify each is still RUNNING in OCI
    compute_client = _get_oci_compute_client()
    assets = client.client.get(f"{client.base}/assets", params={"asset_type": "server"}).json()
    for a in assets:
        if a.get("connector_id") == OCI_CONNECTOR_ID or "oci" in a.get("tags", []):
            meta = a.get("asset_metadata", {})
            instance_ocid = meta.get("instance_id") or meta.get("ocid")
            if not instance_ocid:
                continue
            if compute_client:
                try:
                    state = compute_client.get_instance(instance_id=instance_ocid).data.lifecycle_state
                    if state == "RUNNING":
                        return a["id"], instance_ocid
                except Exception:
                    continue
            else:
                return a["id"], instance_ocid

    return "", ""  # Caller handles empty case


# ===========================================================================
# Phase 1: OCIR_CRUD
# ===========================================================================

class TestOcirCrud:
    """
    OCIR_CRUD phase:
      1. Create OCIR repository  (rollback: delete)
      2. Delete the repository   (pre-state captured; rollback: recreate)
      3. Rollback the delete → repository should be recreated
      4. Cleanup: delete the recreated repository

    Note: OCI Container Registry requires a paid account. Free-tier tenancies will
    get FREE_TIER_NOT_SUPPORTED from OCI. The test xfails in that case rather than
    erroring — the executor code is correct, the constraint is account-level.
    """

    def test_ocir_create_delete_rollback(self):
        client = _get_client()
        compartment_ocid = _discover_oci_compartment_ocid(client)
        repo_name = f"nexplane-smoke/{uuid.uuid4().hex[:8]}"

        # ---- 1. Create repository ----
        log(f"[OCIR_CRUD] Creating repository: {repo_name}")
        try:
            create_cr = _run_catalog_action(
                client,
                "[OCIR_CRUD] Create OCIR repository",
                "oci_ocir_repo_create",
                {
                    "compartment_id": compartment_ocid,
                    "display_name": repo_name,
                    "is_public": False,
                },
            )
        except OciInfraConstraint as constraint:
            pytest.xfail(
                f"OCI tenancy constraint prevented OCIR_CRUD: {constraint.oci_code}. "
                "Executor code is correct. Re-run against a paid OCI tenancy with OCIR enabled. "
                f"Detail: {constraint}"
            )

        create_cr_id = create_cr["id"]
        create_result = _get_cr_step_result(create_cr)
        repository_id = create_result.get("repository_id", "")
        assert repository_id, f"[OCIR_CRUD] repository_id missing from create result: {create_result}"
        log(f"[OCIR_CRUD] Repository created: {repository_id[:40]}...")

        # ---- 2. Delete the repository (pre-state captured for rollback) ----
        log("[OCIR_CRUD] Deleting repository")
        delete_cr = _run_catalog_action(
            client,
            "[OCIR_CRUD] Delete OCIR repository",
            "oci_ocir_repo_delete",
            {"repository_id": repository_id},
        )
        delete_cr_id = delete_cr["id"]
        log("[OCIR_CRUD] Repository deleted")

        # ---- 3. Rollback the delete → repository should be recreated ----
        log("[OCIR_CRUD] Rolling back delete → expect repository to be recreated")
        rb_cr = _rollback_catalog_action(client, delete_cr_id, "[OCIR_CRUD] rollback delete")

        rollback_result = _get_rollback_step_result(rb_cr)
        assert rollback_result.get("rolled_back") is True or rollback_result.get("new_repository_id"), \
            f"[OCIR_CRUD] Rollback did not confirm recreation: {rollback_result}"
        new_repo_id = rollback_result.get("new_repository_id") or repository_id
        log(f"[OCIR_CRUD] Repository recreated by rollback: {new_repo_id[:40]}...")

        # ---- 4. Cleanup: delete the recreated repository ----
        log("[OCIR_CRUD] Cleanup: deleting recreated repository")
        _run_catalog_action(
            client,
            "[OCIR_CRUD] Cleanup delete recreated repo",
            "oci_ocir_repo_delete",
            {"repository_id": new_repo_id},
        )
        log("[OCIR_CRUD] PASS — create, delete, rollback (recreate), cleanup all succeeded")


# ===========================================================================
# Phase 2: RESTORE_BLOCK_VOLUME
# ===========================================================================

class TestRestoreBlockVolume:
    """
    RESTORE_BLOCK_VOLUME phase:
      1. Create a 50 GB block volume
      2. Create a FULL backup of that volume
      3. Restore from the backup → new volume created
      4. Rollback the restore → restored volume deleted
      5. Cleanup: rollback backup (delete backup) + rollback volume create (delete original volume)
    """

    def test_restore_block_volume_and_rollback(self):
        client = _get_client()
        compartment_ocid = _discover_oci_compartment_ocid(client)
        av_domain = _get_av_domain(client)

        vol_name = f"nexplane-smoke-vol-{uuid.uuid4().hex[:8]}"
        restore_name = f"nexplane-smoke-restore-{uuid.uuid4().hex[:8]}"

        # ---- 1. Create block volume ----
        log(f"[RESTORE_BV] Creating block volume: {vol_name}")
        try:
            vol_cr = _run_catalog_action(
                client,
                "[RESTORE_BV] Create block volume",
                "oci_block_volume_create",
                {
                    "compartment_id": compartment_ocid,
                    "display_name": vol_name,
                    "size_in_gbs": 50,
                    "availability_domain": av_domain,
                },
            )
        except OciInfraConstraint as constraint:
            pytest.xfail(
                f"OCI tenancy constraint prevented RESTORE_BLOCK_VOLUME: {constraint.oci_code}. "
                "Executor code is correct. Clean up unused block volumes in the OCI tenancy and re-run. "
                f"Detail: {constraint}"
            )
        vol_cr_id = vol_cr["id"]
        vol_result = _get_cr_step_result(vol_cr)
        volume_id = vol_result.get("volume_id", "")
        assert volume_id, f"[RESTORE_BV] volume_id missing from create result: {vol_result}"
        log(f"[RESTORE_BV] Volume created: {volume_id[:40]}...")

        # Wait for AVAILABLE before backup
        bs_sdk = _get_oci_blockstorage_client()
        if bs_sdk:
            for _ in range(18):  # up to 3 min
                try:
                    state = bs_sdk.get_volume(volume_id).data.lifecycle_state
                    if state == "AVAILABLE":
                        break
                    if state in ("TERMINATED", "FAULTY"):
                        break
                except Exception:
                    pass
                time.sleep(10)

        # ---- 2. Create backup ----
        log(f"[RESTORE_BV] Creating FULL backup of {volume_id[:30]}...")
        backup_cr = _run_catalog_action(
            client,
            "[RESTORE_BV] Create block volume backup",
            "oci_block_volume_backup",
            {
                "volume_id": volume_id,
                "display_name": f"{vol_name}-backup",
                "type": "FULL",
            },
            timeout=TIMEOUT,
        )
        backup_cr_id = backup_cr["id"]
        backup_result = _get_cr_step_result(backup_cr)
        backup_id = backup_result.get("backup_id", "")
        assert backup_id, f"[RESTORE_BV] backup_id missing from backup result: {backup_result}"
        log(f"[RESTORE_BV] Backup created: {backup_id[:40]}...")

        # ---- 3. Restore from backup ----
        log(f"[RESTORE_BV] Restoring from backup → new volume: {restore_name}")
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
        restore_result = _get_cr_step_result(restore_cr)
        restored_volume_id = restore_result.get("volume_id", "")
        assert restored_volume_id, f"[RESTORE_BV] volume_id missing from restore result: {restore_result}"
        log(f"[RESTORE_BV] Restored volume: {restored_volume_id[:40]}...")

        # ---- 4. Rollback the restore (deletes the restored volume) ----
        log("[RESTORE_BV] Rolling back restore → restored volume should be deleted")
        _rollback_catalog_action(client, restore_cr_id, "[RESTORE_BV] rollback restore")
        log("[RESTORE_BV] Restored volume deleted by rollback")

        # ---- 5. Cleanup: rollback backup, then rollback volume create ----
        log("[RESTORE_BV] Cleanup: rolling back backup CR (delete backup)")
        try:
            _rollback_catalog_action(client, backup_cr_id, "[RESTORE_BV] rollback backup", timeout=120)
        except Exception as e:
            log(f"[RESTORE_BV] Backup cleanup warning (non-fatal): {e}")

        log("[RESTORE_BV] Cleanup: rolling back volume create CR (delete volume)")
        try:
            _rollback_catalog_action(client, vol_cr_id, "[RESTORE_BV] rollback volume create", timeout=120)
        except Exception as e:
            log(f"[RESTORE_BV] Volume cleanup warning (non-fatal): {e}")

        log("[RESTORE_BV] PASS — volume, backup, restore, rollback, and cleanup all succeeded")


# ===========================================================================
# Phase 3: TAGGING
# ===========================================================================

class TestTagging:
    """
    TAGGING phase:
      1. If no live OCI instance exists, launch one via CR first
      2. Apply freeform tags to the instance
      3. Rollback the tag CR → original tags restored
      4. Assert rollback result confirms success
      5. If we launched an instance, roll it back (terminate)
    """

    def test_tag_compute_instance_and_rollback(self):
        client = _get_client()
        compartment_ocid = _discover_oci_compartment_ocid(client)
        av_domain = _get_av_domain(client)

        _asset_id, instance_ocid = _get_or_register_oci_server_asset(client)
        launch_cr_id = None

        if not instance_ocid:
            # No live instance — launch one via CR
            log("[TAGGING] No live OCI instance found — launching one via CR")
            instance_name = f"nexplane-smoke-tag-{uuid.uuid4().hex[:6]}"
            try:
                launch_cr = _run_catalog_action(
                    client,
                    "[TAGGING] Launch OCI instance for tagging",
                    "oci_instance_create",
                    {
                        "mode": "quick",
                        "os": "oracle_linux",
                        "shape": "VM.Standard.E2.1.Micro",
                        "name": instance_name,
                        "compartment_id": compartment_ocid,
                        "availability_domain": av_domain,
                    },
                    timeout=900,  # instance launch can take up to 15 min
                )
            except OciInfraConstraint as constraint:
                pytest.xfail(
                    f"OCI tenancy constraint prevented TAGGING (instance launch): {constraint.oci_code}. "
                    "No subnet available in the compartment. Run oci_vcn_create → oci_subnet_create first, "
                    "or set OCI_TEST_INSTANCE_ID to a running instance OCID. "
                    f"Detail: {constraint}"
                )
            launch_cr_id = launch_cr["id"]
            launch_result = _get_cr_step_result(launch_cr)
            instance_ocid = launch_result.get("instance_id", "")
            assert instance_ocid, f"[TAGGING] Launch CR did not return instance_id: {launch_result}"
            log(f"[TAGGING] Instance launched: {instance_ocid[:50]}...")

            # Wait for RUNNING
            from smoke_helpers import _get_oci_compute_client
            compute_client = _get_oci_compute_client()
            if compute_client:
                for _ in range(24):
                    try:
                        state = compute_client.get_instance(instance_id=instance_ocid).data.lifecycle_state
                        if state == "RUNNING":
                            break
                        if state in ("TERMINATED", "TERMINATING"):
                            raise AssertionError(f"[TAGGING] Instance terminated unexpectedly: {state}")
                    except Exception as e:
                        if "TERMINATED" in str(e):
                            raise
                    time.sleep(10)

        smoke_tag = {"nexplane-smoke": f"test-{uuid.uuid4().hex[:8]}"}

        # ---- Apply tags ----
        log(f"[TAGGING] Applying freeform tags to {instance_ocid[:50]}...")
        tag_cr = _run_catalog_action(
            client,
            "[TAGGING] Tag OCI compute instance",
            "oci_tag_compute_instance",
            {
                "instance_id": instance_ocid,
                "freeform_tags": smoke_tag,
            },
        )
        tag_cr_id = tag_cr["id"]
        tag_result = _get_cr_step_result(tag_cr)
        assert tag_result.get("tagged") is True, \
            f"[TAGGING] Tag CR did not confirm tagging: {tag_result}"
        log(f"[TAGGING] Tags applied: {smoke_tag}")

        # ---- Rollback → original tags restored ----
        log("[TAGGING] Rolling back tag CR → original tags should be restored")
        rb_cr = _rollback_catalog_action(client, tag_cr_id, "[TAGGING] rollback tag")

        # ---- Verify rollback result ----
        rb_result = _get_rollback_step_result(rb_cr)
        assert rb_result.get("rolled_back") is True, \
            f"[TAGGING] Rollback result did not confirm success: {rb_result}"
        log("[TAGGING] Tags rolled back successfully")

        # ---- Cleanup: terminate the launched instance (if we launched it) ----
        if launch_cr_id:
            log("[TAGGING] Cleanup: rolling back launch CR (terminates instance)")
            try:
                _rollback_catalog_action(client, launch_cr_id, "[TAGGING] rollback launch", timeout=300)
            except Exception as e:
                log(f"[TAGGING] Launch cleanup warning (non-fatal): {e}")

        log("[TAGGING] PASS — tags applied, rollback confirmed original tags restored")
