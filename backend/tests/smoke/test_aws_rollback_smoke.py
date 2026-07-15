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

PHASE = "AWS_ROLLBACK"
TIMEOUT = 120


def _run_cr(client, label, action_id, params, timeout=TIMEOUT):
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {"connector_type": "aws", "action_id": action_id, "params": params},
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


class TestAwsRollbackSmoke:
    """
    End-to-end rollback smoke for the AWS Route53 delete_route53_record executor.
    Creates a test A record via boto3, deletes it via CR, rolls back, verifies restoration.
    """

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.creds = get_connector_creds_from_db("aws")
        if self.creds is None:
            pytest.skip("No AWS credentials in platform DB")
        self.zone_id = self.creds.get("hosted_zone_id")
        if not self.zone_id:
            pytest.skip("No hosted_zone_id in AWS creds — cannot run Route53 rollback smoke")
        import boto3
        self.r53 = boto3.client(
            "route53",
            aws_access_key_id=self.creds["aws_access_key_id"],
            aws_secret_access_key=self.creds["aws_secret_access_key"],
            region_name=self.creds.get("region", "us-east-1"),
        )
        self.test_record = None

    def _create_test_record(self, name):
        self.r53.change_resource_record_sets(
            HostedZoneId=self.zone_id,
            ChangeBatch={
                "Changes": [{
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": name,
                        "Type": "A",
                        "TTL": 60,
                        "ResourceRecords": [{"Value": "1.2.3.4"}],
                    },
                }]
            },
        )

    def _delete_test_record(self, name):
        try:
            self.r53.change_resource_record_sets(
                HostedZoneId=self.zone_id,
                ChangeBatch={
                    "Changes": [{
                        "Action": "DELETE",
                        "ResourceRecordSet": {
                            "Name": name,
                            "Type": "A",
                            "TTL": 60,
                            "ResourceRecords": [{"Value": "1.2.3.4"}],
                        },
                    }]
                },
            )
        except Exception:
            pass  # best-effort cleanup

    def _record_exists(self, name):
        paginator = self.r53.get_paginator("list_resource_record_sets")
        for page in paginator.paginate(HostedZoneId=self.zone_id):
            for rrs in page["ResourceRecordSets"]:
                if rrs["Name"].rstrip(".") == name.rstrip(".") and rrs["Type"] == "A":
                    values = [r["Value"] for r in rrs.get("ResourceRecords", [])]
                    return values
        return None

    def test_delete_route53_record_rollback(self):
        suffix = uuid4().hex[:8]
        # Derive a domain suffix from zone — use a placeholder if not resolvable
        zone_name = self.creds.get("hosted_zone_name", "example.com")
        test_name = f"nexplane-smoke-rollback-{suffix}.{zone_name.rstrip('.')}."
        self.test_record = test_name

        try:
            # Step 1: Create the record via boto3 so we have known prior state
            self._create_test_record(test_name)
            log(f"{PHASE}: test record created: {test_name}")

            # Step 2: Execute CR to delete it
            cr = _run_cr(
                self.client,
                f"[smoke] delete route53 record {suffix}",
                "delete_route53_record",
                {
                    "zone_id": self.zone_id,
                    "name": test_name,
                    "record_type": "A",
                    "values": ["1.2.3.4"],
                    "ttl": 60,
                },
            )
            cr_id = cr["id"]

            delete_result = _step_result(cr, rollback=False)
            assert delete_result.get("deleted") is True, (
                f"Expected deleted=True, got: {delete_result}"
            )
            log(f"{PHASE}: delete_route53_record executed (cr={cr_id})")

            # Step 3: Verify record is gone
            values = self._record_exists(test_name)
            assert values is None, f"Record should be deleted but found: {values}"

            # Step 4: Rollback
            cr = _rollback_cr(self.client, cr_id, f"rollback delete route53 {suffix}")

            rollback_result = _step_result(cr, rollback=True)
            assert rollback_result.get("rolled_back") is True, (
                f"Expected rolled_back=True, got: {rollback_result}"
            )
            log(f"{PHASE}: rollback completed (cr={cr_id})")

            # Step 5: Verify record is restored
            values = self._record_exists(test_name)
            assert values is not None, "Record should be restored after rollback but not found"
            assert "1.2.3.4" in values, f"Expected '1.2.3.4' in restored record, got: {values}"
            log(f"{PHASE}: record restored with correct value after rollback")

        finally:
            if self.test_record:
                self._delete_test_record(self.test_record)
                log(f"{PHASE}: cleanup — deleted test record {self.test_record}")
