# Cloud Account Baseline Monitoring — Design Spec

**Date:** 2026-08-02
**Status:** Approved

## Goal

Four new CR types — one per cloud provider — that enable foundational security monitoring controls across an entire cloud account in a single operation. Each CR auto-discovers all regions, enables the relevant services, verifies they are active, and supports full rollback to pre-existing state.

This is tier A of a two-tier model. Tier B (`_hardening` variants) covers account hygiene controls and is a separate future spec. The monitoring CR emits a `promote_to` field in its execution result to surface the promotion path in the UI after a soak period.

## CR Types

- `aws_account_baseline_monitoring`
- `gcp_account_baseline_monitoring`
- `azure_account_baseline_monitoring`
- `oci_account_baseline_monitoring`

No parameters required on any CR type. The connector provides credentials; the executor discovers regions automatically.

## Architecture

Four independent executors, one per cloud, each in its connector's executor directory:

- `backend/app/connectors/executors/aws/aws_account_baseline_monitoring.py`
- `backend/app/connectors/executors/gcp/gcp_account_baseline_monitoring.py`
- `backend/app/connectors/executors/azure/azure_account_baseline_monitoring.py`
- `backend/app/connectors/executors/oci/oci_account_baseline_monitoring.py`

No shared base class. Consistent with all existing connector-specific executors.

## Phase Structure (identical scaffold, all four clouds)

1. **Preflight** — verify connector credentials have sufficient permissions to enable each service; fail fast with a clear permission list if anything is missing
2. **Snapshot** — capture current enabled/disabled state of each service in each region; stored in `rollback_data` for exact restore
3. **Enable** — turn on each service in each region; build a FILO rollback stack as each enable completes
4. **Verify** — confirm each service reports active; flag any that didn't take
5. **Report** — structured summary: `already_enabled` (skipped), `newly_enabled`, `failed`, `skipped_with_warning`

`ROLLBACK_CAPABILITY: "full"` on all four executors. Rollback disables only services the CR turned on — services already active before the CR ran are left alone. The `newly_enabled` list in rollback_data drives the undo sequence.

## Execution Result Shape

```json
{
  "phases": [...],
  "summary": {
    "already_enabled": ["guardduty:us-west-2", "..."],
    "newly_enabled": ["cloudtrail:global", "guardduty:eu-west-1", "..."],
    "failed": [],
    "skipped_with_warning": []
  },
  "promote_to": "aws_account_baseline_hardening",
  "rollback_data": {
    "newly_enabled": ["cloudtrail:global", "guardduty:eu-west-1", "..."],
    "pre_existing_states": { ... }
  }
}
```

The `promote_to` field is a forward reference only — the hardening CR types do not exist yet. The UI surfaces it as a suggested next CR after a soak period.

---

## AWS Controls

**Executor:** `executors/aws/aws_account_baseline_monitoring.py`
**Connector type:** `aws`

| Control | Scope | Action |
|---------|-------|--------|
| CloudTrail | Account-wide | Create multi-region trail logging to new S3 bucket `nexplane-cloudtrail-{account_id}`; enable log file validation |
| GuardDuty | Per region | Enable detector; set finding publish frequency to 15 min |
| Security Hub | Per region | Enable SecurityHub; activate CIS AWS Foundations Benchmark standard |
| AWS Config | Per region | Enable Config recorder + delivery channel to S3 bucket `nexplane-config-{account_id}-{region}` |
| S3 account public access block | Account-wide | Set all four `BlockPublicAccess` flags; import and call the helper from `block_s3_public_access.py` directly — do not duplicate |
| IAM password policy | Account-wide | Minimum 14 chars, require uppercase/lowercase/numbers/symbols, 90-day expiry, prevent reuse of last 24 |

**Required IAM permissions:**
- `cloudtrail:CreateTrail`, `cloudtrail:StartLogging`, `cloudtrail:GetTrailStatus`
- `guardduty:CreateDetector`, `guardduty:GetDetector`
- `securityhub:EnableSecurityHub`, `securityhub:BatchEnableStandards`
- `config:PutConfigurationRecorder`, `config:PutDeliveryChannel`, `config:StartConfigurationRecorder`
- `s3:CreateBucket`, `s3:PutBucketPolicy`, `s3:PutAccountPublicAccessBlock`
- `iam:UpdateAccountPasswordPolicy`, `iam:GetAccountPasswordPolicy`

**Rollback:** Delete CloudTrail trail + S3 bucket if CR created them; disable GuardDuty detector per region; disable SecurityHub per region; stop Config recorder + delete delivery channel per region; restore previous S3 public access block state; restore previous password policy.

---

## GCP Controls

**Executor:** `executors/gcp/gcp_account_baseline_monitoring.py`
**Connector type:** `gcp`

| Control | Scope | Action |
|---------|-------|--------|
| Cloud Audit Logs | Per project | Enable Admin Activity + Data Access (READ, WRITE) for all services via `setIamPolicy` on project audit config |
| Security Command Center | Organization-level | Enable SCC Standard tier on the organization; derive org ID from project resource ancestry if not in connector config |
| VPC Flow Logs | Per region, per subnet | Enable flow logs on all subnets: `aggregationInterval: INTERVAL_5_SEC`, `flowSampling: 0.5`, `metadata: INCLUDE_ALL_METADATA` |
| Cloud DNS logging | Per managed private zone | Enable query logging on all managed private zones |

**SCC org-level caveat:** If the service account lacks org-level permissions, SCC activation is skipped and recorded in `skipped_with_warning` with an explanation — the CR does not fail. All other controls proceed.

**Required permissions:**
- `resourcemanager.projects.setIamPolicy` (audit logs)
- `securitycenter.organizations.enableSecurityCenter` (SCC — org-level)
- `compute.subnetworks.update` (flow logs)
- `dns.managedZones.update` (DNS logging)

**Rollback:** Restore previous project audit config; disable SCC if CR enabled it; disable flow logs on subnets that had them off; disable DNS query logging on zones that had it off.

---

## Azure Controls

**Executor:** `executors/azure/azure_account_baseline_monitoring.py`
**Connector type:** `azure`

| Control | Scope | Action |
|---------|-------|--------|
| Microsoft Defender for Cloud | Per subscription, per resource type | Enable standard/P2 plan for: Servers, Storage, SQL, KeyVault, AppService, Containers, ARM via `SecurityPricings` API |
| Microsoft Defender for DNS | Subscription-wide | Enable DNS-layer threat detection via `SecurityPricings` API |
| Activity Log diagnostic settings | Per subscription | Create diagnostic setting routing all Activity Log categories to new Log Analytics workspace `nexplane-logs-{subscription_id}` |
| Microsoft Entra ID security defaults | Tenant-level | Enable via Graph API `authorizationPolicy`; skip with warning if Conditional Access policies already exist |

**Entra ID security defaults caveat:** The executor checks for existing Conditional Access policies before enabling. If CA policies are present, this control is skipped and recorded in `skipped_with_warning` — operators with CA policies already have equivalent or stronger controls.

**Required permissions:**
- `Security Admin` role (Defender plans)
- `Monitoring Contributor` (diagnostic settings, Log Analytics workspace)
- `Global Administrator` or `Security Administrator` in Entra ID (security defaults)

**Rollback:** Restore Defender plans to Free tier for resource types the CR upgraded; delete Log Analytics workspace and diagnostic setting if CR created them; disable security defaults if CR enabled them.

---

## OCI Controls

**Executor:** `executors/oci/oci_account_baseline_monitoring.py`
**Connector type:** `oci`

| Control | Scope | Action |
|---------|-------|--------|
| Cloud Guard | Tenancy-wide | Enable Cloud Guard; set reporting region to connector home region; create tenancy-level target with all Oracle-managed detector recipes (activity, configuration, threat) |
| Audit service retention | Tenancy-wide | Verify Audit service is active (always on, cannot be disabled); set retention to 365 days if currently less |
| VCN Flow Logs | Per VCN, per region | Enable flow logs on all VCN subnets across all compartments and regions via Logging service |
| IAM password policy | Tenancy-wide | Minimum 14 chars, require uppercase/lowercase/numbers/symbols, 90-day expiry, prevent reuse of last 24 |

**Cloud Guard home region caveat:** Executor reads `home_region` from connector config; if absent, derives from first region in tenancy's region subscriptions. Fails preflight with a clear message if it cannot be determined.

**VCN flow logs caveat:** If no VCNs exist in the tenancy, this control is skipped and recorded in `skipped_with_warning` rather than failing the CR.

**Required permissions (IAM group policy):**
- `CloudGuard-Admin` managed policy
- `AuditAdmin`
- `LoggingAdmin`
- `IdentityAdmin`

**Rollback:** Disable Cloud Guard (delete tenancy target + disable service); restore previous audit retention period; disable flow logs on subnets the CR enabled; restore previous password policy.

---

## Smoke Testing

**File:** `tests/smoke/test_smoke_cloud_baseline_monitoring.py`

Four phases, one per cloud. Each phase: enable → verify active → rollback → verify disabled.

**AWS phase** — all 6 controls across all regions. Verify GuardDuty detectors and SecurityHub active; rollback and confirm disabled, CloudTrail trail deleted.

**GCP phase** — audit logs, flow logs, DNS logging run fully. SCC phase marked `xfail` if service account lacks org permissions (consistent with existing GCP smoke pattern).

**Azure phase** — Defender plans enabled and immediately rolled back (billed hourly — short window is acceptable). Security defaults skipped if CA policies detected. Log Analytics workspace created and deleted in same run.

**OCI phase** — Cloud Guard available on free-tier tenancy. VCN flow logs skipped with note if no VCNs exist. Audit retention and password policy run unconditionally.

## Files Changed

**New:**
- `backend/app/connectors/executors/aws/aws_account_baseline_monitoring.py`
- `backend/app/connectors/executors/gcp/gcp_account_baseline_monitoring.py`
- `backend/app/connectors/executors/azure/azure_account_baseline_monitoring.py`
- `backend/app/connectors/executors/oci/oci_account_baseline_monitoring.py`
- `backend/app/connectors/change_type_definitions/aws_account_baseline_monitoring.json`
- `backend/app/connectors/change_type_definitions/gcp_account_baseline_monitoring.json`
- `backend/app/connectors/change_type_definitions/azure_account_baseline_monitoring.json`
- `backend/app/connectors/change_type_definitions/oci_account_baseline_monitoring.json`
- `tests/smoke/test_smoke_cloud_baseline_monitoring.py`
- `backend/alembic/versions/{hash}_add_cloud_baseline_monitoring_change_types.py`

**Modified:**
- `backend/app/connectors/catalog/aws.json` — add `aws_account_baseline_monitoring` entry
- `backend/app/connectors/catalog/gcp.json` — add `gcp_account_baseline_monitoring` entry
- `backend/app/connectors/catalog/azure.json` — add `azure_account_baseline_monitoring` entry
- `backend/app/connectors/catalog/oci.json` — add `oci_account_baseline_monitoring` entry
- `backend/app/models/change_request.py` — add 4 ChangeType enum values
- `backend/app/connectors/safety_engine.py` — register 4 new CR types
- `backend/app/connectors/executor_registry.py` — register 4 new executors
