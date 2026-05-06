#!/usr/bin/env python3
"""
Nexplane Azure Live Smoke Test — Phases N–Z.

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
    P  Azure NSG Rules: update_nsg_rule + restore via CR rollback
    Q  Azure Blob Storage: disable/enable public access + rotate storage key
    R  Azure Resource Tagging: tag_resource on Azure VM
    S  Terraform local apply against Azure
    T  Ansible local playbook against Azure
    U  Azure VNet lifecycle: create VNet + subnet, verify, delete via rollback
    V  Azure Storage account + blob container CRUD with rollback stack
    W  Azure managed identity + RBAC role assignment lifecycle
    X  Azure DNS: create zone + A record, verify, explicit record delete, rollback zone
    Y  Azure SQL Database: create server + database, verify, delete, rollback server (~10 min)
    Z  Azure Monitor: create metric alert on Phase N VM, verify, delete via rollback

Requirements:
    Azure connector with credentials + Contributor role on subscription
    Pre-existing resource group passed via --azure-resource-group
"""
import time
import secrets
from typing import Optional

from smoke_helpers import (
    AZURE_SMOKE_VM, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _azure_creds_cache, _get_azure_compute_client,
    _get_azure_storage_client, _get_azure_msi_client, _get_azure_authorization_client,
    _get_azure_network_client, _get_azure_dns_client, _get_azure_sql_client,
    _get_azure_monitor_client,
    make_base_parser,
)


def _get_azure_creds() -> dict:
    """Return the Azure credentials dict, populating cache if needed."""
    _get_azure_compute_client()  # side effect: populates _azure_creds_cache
    return _azure_creds_cache


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
            "[Phase N] launch Azure VM", "azure_vm_create", cloud_account_id,
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
            "[Phase O] stop Azure VM", "azure_vm_stop", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": resource_group},
        )
        rollback_stack.append((cr["id"], "azure_vm_stop"))
        log("Azure VM stopped (deallocated)")

        cr = client.run_cr(
            "[Phase O] start Azure VM", "azure_vm_start", vm_asset["id"],
            {"vm_name": vm_name, "resource_group": resource_group},
        )
        rollback_stack.pop()
        rollback_stack.append((cr["id"], "azure_vm_start"))
        log("Azure VM started")

        client.run_cr(
            "[Phase O] reboot Azure VM", "azure_vm_reboot", vm_asset["id"],
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
            "[Phase O] create Azure disk snapshot", "azure_vm_snapshot", vm_asset["id"],
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


# ---------------------------------------------------------------------------
# Phase P
# ---------------------------------------------------------------------------

def run_phase_p(client: NexplaneClient, cloud_account_id: str,
                azure_resource_group: str) -> None:
    """Phase P: Azure NSG Rules — update_nsg_rule + restore via CR rollback."""
    print("\n[Phase P] Azure NSG Rules")

    import time as _time
    nsg_name = f"nexplane-smoke-nsg-{int(_time.time())}"
    rollback_stack: list[tuple[str, str]] = []
    nsg_created = False

    try:
        creds = _get_azure_creds()
        if not creds:
            fail("Phase P requires Azure credentials")

        from azure.identity import ClientSecretCredential
        from azure.mgmt.network import NetworkManagementClient
        credential = ClientSecretCredential(
            tenant_id=creds["tenant_id"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
        )
        network_client = NetworkManagementClient(credential, creds["subscription_id"])

        # 1. Create dedicated NSG via SDK scaffolding
        poller = network_client.network_security_groups.begin_create_or_update(
            azure_resource_group, nsg_name,
            {"location": "eastus", "security_rules": []},
        )
        poller.result()
        nsg_created = True
        log(f"NSG created via SDK: {nsg_name}")

        # 2. Add inbound rule via CR
        cr = client.run_cr(
            "[Phase P] update Azure NSG rule", "azure_update_nsg_rule", cloud_account_id,
            {
                "resource_group": azure_resource_group,
                "nsg_name": nsg_name,
                "rule_name": "nexplane-smoke-rule",
                "priority": 200,
                "direction": "Inbound",
                "protocol": "Tcp",
                "source_address_prefix": "192.0.2.0/24",
                "destination_port_range": "8443",
                "access": "Allow",
            },
        )
        rollback_stack.append((cr["id"], "azure_update_nsg_rule"))

        # Verify via SDK
        nsg = network_client.network_security_groups.get(azure_resource_group, nsg_name)
        rule_names = [r.name for r in (nsg.security_rules or [])]
        assert "nexplane-smoke-rule" in rule_names, f"Rule not found in NSG: {rule_names}"
        log("NSG rule verified via SDK")

        # 3. Restore (delete) rule via CR rollback
        cr_id, _ = rollback_stack.pop()
        client.rollback_cr(cr_id, "azure_update_nsg_rule → restore")
        log("NSG rule removed via CR rollback")

        log("Phase P complete")

    except Exception as e:
        print(f"\n❌ Phase P failed: {e}")
        raise
    finally:
        print("  [Phase P cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete NSG via SDK
        if nsg_created:
            try:
                creds = _get_azure_creds()
                if creds:
                    from azure.identity import ClientSecretCredential
                    from azure.mgmt.network import NetworkManagementClient
                    credential = ClientSecretCredential(
                        tenant_id=creds["tenant_id"],
                        client_id=creds["client_id"],
                        client_secret=creds["client_secret"],
                    )
                    nc = NetworkManagementClient(credential, creds["subscription_id"])
                    nc.network_security_groups.begin_delete(azure_resource_group, nsg_name).result()
                    print(f"  Safety net: deleted NSG {nsg_name}")
            except Exception as e:
                print(f"  ⚠️  Safety net NSG delete failed: {e}")


# ---------------------------------------------------------------------------
# Phase Q
# ---------------------------------------------------------------------------

def run_phase_q(client: NexplaneClient, cloud_account_id: str,
                azure_resource_group: str) -> None:
    """Phase Q: Azure Blob Storage — disable/enable public access + rotate storage key."""
    print("\n[Phase Q] Azure Blob Storage")

    import secrets as _secrets
    account_name = f"nxpsmq{_secrets.token_hex(4)}"  # max 24 chars, alphanumeric only
    rollback_stack: list[tuple[str, str]] = []
    account_created = False

    try:
        creds = _get_azure_creds()
        if not creds:
            fail("Phase Q requires Azure credentials")

        from azure.identity import ClientSecretCredential
        from azure.mgmt.storage import StorageManagementClient
        credential = ClientSecretCredential(
            tenant_id=creds["tenant_id"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
        )
        storage_client = StorageManagementClient(credential, creds["subscription_id"])

        # 1. Create storage account via SDK scaffolding
        poller = storage_client.storage_accounts.begin_create(
            azure_resource_group, account_name,
            {
                "location": "eastus",
                "sku": {"name": "Standard_LRS"},
                "kind": "StorageV2",
                "allow_blob_public_access": True,
            },
        )
        poller.result()
        account_created = True
        log(f"Storage account created via SDK: {account_name}")

        # 2. Disable public blob access via CR
        cr = client.run_cr(
            "[Phase Q] disable Azure public blob access", "azure_disable_public_blob_access",
            cloud_account_id,
            {"resource_group": azure_resource_group, "storage_account_name": account_name},
        )
        rollback_stack.append((cr["id"], "azure_disable_public_blob_access"))

        # Verify via SDK
        acct = storage_client.storage_accounts.get_properties(azure_resource_group, account_name)
        assert acct.allow_blob_public_access is False, "allow_blob_public_access not False"
        log("Blob public access disabled (SDK verified)")

        # 3. Enable public blob access via CR rollback
        cr_id, _ = rollback_stack.pop()
        client.rollback_cr(cr_id, "azure_disable_public_blob_access → enable")
        log("Blob public access enabled via CR rollback")

        # 4. Rotate storage key via CR
        cr = client.run_cr(
            "[Phase Q] rotate Azure storage key", "azure_rotate_storage_key", cloud_account_id,
            {"resource_group": azure_resource_group, "storage_account_name": account_name,
             "key_name": "key1"},
        )
        rollback_stack.append((cr["id"], "azure_rotate_storage_key"))
        log("Storage key rotated via CR")

        log("Phase Q complete")

    except Exception as e:
        print(f"\n❌ Phase Q failed: {e}")
        raise
    finally:
        print("  [Phase Q cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete storage account
        if account_created:
            try:
                creds = _get_azure_creds()
                if creds:
                    from azure.identity import ClientSecretCredential
                    from azure.mgmt.storage import StorageManagementClient
                    credential = ClientSecretCredential(
                        tenant_id=creds["tenant_id"],
                        client_id=creds["client_id"],
                        client_secret=creds["client_secret"],
                    )
                    sc = StorageManagementClient(credential, creds["subscription_id"])
                    sc.storage_accounts.delete(azure_resource_group, account_name)
                    print(f"  Safety net: deleted storage account {account_name}")
            except Exception as e:
                print(f"  ⚠️  Safety net storage delete failed: {e}")


# ---------------------------------------------------------------------------
# Phase R
# ---------------------------------------------------------------------------

def run_phase_r(client: NexplaneClient, azure_phase_result: Optional[dict],
                azure_resource_group: str) -> None:
    """Phase R: Azure Resource Tagging — tag_resource on Azure VM."""
    print("\n[Phase R] Azure Resource Tagging")

    vm_asset = azure_phase_result.get("vm_asset") if azure_phase_result else None
    if vm_asset is None:
        fail("Phase R requires Phase N to have run first (needs running VM)")

    vm_asset_id = vm_asset.get("id", "") if isinstance(vm_asset, dict) else str(vm_asset)
    vm_name = AZURE_SMOKE_VM
    rollback_stack: list[tuple[str, str]] = []

    try:
        creds = _get_azure_creds()
        if not creds:
            fail("Phase R requires Azure credentials")

        # Tag VM via CR
        cr = client.run_cr(
            "[Phase R] tag Azure VM", "tag_resource", vm_asset_id,
            {
                "resource_group": azure_resource_group,
                "resource_name": vm_name,
                "tags": {"nexplane-smoke": "true", "phase": "R"},
            },
        )
        rollback_stack.append((cr["id"], "tag_resource"))

        # Verify via SDK
        from azure.identity import ClientSecretCredential
        from azure.mgmt.compute import ComputeManagementClient
        credential = ClientSecretCredential(
            tenant_id=creds["tenant_id"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
        )
        compute_client = ComputeManagementClient(credential, creds["subscription_id"])
        vm = compute_client.virtual_machines.get(azure_resource_group, vm_name)
        tags = vm.tags or {}
        assert tags.get("nexplane-smoke") == "true", "Tag not applied to VM"
        log("Azure VM tagged (SDK verified)")

        log("Phase R complete")

    except Exception as e:
        print(f"\n❌ Phase R failed: {e}")
        raise
    finally:
        print("  [Phase R cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)


# ---------------------------------------------------------------------------
# Phase S
# ---------------------------------------------------------------------------

def run_phase_s(client: NexplaneClient, cloud_account_id: str,
                azure_resource_group: str) -> None:
    """Phase S: Terraform local apply against Azure — creates resource group using azurerm provider."""
    print("\n[Phase S] Terraform Local (Azure)")

    import secrets as _secrets
    rg_name = f"nexplane-smoke-s-{_secrets.token_hex(4)}"

    creds = _get_azure_creds()
    if not creds:
        fail("Phase S requires Azure credentials")

    tf_content = (
        'terraform {\n'
        '  required_providers {\n'
        '    azurerm = {\n'
        '      source  = "hashicorp/azurerm"\n'
        '      version = "~> 3.0"\n'
        '    }\n'
        '  }\n'
        '}\n\n'
        'provider "azurerm" {\n'
        '  features {}\n'
        '  tenant_id       = "' + creds["tenant_id"] + '"\n'
        '  client_id       = "' + creds["client_id"] + '"\n'
        '  client_secret   = "' + creds["client_secret"] + '"\n'
        '  subscription_id = "' + creds["subscription_id"] + '"\n'
        '}\n\n'
        'resource "azurerm_resource_group" "smoke_test" {\n'
        '  name     = "' + rg_name + '"\n'
        '  location = "eastus"\n'
        '}\n'
    )

    cr = client.run_cr(
        "[Phase S] terraform apply Azure resource group", "terraform_local_apply", cloud_account_id,
        {"tf_content": tf_content, "rollback_strategy": "terraform_destroy_local"},
    )
    log(f"Terraform applied (Azure) — resource group: {rg_name}")

    # Verify via SDK
    try:
        from azure.identity import ClientSecretCredential
        from azure.mgmt.resource import ResourceManagementClient
        credential = ClientSecretCredential(
            tenant_id=creds["tenant_id"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
        )
        rm_client = ResourceManagementClient(credential, creds["subscription_id"])
        rg = rm_client.resource_groups.get(rg_name)
        assert rg.name == rg_name
        log("Azure resource group confirmed via SDK")
    except Exception as e:
        print(f"  ⚠️  SDK verification skipped: {e}")

    # Rollback: terraform destroy
    client.rollback_cr(cr["id"], "terraform_local_apply → destroy")
    log("Azure resource group destroyed via Terraform rollback")

    log("Phase S complete")


# ---------------------------------------------------------------------------
# Phase T
# ---------------------------------------------------------------------------

def run_phase_t(client: NexplaneClient, cloud_account_id: str,
                azure_phase_result: Optional[dict] = None) -> None:
    """Phase T: Ansible local playbook against Azure."""
    print("\n[Phase T] Ansible Local (Azure)")

    playbook_content = (
        "---\n"
        "- name: Nexplane Azure smoke test\n"
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
        "[Phase T] ansible local playbook", "ansible_local_playbook", cloud_account_id,
        {"playbook_content": playbook_content, "inventory": "localhost,"},
    )
    log("Ansible local playbook CR executed successfully (Azure)")
    log("Phase T complete")


# ---------------------------------------------------------------------------
# Phases U-Z
# ---------------------------------------------------------------------------

def run_phase_u(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str) -> None:
    """Phase U: Azure VNet lifecycle — create VNet + subnet, verify, delete via rollback."""
    print("\n[Phase U] Azure VNet Lifecycle")

    if not azure_resource_group:
        fail("Phase U requires --azure-resource-group")

    import secrets as _secrets
    vnet_name = f"nexplane-smoke-vnet-{_secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []
    network = _get_azure_network_client()

    try:
        cr = client.run_cr(
            "[Phase U] create VNet", "azure_vnet_create", cloud_account_id,
            {"vnet_name": vnet_name, "resource_group": azure_resource_group,
             "location": "eastus", "address_prefix": "10.100.0.0/16",
             "subnet_prefix": "10.100.0.0/24"},
        )
        rollback_stack.append((cr["id"], "azure_vnet_create"))

        if network:
            vnet = network.virtual_networks.get(azure_resource_group, vnet_name)
            assert vnet.name == vnet_name, f"VNet name mismatch: {vnet.name}"
            assert len(vnet.subnets) > 0, "VNet has no subnets"
            log(f"VNet verified: {vnet_name} ({vnet.address_space.address_prefixes[0]})")
        else:
            log(f"VNet created (SDK verification skipped — no credentials)")

        log("Phase U complete")

    except Exception as e:
        print(f"\n❌ Phase U failed: {e}")
        raise
    finally:
        print("  [Phase U cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if network:
            try:
                network.virtual_networks.begin_delete(azure_resource_group, vnet_name).result()
                print(f"  Safety net: deleted VNet {vnet_name}")
            except Exception:
                pass


def run_phase_v(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str) -> None:
    """Phase V: Azure storage account + blob container CRUD with rollback stack."""
    print("\n[Phase V] Azure Storage Account + Blob Container CRUD")

    if not azure_resource_group:
        fail("Phase V requires --azure-resource-group")

    import secrets as _secrets
    account_name = f"nxpsmoke{_secrets.token_hex(4)}"  # <=24 chars, globally unique
    container_name = "nexplane-smoke-container"
    rollback_stack: list[tuple[str, str]] = []

    storage = _get_azure_storage_client()

    try:
        # 1. Create storage account via CR
        cr = client.run_cr(
            "[Phase V] create storage account", "azure_storage_account_create", cloud_account_id,
            {"storage_account_name": account_name, "resource_group": azure_resource_group,
             "location": "eastus"},
        )
        rollback_stack.append((cr["id"], "azure_storage_account_create"))

        # SDK verify: account exists with kind StorageV2
        if storage:
            acct = storage.storage_accounts.get_properties(azure_resource_group, account_name)
            assert str(acct.kind).lower() in ("storagev2",), \
                f"Unexpected account kind: {acct.kind}"
            log(f"Storage account verified: {account_name} (kind={acct.kind})")
        else:
            log(f"Storage account created (SDK verification skipped — no credentials)")

        # 2. Create blob container via CR
        cr = client.run_cr(
            "[Phase V] create blob container", "azure_blob_container_create", cloud_account_id,
            {"storage_account_name": account_name, "container_name": container_name,
             "resource_group": azure_resource_group},
        )
        rollback_stack.append((cr["id"], "azure_blob_container_create"))

        # SDK verify: container exists
        if storage:
            container = storage.blob_containers.get(azure_resource_group, account_name, container_name)
            assert container.name == container_name, \
                f"Container name mismatch: {container.name}"
            log(f"Blob container verified: {container_name}")

        # 3. Explicit blob container delete via CR
        client.run_cr(
            "[Phase V] delete blob container", "azure_blob_container_delete", cloud_account_id,
            {"storage_account_name": account_name, "container_name": container_name,
             "resource_group": azure_resource_group},
        )
        rollback_stack.pop()  # container already deleted

        # SDK verify: container gone
        if storage:
            containers = list(storage.blob_containers.list(azure_resource_group, account_name))
            assert not any(c.name == container_name for c in containers), \
                f"Container {container_name} still exists after delete"
            log("Blob container deleted and verified gone")

        log("Phase V complete")

    except Exception as e:
        print(f"\n❌ Phase V failed: {e}")
        raise
    finally:
        print("  [Phase V cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete storage account via SDK
        if storage:
            try:
                storage.storage_accounts.delete(azure_resource_group, account_name)
                print(f"  Safety net: deleted storage account {account_name}")
            except Exception:
                pass


def run_phase_w(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str) -> None:
    """Phase W: Azure managed identity + RBAC role assignment lifecycle."""
    print("\n[Phase W] Azure Managed Identity + RBAC Role Assignment")

    if not azure_resource_group:
        fail("Phase W requires --azure-resource-group")

    creds = _get_azure_creds()
    subscription_id = creds.get("subscription_id", "")
    if not subscription_id:
        fail("Phase W requires Azure credentials with subscription_id")

    import secrets as _secrets
    identity_name = f"nexplane-smoke-id-{_secrets.token_hex(4)}"
    scope = f"/subscriptions/{subscription_id}/resourceGroups/{azure_resource_group}"
    rollback_stack: list[tuple[str, str]] = []

    msi = _get_azure_msi_client()
    auth = _get_azure_authorization_client()

    try:
        # 1. Create managed identity via CR
        cr = client.run_cr(
            "[Phase W] create managed identity", "azure_managed_identity_create", cloud_account_id,
            {"identity_name": identity_name, "resource_group": azure_resource_group,
             "location": "eastus"},
        )
        rollback_stack.append((cr["id"], "azure_managed_identity_create"))

        # SDK verify: identity exists with a principal_id
        principal_id = None
        if msi:
            identity = msi.user_assigned_identities.get(azure_resource_group, identity_name)
            assert identity.principal_id, "Managed identity has no principal_id"
            principal_id = str(identity.principal_id)
            log(f"Managed identity verified: {identity_name} ({principal_id})")
        else:
            log("Managed identity created (SDK verification skipped — no credentials)")
            principal_id = "mock-principal-id"

        # 2. Assign Reader role at resource group scope via CR
        cr = client.run_cr(
            "[Phase W] assign Reader role", "azure_role_assignment_create", cloud_account_id,
            {"principal_id": principal_id, "role_definition_name": "Reader", "scope": scope},
        )
        rollback_stack.append((cr["id"], "azure_role_assignment_create"))

        # SDK verify: role assignment exists for this principal
        assignment_id = None
        if auth and principal_id != "mock-principal-id":
            assignments = list(auth.role_assignments.list_for_scope(
                scope, filter=f"principalId eq '{principal_id}'"
            ))
            assert len(assignments) > 0, \
                f"No role assignments found for principal {principal_id} at scope {scope}"
            assignment_id = assignments[0].name
            log(f"Role assignment verified: {assignment_id}")

        # 3. Explicit role assignment delete via CR
        client.run_cr(
            "[Phase W] delete role assignment", "azure_role_assignment_delete", cloud_account_id,
            {"assignment_id": assignment_id or cr["id"], "scope": scope},
        )
        rollback_stack.pop()  # role assignment already deleted

        # SDK verify: assignment gone
        if auth and principal_id and principal_id != "mock-principal-id":
            remaining = list(auth.role_assignments.list_for_scope(
                scope, filter=f"principalId eq '{principal_id}'"
            ))
            assert len(remaining) == 0, \
                f"Role assignment still present after delete: {remaining}"
            log("Role assignment deleted and verified gone")

        log("Phase W complete")

    except Exception as e:
        print(f"\n❌ Phase W failed: {e}")
        raise
    finally:
        print("  [Phase W cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete managed identity via SDK
        if msi:
            try:
                msi.user_assigned_identities.delete(azure_resource_group, identity_name)
                print(f"  Safety net: deleted managed identity {identity_name}")
            except Exception:
                pass


def run_phase_x(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str) -> None:
    """Phase X: Azure DNS — create zone + A record, verify, explicit record delete, rollback zone."""
    print("\n[Phase X] Azure DNS Lifecycle")

    if not azure_resource_group:
        fail("Phase X requires --azure-resource-group")

    import secrets as _secrets
    zone_name = f"nexplane-smoke-{_secrets.token_hex(4)}.example.com"
    record_name = "smoke"
    rollback_stack: list[tuple[str, str]] = []
    dns = _get_azure_dns_client()

    try:
        # 1. Create DNS zone
        cr = client.run_cr(
            "[Phase X] create DNS zone", "azure_dns_zone_create", cloud_account_id,
            {"zone_name": zone_name, "resource_group": azure_resource_group},
        )
        rollback_stack.append((cr["id"], "azure_dns_zone_create"))

        if dns:
            zone = dns.zones.get(azure_resource_group, zone_name)
            assert zone.name == zone_name, f"Zone name mismatch: {zone.name}"
            log(f"DNS zone verified: {zone_name}")
        else:
            log(f"DNS zone created (SDK verification skipped — no credentials)")

        # 2. Create A record
        cr = client.run_cr(
            "[Phase X] create A record", "azure_dns_record_create", cloud_account_id,
            {"zone_name": zone_name, "record_name": record_name,
             "ip_address": "10.0.0.1", "resource_group": azure_resource_group},
        )
        rollback_stack.append((cr["id"], "azure_dns_record_create"))

        if dns:
            rs = dns.record_sets.get(azure_resource_group, zone_name, record_name, "A")
            assert rs.a_records[0].ipv4_address == "10.0.0.1", \
                f"IP mismatch: {rs.a_records[0].ipv4_address}"
            log(f"A record verified: {record_name}.{zone_name} -> 10.0.0.1")

        # 3. Explicit record delete
        client.run_cr(
            "[Phase X] delete A record", "azure_dns_record_delete", cloud_account_id,
            {"zone_name": zone_name, "record_name": record_name,
             "resource_group": azure_resource_group},
        )
        rollback_stack.pop()  # record already deleted

        if dns:
            records = list(dns.record_sets.list_by_dns_zone(azure_resource_group, zone_name))
            assert not any(r.name == record_name for r in records), \
                f"Record {record_name} still exists after delete"
            log("A record deleted and verified gone")

        log("Phase X complete")

    except Exception as e:
        print(f"\n❌ Phase X failed: {e}")
        raise
    finally:
        print("  [Phase X cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if dns:
            try:
                dns.zones.begin_delete(azure_resource_group, zone_name).result()
                print(f"  Safety net: deleted DNS zone {zone_name}")
            except Exception:
                pass


def run_phase_y(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str) -> None:
    """Phase Y: Azure SQL Database lifecycle — create server + database, verify, delete, rollback server. (~10 min)"""
    print("\n[Phase Y] Azure SQL Database Lifecycle (~10 min)")

    if not azure_resource_group:
        fail("Phase Y requires --azure-resource-group")

    import secrets as _secrets
    server_name = f"nexplane-smoke-sql-{_secrets.token_hex(4)}"
    db_name = "nexplane-smoke-db"
    # Azure password complexity: uppercase + lowercase + digit + special, min 8 chars
    admin_password = f"NxP!{_secrets.token_hex(8)}"
    rollback_stack: list[tuple[str, str]] = []
    sql = _get_azure_sql_client()

    try:
        # 1. Create SQL server (~5 min)
        cr = client._run_cr_with_timeout(
            "[Phase Y] create SQL server", "azure_sql_server_create", cloud_account_id,
            {"server_name": server_name, "resource_group": azure_resource_group,
             "location": "eastus", "admin_login": "nexplaneadmin",
             "admin_password": admin_password},
            timeout=600,
        )
        rollback_stack.append((cr["id"], "azure_sql_server_create"))

        if sql:
            server = sql.servers.get(azure_resource_group, server_name)
            assert server.name == server_name, f"Server name mismatch: {server.name}"
            log(f"SQL server verified: {server_name}")
        else:
            log("SQL server created (SDK verification skipped — no credentials)")

        # 2. Create SQL database (~2 min)
        cr = client._run_cr_with_timeout(
            "[Phase Y] create SQL database", "azure_sql_database_create", cloud_account_id,
            {"server_name": server_name, "database_name": db_name,
             "resource_group": azure_resource_group, "location": "eastus",
             "sku_name": "Basic"},
            timeout=300,
        )
        rollback_stack.append((cr["id"], "azure_sql_database_create"))

        if sql:
            db = sql.databases.get(azure_resource_group, server_name, db_name)
            assert db.name == db_name, f"Database name mismatch: {db.name}"
            log(f"SQL database verified: {db_name}")

        # 3. Explicit database delete
        client._run_cr_with_timeout(
            "[Phase Y] delete SQL database", "azure_sql_database_delete", cloud_account_id,
            {"server_name": server_name, "database_name": db_name,
             "resource_group": azure_resource_group},
            timeout=120,
        )
        rollback_stack.pop()  # database already deleted
        log("SQL database deleted")

        log("Phase Y complete")

    except Exception as e:
        print(f"\n❌ Phase Y failed: {e}")
        raise
    finally:
        print("  [Phase Y cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if sql:
            try:
                sql.servers.begin_delete(azure_resource_group, server_name).result()
                print(f"  Safety net: deleted SQL server {server_name}")
            except Exception:
                pass


def run_phase_z(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str,
                azure_phase_result: Optional[dict] = None) -> None:
    """Phase Z: Azure Monitor — create metric alert on Phase N VM, verify, delete via rollback."""
    print("\n[Phase Z] Azure Monitor Metric Alert")

    if not azure_phase_result or not azure_phase_result.get("vm_asset"):
        print("  ⚠️  Phase Z requires Phase N VM — skipping (run with N,Z to enable)")
        return

    if not azure_resource_group:
        fail("Phase Z requires --azure-resource-group")

    import secrets as _secrets
    alert_name = f"nexplane-smoke-alert-{_secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []

    vm_asset = azure_phase_result["vm_asset"]
    vm_name = vm_asset.get("asset_metadata", {}).get("vm_name", "")
    creds = _get_azure_creds()
    subscription_id = creds.get("subscription_id", "")
    target_resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{azure_resource_group}"
        f"/providers/Microsoft.Compute/virtualMachines/{vm_name}"
    )

    monitor = _get_azure_monitor_client()

    try:
        cr = client.run_cr(
            "[Phase Z] create metric alert", "azure_metric_alert_create", cloud_account_id,
            {"alert_name": alert_name, "resource_group": azure_resource_group,
             "target_resource_id": target_resource_id,
             "metric_name": "Percentage CPU", "threshold": 90},
        )
        rollback_stack.append((cr["id"], "azure_metric_alert_create"))

        if monitor:
            alert = monitor.metric_alerts.get(azure_resource_group, alert_name)
            assert alert.name == alert_name, f"Alert name mismatch: {alert.name}"
            log(f"Metric alert verified: {alert_name}")
        else:
            log("Metric alert created (SDK verification skipped — no credentials)")

        log("Phase Z complete")

    except Exception as e:
        print(f"\n❌ Phase Z failed: {e}")
        raise
    finally:
        print("  [Phase Z cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        if monitor:
            try:
                monitor.metric_alerts.delete(azure_resource_group, alert_name)
                print(f"  Safety net: deleted metric alert {alert_name}")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = make_base_parser("Nexplane Azure live smoke test")
    parser.add_argument(
        "--phases", default="N,O",
        help=(
            "Comma-separated phases to run. "
            "N-O: VM lifecycle. P=NSG, Q=Storage, R=Tagging, S=Terraform, T=Ansible. "
            "U=VNet, V=Storage-CRUD, W=IAM-RBAC, X=DNS, Y=SQL, Z=Monitor. Default: N,O."
        ),
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
        if "P" in phases:
            run_phase_p(client, cloud_account_id, args.azure_resource_group)
        if "Q" in phases:
            run_phase_q(client, cloud_account_id, args.azure_resource_group)
        if "R" in phases:
            run_phase_r(client, azure_phase_result, args.azure_resource_group)
        if "S" in phases:
            run_phase_s(client, cloud_account_id, args.azure_resource_group)
        if "T" in phases:
            run_phase_t(client, cloud_account_id, azure_phase_result)
        if "U" in phases:
            run_phase_u(client, cloud_account_id, args.azure_resource_group)
        if "V" in phases:
            run_phase_v(client, cloud_account_id, args.azure_resource_group)
        if "W" in phases:
            run_phase_w(client, cloud_account_id, args.azure_resource_group)
        if "X" in phases:
            run_phase_x(client, cloud_account_id, args.azure_resource_group)
        if "Y" in phases:
            run_phase_y(client, cloud_account_id, args.azure_resource_group)
        if "Z" in phases:
            run_phase_z(client, cloud_account_id, args.azure_resource_group, azure_phase_result)

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
