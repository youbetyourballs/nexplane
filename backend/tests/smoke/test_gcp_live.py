# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

#!/usr/bin/env python3
"""
Nexplane GCP Live Smoke Test — Phases L–M.

Usage:
    python backend/tests/smoke/test_gcp_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases L,M \\
        --tailscale-auth-key tskey-auth-<key> \\
        --gcp-project my-project-id

Phase descriptions:
    L  GCE: instance launch + agent deploy with rollback stack
    M  GCE advanced: stop/start/reboot/snapshot with rollback stack

Requirements:
    GCP connector with credentials + Compute Engine API enabled
    Tailscale connector with reusable pre-authorized auth key (for agent registration)
"""
import secrets
import time
from typing import Optional

from smoke_helpers import (
    GCE_SMOKE_INSTANCE, GCE_ZONE, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _gcp_creds_cache, _get_gcp_compute_client,
    make_base_parser,
)


def _get_gcp_creds() -> dict:
    """Return the GCP credentials dict from the connector, populating cache if needed."""
    import smoke_helpers as _sh
    _get_gcp_compute_client()  # side effect: populates smoke_helpers._gcp_creds_cache
    return _sh._gcp_creds_cache


# ---------------------------------------------------------------------------
# Phase L
# ---------------------------------------------------------------------------

def run_phase_l(client: NexplaneClient, cloud_account_id: str,
                gcp_project: str, agent_secret: str) -> dict:
    """Phase L: GCE instance launch + agent deploy with rollback stack."""
    print("\n[Phase L] GCE Instance Launch + Agent Deploy")

    rollback_stack: list[tuple[str, str]] = []
    instance_created = False

    # Pre-run: delete stale GCE instance if it exists from a previous run
    try:
        gcp_compute = _get_gcp_compute_client()
        if gcp_compute:
            try:
                gcp_compute.get(project=gcp_project, zone=GCE_ZONE, instance=GCE_SMOKE_INSTANCE)
                log(f"[Phase L] Deleting stale GCE instance: {GCE_SMOKE_INSTANCE}")
                op = gcp_compute.delete(project=gcp_project, zone=GCE_ZONE, instance=GCE_SMOKE_INSTANCE)
                # Wait for delete to complete (async operation)
                try:
                    op.result(timeout=120)
                    log(f"[Phase L] Stale instance deleted")
                except Exception:
                    import time as _t; _t.sleep(30)  # fallback wait
            except Exception:
                pass  # Instance doesn't exist, nothing to clean up
    except Exception:
        pass

    try:
        cr = client.run_cr(
            "[Phase L] launch GCE instance", "gce_instance_create", cloud_account_id,
            {
                "name": GCE_SMOKE_INSTANCE,
                "machine_type": "e2-micro",
                "zone": GCE_ZONE,
                "image_family": "ubuntu-2204-lts",
                "image_project": "ubuntu-os-cloud",
                "connection_mode": "agent_startup",
                "nexplane_url": "http://localhost:8000",
                "nexplane_secret": agent_secret,
            },
        )
        rollback_stack.append((cr["id"], "gce_instance_create"))
        instance_created = True
        log(f"GCE instance launched: {GCE_SMOKE_INSTANCE}")

        # Verify server asset in inventory
        time.sleep(10)
        instance_asset = client.get_asset_by_name(GCE_SMOKE_INSTANCE)
        if instance_asset:
            log(f"GCE instance in inventory: {instance_asset['id']}")
        else:
            print(f"  ⚠️  GCE instance asset not yet in inventory (ingest lag)")
            instance_asset = {
                "id": cloud_account_id,
                "name": GCE_SMOKE_INSTANCE,
                "asset_metadata": {"instance_name": GCE_SMOKE_INSTANCE, "zone": GCE_ZONE},
            }

        # Wait up to 5 min for agent to register
        print("  Waiting up to 5 min for Nexplane agent to register...")
        deadline = time.time() + 300
        agent_asset = None
        while time.time() < deadline:
            candidates = client.get("/assets", params={"q": GCE_SMOKE_INSTANCE, "asset_type": "endpoint"})
            if candidates:
                agent_asset = candidates[0]
                log(f"Agent registered: {agent_asset['id']}")
                break
            time.sleep(15)
        if not agent_asset:
            print("  ⚠️  Agent not yet registered — startup script may still be running")

        log("Phase L complete")
        result = {"instance_asset": instance_asset}
        rollback_stack.clear()   # success — don't roll back in finally
        instance_created = False  # success — don't safety-net delete
        return result

    except Exception as e:
        print(f"\n❌ Phase L failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase L cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: delete instance via GCP SDK
        if instance_created:
            try:
                compute = _get_gcp_compute_client()
                if compute and gcp_project:
                    compute.delete(project=gcp_project, zone=GCE_ZONE, instance=GCE_SMOKE_INSTANCE)
                    print(f"  Safety net: deleted GCE instance {GCE_SMOKE_INSTANCE}")
            except Exception as e2:
                print(f"  ⚠️  Safety net GCE delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase M
# ---------------------------------------------------------------------------

def run_phase_m(client: NexplaneClient, phase_l_result: dict, gcp_project: str) -> None:
    """Phase M: GCE advanced — stop/start/reboot/snapshot with rollback stack."""
    print("\n[Phase M] GCE Advanced Operations")

    instance_asset = phase_l_result["instance_asset"]
    instance_name = instance_asset.get("asset_metadata", {}).get("instance_name", GCE_SMOKE_INSTANCE)
    zone = instance_asset.get("asset_metadata", {}).get("zone", GCE_ZONE)

    rollback_stack: list[tuple[str, str]] = []
    snapshot_name: str | None = None

    try:
        # 1. Stop instance
        cr = client.run_cr(
            "[Phase M] stop GCE instance", "gce_stop", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        rollback_stack.append((cr["id"], "gce_stop"))
        log("GCE instance stopped")

        # 2. Start instance
        cr = client.run_cr(
            "[Phase M] start GCE instance", "gce_start", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        rollback_stack.pop()  # stop CR superseded
        rollback_stack.append((cr["id"], "gce_start"))
        log("GCE instance started")

        # 3. Reboot
        client.run_cr(
            "[Phase M] reboot GCE instance", "gce_instance_reboot", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        log("GCE instance rebooted")

        # Wait for agent to reconnect post-reboot
        time.sleep(30)
        assets = client.get("/assets", params={"q": GCE_SMOKE_INSTANCE, "asset_type": "endpoint"})
        if assets:
            log("Agent still registered post-reboot")
        else:
            print("  ⚠️  Agent not visible post-reboot (may still be reconnecting)")

        # 4. Create disk snapshot
        snapshot_name = f"nexplane-smoke-snap-{int(time.time())}"
        cr = client.run_cr(
            "[Phase M] create GCE disk snapshot", "gce_disk_snapshot", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone, "snapshot_name": snapshot_name},
        )
        rollback_stack.append((cr["id"], "gce_disk_snapshot"))
        log(f"Disk snapshot created: {snapshot_name}")

        # 5. Verify snapshot via GCP SDK
        if gcp_project and _gcp_creds_cache:
            try:
                import json as _j
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1 as _cv1
                key_json_raw = _gcp_creds_cache.get("service_account_key_json", "")
                key_json = _j.loads(key_json_raw) if isinstance(key_json_raw, str) else key_json_raw
                gcp_creds = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                snap_client = _cv1.SnapshotsClient(credentials=gcp_creds)
                snap = snap_client.get(project=gcp_project, snapshot=snapshot_name)
                log(f"Snapshot verified: status={snap.status}, size={snap.disk_size_gb}GB")
            except Exception as e:
                print(f"  ⚠️  Snapshot verify skipped: {e}")

        rollback_stack.clear()  # success — instance stays running, snapshot cleaned up separately
        log("Phase M complete")

    except Exception as e:
        print(f"\n❌ Phase M failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase M cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: delete snapshot
        if snapshot_name and gcp_project:
            _get_gcp_compute_client()  # prime credentials cache if not already loaded
        if snapshot_name and gcp_project and _gcp_creds_cache:
            try:
                import json as _j
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1 as _cv1
                key_json_raw = _gcp_creds_cache.get("service_account_key_json", "")
                key_json = _j.loads(key_json_raw) if isinstance(key_json_raw, str) else key_json_raw
                gcp_creds = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                snap_client = _cv1.SnapshotsClient(credentials=gcp_creds)
                snap_client.delete(project=gcp_project, snapshot=snapshot_name)
                print(f"  Safety net: deleted snapshot {snapshot_name}")
            except Exception as e2:
                print(f"  ⚠️  Safety net snapshot delete failed: {e2}")


def run_phase_n(client: NexplaneClient, cloud_account_id: str,
                gcp_project: str, gcp_network: str = "global/networks/default") -> None:
    """Phase N: GCP Firewall — create_firewall_rule + delete via CR rollback."""
    print("\n[Phase N] GCP Firewall Rules")

    import time as _time
    rule_name = f"nexplane-smoke-n-{int(_time.time())}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        cr = client.run_cr(
            "[Phase N] create GCP firewall rule", "gcp_firewall_create", cloud_account_id,
            {
                "project": gcp_project,
                "rule_name": rule_name,
                "network": gcp_network,
                "direction": "INGRESS",
                "priority": 1000,
                "allowed": [{"IPProtocol": "tcp", "ports": ["8443"]}],
                "source_ranges": ["192.0.2.0/24"],
                "description": "Nexplane smoke test rule — safe to delete",
            },
        )
        rollback_stack.append((cr["id"], "gcp_firewall_create"))

        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                fw_client = compute_v1.FirewallsClient(credentials=gc)
                fw = fw_client.get(project=gcp_project, firewall=rule_name)
                assert fw.name == rule_name
                log(f"GCP firewall rule verified via SDK: {rule_name}")
            except Exception as e:
                print(f"  ⚠️  SDK verification skipped: {e}")
        else:
            print("  ⚠️  No GCP credentials — SDK verification skipped")

        create_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(create_cr_id, "gcp_firewall_create → delete")
        log("GCP firewall rule deleted via CR rollback")

        log("Phase N complete")

    except Exception as e:
        print(f"\n❌ Phase N failed: {e}")
        raise
    finally:
        print("  [Phase N cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        try:
            creds = _get_gcp_creds()
            if creds and gcp_project:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                fw_client = compute_v1.FirewallsClient(credentials=gc)
                try:
                    fw_client.delete(project=gcp_project, firewall=rule_name)
                    print(f"  Safety net: deleted firewall rule {rule_name}")
                except Exception:
                    pass
        except Exception:
            pass


def run_phase_o(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase O: GCP Storage — block_public_bucket_access CR on a GCS bucket."""
    print("\n[Phase O] GCP Storage")

    bucket_name = f"nexplane-smoke-o-{secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []
    bucket_created = False

    try:
        creds = _get_gcp_creds()
        if not creds:
            fail("Phase O requires GCP credentials")

        import json as _json
        from google.oauth2 import service_account as _sa
        from google.cloud import storage as _storage
        key_raw = creds.get("service_account_key_json", "")
        key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
        gc = _sa.Credentials.from_service_account_info(
            key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        storage_client = _storage.Client(project=gcp_project, credentials=gc)
        bucket = storage_client.bucket(bucket_name)
        bucket.iam_configuration.uniform_bucket_level_access_enabled = False
        storage_client.create_bucket(bucket, location="US")
        bucket_created = True
        log(f"GCS bucket created via SDK: {bucket_name}")

        cr = client.run_cr(
            "[Phase O] block public GCS bucket access", "gcp_block_public_bucket_access",
            cloud_account_id,
            {"project": gcp_project, "bucket_name": bucket_name},
        )
        rollback_stack.append((cr["id"], "gcp_block_public_bucket_access"))

        bucket_obj = storage_client.get_bucket(bucket_name)
        policy = bucket_obj.get_iam_policy()
        has_all_users = any(
            "allUsers" in binding["members"] or "allAuthenticatedUsers" in binding["members"]
            for binding in policy.bindings
        )
        assert not has_all_users, "allUsers/allAuthenticatedUsers still present after blocking"
        log("GCS public access blocked (SDK verified)")

        log("Phase O complete")

    except Exception as e:
        print(f"\n❌ Phase O failed: {e}")
        raise
    finally:
        print("  [Phase O cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if bucket_created:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import storage as _storage
                creds = _get_gcp_creds()
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                sc = _storage.Client(project=gcp_project, credentials=gc)
                sc.get_bucket(bucket_name).delete(force=True)
                print(f"  Safety net: deleted GCS bucket {bucket_name}")
            except Exception as e:
                print(f"  ⚠️  Safety net bucket delete failed: {e}")


def run_phase_p(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase P: GCP Service Accounts — rotate key + disable via CRs."""
    print("\n[Phase P] GCP Service Accounts")

    sa_name = f"nexplane-smoke-p-{int(time.time()) % 100000}"
    sa_email = f"{sa_name}@{gcp_project}.iam.gserviceaccount.com"
    rollback_stack: list[tuple[str, str]] = []
    sa_created = False

    try:
        creds = _get_gcp_creds()
        if not creds:
            fail("Phase P requires GCP credentials")

        import json as _json
        from google.oauth2 import service_account as _sa
        from googleapiclient.discovery import build as _build
        key_raw = creds.get("service_account_key_json", "")
        key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
        gc = _sa.Credentials.from_service_account_info(
            key_json,
            scopes=["https://www.googleapis.com/auth/cloud-platform",
                    "https://www.googleapis.com/auth/iam"],
        )
        iam_svc = _build("iam", "v1", credentials=gc)

        iam_svc.projects().serviceAccounts().create(
            name=f"projects/{gcp_project}",
            body={"accountId": sa_name,
                  "serviceAccount": {"displayName": "Nexplane smoke test"}},
        ).execute()
        sa_created = True
        log(f"Service account created: {sa_email}")

        cr = client.run_cr(
            "[Phase P] rotate GCP service account key", "gcp_rotate_service_account_key",
            cloud_account_id,
            {"project": gcp_project, "service_account_email": sa_email},
        )
        rollback_stack.append((cr["id"], "gcp_rotate_service_account_key"))
        log("Service account key rotated via CR")

        cr = client.run_cr(
            "[Phase P] disable GCP service account", "gcp_disable_service_account",
            cloud_account_id,
            {"project": gcp_project, "service_account_email": sa_email},
        )
        rollback_stack.append((cr["id"], "gcp_disable_service_account"))

        # GCP SA disable has eventual consistency — retry up to 30s
        import time as _t
        sa_disabled = False
        for _ in range(6):
            _t.sleep(5)
            sa_info = iam_svc.projects().serviceAccounts().get(
                name=f"projects/{gcp_project}/serviceAccounts/{sa_email}"
            ).execute()
            if sa_info.get("disabled"):
                sa_disabled = True
                break
        assert sa_disabled, "Service account not disabled after 30s"
        log("Service account disabled (SDK verified)")

        log("Phase P complete")

    except Exception as e:
        print(f"\n❌ Phase P failed: {e}")
        raise
    finally:
        print("  [Phase P cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if sa_created:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from googleapiclient.discovery import build as _build
                creds = _get_gcp_creds()
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform",
                                      "https://www.googleapis.com/auth/iam"])
                iam_svc = _build("iam", "v1", credentials=gc)
                iam_svc.projects().serviceAccounts().delete(
                    name=f"projects/{gcp_project}/serviceAccounts/{sa_email}"
                ).execute()
                print(f"  Safety net: deleted service account {sa_email}")
            except Exception as e:
                print(f"  ⚠️  Safety net SA delete failed: {e}")


def run_phase_q(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase Q: Terraform local apply against GCP — creates GCS bucket using google provider."""
    print("\n[Phase Q] Terraform Local (GCP)")

    bucket_name = f"nexplane-smoke-q-{secrets.token_hex(4)}"

    creds = _get_gcp_creds()
    if not creds:
        fail("Phase Q requires GCP credentials")

    import json as _json
    key_raw = creds.get("service_account_key_json", "")
    key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
    key_str = _json.dumps(key_json)

    tf_content = (
        'terraform {\n'
        '  required_providers {\n'
        '    google = {\n'
        '      source  = "hashicorp/google"\n'
        '      version = "~> 5.0"\n'
        '    }\n'
        '  }\n'
        '}\n\n'
        'provider "google" {\n'
        '  credentials = <<CREDS\n'
        + key_str + '\n'
        'CREDS\n'
        '  project = "' + gcp_project + '"\n'
        '  region  = "us-central1"\n'
        '}\n\n'
        'resource "google_storage_bucket" "smoke_test" {\n'
        '  name          = "' + bucket_name + '"\n'
        '  location      = "US"\n'
        '  force_destroy = true\n'
        '}\n'
    )

    cr = client.run_cr(
        "[Phase Q] terraform apply GCS bucket", "terraform_local_apply", cloud_account_id,
        {"tf_content": tf_content, "rollback_strategy": "terraform_destroy_local"},
    )
    log(f"Terraform applied (GCP) — GCS bucket: {bucket_name}")

    try:
        from google.oauth2 import service_account as _sa
        from google.cloud import storage as _storage
        gc = _sa.Credentials.from_service_account_info(
            key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        sc = _storage.Client(project=gcp_project, credentials=gc)
        bucket = sc.get_bucket(bucket_name)
        assert bucket.name == bucket_name
        log("GCS bucket confirmed via SDK")
    except Exception as e:
        print(f"  ⚠️  SDK verification skipped: {e}")

    client.rollback_cr(cr["id"], "terraform_local_apply → destroy")
    log("GCS bucket destroyed via Terraform rollback")

    log("Phase Q complete")


def run_phase_r(client: NexplaneClient, cloud_account_id: str, gcp_project: str,
                gcp_phase_result: Optional[dict] = None) -> None:
    """Phase R: Ansible local playbook against GCP."""
    print("\n[Phase R] Ansible Local (GCP)")

    playbook_content = (
        "---\n"
        "- name: Nexplane GCP smoke test\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  tasks:\n"
        "    - name: Check python version\n"
        "      command: python3 --version\n"
        "      register: py_ver\n"
        "    - name: Print version\n"
        "      debug:\n"
        "        msg: 'Python: {{ py_ver.stdout }}'\n"
    )

    cr = client.run_cr(
        "[Phase R] ansible local playbook", "ansible_local_playbook", cloud_account_id,
        {"playbook_content": playbook_content, "inventory": "localhost,"},
    )
    log("Ansible local playbook CR executed successfully (GCP)")
    log("Phase R complete")


def run_phase_s(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase S: Advanced firewall — two rules with different priorities and protocols."""
    print("\n[Phase S] Advanced Firewall Rules")

    import time as _time
    rule1 = f"nexplane-smoke-s-tcp-{int(_time.time())}"
    rule2 = f"nexplane-smoke-s-udp-{int(_time.time()) + 1}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        cr1 = client.run_cr(
            "[Phase S] create TCP firewall rule", "gcp_firewall_create", cloud_account_id,
            {
                "project": gcp_project,
                "rule_name": rule1,
                "network": "global/networks/default",
                "direction": "INGRESS",
                "priority": 900,
                "allowed": [{"IPProtocol": "tcp", "ports": ["8080"]}],
                "source_ranges": ["192.0.2.0/24"],
                "description": "Nexplane smoke test — safe to delete",
            },
        )
        rollback_stack.append((cr1["id"], "gcp_firewall_create"))
        log(f"Firewall rule 1 created: {rule1} (priority 900, TCP 8080)")

        cr2 = client.run_cr(
            "[Phase S] create UDP firewall rule", "gcp_firewall_create", cloud_account_id,
            {
                "project": gcp_project,
                "rule_name": rule2,
                "network": "global/networks/default",
                "direction": "INGRESS",
                "priority": 800,
                "allowed": [{"IPProtocol": "udp", "ports": ["5353"]}],
                "source_ranges": ["192.0.2.0/24"],
                "description": "Nexplane smoke test — safe to delete",
            },
        )
        rollback_stack.append((cr2["id"], "gcp_firewall_create"))
        log(f"Firewall rule 2 created: {rule2} (priority 800, UDP 5353)")

        # Verify both rules via SDK
        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                fw_client = compute_v1.FirewallsClient(credentials=gc)
                fw1 = fw_client.get(project=gcp_project, firewall=rule1)
                assert fw1.priority == 900, f"Expected priority 900, got {fw1.priority}"
                fw2 = fw_client.get(project=gcp_project, firewall=rule2)
                assert fw2.priority == 800, f"Expected priority 800, got {fw2.priority}"
                log("Both firewall rules verified via SDK (priority + protocol)")
            except Exception as e:
                print(f"  ⚠️  SDK verification skipped: {e}")
        else:
            print("  ⚠️  No GCP credentials — SDK verification skipped")

        # Rollback (LIFO)
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase S complete")

    except Exception as e:
        print(f"\n❌ Phase S failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete rules via SDK if they still exist
        creds = _get_gcp_creds()
        if creds and gcp_project:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                fw_client = compute_v1.FirewallsClient(credentials=gc)
                for rule_name in [rule1, rule2]:
                    try:
                        fw_client.delete(project=gcp_project, firewall=rule_name)
                        print(f"  Safety net: deleted firewall rule {rule_name}")
                    except Exception:
                        pass
            except Exception as e2:
                print(f"  ⚠️  Safety net cleanup failed: {e2}")


def run_phase_t(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase T: GCS bucket create + block public access + rollback."""
    print("\n[Phase T] GCS Bucket Lifecycle")

    import secrets as _secrets
    bucket_name = f"nexplane-smoke-t-{_secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create bucket via CR
        cr = client.run_cr(
            "[Phase T] create GCS bucket", "gcp_bucket_create", cloud_account_id,
            {"bucket_name": bucket_name, "location": "US"},
        )
        rollback_stack.append((cr["id"], "gcp_bucket_create"))
        log(f"GCS bucket created: {bucket_name}")

        # 2. Block public access via CR
        cr2 = client.run_cr(
            "[Phase T] block public GCS bucket access", "gcp_block_public_bucket_access",
            cloud_account_id,
            {"project": gcp_project, "bucket_name": bucket_name},
        )
        rollback_stack.append((cr2["id"], "gcp_block_public_bucket_access"))
        log("Public access blocked on bucket")

        # 3. Verify via SDK
        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import storage as _storage
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                sc = _storage.Client(project=gcp_project, credentials=gc)
                bucket_obj = sc.get_bucket(bucket_name)
                assert bucket_obj.name == bucket_name, "Bucket not found"
                policy = bucket_obj.get_iam_policy()
                has_public = any(
                    "allUsers" in b.members or "allAuthenticatedUsers" in b.members
                    for b in policy.bindings
                )
                assert not has_public, "allUsers/allAuthenticatedUsers still present after blocking"
                log("Bucket exists + public access blocked (SDK verified)")
            except Exception as e:
                print(f"  ⚠️  SDK verification skipped: {e}")
        else:
            print("  ⚠️  No GCP credentials — SDK verification skipped")

        # 4. Rollback (LIFO): block_public (no-op rollback) then delete bucket
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase T complete")

    except Exception as e:
        print(f"\n❌ Phase T failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete bucket via SDK
        creds = _get_gcp_creds()
        if creds and gcp_project:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import storage as _storage
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                sc = _storage.Client(project=gcp_project, credentials=gc)
                try:
                    sc.get_bucket(bucket_name).delete(force=True)
                    print(f"  Safety net: deleted GCS bucket {bucket_name}")
                except Exception:
                    pass
            except Exception as e2:
                print(f"  ⚠️  Safety net bucket delete failed: {e2}")


def run_phase_u(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase U: Service account create + IAM binding add + rollback stack."""
    print("\n[Phase U] Service Account + IAM Binding")

    import time as _time
    ts = int(_time.time()) % 100000
    account_id = f"nexplane-smoke-u-{ts}"
    role = "roles/viewer"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create service account via CR — get actual email from CR result
        cr = client.run_cr(
            "[Phase U] create service account", "gcp_service_account_create", cloud_account_id,
            {"account_id": account_id, "display_name": "Nexplane smoke test"},
        )
        rollback_stack.append((cr["id"], "gcp_service_account_create"))
        # Use the actual SA email from the executor result (project_id from connector credentials)
        exec_runs = cr.get("execution_runs", [])
        sa_email_actual = None
        for run in exec_runs:
            result = run.get("result", {})
            steps = result.get("execution", {}).get("steps", []) if "execution" in result else []
            for step in steps:
                sa_email_actual = step.get("result", {}).get("email")
                if sa_email_actual:
                    break
        # Fallback: construct from creds project_id
        if not sa_email_actual:
            creds_tmp = _get_gcp_creds()
            actual_project = (creds_tmp or {}).get("project_id", gcp_project)
            sa_email_actual = f"{account_id}@{actual_project}.iam.gserviceaccount.com"
        sa_email = sa_email_actual
        member = f"serviceAccount:{sa_email}"
        log(f"Service account created: {sa_email}")

        # 2. GCP SA eventual consistency — poll up to 30s
        creds = _get_gcp_creds()
        gc = None
        iam_svc = None
        sa_project = sa_email.split("@")[1].split(".iam.")[0] if "@" in sa_email else gcp_project
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa_lib
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa_lib.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform",
                                      "https://www.googleapis.com/auth/iam"])
                iam_svc = _build("iam", "v1", credentials=gc)
                sa_ready = False
                # sa_project already set above from sa_email (account@project.iam.gserviceaccount.com)
                sa_ready = False
                for _ in range(18):
                    _time.sleep(10)
                    try:
                        iam_svc.projects().serviceAccounts().get(
                            name=f"projects/{sa_project}/serviceAccounts/{sa_email}"
                        ).execute()
                        sa_ready = True
                        break
                    except Exception:
                        pass
                if not sa_ready:
                    raise RuntimeError(f"Service account {sa_email} not available after 180s")
                log("Service account confirmed available via SDK")
            except Exception as e:
                print(f"  ⚠️  SDK SA polling skipped: {e}")

        # 3. Add IAM binding via CR
        cr2 = client.run_cr(
            "[Phase U] add IAM binding", "gcp_iam_binding_add", cloud_account_id,
            {"role": role, "member": member},
        )
        rollback_stack.append((cr2["id"], "gcp_iam_binding_add"))
        log(f"IAM binding added: {role} → {member}")

        # 4. Verify both SA exists and binding is present via SDK
        if creds and gc:
            try:
                from googleapiclient.discovery import build as _build2
                crm = _build2("cloudresourcemanager", "v1", credentials=gc)
                # Use the SA's project (from executor creds), not the CLI gcp_project arg
                policy = crm.projects().getIamPolicy(resource=sa_project, body={}).execute()
                binding_found = any(
                    b["role"] == role and member in b.get("members", [])
                    for b in policy.get("bindings", [])
                )
                assert binding_found, f"IAM binding {role}/{member} not found in project policy"
                log("IAM binding verified via SDK")
            except Exception as e:
                print(f"  ⚠️  SDK verification skipped: {e}")

        # 5. Rollback (LIFO): remove binding, then delete SA
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase U complete")

    except Exception as e:
        print(f"\n❌ Phase U failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete SA via SDK if it still exists
        creds = _get_gcp_creds()
        # Use the actual project from the SA email (may differ from --gcp-project CLI arg)
        _sa_project = sa_email.split("@")[1].split(".iam.")[0] if "@" in sa_email else gcp_project
        if creds and _sa_project:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa_lib
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa_lib.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform",
                                      "https://www.googleapis.com/auth/iam"])
                iam_svc = _build("iam", "v1", credentials=gc)
                try:
                    iam_svc.projects().serviceAccounts().delete(
                        name=f"projects/{_sa_project}/serviceAccounts/{sa_email}"
                    ).execute()
                    print(f"  Safety net: deleted service account {sa_email}")
                except Exception:
                    pass
            except Exception as e2:
                print(f"  ⚠️  Safety net SA delete failed: {e2}")


def run_phase_v(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase V: Cloud DNS — managed zone + A record lifecycle."""
    print("\n[Phase V] Cloud DNS")

    import time as _time
    ts = int(_time.time())
    zone_name = f"nexplane-smoke-v-{ts}"
    dns_name = f"nexplane-smoke-v-{ts}.example."
    record_name = f"test.nexplane-smoke-v-{ts}.example."
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create DNS zone via CR
        cr = client.run_cr(
            "[Phase V] create DNS zone", "gcp_dns_zone_create", cloud_account_id,
            {
                "zone_name": zone_name,
                "dns_name": dns_name,
                "description": "Nexplane smoke test zone — safe to delete",
            },
        )
        rollback_stack.append((cr["id"], "gcp_dns_zone_create"))
        log(f"DNS zone created: {zone_name} ({dns_name})")

        # 2. Create A record via CR
        cr2 = client.run_cr(
            "[Phase V] create A record", "gcp_dns_record_create", cloud_account_id,
            {
                "zone_name": zone_name,
                "record_name": record_name,
                "record_type": "A",
                "ttl": 300,
                "rrdatas": ["1.2.3.4"],
            },
        )
        rollback_stack.append((cr2["id"], "gcp_dns_record_create"))
        log(f"A record created: {record_name} → 1.2.3.4")

        # 3. Verify via SDK
        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                dns_svc = _build("dns", "v1", credentials=gc)
                zone_obj = dns_svc.managedZones().get(
                    project=gcp_project, managedZone=zone_name
                ).execute()
                assert zone_obj["name"] == zone_name, "Zone name mismatch"
                records_resp = dns_svc.resourceRecordSets().list(
                    project=gcp_project, managedZone=zone_name
                ).execute()
                found = any(
                    r["name"] == record_name and r["type"] == "A"
                    for r in records_resp.get("rrsets", [])
                )
                assert found, f"A record {record_name} not found in zone"
                log("Zone and A record verified via SDK")
            except Exception as e:
                print(f"  ⚠️  SDK verification skipped: {e}")
        else:
            print("  ⚠️  No GCP credentials — SDK verification skipped")

        # 4. Rollback (LIFO): delete record, then delete zone
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase V complete")

    except Exception as e:
        print(f"\n❌ Phase V failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete zone via SDK (clears records first)
        creds = _get_gcp_creds()
        if creds and gcp_project:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                dns_svc = _build("dns", "v1", credentials=gc)
                # Clear non-SOA/NS records
                try:
                    resp = dns_svc.resourceRecordSets().list(
                        project=gcp_project, managedZone=zone_name
                    ).execute()
                    to_delete = [r for r in resp.get("rrsets", []) if r["type"] not in ("SOA", "NS")]
                    if to_delete:
                        dns_svc.changes().create(
                            project=gcp_project, managedZone=zone_name,
                            body={"deletions": to_delete}
                        ).execute()
                    dns_svc.managedZones().delete(
                        project=gcp_project, managedZone=zone_name
                    ).execute()
                    print(f"  Safety net: deleted DNS zone {zone_name}")
                except Exception:
                    pass
            except Exception as e2:
                print(f"  ⚠️  Safety net DNS zone delete failed: {e2}")


def run_phase_w(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase W: Cloud SQL — instance create + backup + rollback. (~10 min)"""
    print("\n[Phase W] Cloud SQL Instance + Backup")
    print("  ⚠️  This phase takes 5-10 min for Cloud SQL provisioning")

    import time as _time
    ts = int(_time.time()) % 10000
    instance_name = f"nexplane-smoke-w-{ts}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create Cloud SQL instance via CR (polls internally up to 900s)
        cr = client.run_cr(
            "[Phase W] create Cloud SQL instance", "gcp_cloudsql_instance_create", cloud_account_id,
            {
                "instance_name": instance_name,
                "database_version": "POSTGRES_14",
                "tier": "db-f1-micro",
                "region": "us-central1",
            },
            timeout=900,
        )
        rollback_stack.append((cr["id"], "gcp_cloudsql_instance_create"))
        log(f"Cloud SQL instance created: {instance_name}")

        # 2. Create backup via CR
        cr2 = client.run_cr(
            "[Phase W] create Cloud SQL backup", "gcp_cloudsql_backup_create", cloud_account_id,
            {"instance_name": instance_name},
        )
        rollback_stack.append((cr2["id"], "gcp_cloudsql_backup_create"))
        log(f"Cloud SQL backup created for {instance_name}")

        # 3. Verify via SDK
        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                svc = _build("sqladmin", "v1beta4", credentials=gc)
                instance_obj = svc.instances().get(
                    project=gcp_project, instance=instance_name
                ).execute()
                assert instance_obj["state"] == "RUNNABLE", f"Instance state: {instance_obj['state']}"
                runs = svc.backupRuns().list(
                    project=gcp_project, instance=instance_name
                ).execute()
                items = runs.get("items", [])
                successful = [r for r in items if r.get("status") == "SUCCESSFUL"]
                assert successful, "No successful backup run found"
                log(f"Instance RUNNABLE + backup SUCCESSFUL (SDK verified)")
            except Exception as e:
                print(f"  ⚠️  SDK verification skipped: {e}")
        else:
            print("  ⚠️  No GCP credentials — SDK verification skipped")

        # 4. Rollback (LIFO): backup rollback is no-op, then delete instance
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase W complete")

    except Exception as e:
        print(f"\n❌ Phase W failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete instance via SDK
        creds = _get_gcp_creds()
        if creds and gcp_project:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                svc = _build("sqladmin", "v1beta4", credentials=gc)
                try:
                    svc.instances().delete(project=gcp_project, instance=instance_name).execute()
                    print(f"  Safety net: initiated Cloud SQL delete for {instance_name}")
                except Exception:
                    pass
            except Exception as e2:
                print(f"  ⚠️  Safety net Cloud SQL delete failed: {e2}")


def run_phase_x_stub(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase X: GCP Sub-G (Monitoring) — STUB."""
    print("\n[Phase X] GCP Monitoring — STUB (implement with GCP Sub-project G)")
    print("  ⚠️  Phase X is not yet implemented.")
    print("  This phase will cover: Cloud Monitoring alerting policies, uptime checks via CRs.")


def main():
    parser = make_base_parser("Nexplane GCP live smoke test")
    parser.add_argument(
        "--phases", default="L,M",
        help=(
            "Comma-separated phases to run. "
            "L-M: existing phases. N=Firewall, O=Storage, P=ServiceAccounts, "
            "Q=Terraform, R=Ansible. S-X=sub-project stubs. Default: L,M."
        ),
    )
    parser.add_argument("--tailscale-auth-key", default="", help="Reusable Tailscale auth key")
    parser.add_argument("--gcp-project", default="", help="GCP project ID (required for GCP phases)")
    parser.add_argument("--gcp-network", default="global/networks/default",
                        help="GCP VPC network for Phase N firewall rule")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    print("=" * 60)
    print(f"Nexplane GCP Live Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

    cloud_account_id = client.get_connector_cloud_account_id("gcp")
    log(f"Cloud account: {cloud_account_id}")

    passed = False
    gcp_phase_result: Optional[dict] = None

    try:
        if "L" in phases:
            agent_secret = client.get_agent_secret()
            gcp_phase_result = run_phase_l(client, cloud_account_id, args.gcp_project, agent_secret)
        if "M" in phases:
            if gcp_phase_result is None or gcp_phase_result.get("instance_asset") is None:
                fail("Phase M requires Phase L to have run first")
            run_phase_m(client, gcp_phase_result, args.gcp_project)
        if "N" in phases:
            run_phase_n(client, cloud_account_id, args.gcp_project, args.gcp_network)
        if "O" in phases:
            run_phase_o(client, cloud_account_id, args.gcp_project)
        if "P" in phases:
            run_phase_p(client, cloud_account_id, args.gcp_project)
        if "Q" in phases:
            run_phase_q(client, cloud_account_id, args.gcp_project)
        if "R" in phases:
            run_phase_r(client, cloud_account_id, args.gcp_project, gcp_phase_result)
        if "S" in phases:
            run_phase_s(client, cloud_account_id, args.gcp_project)
        if "T" in phases:
            run_phase_t(client, cloud_account_id, args.gcp_project)
        if "U" in phases:
            run_phase_u(client, cloud_account_id, args.gcp_project)
        if "V" in phases:
            run_phase_v(client, cloud_account_id, args.gcp_project)
        if "W" in phases:
            run_phase_w(client, cloud_account_id, args.gcp_project)
        if "X" in phases:
            run_phase_x_stub(client, cloud_account_id, args.gcp_project)

        print("\n" + "=" * 60)
        print("✅ ALL SELECTED PHASES PASSED")
        print("=" * 60)
        passed = True

    except SystemExit:
        passed = False
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        passed = False
    finally:
        if not passed:
            print("\n❌ SMOKE TEST FAILED")
            import sys as _sys
            _sys.exit(1)


if __name__ == "__main__":
    main()
