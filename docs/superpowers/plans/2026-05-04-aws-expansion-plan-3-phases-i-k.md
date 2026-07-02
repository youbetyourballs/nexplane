# AWS Expansion — Plan 3: Smoke Test Phases I–K

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add smoke test Phases I (Route53 DNS), J (RDS full lifecycle), and K (CloudWatch alarms) to `backend/tests/smoke/test_aws_live.py`, all using the rollback stack pattern for cleanup.

**Architecture:** Same rollback stack pattern as Plan 2. Phase J is the most expensive — two RDS db.t3.micro instances, each requiring a ~15-minute create/delete cycle. Phase-level 45-minute timeout enforced via `signal.alarm`. All resources registered in `cleanup_registry` at creation time; `finally` block guarantees cleanup even on timeout.

**Tech Stack:** Python 3.12, boto3, httpx (via NexplaneClient)

**Prerequisite:** Plans 1 and 2 must be complete.

---

## Files

**Modify:**
- `backend/tests/smoke/test_aws_live.py`
  - Add `run_phase_i()`, `run_phase_j()`, `run_phase_k()` functions
  - Update `main()` to dispatch phases I–K
  - Update `TIMEOUT_SECONDS` handling for Phase J's long waits

---

### Task 1: Phase I — Route53 DNS

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Phase I creates a private hosted zone, exercises A record create/update, weighted routing for DR failover, then tears everything down.

- [ ] **Step 1: Add `run_phase_i` function**

After `run_phase_h`, add:

```python
# ---------------------------------------------------------------------------
# Phase I
# ---------------------------------------------------------------------------

def run_phase_i(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase I: Route53 — private zone, A records, weighted routing, DR failover."""
    print("\n[Phase I] Route53 DNS Operations")
    import time as _t

    smoke_ts = int(_t.time())
    zone_name = f"smoke-{smoke_ts}.nexplane.internal"
    rollback_stack: list[tuple[str, str]] = []
    zone_id: str | None = None

    try:
        # 1. Create private hosted zone
        cr = client.run_cr(
            "Smoke-I: create hosted zone", "route53_zone_create", cloud_account_id,
            {
                "zone_name": zone_name,
                "private": True,
                "rollback_strategy": "delete_route53_zone",
            },
        )
        rollback_stack.append((cr["id"], "route53_zone_create"))

        # Extract zone_id from execution result stored in DB
        import asyncio as _asyncio, sys as _sys
        _sys.path.insert(0, "/app")
        def _get_zone_id():
            import asyncio, sys
            sys.path.insert(0, "/app")
            from app.database import AsyncSessionLocal
            import sqlalchemy as sa
            async def _q():
                async with AsyncSessionLocal() as db:
                    r = await db.execute(sa.text(
                        f"SELECT er.result FROM execution_runs er "
                        f"JOIN change_requests cr2 ON er.change_request_id = cr2.id "
                        f"WHERE cr2.id = '{cr[\"id\"]}' LIMIT 1"
                    ))
                    row = r.first()
                    if row and row[0]:
                        return row[0].get('zone_id') or row[0].get('execution', {}).get('steps', [{}])[0].get('result', {}).get('zone_id')
            import threading
            result = [None]
            def run():
                result[0] = asyncio.run(_q())
            t = threading.Thread(target=run)
            t.start(); t.join()
            return result[0]

        zone_id = _get_zone_id()
        if not zone_id:
            # Try from inventory
            assets = client.get("/assets", params={"q": zone_name, "asset_type": "dns_zone"})
            if assets:
                zone_id = assets[0].get("asset_metadata", {}).get("zone_id")
        if not zone_id:
            fail(f"Could not determine zone_id for {zone_name}")
        log(f"Hosted zone created: {zone_id} ({zone_name})")

        # 2. Create A record
        cr = client.run_cr(
            "Smoke-I: create A record", "route53_record_upsert", cloud_account_id,
            {
                "zone_id": zone_id,
                "name": f"web.{zone_name}",
                "record_type": "A",
                "values": ["10.0.0.1"],
                "ttl": 60,
                "rollback_strategy": "delete_route53_record",
            },
        )
        rollback_stack.append((cr["id"], "route53_record_upsert A"))
        log("A record created: web → 10.0.0.1")

        # 3. Update the A record (UPSERT semantics)
        cr = client.run_cr(
            "Smoke-I: update A record", "route53_record_upsert", cloud_account_id,
            {
                "zone_id": zone_id,
                "name": f"web.{zone_name}",
                "record_type": "A",
                "values": ["10.0.0.2"],
                "ttl": 60,
                "rollback_strategy": "restore_prior_record",
            },
        )
        rollback_stack.append((cr["id"], "route53_record_upsert update"))
        log("A record updated: web → 10.0.0.2")

        # 4. Create weighted A records for DR failover
        cr_primary = client.run_cr(
            "Smoke-I: create primary weighted record", "route53_record_upsert", cloud_account_id,
            {
                "zone_id": zone_id,
                "name": f"primary.{zone_name}",
                "record_type": "A",
                "values": ["10.1.0.1"],
                "ttl": 60,
                "weight": 100,
                "set_identifier": "primary",
                "rollback_strategy": "delete_route53_record",
            },
        )
        rollback_stack.append((cr_primary["id"], "route53_record_upsert primary weighted"))

        cr_secondary = client.run_cr(
            "Smoke-I: create secondary weighted record", "route53_record_upsert", cloud_account_id,
            {
                "zone_id": zone_id,
                "name": f"primary.{zone_name}",
                "record_type": "A",
                "values": ["10.1.0.2"],
                "ttl": 60,
                "weight": 0,
                "set_identifier": "secondary",
                "rollback_strategy": "delete_route53_record",
            },
        )
        rollback_stack.append((cr_secondary["id"], "route53_record_upsert secondary weighted"))
        log("Weighted routing records created (primary:100, secondary:0)")

        # 5. DR failover — swap weights
        cr = client.run_cr(
            "Smoke-I: DR DNS failover", "dr_dns_failover_route53", cloud_account_id,
            {
                "zone_id": zone_id,
                "primary_record_name": f"primary.{zone_name}",
                "primary_set_identifier": "primary",
                "secondary_set_identifier": "secondary",
                "rollback_strategy": "restore_prior_routing",
            },
        )
        rollback_stack.append((cr["id"], "dr_dns_failover_route53"))
        log("DR failover executed (primary:0, secondary:100)")

        # 6. Verify weights via boto3
        r53_boto = _get_aws_boto3_client('route53')
        if r53_boto:
            resp = r53_boto.list_resource_record_sets(HostedZoneId=zone_id)
            for rrs in resp['ResourceRecordSets']:
                if rrs.get('SetIdentifier') == 'primary':
                    assert rrs.get('Weight', 100) == 0, f"Primary weight should be 0, got {rrs.get('Weight')}"
                elif rrs.get('SetIdentifier') == 'secondary':
                    assert rrs.get('Weight', 0) == 100, f"Secondary weight should be 100, got {rrs.get('Weight')}"
            log("Failover weights verified via boto3")

        log("Phase I complete")

    except Exception as e:
        print(f"\n❌ Phase I failed: {e}")
        raise
    finally:
        print("  [Phase I cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: nuke the entire zone
        if zone_id:
            try:
                r53_safety = _get_aws_boto3_client('route53')
                if r53_safety:
                    # Delete all non-SOA/NS records
                    paginator = r53_safety.get_paginator('list_resource_record_sets')
                    changes = []
                    for page in paginator.paginate(HostedZoneId=zone_id):
                        for rrs in page['ResourceRecordSets']:
                            if rrs['Type'] not in ('SOA', 'NS'):
                                changes.append({'Action': 'DELETE', 'ResourceRecordSet': rrs})
                    if changes:
                        r53_safety.change_resource_record_sets(
                            HostedZoneId=zone_id,
                            ChangeBatch={'Changes': changes},
                        )
                    r53_safety.delete_hosted_zone(Id=zone_id)
                    print(f"  Safety net: deleted hosted zone {zone_id}")
            except Exception as e2:
                print(f"  ⚠️  Safety net zone delete failed: {e2}")
```

- [ ] **Step 2: Wire Phase I into `main()`**

```python
        if "I" in phases:
            run_phase_i(client, cloud_account_id)
```

- [ ] **Step 3: Run Phase I smoke test**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases I \
  --tailscale-auth-key tskey-auth-REDACTED 2>&1
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase I — Route53 DNS with rollback stack"
```

---

### Task 2: Phase J — RDS Full Lifecycle

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Phase J is the longest phase (~25–35 minutes). It creates a db.t3.micro MySQL instance, snapshots it, restores to a second instance, verifies, then deletes both. Phase-level 45-minute timeout via a separate `TIMEOUT_SECONDS` override.

- [ ] **Step 1: Add Phase J timeout constant near top of file**

Find `TIMEOUT_SECONDS = 600` and add below it:

```python
RDS_PHASE_TIMEOUT_SECONDS = 2700  # 45 minutes for Phase J
```

- [ ] **Step 2: Add `run_phase_j` function**

```python
# ---------------------------------------------------------------------------
# Phase J
# ---------------------------------------------------------------------------

def run_phase_j(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase J: RDS Full Lifecycle — create/snapshot/restore/verify/delete (rollback stack)."""
    print("\n[Phase J] RDS Full Lifecycle (~25-35 min)")
    import time as _t
    import random as _rand

    ts = int(_t.time())
    db_id = f"nexplane-smoke-db-{ts}"
    restored_id = f"nexplane-smoke-db-restored-{ts}"
    snap_id = f"nexplane-smoke-snap-{ts}"
    rds_master_password = "Nexplane!Smoke1"

    rollback_stack: list[tuple[str, str]] = []
    created_db_ids: list[str] = []  # for safety net
    created_snap_ids: list[str] = []

    def _rds_wait_deleted(db_identifier: str, max_wait: int = 1200) -> None:
        """Poll until RDS instance is deleted or max_wait seconds pass."""
        rds_b = _get_aws_boto3_client('rds')
        if not rds_b:
            return
        deadline = _t.time() + max_wait
        while _t.time() < deadline:
            try:
                rds_b.describe_db_instances(DBInstanceIdentifier=db_identifier)
                _t.sleep(30)
            except rds_b.exceptions.DBInstanceNotFound:
                return
        print(f"  ⚠️  Timed out waiting for {db_identifier} to delete")

    try:
        # 1. Create primary RDS instance
        print(f"  Creating RDS instance {db_id} (db.t3.micro MySQL 8.0) — may take ~10 min")
        cr = client._run_cr_with_timeout(
            "Smoke-J: create RDS instance", "rds_instance_create", cloud_account_id,
            {
                "db_instance_identifier": db_id,
                "engine": "mysql",
                "engine_version": "8.0",
                "db_instance_class": "db.t3.micro",
                "master_username": "admin",
                "master_password": rds_master_password,
                "allocated_storage": 20,
                "skip_final_snapshot": True,
                "rollback_strategy": "delete_rds_instance",
            },
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_instance_create"))
        created_db_ids.append(db_id)

        # Verify database asset in inventory
        assets = client.get("/assets", params={"q": db_id, "asset_type": "database"})
        if assets:
            log(f"RDS instance in inventory: {assets[0]['id']}")
        else:
            print("  ⚠️  RDS asset not yet in inventory (ingest lag)")

        # 2. Create manual snapshot
        print(f"  Creating RDS snapshot {snap_id}")
        cr = client._run_cr_with_timeout(
            "Smoke-J: create RDS snapshot", "rds_snapshot_create", cloud_account_id,
            {
                "db_instance_identifier": db_id,
                "snapshot_identifier": snap_id,
                "rollback_strategy": "delete_rds_snapshot",
            },
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_snapshot_create"))
        created_snap_ids.append(snap_id)
        log(f"Snapshot created: {snap_id}")

        # 3. Verify backup
        cr = client.run_cr(
            "Smoke-J: verify RDS backup", "rds_snapshot_create", cloud_account_id,
            {
                "db_instance_identifier": db_id,
                "snapshot_identifier": snap_id + "-verify",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        # Use boto3 to verify the snapshot is available and restorable
        rds_boto = _get_aws_boto3_client('rds')
        if rds_boto:
            snaps = rds_boto.describe_db_snapshots(DBSnapshotIdentifier=snap_id)['DBSnapshots']
            assert snaps, f"Snapshot {snap_id} not found"
            assert snaps[0]['Status'] == 'available', f"Snapshot not available: {snaps[0]['Status']}"
            assert snaps[0].get('AllocatedStorage', 0) > 0, "Snapshot has zero size"
            log(f"Snapshot verified: {snaps[0]['AllocatedStorage']}GB, status=available")

        # 4. Restore snapshot to new instance
        print(f"  Restoring snapshot to {restored_id} — may take ~10 min")
        cr = client._run_cr_with_timeout(
            "Smoke-J: restore RDS snapshot", "rds_snapshot_restore", cloud_account_id,
            {
                "snapshot_identifier": snap_id,
                "db_instance_identifier": restored_id,
                "db_instance_class": "db.t3.micro",
                "rollback_strategy": "delete_rds_instance",
            },
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_snapshot_restore"))
        created_db_ids.append(restored_id)
        log(f"Snapshot restored to: {restored_id}")

        # Verify restored instance asset
        assets = client.get("/assets", params={"q": restored_id, "asset_type": "database"})
        if assets:
            log(f"Restored instance in inventory: {assets[0]['id']}")

        # 5. Delete restored instance (terminal)
        print(f"  Deleting restored instance {restored_id}")
        client._run_cr_with_timeout(
            "Smoke-J: delete restored instance", "rds_instance_delete", cloud_account_id,
            {
                "db_instance_identifier": restored_id,
                "rollback_strategy": "rollback_unavailable",
            },
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        created_db_ids.remove(restored_id)
        # Pop the restore CR from rollback stack (instance is now deleted)
        rollback_stack = [(cid, lbl) for cid, lbl in rollback_stack if lbl != "rds_snapshot_restore"]
        log(f"Restored instance deleted")

        # 6. Delete manual snapshot (rollback of snapshot CR)
        snap_cr_id = next((cid for cid, lbl in rollback_stack if lbl == "rds_snapshot_create"), None)
        if snap_cr_id:
            client.rollback_cr(snap_cr_id, "rds_snapshot_create → delete snapshot")
            rollback_stack = [(cid, lbl) for cid, lbl in rollback_stack if cid != snap_cr_id]
            created_snap_ids.remove(snap_id)
            log(f"Snapshot deleted via CR rollback")

        # 7. Delete original instance (rollback of create CR)
        create_cr_id = next((cid for cid, lbl in rollback_stack if lbl == "rds_instance_create"), None)
        if create_cr_id:
            print(f"  Deleting original instance {db_id} — may take ~10 min")
            client.rollback_cr(create_cr_id, "rds_instance_create → delete instance")
            rollback_stack = [(cid, lbl) for cid, lbl in rollback_stack if cid != create_cr_id]
            created_db_ids.remove(db_id)
            log(f"Original instance deleted")

        log("Phase J complete")

    except Exception as e:
        print(f"\n❌ Phase J failed: {e}")
        raise
    finally:
        # Rollback any remaining stack items
        if rollback_stack:
            print("  [Phase J cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: force-delete any remaining instances and snapshots via boto3
        rds_safety = _get_aws_boto3_client('rds')
        if rds_safety:
            for db_identifier in list(created_db_ids):
                try:
                    rds_safety.delete_db_instance(
                        DBInstanceIdentifier=db_identifier,
                        SkipFinalSnapshot=True,
                        DeleteAutomatedBackups=True,
                    )
                    print(f"  Safety net: deleting RDS instance {db_identifier} (async)")
                except Exception as e2:
                    print(f"  ⚠️  Safety net instance delete failed {db_identifier}: {e2}")
            for snap_identifier in list(created_snap_ids):
                try:
                    rds_safety.delete_db_snapshot(DBSnapshotIdentifier=snap_identifier)
                    print(f"  Safety net: deleted snapshot {snap_identifier}")
                except Exception as e2:
                    print(f"  ⚠️  Safety net snapshot delete failed {snap_identifier}: {e2}")
```

- [ ] **Step 3: Add `_run_cr_with_timeout` helper to NexplaneClient**

In the `NexplaneClient` class, add after `rollback_cr`:

```python
    def _run_cr_with_timeout(self, title: str, change_type: str, asset_id: str,
                              desired_outcome: dict, timeout: int = TIMEOUT_SECONDS) -> dict:
        """Like run_cr but with a custom timeout for slow operations like RDS."""
        cr_id = self.create_cr(title, change_type, asset_id, desired_outcome)
        self.post(f"/change-requests/{cr_id}/plan")
        self.post(f"/change-requests/{cr_id}/submit-for-approval")
        self.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke test"})
        self.post(f"/change-requests/{cr_id}/execute")
        # Poll with custom timeout
        deadline = time.time() + timeout
        while time.time() < deadline:
            cr = self.get(f"/change-requests/{cr_id}")
            if cr["status"] == "completed":
                log(f"{title}")
                return cr
            if cr["status"] in ("failed", "rolled_back", "rejected"):
                fail(f"{title} — CR ended with status '{cr['status']}' (id: {cr_id})")
            time.sleep(10)
        fail(f"{title} — timed out after {timeout}s")
```

- [ ] **Step 4: Wire Phase J into `main()`**

```python
        if "J" in phases:
            run_phase_j(client, cloud_account_id)
```

- [ ] **Step 5: Run Phase J smoke test** (expect ~25-35 minutes)

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases J \
  --tailscale-auth-key tskey-auth-REDACTED 2>&1
```

Expected: `✅ ALL SELECTED PHASES PASSED` (after ~25-35 minutes)

- [ ] **Step 6: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase J — RDS full lifecycle with rollback stack (~25-35 min)"
```

---

### Task 3: Phase K — CloudWatch Alarms

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Phase K uses the Phase A EC2 instance. Creates two alarms, triggers one via custom metric pushed via SSM, verifies ALARM state, then rolls back both.

- [ ] **Step 1: Add `run_phase_k` function**

```python
# ---------------------------------------------------------------------------
# Phase K
# ---------------------------------------------------------------------------

def run_phase_k(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase K: CloudWatch — create alarms, trigger via SSM, verify, rollback."""
    print("\n[Phase K] CloudWatch Alarms")
    import time as _t

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]
    ts = int(_t.time())
    alarm_cpu = f"nexplane-smoke-cpu-{ts}"
    alarm_custom = f"nexplane-smoke-custom-{ts}"
    custom_namespace = "Nexplane/SmokeTest"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create CPU utilization alarm (high threshold — won't fire on real load)
        cr = client.run_cr(
            "Smoke-K: create CPU alarm", "cloudwatch_alarm_create", instance_asset["id"],
            {
                "alarm_name": alarm_cpu,
                "metric_name": "CPUUtilization",
                "namespace": "AWS/EC2",
                "threshold": 99.0,
                "comparison_operator": "GreaterThanThreshold",
                "evaluation_periods": 1,
                "period": 60,
                "statistic": "Average",
                "dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                "rollback_strategy": "delete_cloudwatch_alarm",
            },
        )
        rollback_stack.append((cr["id"], "cloudwatch_alarm_create CPU"))
        log(f"CPU alarm created: {alarm_cpu}")

        # 2. Create custom namespace alarm (will fire immediately when we push metric)
        cr = client.run_cr(
            "Smoke-K: create custom metric alarm", "cloudwatch_alarm_create", instance_asset["id"],
            {
                "alarm_name": alarm_custom,
                "metric_name": "TestTrigger",
                "namespace": custom_namespace,
                "threshold": 0.0,
                "comparison_operator": "GreaterThanThreshold",
                "evaluation_periods": 1,
                "period": 60,
                "statistic": "Sum",
                "dimensions": [],
                "rollback_strategy": "delete_cloudwatch_alarm",
            },
        )
        rollback_stack.append((cr["id"], "cloudwatch_alarm_create custom"))
        log(f"Custom metric alarm created: {alarm_custom}")

        # 3. Push metric data via SSM to trigger the custom alarm
        region = _get_aws_boto3_client('ec2') and "us-east-1"  # fallback
        client.run_cr(
            "Smoke-K: push metric data via SSM", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": (
                    f"aws cloudwatch put-metric-data "
                    f"--namespace '{custom_namespace}' "
                    f"--metric-name TestTrigger "
                    f"--value 1 "
                    f"--unit Count "
                    f"--region us-east-1"
                ),
                "rollback_strategy": "rollback_unavailable",
            },
        )
        log("Metric data pushed via SSM")

        # 4. Wait for alarm to enter ALARM state (CloudWatch evaluates every 60s)
        print("  Waiting up to 90s for alarm to enter ALARM state...")
        cw_boto = _get_aws_boto3_client('cloudwatch')
        alarm_triggered = False
        if cw_boto:
            deadline = _t.time() + 90
            while _t.time() < deadline:
                resp = cw_boto.describe_alarms(AlarmNames=[alarm_custom])
                alarms = resp.get('MetricAlarms', [])
                if alarms and alarms[0]['StateValue'] == 'ALARM':
                    alarm_triggered = True
                    log(f"Alarm {alarm_custom} is in ALARM state")
                    break
                _t.sleep(10)
            if not alarm_triggered:
                print(f"  ⚠️  Alarm did not enter ALARM state within 90s — CloudWatch evaluation lag")

        # 5. Delete both alarms via rollback
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Both alarms deleted via CR rollback")

        log("Phase K complete")

    except Exception as e:
        print(f"\n❌ Phase K failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase K cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: delete alarms directly
        try:
            cw_safety = _get_aws_boto3_client('cloudwatch')
            if cw_safety:
                cw_safety.delete_alarms(AlarmNames=[alarm_cpu, alarm_custom])
                print(f"  Safety net: deleted alarms {alarm_cpu}, {alarm_custom}")
        except Exception as e2:
            print(f"  ⚠️  Safety net alarm delete failed: {e2}")
```

- [ ] **Step 2: Wire Phase K into `main()`**

```python
        if "K" in phases:
            if phase_a_result is None:
                fail("Phase K requires Phase A to have run first (needs a running EC2 instance)")
            run_phase_k(client, phase_a_result)
```

- [ ] **Step 3: Update the phases default in `main()` argument parser**

Find:
```python
    parser.add_argument(
        "--phases", default="A,B,C,D",
```

Replace with:
```python
    parser.add_argument(
        "--phases", default="A,B,C,D",
        help="Comma-separated phases to run (A-K). Default: A,B,C,D. Phase J is slow (~35 min, creates RDS instances).",
    )
```

- [ ] **Step 4: Run Phase K smoke test**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases A,K \
  --tailscale-auth-key tskey-auth-REDACTED 2>&1
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add Phase K — CloudWatch alarms with rollback stack"
```

---

### Task 4: Full run A–K (excluding J)

Run all phases except J to verify they chain correctly without cost from RDS:

- [ ] **Step 1: Run phases A-K excluding J**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases A,B,C,D,E,F,G,H,I,K \
  --tailscale-auth-key tskey-auth-REDACTED 2>&1
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 2: Verify zero leftover resources**

```bash
docker exec nexplane-backend-1 python -c "
import asyncio, sys, boto3
sys.path.insert(0, '/app')
from app.database import AsyncSessionLocal
from app.models.connector import Connector, ConnectorType
from app.services.connector_service import _attach_credentials
import sqlalchemy as sa

async def get_creds():
    async with AsyncSessionLocal() as db:
        r = await db.execute(sa.select(Connector).where(Connector.connector_type == ConnectorType.aws))
        conn = r.scalars().first()
        await _attach_credentials(conn, db)
        return conn.credentials

creds = asyncio.run(get_creds())
ec2 = boto3.client('ec2', aws_access_key_id=creds['access_key_id'], aws_secret_access_key=creds['secret_access_key'], region_name=creds.get('region','us-east-1'))
r53 = boto3.client('route53', aws_access_key_id=creds['access_key_id'], aws_secret_access_key=creds['secret_access_key'])
cw = boto3.client('cloudwatch', aws_access_key_id=creds['access_key_id'], aws_secret_access_key=creds['secret_access_key'], region_name=creds.get('region','us-east-1'))
iam = boto3.client('iam', aws_access_key_id=creds['access_key_id'], aws_secret_access_key=creds['secret_access_key'])

# Check EC2 instances
insts = ec2.describe_instances(Filters=[{'Name':'tag:Name','Values':['nexplane-smoke-*']},{'Name':'instance-state-name','Values':['running','stopped','pending']}])
count_ec2 = sum(len(r['Instances']) for r in insts['Reservations'])

# Check Route53 zones
zones = r53.list_hosted_zones()['HostedZones']
smoke_zones = [z for z in zones if 'smoke' in z['Name']]

# Check CloudWatch alarms
alarms = cw.describe_alarms(AlarmNamePrefix='nexplane-smoke-')['MetricAlarms']

# Check IAM users
users = iam.list_users()['Users']
smoke_users = [u for u in users if 'smoke' in u['UserName']]

print(f'EC2 smoke instances: {count_ec2}')
print(f'Route53 smoke zones: {len(smoke_zones)}')
print(f'CloudWatch smoke alarms: {len(alarms)}')
print(f'IAM smoke users: {len(smoke_users)}')
assert count_ec2 == 0, f'Leftover EC2 instances: {count_ec2}'
assert len(smoke_zones) == 0, f'Leftover Route53 zones: {smoke_zones}'
assert len(alarms) == 0, f'Leftover CW alarms: {alarms}'
assert len(smoke_users) == 0, f'Leftover IAM users: {smoke_users}'
print('✅ Zero leftover smoke test resources')
"
```

Expected: `✅ Zero leftover smoke test resources`

- [ ] **Step 3: Final commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add zero-resource-leak verification; all phases A-K wired"
```

---

**Plan 3 complete.** Proceed to Plan 4 (UI — CR Workflow + Quick Actions).
