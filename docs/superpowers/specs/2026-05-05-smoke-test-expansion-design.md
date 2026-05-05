# Smoke Test Expansion — Comprehensive Coverage + Multi-Cloud Consolidation Design

**Date:** 2026-05-05
**Status:** Approved for implementation

---

## Goals

1. **Comprehensive per-cloud coverage:** Close all known executor mock-path gaps across AWS, GCP, and Azure. Every executor that can be tested live must be tested live.
2. **Exhaustive agent coverage:** All 47 Nexplane agent commands (Linux and Windows) covered by a dedicated test file using real EC2 instances.
3. **Smoke test as a sub-project contract:** GCP and Azure Sub-projects B–G each include a corresponding smoke test phase as part of their definition of done. Skeleton stubs define the contract; each sub-project fills in the implementation.
4. **Multi-cloud consolidation:** Cross-cloud phases that verify the same operation runs correctly on all three providers simultaneously. Built after Azure B–G are complete.
5. **README update:** Multi-cloud capabilities and test coverage documented.

---

## Architecture

The existing `test_cloud_live.py` (2,028 lines, phases A–O) is refactored into six focused files. Each provider file is independently runnable. `smoke_helpers.py` is never run directly.

```
backend/tests/smoke/
  smoke_helpers.py          # Shared infrastructure (imported by all other files)
  test_aws_live.py          # AWS phases A–T
  test_gcp_live.py          # GCP phases L–V (L–M existing + N–P new + Q–V stubs)
  test_azure_live.py        # Azure phases N–X (N–O existing + P–R new + S–X stubs)
  test_agent_live.py        # All 47 agent commands (Linux + Windows EC2)
  test_cloud_live.py        # Cross-cloud phases X1–X2 (built after Azure B–G)
```

---

## Parity Map and Coverage Contract

Every AWS capability has GCP and Azure equivalents. As GCP/Azure Sub-projects B–G ship, they fill in the corresponding stub phase. A sub-project is not complete until its smoke test phase is green.

| Capability | AWS | GCP | Azure |
|-----------|-----|-----|-------|
| Instance launch + agent deploy | Phase A | Phase L ✅ | Phase N ✅ |
| Instance advanced (stop/start/reboot/snapshot) | Phase E | Phase M ✅ | Phase O ✅ |
| Firewall / Security groups | Phase F | Phase N (new) | Phase P (new) |
| IAM / Identity lifecycle | Phase G | Phase P (new — service accounts) | **GCP Sub-D stub** / **Azure Sub-D stub** |
| Storage advanced | Phase H | Phase O (new — block public access) | Phase Q (new) |
| DNS | Phase I | **GCP Sub-E stub** | **Azure Sub-E stub** |
| Managed database | Phase J (~35 min) | **GCP Sub-F stub** | **Azure Sub-F stub** |
| Monitoring / Alarms | Phase K | **GCP Sub-G stub** | **Azure Sub-G stub** |
| Terraform local (provider-specific resources) | Phase C (AWS resources) | Phase Q (GCP resources) | Phase S (Azure resources) |
| Ansible local (provider-specific inventory) | Phase D (AWS targets) | Phase R (GCP targets) | Phase T (Azure targets) |
| Agent hardening — Linux (cloud-agnostic binary, tested on AWS) | Phase B (partial) + `test_agent_live.py` | `test_agent_live.py --cloud gcp` (optional) | `test_agent_live.py --cloud azure` (optional) |
| Agent hardening — Windows (cloud-agnostic binary) | `test_agent_live.py` | `test_agent_live.py --cloud gcp` (optional) | `test_agent_live.py --cloud azure` (optional) |
| Cross-cloud equivalence | — | Phase X1–X2 (future) | Phase X1–X2 (future) |

**Note on generic actions:** Terraform local, Ansible local, and the Nexplane agent are cloud-agnostic — the same binary/tool works on AWS, GCP, and Azure instances. Testing them against each cloud verifies that cloud-specific resource creation (via the respective Terraform provider) and cloud-specific instance targeting (via each cloud's run-command mechanism) work correctly. The agent hardening commands themselves are provider-agnostic; the optional `--cloud` flag in `test_agent_live.py` lets operators run the same 47 command set against a GCP or Azure instance when needed, defaulting to AWS.

---

## `smoke_helpers.py` — Shared Infrastructure

Extracted from `test_cloud_live.py`. All other smoke test files import from here.

**Contents:**
- `NexplaneClient` class: `__init__`, `get`, `post`, `run_cr`, `_run_cr_with_timeout`, `_wait`, `rollback_cr`, `_wait_rollback`, `create_cr`, `get_cloud_account_asset_id`, `get_asset_by_name`, `get_agent_secret`, `get_tailscale_auth_key`
- `log(msg, ok=True)`, `fail(msg)` helpers
- Constants: `TIMEOUT_SECONDS = 600`, `RDS_PHASE_TIMEOUT_SECONDS = 2700`
- AWS SDK helper: `_get_aws_boto3_client(service)` with `_aws_creds_cache`
- GCP SDK helper: `_get_gcp_compute_client()` with `_gcp_creds_cache`
- Azure SDK helper: `_get_azure_compute_client()` with `_azure_creds_cache`
- Base argument parser factory: `make_base_parser()` returning `argparse.ArgumentParser` with `--base-url`, `--email`, `--password` pre-added

---

## `test_aws_live.py` — AWS Phases A–T

### Existing phases (A–K unchanged, refactored to import from `smoke_helpers`)

### New Phase P: IAM Advanced

Exercises executors not covered by Phase G: `attach_iam_policy`, `detach_iam_policy`, `disable_iam_user`, `enable_iam_user`, `rotate_iam_key`.

**Requires Phase G** (uses the IAM user created there; Phase G's rollback deletes it, so Phase P runs before Phase G's cleanup — Phase P wires after G in `main()`).

Steps:
1. `iam_user_create` CR → create test IAM user → push rollback stack
2. `attach_iam_policy` CR → attach `ReadOnlyAccess` → verify via boto3 `list_attached_user_policies`
3. `disable_iam_user` CR → deactivate all access keys → verify via boto3 `list_access_keys` (status=Inactive)
4. `enable_iam_user` CR → reactivate keys → verify status=Active
5. `rotate_iam_key` CR → create new key, delete old → verify new key present via boto3
6. `detach_iam_policy` CR → verify no attached policies
7. Rollback: `iam_user_create` rollback → deletes user

### New Phase Q: S3 Advanced Gaps

Exercises `put_bucket_policy` and `tag_resource`.

Steps:
1. `s3_bucket_create` CR → create test bucket → push rollback stack
2. `put_bucket_policy` CR → apply deny-non-TLS policy → verify via boto3 `get_bucket_policy`
3. `tag_resource` CR → add tag `{"nexplane-smoke": "true"}` → verify via boto3 `get_bucket_tagging`
4. Rollback: `s3_bucket_create` rollback → deletes bucket (and its policy)

### New Phase R: DNS DR Failover

Exercises `dr_dns_failover_route53` — weighted record weight swap.

**Requires Phase I** (reuses the private hosted zone and records).

Steps:
1. Create two weighted A records in Phase I zone (primary:100, secondary:0) via `route53_record_upsert` CRs → push both to rollback stack
2. `dr_dns_failover_route53` CR → swaps weights (primary:0, secondary:100) → push to rollback stack
3. Verify weight swap via boto3 `list_resource_record_sets`
4. Rollback: restore weights via rollback CRs; delete both records

### New Phase S: RDS Advanced

Exercises `verify_rds_backup` and `promote_rds_replica` (~45 minutes total, flagged slow).

**Note:** `promote_rds_replica` requires a read replica. Rather than using boto3 scaffolding (which would violate the "eat our own dogfood" principle), this phase delivers a new `rds_replica_create` change type and executor as part of its implementation. This is a small addition — one executor (`create_rds_replica.py`) wrapping `create_db_instance_read_replica` — that makes the full promote lifecycle testable via Nexplane CRs. Phase S is opt-in via `--phases S` and excluded from the default run.

Steps (verify_rds_backup):
1. `rds_instance_create` CR (db.t3.micro MySQL) → push rollback stack
2. `rds_snapshot_create` CR → push rollback stack
3. `verify_rds_backup` CR → describe snapshot, verify `AllocatedStorage > 0`, status=available

Steps (promote_rds_replica):
4. `rds_replica_create` CR → create read replica from the source instance → push rollback stack; wait ~15 min
5. `promote_rds_replica` CR → promotes replica to standalone → push rollback stack
6. Verify via boto3: replica has no `ReadReplicaSourceDBInstanceIdentifier`
7. `rds_instance_delete` CR (promoted instance) → pop from rollback stack
8. Delete snapshot and original instance via rollback

**New deliverable:** `backend/app/connectors/executors/aws/create_rds_replica.py` + `rds_replica_create` change type JSON + catalog entry. Rollback = `delete_rds_instance`.

### New Phase T: Agent Lifecycle + Resource Tagging

Exercises `remove_nexplane_agent`, `tag_resource` on EC2.

**Requires Phase A** (uses running EC2 instance with agent).

Steps:
1. `tag_resource` CR → tag instance with `{"nexplane-smoke-tag": "true"}` → verify via boto3 `describe_instances`
2. `remove_nexplane_agent` CR → removes agent via SSM → verify endpoint asset disappears from inventory
3. `deploy_nexplane_agent` CR → re-deploys agent → verify endpoint asset re-registers

---

## `test_gcp_live.py` — GCP Phases L–V

### Existing phases (L–M unchanged, refactored to import from `smoke_helpers`)

### New Phase N: GCP Firewall Rules

Exercises `create_firewall_rule`, `delete_firewall_rule`.

Accepts `--gcp-network` CLI flag (default: `global/networks/default`) specifying the VPC network the firewall rule is scoped to. If the default VPC has been deleted, the user must pass their network name.

Steps:
1. `gcp_firewall_create` CR → create rule on `--gcp-network` allowing TCP/8443 from RFC5737 test CIDR `192.0.2.0/24` → push rollback stack
2. Verify rule exists via GCP SDK `compute.firewalls.get()`
3. `gcp_firewall_delete` CR (rollback of create) → verify rule deleted via SDK

### New Phase O: GCP Storage

Exercises `block_public_bucket_access`.

Steps:
1. Create GCS bucket via GCP SDK (test scaffolding — no `gcs_bucket_create` CR yet, pending Sub-project C)
2. `block_public_bucket_access` CR → remove `allUsers`/`allAuthenticatedUsers` IAM bindings → push rollback stack
3. Verify bindings removed via GCP SDK `storage.bucket_iam().get()`
4. Rollback: restore original IAM bindings
5. Safety net: delete bucket via SDK

### New Phase P: GCP Service Accounts

Exercises `disable_service_account`, `rotate_service_account_key`.

Steps:
1. Create test service account via GCP SDK (scaffolding): `projects.serviceAccounts.create()`
2. Create initial key via SDK
3. `rotate_service_account_key` CR → creates new key, deletes old → verify new key exists via SDK
4. `disable_service_account` CR → verifies `disabled: true` via SDK
5. Safety net: delete service account via SDK

### New Phase Q: Terraform Local against GCP

Exercises `terraform_local_apply` using the GCP Terraform provider (`hashicorp/google`). Verifies that Terraform running inside the backend container can create and destroy GCP resources using the GCP connector's service account credentials.

Steps:
1. `terraform_local_apply` CR with a Terraform config that creates a GCS bucket using the `google` provider and the GCP connector credentials injected as environment variables (`GOOGLE_CREDENTIALS`, `GOOGLE_PROJECT`)
2. Verify bucket exists via GCP SDK `storage.buckets.get()`
3. Rollback: `terraform_local_apply` rollback (= `terraform_destroy_local`) → verify bucket deleted

### New Phase R: Ansible Local against GCP

Exercises `ansible_local_playbook` targeting a running GCE instance (reuses Phase L/M instance or spins up a dedicated one).

Steps:
1. `ansible_local_playbook` CR with `connection: local` playbook that verifies Python is present on the GCE instance via GCP Run Command
2. Verify playbook executed successfully (CR completes, no task failures)

### Sub-project contract stubs (S–V)

Each stub is a function with a `pass` body and a contract comment:
```python
def run_phase_s_stub(*args):
    """GCP Sub-B (Networking): Advanced firewall lifecycle — implement when GCP Sub-project B ships."""
    print("\n[Phase S] GCP Networking — STUB (implement with GCP Sub-project B)")

def run_phase_t_stub(*args):
    """GCP Sub-C (Storage): GCS bucket create/delete/lifecycle — implement when GCP Sub-project C ships."""
    ...
# phases U (IAM/Sub-D), V (DNS/Sub-E), W (SQL/Sub-F), X (Monitor/Sub-G)
```

Each stub prints a clear message and exits cleanly. When a Sub-project ships, it replaces the stub with a real phase using the rollback stack pattern.

---

## `test_azure_live.py` — Azure Phases N–X

### Existing phases (N–O unchanged, refactored to import from `smoke_helpers`)

### New Phase P: NSG Rules

Exercises `update_nsg_rule`, `restore_nsg_rule`.

Creates a **new, dedicated NSG** (named `nexplane-smoke-nsg-{timestamp}`) inside `--azure-resource-group`. Does not reuse the Phase N/O VM's NSG — isolates the firewall test from the VM lifecycle test so failures don't interfere.

Steps:
1. Create test NSG via Azure SDK (scaffolding): `network.network_security_groups.begin_create_or_update(resource_group, "nexplane-smoke-nsg-{ts}", ...)`
2. `update_nsg_rule` CR → add inbound rule TCP/8443 from `192.0.2.0/24` → push rollback stack
3. Verify rule exists via Azure SDK `network_security_groups.get()`
4. `restore_nsg_rule` CR (rollback of update) → verify rule removed
5. Safety net: delete NSG via SDK (`network.network_security_groups.begin_delete(resource_group, nsg_name)`)

### New Phase Q: Blob Storage

Exercises `disable_public_blob_access`, `enable_public_blob_access`, `rotate_storage_key`.

Steps:
1. Create storage account via Azure SDK (scaffolding)
2. `disable_public_blob_access` CR → verify `allow_blob_public_access: false` via SDK → push rollback stack
3. `enable_public_blob_access` CR → verify `allow_blob_public_access: true` (rollback of disable)
4. `rotate_storage_key` CR → regenerate key1 → verify key1 value changed via SDK `storage_accounts.list_keys()`
5. Safety net: delete storage account via SDK

### New Phase R: Resource Tagging

Exercises `tag_resource` on Azure VM.

**Requires Phase N** (uses running Azure VM).

Steps:
1. `tag_resource` CR → apply `{"nexplane-smoke": "true"}` to VM
2. Verify via Azure SDK `virtual_machines.get()` → check `vm.tags`

### New Phase S: Terraform Local against Azure

Exercises `terraform_local_apply` using the Azure Terraform provider (`hashicorp/azurerm`). Verifies that Terraform running inside the backend container can create and destroy Azure resources using the Azure connector's service principal credentials.

Steps:
1. `terraform_local_apply` CR with a Terraform config that creates an Azure Resource Group or Storage Account using the `azurerm` provider, with Azure credentials injected as environment variables (`ARM_TENANT_ID`, `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`, `ARM_SUBSCRIPTION_ID`)
2. Verify resource exists via Azure SDK
3. Rollback: `terraform_destroy_local` → verify resource deleted

### New Phase T: Ansible Local against Azure

Exercises `ansible_local_playbook` targeting a running Azure VM (reuses Phase N/O instance).

Steps:
1. `ansible_local_playbook` CR with `connection: local` playbook that verifies Python is available on the Azure VM via Azure Run Command
2. Verify playbook executed successfully

### Sub-project contract stubs (U–Z)

```python
def run_phase_u_stub(*args):
    """Azure Sub-B (Networking): Advanced NSG lifecycle — implement when Azure Sub-project B ships."""
    print("\n[Phase U] Azure Networking — STUB (implement with Azure Sub-project B)")
# phases V (Storage/Sub-C), W (IAM/Sub-D), X (DNS/Sub-E), Y (SQL/Sub-F), Z (Monitor/Sub-G)
```

---

## `test_agent_live.py` — All 47 Agent Commands

Entirely new file. Two independent test tracks: Linux and Windows.

### Prerequisites

- `--tailscale-auth-key`: for agent → backend communication
- `--windows-instance-profile`: IAM instance profile for Windows EC2 (default: `NexplaneEC2TestProfile`)
- `--cloud`: which cloud to provision the test instance on (`aws` default, `gcp`, or `azure`). The agent binary and all 47 commands are identical regardless of cloud — this flag only changes how the instance is launched and how the run-command mechanism works (SSM for AWS, Azure Run Command for Azure, GCP Run Command for GCP). AWS is the default because it has the most mature SSM integration and is already used for Phases A–T.
- AWS connector with credentials (always required for agent binary S3 download, even when `--cloud gcp` or `--cloud azure`)

### Linux track — Amazon Linux EC2 (e2 phases)

Spins up a dedicated Amazon Linux EC2 instance (separate from test_aws_live.py Phase A). Deploys agent. Runs all Linux agent commands grouped by package, each as a separate phase:

| Phase | Package | Commands covered |
|-------|---------|-----------------|
| `linux_patch` | `linuxpatch` | `apply_linux_patches`, `audit_linux_patch_status` |
| `ossecurity` | `ossecurity` | `configure_selinux`, `configure_apparmor`, `configure_seccomp`, `apply_sysctl_hardening`, `configure_host_firewall`, `blacklist_kernel_modules`, `harden_mount_options`, `deploy_auditd_rules`, `setup_file_integrity_monitoring`, `audit_os_security_posture`, `audit_ebpf_posture`, `configure_ebpf_security_policy`, `deploy_ebpf_policy` |
| `linuxauth` | `linuxauth` | `harden_ssh`, `configure_pam`, `manage_ca_certificates`, `configure_ntp`, `audit_users_and_groups`, `audit_privesc_vulnerabilities` |
| `crossplatform` | `crossplatform` | `harden_tls_protocols`, `configure_dns_resolver`, `audit_software_inventory`, `configure_syslog` |
| `compliance` | `compliance` | `audit_cis_compliance`, `collect_evidence` |
| `forensics` | `forensics` | (collect forensics bundle — verify tar.gz created) |
| `fleet` | `fleet` | `restart_service` (restart a benign service), `push_config_file`, `health_check` |
| `backup` | `backup` | `create_backup` (restic to /tmp), `restore_files` |
| `reboot` | `reboot` | `graceful_reboot`, `verify_post_reboot` |
| `credrotation` | `credrotation` | `update_agent_env_file`, `rotate_ssh_keys` (adds new key to authorized_keys, removes old by fingerprint — verified via SSM). `rotate_db_credentials` deferred to `test_db_admin_live.py` (requires live DB). |
| `iac` | `iac` | `terraform_plan` (plan-only, no apply). `terraform_apply`, `terraform_rollback`, `ansible_check`, `ansible_run`, `helm_diff`, `helm_upgrade`, `helm_rollback` require Terraform/Ansible/Helm installed on the EC2 instance. The Linux EC2 launch phase installs these via SSM RunShellScript (`yum install -y terraform ansible` + Helm install script) as part of test scaffolding before iac phases run. |
| `linuxupgrade` | `linuxupgrade` | `estimate_image_size` (non-destructive check only). `upgrade_linux_instance`, `virtualize_for_migration`, `upload_image` are destructive multi-hour operations — deferred to a future dedicated upgrade test. |

Each phase uses SSM RunShellScript to verify the effect on the instance, not just that the CR succeeded.

### Windows track — Windows Server 2022 EC2

Spins up a dedicated Windows Server 2022 EC2 resolved at runtime using the filter `windows-server-2022-english-full-base-*` (same boto3 AMI lookup pattern as Phase A's `al2023-ami-2023*`), `t3.medium` (4 GB RAM required for Credential Guard and BitLocker), `us-east-1`. Deploys Windows agent binary from S3 via SSM `AWS-RunPowerShellScript` document (not `AWS-RunShellScript` — Windows instances use the PowerShell variant). Runs all Windows-specific commands:

| Phase | Package | Commands covered |
|-------|---------|-----------------|
| `win_patch` | `winpatch` | `apply_windows_patches`, `audit_windows_patch_status` |
| `winharden` | `winharden` | `configure_laps`, `enable_credential_guard`, `enforce_powershell_clm`, `deploy_applocker_policy`, `harden_smb`, `enable_bitlocker`, `configure_windows_firewall`, `harden_tls_protocols`, `harden_rdp`, `configure_windows_audit_policy`, `harden_registry` |

### Cleanup

Both tracks use the rollback stack pattern. Linux EC2 and Windows EC2 are each terminated via their `ec2_launch` CR rollback (= `terminate_instance`). EBS snapshots from backup phases cleaned up via `delete_ebs_snapshot`. Forensic bundles cleaned up from local `/tmp`.

---

## `test_cloud_live.py` — Cross-Cloud Consolidation (Future)

Refactored to import from `smoke_helpers`. Existing phases A–O are **no longer handled by this file** — they live in `test_aws_live.py`, `test_gcp_live.py`, and `test_azure_live.py` respectively. If a user runs `test_cloud_live.py --phases A`, the `main()` function prints a clear error:

```
Phase 'A' is no longer in test_cloud_live.py.
  AWS phases A-T → python backend/tests/smoke/test_aws_live.py --phases A
  GCP phases L-V → python backend/tests/smoke/test_gcp_live.py --phases L
  Azure phases N-X → python backend/tests/smoke/test_azure_live.py --phases N
  Agent phases → python backend/tests/smoke/test_agent_live.py --phases linux_patch
```

New cross-cloud phases added after Azure B–G complete:

### Phase X1: Cross-cloud instance lifecycle

1. Launch VM on AWS (`ec2_launch`), GCP (`gce_instance_create`), Azure (`azure_vm_create`) in parallel sequence
2. Verify all three appear as `server` assets in inventory within 5 minutes
3. Verify `connector_type` on each asset matches its provider
4. Stop all three (`ec2_stop`, `gce_stop`, `azure_vm_stop`)
5. Verify power state on all three via each cloud's SDK
6. Start all three; verify running
7. Rollback: terminate all three

### Phase X2: Cross-cloud snapshot

1. Requires Phase X1 instances running
2. Snapshot all three (`snapshot_asset`, `gce_disk_snapshot`, `azure_vm_snapshot`)
3. Verify snapshots exist via each cloud's SDK
4. Delete snapshots via rollback

---

## CLI Conventions

Each file is independently runnable:

```bash
# AWS (all non-RDS phases)
python backend/tests/smoke/test_aws_live.py \
  --phases A,B,C,D,E,F,G,H,I,K,P,Q,R,T \
  --tailscale-auth-key tskey-auth-<key>

# AWS (with slow phases)
python backend/tests/smoke/test_aws_live.py --phases J,S

# GCP
python backend/tests/smoke/test_gcp_live.py \
  --phases L,M,N,O,P --gcp-project <project-id> [--gcp-network global/networks/default]

# Azure
python backend/tests/smoke/test_azure_live.py \
  --phases N,O,P,Q,R --azure-resource-group <rg>

# Agent (Linux, default AWS)
python backend/tests/smoke/test_agent_live.py \
  --phases linux_patch,ossecurity,linuxauth,crossplatform,compliance,forensics,fleet,backup,reboot,credrotation,iac \
  --tailscale-auth-key tskey-auth-<key>

# Agent (Linux, run against GCP instance instead)
python backend/tests/smoke/test_agent_live.py \
  --phases linux_patch,ossecurity,linuxauth \
  --cloud gcp --gcp-project <project-id> \
  --tailscale-auth-key tskey-auth-<key>

# Agent (Linux, run against Azure instance instead)
python backend/tests/smoke/test_agent_live.py \
  --phases linux_patch,ossecurity,linuxauth \
  --cloud azure --azure-resource-group <rg> \
  --tailscale-auth-key tskey-auth-<key>

# Agent (Windows only, default AWS)
python backend/tests/smoke/test_agent_live.py \
  --phases win_patch,winharden \
  --tailscale-auth-key tskey-auth-<key>

# Cross-cloud (future, after Azure B-G)
python backend/tests/smoke/test_cloud_live.py --phases X1,X2
```

---

## README Update

`README.md` updated to reflect:
- Multi-cloud architecture (AWS + GCP + Azure, `connectorType` routing)
- Smoke test file structure (6 files, what each covers)
- Per-cloud quick-run examples for each provider file
- Agent test coverage (Linux + Windows, packages covered)
- Sub-project contract: GCP/Azure B–G phases are stubs until each sub-project ships

---

## Sub-project Contract — Definition of Done

Every GCP and Azure sub-project (B–G) is **not complete** until:

1. All executors for that sub-project are implemented and tested in unit tests
2. The corresponding smoke test stub in `test_gcp_live.py` or `test_azure_live.py` is replaced with a real phase using the rollback stack pattern
3. The real phase runs green against live cloud credentials
4. The README smoke test section reflects the new phase

This is enforced by the stub function — CI will print `"STUB — implement with Sub-project X"` if the phase is invoked but not implemented, making incomplete sub-projects visible.

---

## Out of Scope

- **`dbadmin` agent commands** (`provision_db_user`, `deprovision_db_user`, `grant_permissions`, `revoke_permissions`, `configure_db_audit`, `db_connection_config`) and **`credrotation.rotate_db_credentials`** — all require a live PostgreSQL, MySQL, or MSSQL instance running on the EC2 target. Deferred to a future `test_db_admin_live.py` which will spin up an RDS instance, deploy agent to an EC2 in the same VPC, and run all DB commands against it. This is tracked as a future task.
- **`linuxupgrade` full OS upgrade** (`upgrade_linux_instance`, `virtualize_for_migration`, `upload_image`) — destructive and multi-hour; deferred to a future dedicated upgrade test.
- **Cross-cloud phases X1–X2** — blocked until Azure B–G complete.
- **Azure Sub-project B–G smoke test implementations** — each sub-project delivers its own real phase to replace the stub.
