# Sub-project 6a: Existing Connector Upgrades — Design Spec

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Rename all `_mock` connector type suffixes to production names via PostgreSQL enum migration. Fill critical action gaps in all 10 existing connectors. Replace the incorrect Okta action set with proper Okta user lifecycle actions.

---

## Design Decisions

- **Rename via ALTER TYPE:** PostgreSQL supports `ALTER TYPE connector_type RENAME VALUE 'aws_mock' TO 'aws'` since PG 10. No data loss, no column updates needed — enum rename propagates automatically.
- **Real-if-credentials pattern preserved:** All new executor files follow the established pattern (check `connector.credentials`, use real SDK if present, return existing mock response if not).
- **Okta replacement:** The current Okta actions (`generate_key`, `distribute_key`, `verify_consumers`, `schedule_revoke`, `cancel_revoke`) are a generic key rotation workflow that does not map to Okta's API. These are removed and replaced with proper Okta user lifecycle and identity management actions.
- **Frontend display names:** `CONNECTOR_LABELS` and `CONNECTOR_ICONS` updated to drop "Mock" suffix from display names.

---

## Section 1: Database Migration (009)

Rename all enum values in a single migration:

```sql
ALTER TYPE connector_type RENAME VALUE 'aws_mock' TO 'aws';
ALTER TYPE connector_type RENAME VALUE 'azure_mock' TO 'azure';
ALTER TYPE connector_type RENAME VALUE 'cloudflare_mock' TO 'cloudflare';
ALTER TYPE connector_type RENAME VALUE 'okta_mock' TO 'okta';
ALTER TYPE connector_type RENAME VALUE 'paloalto_mock' TO 'paloalto';
ALTER TYPE connector_type RENAME VALUE 'ssh_runner_mock' TO 'ssh';
ALTER TYPE connector_type RENAME VALUE 'active_directory_mock' TO 'active_directory';
ALTER TYPE connector_type RENAME VALUE 'crowdstrike_mock' TO 'crowdstrike';
ALTER TYPE connector_type RENAME VALUE 'tenable_mock' TO 'tenable';
```

`nexplane_agent` already has no `_mock` suffix — no rename needed.

Update `backend/app/models/connector.py` ConnectorType enum accordingly. Update all references in catalog filenames, executor directory names, catalog `connector_type` field values, and frontend `ConnectorType` type + `CONNECTOR_LABELS`/`CONNECTOR_ICONS` maps.

---

## Section 2: AWS — Missing Actions

Add to `aws.json` catalog and `executors/aws/`:

| action_id | type | description |
|-----------|------|-------------|
| `discover_instances` | ingest | EC2 instances: ID, type, state, AZ, VPC, subnet, public IP, IAM role, tags |
| `discover_iam_users` | ingest | IAM users: MFA enabled, access key age, last login, attached policies |
| `discover_s3_buckets` | ingest | S3 buckets: public access block settings, versioning, encryption, ACL |
| `discover_security_groups` | ingest | All security groups: rules, attached resources |
| `ingest_guardduty_findings` | ingest | Import active GuardDuty findings as asset tags + security signals |
| `ingest_security_hub_findings` | ingest | Import Security Hub findings (cross-service aggregation) |
| `launch_instance` | change | Launch EC2 from specified AMI with instance type, subnet, SG, IAM role. Returns instance ID. Rollback: terminate instance. |
| `stop_instance` | change | Stop a running EC2 instance. Rollback: start_instance. |
| `start_instance` | change | Start a stopped EC2 instance. |
| `terminate_instance` | change | Terminate an EC2 instance (requires snapshot first — enforced via safety engine). |
| `block_s3_public_access` | change | Enable Block Public Access on a bucket. Rollback restores previous setting. |
| `enable_cloudtrail` | change | Enable CloudTrail logging for a region. Rollback: disable trail. |
| `enable_guardduty` | change | Enable GuardDuty detector in current region. |
| `enforce_imdsv2` | change | Set EC2 instance metadata service to require v2 tokens (HttpTokens=required). Rollback restores optional. |
| `rotate_iam_access_key` | change | Create new access key, return in result, delete old key. Rollback: cannot undo key deletion — warns operator. |

SDK: `boto3` (already installed). New IAM calls use `iam` client; GuardDuty uses `guardduty` client; Security Hub uses `securityhub` client.

---

## Section 3: Azure — Missing Actions

Add to `azure.json` catalog and `executors/azure/`:

| action_id | type | description |
|-----------|------|-------------|
| `discover_entra_users` | ingest | Entra ID (Azure AD) users: MFA status, last sign-in, assigned roles, license |
| `discover_entra_groups` | ingest | Entra ID groups and members |
| `discover_subscriptions` | ingest | All accessible subscriptions |
| `ingest_defender_alerts` | ingest | Microsoft Defender for Cloud security alerts — enriches assets with threat tags |
| `ingest_policy_compliance` | ingest | Azure Policy compliance findings per resource |
| `disable_entra_user` | change | Disable Entra ID user (accountEnabled=false). Rollback: enable. |
| `enable_entra_user` | change | Re-enable Entra ID user. |
| `reset_entra_mfa` | change | Reset MFA methods for an Entra user (requires User Auth Admin role). |
| `revoke_entra_sessions` | change | Revoke all active sign-in sessions for a user. |

SDK additions: `msgraph-sdk-python` (Microsoft Graph API) for Entra ID operations; `azure-mgmt-security` for Defender alerts; `azure-mgmt-policyinsights` for compliance.

---

## Section 4: Okta — Replace Action Set

**Remove** all existing Okta executor files (`generate_key.py`, `distribute_key.py`, `verify_consumers.py`, `schedule_revoke.py`, `cancel_revoke.py`). Remove from catalog.

**Add** proper Okta actions:

| action_id | type | description |
|-----------|------|-------------|
| `discover_users` | ingest | All Okta users: status, MFA enrolled, last login, group memberships, app assignments |
| `discover_groups` | ingest | Okta groups and members |
| `discover_applications` | ingest | Okta applications and assigned users/groups |
| `suspend_user` | change | Suspend an Okta user account. Rollback: unsuspend. |
| `unsuspend_user` | change | Unsuspend a previously suspended user. |
| `deactivate_user` | change | Deactivate (deprovision) an Okta user. Rollback: reactivate. |
| `reactivate_user` | change | Reactivate a deactivated user. |
| `reset_password` | change | Expire password and send reset email. |
| `reset_mfa_factors` | change | Reset all enrolled MFA factors — user re-enrolls on next login. |
| `revoke_sessions` | change | Revoke all active Okta sessions for a user. |
| `force_mfa_enrollment` | change | Set user to require MFA enrollment on next login. |
| `deprovision_from_app` | change | Remove user assignment from a specific Okta application. |

SDK: `httpx` with SSWS token auth (already in use for Okta).

---

## Section 5: Palo Alto — Missing Actions

Add to `paloalto.json` catalog and `executors/paloalto/`:

| action_id | type | description |
|-----------|------|-------------|
| `discover_address_objects` | ingest | IP address objects and address groups |
| `discover_security_zones` | ingest | Security zones and associated interfaces |
| `discover_security_rules` | ingest | All security policy rules with source/destination/application/action |
| `discover_nat_rules` | ingest | NAT rules |
| `discover_interfaces` | ingest | Physical and logical interfaces with IP assignments |
| `create_address_object` | change | Create IP/FQDN address object. Rollback: delete. |
| `delete_address_object` | change | Delete address object. |
| `add_to_address_group` | change | Add address object to an address group. Rollback: remove. |
| `remove_from_address_group` | change | Remove address from group. |
| `create_security_rule` | change | Add security policy rule. Rollback: delete rule. |
| `delete_security_rule` | change | Delete a security rule. |
| `block_ip` | change | Block an IP by adding to a designated blocklist address group + commit. |
| `commit_changes` | change | Commit all pending candidate configuration changes. |

SDK: `pan-os-python` (already installed).

---

## Section 6: Active Directory — Missing Actions

Add to `active_directory.json` catalog and `executors/active_directory/`:

| action_id | type | description |
|-----------|------|-------------|
| `discover_domain_admins` | ingest | Members of Domain Admins, Enterprise Admins, Schema Admins groups |
| `discover_stale_accounts` | ingest | Accounts inactive 90+ days (lastLogonTimestamp threshold) |
| `discover_spn_accounts` | ingest | Service accounts with SPNs set (Kerberoastable) |
| `discover_gpos` | ingest | Group Policy Objects: name, linked OUs, enforced status |
| `discover_password_policy` | ingest | Fine-grained password policies + default domain policy |
| `disable_stale_accounts` | change | Disable all accounts inactive 90+ days (bulk). Rollback: re-enable by list. |
| `move_to_ou` | change | Move a computer or user account to a different OU. |

SDK: `ldap3` (already installed).

---

## Section 7: CrowdStrike — Missing Actions

Add to `crowdstrike.json` catalog and `executors/crowdstrike/`:

| action_id | type | description |
|-----------|------|-------------|
| `ingest_spotlight_findings` | ingest | CrowdStrike Spotlight vulnerability findings — enriches assets with CVE tags |
| `discover_unmanaged_devices` | ingest | Devices seen in network traffic without a Falcon sensor |
| `get_host_details` | change | Retrieve detailed host profile via Falcon API |
| `update_prevention_policy` | change | Modify a prevention policy's settings (detection mode, process blocking, etc.) |
| `run_rtr_command` | change | Execute an approved Real Time Response command on a host (allowlisted commands only) |
| `put_file` | change | Upload a file to a host via RTR (for remediation scripts). |

SDK: `falconpy` (already installed). Spotlight uses `Discover.query_vulnerabilities()`. RTR uses `RealTimeResponse` service class.

---

## Section 8: Cloudflare — Missing Actions

Add to `cloudflare.json` catalog and `executors/cloudflare/`:

| action_id | type | description |
|-----------|------|-------------|
| `discover_waf_custom_rules` | ingest | Custom WAF rules per zone |
| `discover_access_policies` | ingest | Zero Trust Access application policies |
| `discover_firewall_rules` | ingest | Zone-level firewall rules (IP block/challenge/allow) |
| `create_firewall_rule` | change | Block/challenge/allow an IP or country code. Rollback: delete rule. |
| `delete_firewall_rule` | change | Remove a firewall rule. |
| `block_ip` | change | Quick block: create a block rule for an IP. Rollback: delete the rule. |
| `update_waf_rule_action` | change | Change WAF custom rule action (block/challenge/log). |
| `set_ssl_mode` | change | Set SSL/TLS encryption mode (off/flexible/full/strict). |

SDK: `httpx` (already in use for Cloudflare).

---

## Section 9: Tenable — Missing Actions

Add to `tenable.json` catalog and `executors/tenable/`:

| action_id | type | description |
|-----------|------|-------------|
| `discover_scan_policies` | ingest | Available scan policies and templates |
| `discover_scan_results` | ingest | Recent scan results summary per asset |
| `create_scan` | change | Create a new scan targeting specific IPs or asset groups. |
| `pause_scan` | change | Pause a running scan. |
| `resume_scan` | change | Resume a paused scan. |
| `export_vulnerability_report` | change | Export full vulnerability report for an asset or scan. Returns CSV/JSON. |

SDK: `pytenable` (already installed).

---

## Section 10: SSH — Additional Action

Add to `ssh.json` catalog and `executors/ssh/`:

| action_id | type | description |
|-----------|------|-------------|
| `check_service_status` | change | Check systemd service status on a remote host. |
| `tail_log` | change | Retrieve last N lines of a log file (allowlisted paths only). |

SDK: `paramiko` (already installed).

---

## Section 11: Files Changed

| File | Change |
|------|--------|
| `backend/alembic/versions/009_rename_connector_types.py` | New migration — ALTER TYPE rename |
| `backend/app/models/connector.py` | Update ConnectorType enum values |
| `backend/app/connectors/catalog/aws_mock.json` → `aws.json` | Rename + add 15 new actions |
| `backend/app/connectors/catalog/azure_mock.json` → `azure.json` | Rename + add 9 new actions |
| `backend/app/connectors/catalog/okta_mock.json` → `okta.json` | Rename + replace all actions |
| `backend/app/connectors/catalog/paloalto_mock.json` → `paloalto.json` | Rename + add 13 new actions |
| `backend/app/connectors/catalog/active_directory_mock.json` → `active_directory.json` | Rename + add 7 new actions |
| `backend/app/connectors/catalog/crowdstrike_mock.json` → `crowdstrike.json` | Rename + add 6 new actions |
| `backend/app/connectors/catalog/cloudflare_mock.json` → `cloudflare.json` | Rename + add 8 new actions |
| `backend/app/connectors/catalog/tenable_mock.json` → `tenable.json` | Rename + add 6 new actions |
| `backend/app/connectors/catalog/ssh_mock.json` → `ssh.json` | Rename + add 2 new actions |
| `backend/app/connectors/catalog/nexplane_agent_mock.json` | Update connector_type field only |
| All executor directories | Rename from `*_mock` to production names; add new executor .py files |
| `frontend/src/pages/Connectors.tsx` | Update ConnectorType, CONNECTOR_LABELS (drop "Mock"), CONNECTOR_ICONS |
| `frontend/src/types/api.ts` | Update ConnectorType union type |
| `frontend/src/components/AddConnectorModal.tsx` | Update CONNECTOR_LABELS/ICONS |
