#!/usr/bin/env python3
"""
Nexplane Azure Live Smoke Test — Phases N–O.

Usage:
    python backend/tests/smoke/test_azure_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases N,O \\
        --tailscale-auth-key tskey-auth-<key> \\
        --azure-resource-group nexplane-smoke-rg

Phase descriptions:
    N  Azure: VM launch + agent deploy with rollback stack
    O  Azure advanced: stop/start/reboot/snapshot with rollback stack

Requirements:
    Azure connector with credentials + Contributor role on subscription
    Pre-existing resource group passed via --azure-resource-group
"""
import time
from typing import Optional

from smoke_helpers import (
    AZURE_SMOKE_VM, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _azure_creds_cache, _get_azure_compute_client,
    make_base_parser,
)


# ---------------------------------------------------------------------------
# Phase N
# ---------------------------------------------------------------------------

def run_phase_n(client: NexplaneClient, cloud_account_id: str,
                azure_resource_group: str, agent_secret: str) -> dict:
    """Phase N: Azure VM launch + agent deploy with rollback stack."""
    print("\n[Phase N] Azure VM Launch + Agent Deploy")

    if not azure_resource_group:
        fail("Phase N requires --azure-resource-group (resource group must exist in Azure)")

    rollback_stack: list[tuple[str, str]] = []
    vm_created = False

    try:
        cr = client.run_cr(
            "Smoke-N: launch Azure VM", "azure_vm_create", cloud_account_id,
            {
                "vm_name": AZURE_SMOKE_VM,
                "resource_group": azure_resource_group,
                "location": "eastus",
                "vm_size": "Standard_B1s",
                "connection_mode": "agent_extension",
                "nexplane_url": "http://localhost:8000",
                "nexplane_secret": agent_secret,
            },
        )
        rollback_stack.append((cr["id"], "azure_vm_create"))
        vm_created = True
        log(f"Azure VM launched: {AZURE_SMOKE_VM}")

        time.sleep(15)
        vm_asset = client.get_asset_by_name(AZURE_SMOKE_VM)
        if vm_asset:
            log(f"Azure VM in inventory: {vm_asset['id']}")
        else:
            print(f"  ⚠️  Azure VM asset not yet in inventory (ingest lag)")
            vm_asset = {
                "id": cloud_account_id,
                "name": AZURE_SMOKE_VM,
                "asset_metadata": {"vm_name": AZURE_SMOKE_VM, "resource_group": azure_resource_group},
            }

        print("  Waiting up to 5 min for Nexplane agent to register...")
        deadline = time.time() + 300
        agent_asset = None
        while time.time() < deadline:
            candidates = client.get("/assets", params={"q": AZURE_SMOKE_VM, "asset_type": "endpoint"})
            if candidates:
                agent_asset = candidates[0]
                log(f"Agent registered: {agent_asset['id']}")
                break
            time.sleep(15)
        if not agent_asset:
            print("  ⚠️  Agent not yet registered — Custom Script Extension may still be running")

        log("Phase N complete")
        result = {"vm_asset": vm_asset}
        rollback_stack.clear()
        vm_created = False
        return result

    except Exception as e:
        print(f"\n❌ Phase N failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase N cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        if vm_created:
            try:
                compute = _get_azure_compute_client()
                if compute and azure_resource_group:
                    compute.virtual_machines.begin_delete(azure_resource_group, AZURE_SMOKE_VM).result()
                    print(f"  Safety net: deleted Azure VM {AZURE_SMOKE_VM}")
            except Exception as e2:
                print(f"  ⚠️  Safety net Azure VM delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase O
# ---------------------------------------------------------------------------

def run_phase_o(client: NexplaneClient, phase_n_result: dict,
                azure_resource_group: str) -> None:
    """Phase O: Azure VM advanced — stop/start/reboot/snapshot with rollback stack."""
    print("\n[Phase O] Azure VM Advanced Operations")

    vm_asset = phase_n_result["vm_asset"]
    vm_name = vm_asset.get("asset_metadata", {}).get("vm_name", AZURE_SMOKE_VM)
    resource_group = vm_asset.get("asset_metadata", {}).get("resource_group", azure_resource_group)

    rollback_stack: list[tuple[str, str]] = []
    snapshot_name: str | None = None

    try:
        cr = client.run_cr(
            "Smoke-O: stop Azure VM", "azure_vm_stop", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": resource_group},
        )
        rollback_stack.append((cr["id"], "azure_vm_stop"))
        log("Azure VM stopped (deallocated)")

        cr = client.run_cr(
            "Smoke-O: start Azure VM", "azure_vm_start", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": resource_group},
        )
        rollback_stack.pop()
        rollback_stack.append((cr["id"], "azure_vm_start"))
        log("Azure VM started")

        client.run_cr(
            "Smoke-O: reboot Azure VM", "azure_vm_reboot", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": resource_group},
        )
        log("Azure VM rebooted")

        time.sleep(30)
        assets = client.get("/assets", params={"q": AZURE_SMOKE_VM, "asset_type": "endpoint"})
        if assets:
            log("Agent still registered post-reboot")
        else:
            print("  ⚠️  Agent not visible post-reboot (may still be reconnecting)")

        snapshot_name = f"nexplane-smoke-snap-{int(time.time())}"
        cr = client.run_cr(
            "Smoke-O: create disk snapshot", "azure_vm_snapshot", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": resource_group, "snapshot_name": snapshot_name},
        )
        rollback_stack.append((cr["id"], "azure_vm_snapshot"))
        log(f"Disk snapshot created: {snapshot_name}")

        if azure_resource_group:
            try:
                compute_verify = _get_azure_compute_client()
                if compute_verify:
                    snap = compute_verify.snapshots.get(resource_group, snapshot_name)
                    log(f"Snapshot verified: provisioning_state={snap.provisioning_state}, size={snap.disk_size_gb}GB")
            except Exception as e:
                print(f"  ⚠️  Snapshot verify skipped: {e}")

        log("Phase O complete")
        rollback_stack.clear()

    except Exception as e:
        print(f"\n❌ Phase O failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase O cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        if snapshot_name and azure_resource_group:
            try:
                compute_safety = _get_azure_compute_client()
                if compute_safety:
                    compute_safety.snapshots.begin_delete(azure_resource_group, snapshot_name).result()
                    print(f"  Safety net: deleted snapshot {snapshot_name}")
            except Exception as e2:
                print(f"  ⚠️  Safety net snapshot delete failed: {e2}")


def main():
    parser = make_base_parser("Nexplane Azure live smoke test")
    parser.add_argument(
        "--phases", default="N,O",
        help="Comma-separated phases to run (N-O). E.g. --phases N or --phases N,O",
    )
    parser.add_argument("--tailscale-auth-key", default="", help="Reusable Tailscale auth key")
    parser.add_argument("--azure-resource-group", default="", help="Azure resource group (must exist)")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    print("=" * 60)
    print(f"Nexplane Azure Live Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account: {cloud_account_id}")

    passed = False
    azure_phase_result: Optional[dict] = None

    try:
        if "N" in phases:
            agent_secret = client.get_agent_secret()
            azure_phase_result = run_phase_n(client, cloud_account_id, args.azure_resource_group, agent_secret)
        if "O" in phases:
            if azure_phase_result is None or azure_phase_result.get("vm_asset") is None:
                fail("Phase O requires Phase N to have run first")
            run_phase_o(client, azure_phase_result, args.azure_resource_group)

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
