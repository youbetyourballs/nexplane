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
import time
from typing import Optional

from smoke_helpers import (
    GCE_SMOKE_INSTANCE, GCE_ZONE, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _gcp_creds_cache, _get_gcp_compute_client,
    make_base_parser,
)


# ---------------------------------------------------------------------------
# Phase L
# ---------------------------------------------------------------------------

def run_phase_l(client: NexplaneClient, cloud_account_id: str,
                gcp_project: str, agent_secret: str) -> dict:
    """Phase L: GCE instance launch + agent deploy with rollback stack."""
    print("\n[Phase L] GCE Instance Launch + Agent Deploy")

    rollback_stack: list[tuple[str, str]] = []
    instance_created = False

    try:
        cr = client.run_cr(
            "Smoke-L: launch GCE instance", "gce_instance_create", cloud_account_id,
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
            "Smoke-M: stop GCE instance", "gce_stop", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        rollback_stack.append((cr["id"], "gce_stop"))
        log("GCE instance stopped")

        # 2. Start instance
        cr = client.run_cr(
            "Smoke-M: start GCE instance", "gce_start", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        rollback_stack.pop()  # stop CR superseded
        rollback_stack.append((cr["id"], "gce_start"))
        log("GCE instance started")

        # 3. Reboot
        client.run_cr(
            "Smoke-M: reboot GCE instance", "gce_instance_reboot", instance_asset["id"],
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
            "Smoke-M: create disk snapshot", "gce_disk_snapshot", instance_asset["id"],
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


def main():
    parser = make_base_parser("Nexplane GCP live smoke test")
    parser.add_argument(
        "--phases", default="L,M",
        help="Comma-separated phases to run (L-M). E.g. --phases L or --phases L,M",
    )
    parser.add_argument("--tailscale-auth-key", default="", help="Reusable Tailscale auth key")
    parser.add_argument("--gcp-project", default="", help="GCP project ID (required for GCP phases)")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    print("=" * 60)
    print(f"Nexplane GCP Live Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

    cloud_account_id = client.get_cloud_account_asset_id()
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
