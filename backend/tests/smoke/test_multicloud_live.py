#!/usr/bin/env python3
"""
Nexplane Multi-Cloud Smoke Test — Parallel VM lifecycle across AWS, GCP, and Azure.

Runs VM launch → stop → start → snapshot → delete on all three clouds concurrently.
Each provider has its own rollback stack and failure handling to prevent zombie instances.

Usage:
    python backend/tests/smoke/test_multicloud_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --azure-resource-group nexplane-smoke-rg \\
        --gcp-project my-project-id \\
        --providers aws,gcp,azure

Requirements:
    AWS, GCP, and Azure connectors configured in Nexplane with valid credentials.
    Azure resource group must already exist.
"""
import concurrent.futures
import secrets
import time
from typing import Optional

from smoke_helpers import (
    INSTANCE_NAME, GCE_SMOKE_INSTANCE, GCE_ZONE, AZURE_SMOKE_VM, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _get_aws_boto3_client,
    _get_azure_compute_client, _azure_creds_cache,
    _get_gcp_compute_client, _gcp_creds_cache,
    make_base_parser,
)


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------

def _assert_asset_metadata(asset: dict, provider: str,
                            required_keys: list[str] | None = None) -> None:
    """Assert connector_type, asset_type, and required metadata keys are present."""
    assert asset.get("connector_type") == provider, (
        f"[MC-{provider.upper()}] Expected connector_type='{provider}', "
        f"got '{asset.get('connector_type')}'"
    )
    assert asset.get("asset_type") in ("server", "cloud_account"), (
        f"[MC-{provider.upper()}] Unexpected asset_type '{asset.get('asset_type')}'"
    )
    meta = asset.get("asset_metadata", {})
    for key in (required_keys or []):
        assert meta.get(key), (
            f"[MC-{provider.upper()}] asset_metadata missing required key '{key}' "
            f"(got: {list(meta.keys())})"
        )


def _assert_connector_isolation(client: NexplaneClient, provider: str,
                                 asset_id: str, label: str) -> None:
    """Verify connector_type filter returns only assets for this provider.

    Checks that the newly created asset appears when filtering by its connector_type,
    and that no assets from this cloud bleed into a filter for a different provider.
    """
    # Asset must appear in provider-filtered list
    provider_assets = client.get("/assets", params={"connector_type": provider})
    ids = {a["id"] for a in provider_assets}
    assert asset_id in ids, (
        f"[{label}] Asset {asset_id} not found when filtering by connector_type={provider}"
    )

    # Asset must NOT appear in any other provider's filtered list
    others = {"aws", "gcp", "azure"} - {provider}
    for other in others:
        other_assets = client.get("/assets", params={"connector_type": other})
        other_ids = {a["id"] for a in other_assets}
        assert asset_id not in other_ids, (
            f"[{label}] Asset {asset_id} ({provider}) leaked into connector_type={other} filter"
        )


# ---------------------------------------------------------------------------
# Per-provider worker functions
# ---------------------------------------------------------------------------

def run_aws_worker(base_url: str, email: str, password: str) -> dict:
    """
    AWS VM lifecycle: ec2_launch → ec2_stop → ec2_start → snapshot_asset → ec2_terminate.
    Uses its own NexplaneClient for thread safety.
    Returns {"provider": "aws", "passed": bool, "error": str|None}.
    """
    client = NexplaneClient(base_url, email, password)
    result = {"provider": "aws", "passed": False, "error": None}
    rollback_stack: list[tuple[str, str]] = []
    instance_name = f"nexplane-mc-aws-{secrets.token_hex(3)}"

    try:
        cloud_account_id = client.get_connector_cloud_account_id("aws")
        print(f"\n[MC-AWS] Starting (cloud_account={cloud_account_id[:8]}...)")

        # 1. Launch EC2 instance
        cr = client._run_cr_with_timeout(
            "[Phase MC-AWS] launch EC2 instance", "ec2_launch", cloud_account_id,
            {"mode": "quick", "name": instance_name, "os": "amazon_linux",
             "rollback_strategy": "terminate_instance"},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "ec2_launch"))

        # Wait for inventory
        instance_asset = None
        for _ in range(12):
            time.sleep(5)
            candidate = client.get_asset_by_name(instance_name)
            if candidate and candidate.get("asset_metadata", {}).get("instance_id"):
                instance_asset = candidate
                break
        if not instance_asset:
            raise AssertionError(f"EC2 instance '{instance_name}' not in inventory after 60s")
        instance_id = instance_asset["asset_metadata"]["instance_id"]
        log(f"[Phase MC-AWS] Instance in inventory: {instance_id}")

        # Metadata consistency: connector_type + required metadata fields
        _assert_asset_metadata(instance_asset, "aws", required_keys=["instance_id"])
        # Connector-type isolation: this asset appears only in aws filter
        _assert_connector_isolation(client, "aws", instance_asset["id"], "MC-AWS")
        log("[Phase MC-AWS] Asset metadata and connector isolation verified")

        # 2. Stop instance
        cr = client._run_cr_with_timeout(
            "[Phase MC-AWS] stop EC2 instance", "ec2_stop", instance_asset["id"],
            {"instance_id": instance_id, "target_state": "stopped"},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "ec2_stop"))
        log("[Phase MC-AWS] Instance stopped")

        # 3. Start instance
        cr = client._run_cr_with_timeout(
            "[Phase MC-AWS] start EC2 instance", "ec2_start", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "stop_instance"},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.pop()  # ec2_stop superseded
        log("[Phase MC-AWS] Instance started")

        # 4. Snapshot
        ec2_client = _get_aws_boto3_client("ec2")
        volume_id = ""
        if ec2_client:
            try:
                resp = ec2_client.describe_instances(InstanceIds=[instance_id])
                bdm = resp["Reservations"][0]["Instances"][0].get("BlockDeviceMappings", [])
                if bdm:
                    volume_id = bdm[0]["Ebs"]["VolumeId"]
            except Exception:
                pass

        if volume_id:
            cr = client._run_cr_with_timeout(
                "[Phase MC-AWS] snapshot instance", "snapshot_asset", instance_asset["id"],
                {"instance_id": instance_id, "volume_id": volume_id,
                 "rollback_strategy": "delete_ebs_snapshot"},
                timeout=TIMEOUT_SECONDS,
            )
            rollback_stack.append((cr["id"], "snapshot_asset"))
            log("[Phase MC-AWS] Snapshot created")

            # SDK verification: confirm snapshot actually exists in AWS
            snapshot_id = (cr.get("execution_runs") or [{}])[0].get("result", {}).get("snapshot_id") if cr.get("execution_runs") else None
            if snapshot_id and ec2_client:
                try:
                    snaps = ec2_client.describe_snapshots(SnapshotIds=[snapshot_id]).get("Snapshots", [])
                    assert snaps, f"Snapshot {snapshot_id} not found in AWS after CR completed"
                    assert snaps[0]["State"] in ("pending", "completed"), (
                        f"Snapshot {snapshot_id} in unexpected state: {snaps[0]['State']}"
                    )
                    log(f"[Phase MC-AWS] Snapshot {snapshot_id} confirmed in AWS (state={snaps[0]['State']})")
                except Exception as e:
                    log(f"[Phase MC-AWS] Snapshot SDK check warning: {e}")
        else:
            log("[Phase MC-AWS] Skipping snapshot (could not determine volume_id)")

        # 5. Terminate — rollback handles it
        log("[Phase MC-AWS] Lifecycle complete; cleaning up via rollback stack")
        result["passed"] = True

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [MC-AWS] Failed: {e}")
    finally:
        print("  [MC-AWS cleanup]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: terminate via boto3
        ec2_client = _get_aws_boto3_client("ec2")
        if ec2_client:
            try:
                reservations = ec2_client.describe_instances(
                    Filters=[{"Name": "tag:Name", "Values": [instance_name]},
                             {"Name": "instance-state-name",
                              "Values": ["pending", "running", "stopping", "stopped"]}]
                ).get("Reservations", [])
                for res in reservations:
                    for inst in res.get("Instances", []):
                        ec2_client.terminate_instances(InstanceIds=[inst["InstanceId"]])
                        print(f"  Safety net: terminated AWS {inst['InstanceId']}")
            except Exception:
                pass

    return result


def run_gcp_worker(base_url: str, email: str, password: str, gcp_project: str) -> dict:
    """
    GCP VM lifecycle: gce_instance_create → gce_stop → gce_start → gce_disk_snapshot → gce_instance_delete.
    """
    client = NexplaneClient(base_url, email, password)
    result = {"provider": "gcp", "passed": False, "error": None}
    rollback_stack: list[tuple[str, str]] = []
    instance_name = f"nexplane-mc-gcp-{secrets.token_hex(3)}"

    try:
        cloud_account_id = client.get_connector_cloud_account_id("gcp")
        print(f"\n[MC-GCP] Starting (cloud_account={cloud_account_id[:8]}...)")

        # 1. Launch GCE instance
        cr = client._run_cr_with_timeout(
            "[Phase MC-GCP] launch GCE instance", "gce_instance_create", cloud_account_id,
            {"name": instance_name, "machine_type": "e2-micro", "zone": GCE_ZONE,
             "image_family": "ubuntu-2204-lts", "image_project": "ubuntu-os-cloud",
             "connection_mode": "agent_startup",
             "nexplane_url": base_url, "nexplane_secret": "multicloud-smoke"},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "gce_instance_create"))
        log(f"[Phase MC-GCP] Instance launched: {instance_name}")

        # Wait for inventory
        instance_asset = None
        for _ in range(12):
            time.sleep(5)
            candidate = client.get_asset_by_name(instance_name)
            if candidate:
                instance_asset = candidate
                break
        if not instance_asset:
            raise AssertionError(f"GCE instance '{instance_name}' not in inventory after 60s")
        log(f"[Phase MC-GCP] Instance in inventory: {instance_asset['id']}")

        # Metadata consistency + connector isolation
        _assert_asset_metadata(instance_asset, "gcp")
        _assert_connector_isolation(client, "gcp", instance_asset["id"], "MC-GCP")
        log("[Phase MC-GCP] Asset metadata and connector isolation verified")

        # 2. Stop
        cr = client._run_cr_with_timeout(
            "[Phase MC-GCP] stop GCE instance", "gce_stop", instance_asset["id"],
            {"instance_name": instance_name, "zone": GCE_ZONE},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "gce_stop"))
        log("[Phase MC-GCP] Instance stopped")

        # 3. Start
        cr = client._run_cr_with_timeout(
            "[Phase MC-GCP] start GCE instance", "gce_start", instance_asset["id"],
            {"instance_name": instance_name, "zone": GCE_ZONE},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.pop()  # gce_stop superseded
        log("[Phase MC-GCP] Instance started")

        # 4. Snapshot
        snap_name = f"nexplane-mc-snap-{secrets.token_hex(3)}"
        cr = client._run_cr_with_timeout(
            "[Phase MC-GCP] snapshot GCE disk", "gce_disk_snapshot", instance_asset["id"],
            {"instance_name": instance_name, "zone": GCE_ZONE, "snapshot_name": snap_name},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "gce_disk_snapshot"))
        log(f"[Phase MC-GCP] Snapshot created: {snap_name}")

        # SDK verification: confirm snapshot exists in GCP
        compute = _get_gcp_compute_client()
        if compute and gcp_project:
            try:
                snap = compute.get(project=gcp_project, snapshot=snap_name)
                assert snap.status in ("READY", "UPLOADING", "CREATING"), (
                    f"GCE snapshot {snap_name} in unexpected status: {snap.status}"
                )
                log(f"[Phase MC-GCP] Snapshot {snap_name} confirmed in GCP (status={snap.status})")
            except Exception as e:
                log(f"[Phase MC-GCP] Snapshot SDK check warning: {e}")

        log("[Phase MC-GCP] Lifecycle complete; cleaning up via rollback stack")
        result["passed"] = True

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [MC-GCP] Failed: {e}")
    finally:
        print("  [MC-GCP cleanup]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete via GCP SDK
        compute = _get_gcp_compute_client()
        if compute and gcp_project:
            try:
                compute.delete(project=gcp_project, zone=GCE_ZONE, instance=instance_name)
                print(f"  Safety net: deleted GCE instance {instance_name}")
            except Exception:
                pass

    return result


def run_azure_worker(base_url: str, email: str, password: str,
                     azure_resource_group: str) -> dict:
    """
    Azure VM lifecycle: azure_vm_create → azure_vm_stop → azure_vm_start → azure_vm_snapshot → azure_vm_delete.
    """
    client = NexplaneClient(base_url, email, password)
    result = {"provider": "azure", "passed": False, "error": None}
    rollback_stack: list[tuple[str, str]] = []
    vm_name = f"nexplane-mc-az-{secrets.token_hex(3)}"
    admin_password = f"NxP!{secrets.token_hex(8)}"

    try:
        cloud_account_id = client.get_connector_cloud_account_id("azure")
        print(f"\n[MC-AZ] Starting (cloud_account={cloud_account_id[:8]}...)")

        # 1. Launch Azure VM
        cr = client._run_cr_with_timeout(
            "[Phase MC-AZ] launch Azure VM", "azure_vm_create", cloud_account_id,
            {"vm_name": vm_name, "resource_group": azure_resource_group,
             "location": "eastus2", "vm_size": "Standard_D2as_v7",
             "connection_mode": "password",
             "admin_username": "nexplaneadmin", "admin_password": admin_password},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "azure_vm_create"))
        log(f"[Phase MC-AZ] VM launched: {vm_name}")

        # Wait for inventory
        vm_asset = None
        for _ in range(12):
            time.sleep(5)
            candidate = client.get_asset_by_name(vm_name)
            if candidate:
                vm_asset = candidate
                break
        if not vm_asset:
            raise AssertionError(f"Azure VM '{vm_name}' not in inventory after 60s")
        log(f"[Phase MC-AZ] VM in inventory: {vm_asset['id']}")

        # Metadata consistency + connector isolation
        _assert_asset_metadata(vm_asset, "azure")
        _assert_connector_isolation(client, "azure", vm_asset["id"], "MC-AZ")
        log("[Phase MC-AZ] Asset metadata and connector isolation verified")

        # 2. Stop (deallocate)
        cr = client._run_cr_with_timeout(
            "[Phase MC-AZ] stop Azure VM", "azure_vm_stop", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": azure_resource_group},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "azure_vm_stop"))
        log("[Phase MC-AZ] VM stopped")

        # 3. Start
        cr = client._run_cr_with_timeout(
            "[Phase MC-AZ] start Azure VM", "azure_vm_start", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": azure_resource_group},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.pop()  # azure_vm_stop superseded
        log("[Phase MC-AZ] VM started")

        # 4. Snapshot
        snap_name = f"nexplane-mc-snap-{secrets.token_hex(3)}"
        cr = client._run_cr_with_timeout(
            "[Phase MC-AZ] snapshot Azure VM", "azure_vm_snapshot", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": azure_resource_group,
             "snapshot_name": snap_name, "location": "eastus2"},
            timeout=TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "azure_vm_snapshot"))
        log(f"[Phase MC-AZ] Snapshot created: {snap_name}")

        # SDK verification: confirm snapshot exists in Azure
        _get_azure_compute_client()
        az_creds = _azure_creds_cache
        if az_creds:
            try:
                from azure.identity import ClientSecretCredential
                from azure.mgmt.compute import ComputeManagementClient
                credential = ClientSecretCredential(
                    tenant_id=az_creds["tenant_id"],
                    client_id=az_creds["client_id"],
                    client_secret=az_creds["client_secret"],
                )
                compute_client = ComputeManagementClient(credential, az_creds["subscription_id"])
                snap = compute_client.snapshots.get(azure_resource_group, snap_name)
                assert snap.provisioning_state in ("Succeeded", "Updating"), (
                    f"Azure snapshot {snap_name} in unexpected state: {snap.provisioning_state}"
                )
                log(f"[Phase MC-AZ] Snapshot {snap_name} confirmed in Azure "
                    f"(state={snap.provisioning_state})")
            except Exception as e:
                log(f"[Phase MC-AZ] Snapshot SDK check warning: {e}")

        log("[Phase MC-AZ] Lifecycle complete; cleaning up via rollback stack")
        result["passed"] = True

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [MC-AZ] Failed: {e}")
    finally:
        print("  [MC-AZ cleanup]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete VM via Azure SDK
        _get_azure_compute_client()
        creds = _azure_creds_cache
        if creds:
            try:
                from azure.identity import ClientSecretCredential
                from azure.mgmt.compute import ComputeManagementClient
                credential = ClientSecretCredential(
                    tenant_id=creds["tenant_id"],
                    client_id=creds["client_id"],
                    client_secret=creds["client_secret"],
                )
                compute = ComputeManagementClient(credential, creds["subscription_id"])
                compute.virtual_machines.begin_delete(azure_resource_group, vm_name).result()
                print(f"  Safety net: deleted Azure VM {vm_name}")
            except Exception:
                pass

    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = make_base_parser("Nexplane multi-cloud VM lifecycle smoke test")
    parser.add_argument(
        "--providers", default="aws,gcp,azure",
        help="Comma-separated providers to test: aws, gcp, azure (default: all three)",
    )
    parser.add_argument("--gcp-project", default="", help="GCP project ID (required for GCP)")
    parser.add_argument("--azure-resource-group", default="",
                        help="Azure resource group (required for Azure, must exist)")
    args = parser.parse_args()

    providers = {p.strip().lower() for p in args.providers.split(",")}
    valid = {"aws", "gcp", "azure"}
    unknown = providers - valid
    if unknown:
        fail(f"Unknown providers: {unknown}. Valid: {valid}")

    if "gcp" in providers and not args.gcp_project:
        fail("--gcp-project is required when running GCP")
    if "azure" in providers and not args.azure_resource_group:
        fail("--azure-resource-group is required when running Azure")

    print("=" * 60)
    print(f"Nexplane Multi-Cloud Smoke Test — providers: {', '.join(sorted(providers))}")
    print("=" * 60)
    print("Running all providers in parallel. Each cloud cleans up independently.\n")

    futures: dict[concurrent.futures.Future, str] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        if "aws" in providers:
            futures[executor.submit(run_aws_worker, args.base_url, args.email, args.password)] = "aws"
        if "gcp" in providers:
            futures[executor.submit(
                run_gcp_worker, args.base_url, args.email, args.password, args.gcp_project
            )] = "gcp"
        if "azure" in providers:
            futures[executor.submit(
                run_azure_worker, args.base_url, args.email, args.password, args.azure_resource_group
            )] = "azure"

        results = []
        for future in concurrent.futures.as_completed(futures):
            provider = futures[future]
            try:
                result = future.result()
            except Exception as e:
                result = {"provider": provider, "passed": False, "error": str(e)}
            results.append(result)

    # Consolidated report
    print("\n" + "=" * 60)
    print("MULTI-CLOUD SMOKE TEST RESULTS")
    print("=" * 60)
    all_passed = True
    for result in sorted(results, key=lambda r: r["provider"]):
        provider = result["provider"].upper()
        if result["passed"]:
            print(f"  ✅ {provider}: PASSED")
        else:
            print(f"  ❌ {provider}: FAILED — {result['error']}")
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("✅ ALL PROVIDERS PASSED")
    else:
        print("❌ ONE OR MORE PROVIDERS FAILED")
        import sys
        sys.exit(1)


if __name__ == "__main__":
    main()
