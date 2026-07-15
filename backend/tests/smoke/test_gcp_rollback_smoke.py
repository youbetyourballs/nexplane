# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import os
import time
import pytest
import sys
from uuid import uuid4

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@nexplane.local")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin")

PHASE = "GCP_ROLLBACK"
TIMEOUT = 120


def _run_cr(client, label, action_id, params, timeout=TIMEOUT):
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {"connector_type": "gcp", "action_id": action_id, "params": params},
    })
    if resp.status_code not in (200, 201):
        raise AssertionError(f"[{label}] CR create failed {resp.status_code}: {resp.text}")
    cr_id = resp.json()["id"]

    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        if r.status_code not in (200, 201, 202, 204):
            raise AssertionError(f"[{label}] /{path} failed {r.status_code}: {r.text}")

    r = client.client.post(f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"})
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
            raise AssertionError(
                f"[{label}] CR {cr_id} status={status!r}: {str(cr.get('execution_runs', ''))[:400]}"
            )
        time.sleep(10)
    raise TimeoutError(f"[{label}] CR {cr_id} timeout after {timeout}s")


def _rollback_cr(client, cr_id, label, timeout=TIMEOUT):
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
        time.sleep(10)
    raise TimeoutError(f"[{label}] rollback timeout after {timeout}s")


def _step_result(cr, rollback=False):
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


def _get_dns_client(creds):
    import json
    from google.cloud import dns as gcp_dns
    from google.oauth2 import service_account

    sa_info = creds.get("service_account_json")
    if isinstance(sa_info, str):
        sa_info = json.loads(sa_info)
    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return gcp_dns.Client(project=creds["project_id"], credentials=credentials)


def _record_exists_in_gcp_dns(dns_client, managed_zone_name, record_name, record_type="TXT"):
    zone = dns_client.zone(managed_zone_name)
    rrsets = list(zone.list_resource_record_sets())
    for rrset in rrsets:
        if rrset.name.rstrip(".") == record_name.rstrip(".") and rrset.record_type == record_type:
            return list(rrset.rrdatas)
    return None


class TestGcpRollbackSmoke:
    """
    End-to-end rollback smoke for the GCP DNS upsert executor.
    Creates a TXT record via CR, rolls back, verifies deletion (rollback of a new record = delete).
    """

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.creds = get_connector_creds_from_db("gcp")
        if self.creds is None:
            pytest.skip("No GCP credentials in platform DB")
        self.dns_zone = self.creds.get("dns_zone")
        self.managed_zone = self.creds.get("dns_managed_zone")
        if not self.dns_zone or not self.managed_zone:
            pytest.skip("No dns_zone/dns_managed_zone in GCP creds — cannot run DNS rollback smoke")
        self.dns_client = _get_dns_client(self.creds)
        self.test_record_name = None

    def test_upsert_dns_record_rollback(self):
        suffix = uuid4().hex[:8]
        record_name = f"nexplane-smoke-rollback-{suffix}.{self.dns_zone.rstrip('.')}."
        self.test_record_name = record_name

        # Execute upsert CR to create a new TXT record
        cr = _run_cr(
            self.client,
            f"[smoke] gcp upsert dns record {suffix}",
            "gcp_upsert_dns_record",
            {
                "project_id": self.creds["project_id"],
                "managed_zone": self.managed_zone,
                "record_name": record_name,
                "record_type": "TXT",
                "ttl": 60,
                "rrdatas": ["smoke-test"],
            },
        )
        cr_id = cr["id"]

        upsert_result = _step_result(cr, rollback=False)
        assert upsert_result.get("upserted") is True or upsert_result.get("created") is True, (
            f"Expected upserted=True or created=True, got: {upsert_result}"
        )
        log(f"{PHASE}: gcp_upsert_dns_record executed (cr={cr_id})")

        # Verify record exists in GCP DNS
        values = _record_exists_in_gcp_dns(self.dns_client, self.managed_zone, record_name, "TXT")
        assert values is not None, "TXT record should exist in GCP DNS after upsert"
        log(f"{PHASE}: record confirmed in GCP DNS: {values}")

        # Rollback — since this was a net-new record, rollback should delete it
        cr = _rollback_cr(self.client, cr_id, f"rollback gcp dns upsert {suffix}")

        rollback_result = _step_result(cr, rollback=True)
        assert rollback_result.get("rolled_back") is True, (
            f"Expected rolled_back=True, got: {rollback_result}"
        )
        log(f"{PHASE}: rollback completed (cr={cr_id})")

        # Verify record is gone after rollback
        values = _record_exists_in_gcp_dns(self.dns_client, self.managed_zone, record_name, "TXT")
        assert values is None, (
            f"Record should be deleted after rollback (it was net-new), but found: {values}"
        )
        log(f"{PHASE}: record confirmed deleted from GCP DNS after rollback")
