# Sub-project 6d: IaC & Configuration Management Connectors — Design Spec

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Infrastructure-as-Code and configuration management connectors: Terraform (HCP Terraform API), Ansible (AWX/Automation Controller API), CloudFormation (boto3), Pulumi (Automation API), Helm (Kubernetes), Azure Bicep (ARM API), Checkov (local scanner ingest), SaltStack (Salt API), Chef InSpec (Chef Automate API).

---

## Design Decisions

- **API-first:** All connectors use management APIs, not local CLI. Operators don't install CLI tools on the Nexplane server.
  - Terraform → HCP Terraform / Terraform Enterprise API
  - Ansible → AWX / Red Hat Ansible Automation Platform API
  - Pulumi → Pulumi Cloud API (Automation API)
  - CloudFormation → boto3 (already available via AWS connector pattern)
  - Helm → Kubernetes API (releases stored as secrets, read via k8s client)
  - Bicep → Azure Resource Manager API (compile + deploy via az SDK)
  - Checkov → subprocess (runs locally against IaC repos checked out to disk)
  - SaltStack → Salt API (HTTP REST)
  - Chef InSpec → Chef Automate Compliance API
- New connector types: `terraform`, `ansible`, `cloudformation`, `pulumi`, `helm`, `bicep`, `checkov`, `saltstack`, `chef_inspec`
- Single migration (012)

---

## Connector 1: Terraform (HCP Terraform)

**Credential fields:** `api_token` (password, required — HCP Terraform team/user token), `organization` (string, required — HCP Terraform org name), `base_url` (string, optional — default `https://app.terraform.io` for Enterprise override)

**Auth pattern:** `Authorization: Bearer {api_token}` to `https://app.terraform.io/api/v2/`

| action_id | type | description |
|-----------|------|-------------|
| `discover_workspaces` | ingest | All workspaces: name, environment, Terraform version, last run status, VCS repo, resource count |
| `discover_runs` | ingest | Recent runs per workspace: status, triggered by, plan changes, apply time |
| `discover_state_resources` | ingest | Resources in workspace state: type, name, provider, attributes (no sensitive values) |
| `discover_variables` | ingest | Workspace variables: name, category (env/terraform), sensitive flag |
| `plan_workspace` | change | Queue a plan-only run for a workspace. Returns run ID. Rollback: N/A (plan only). |
| `apply_workspace` | change | Queue an apply run (with auto-approve). Returns run ID. Rollback: queue destroy or previous apply. |
| `destroy_workspace_resources` | change | Queue a destroy plan + apply. Requires explicit confirmation parameter. |
| `lock_workspace` | change | Lock a workspace to prevent runs. Rollback: unlock. |
| `unlock_workspace` | change | Unlock a workspace. |
| `set_variable` | change | Set or update a workspace variable. Rollback: restore previous value. |

**SDK:** `requests`

---

## Connector 2: Ansible (AWX / Automation Controller)

**Credential fields:** `controller_url` (string, required — e.g. `https://awx.example.com`), `api_token` (password, required — OAuth2 bearer token or basic auth encoded)

**Auth pattern:** `Authorization: Bearer {api_token}` to `{controller_url}/api/v2/`

| action_id | type | description |
|-----------|------|-------------|
| `discover_inventories` | ingest | All inventories: name, host count, group count, last job status |
| `discover_hosts` | ingest | All managed hosts: name, inventory, variables, enabled, last job |
| `discover_job_templates` | ingest | Job templates: name, playbook, inventory, credentials, tags |
| `discover_jobs` | ingest | Recent job history: status, template, started/finished, failed hosts |
| `launch_job` | change | Launch a job template with optional extra_vars and limit. Returns job ID. |
| `cancel_job` | change | Cancel a running job. |
| `run_adhoc_command` | change | Run an ad-hoc command (from allowlist) on inventory. |
| `sync_inventory` | change | Trigger inventory source sync. |
| `update_host_variables` | change | Update host variables in an inventory. |

**SDK:** `requests`

---

## Connector 3: CloudFormation

**Credential fields:** Same as AWS connector (`access_key_id`, `secret_access_key`, `region`, `session_token`) — reuses AWS boto3.

**Note:** Organizations that only use CloudFormation for IaC may configure this as a separate connector scoped to CloudFormation permissions only (without broad EC2/IAM access).

| action_id | type | description |
|-----------|------|-------------|
| `discover_stacks` | ingest | All stacks: name, status, template URL, parameters, outputs, drift status |
| `discover_stack_resources` | ingest | Resources in each stack: logical ID, physical ID, type, status |
| `detect_stack_drift` | change | Initiate drift detection for a stack. Returns drift detection ID. |
| `get_drift_results` | change | Retrieve results of completed drift detection. |
| `create_change_set` | change | Create a CloudFormation change set for a template update (plan). |
| `execute_change_set` | change | Execute a change set (apply). Rollback: create + execute rollback change set. |
| `delete_stack` | change | Delete a stack. Requires snapshot/export of current state first. |
| `update_termination_protection` | change | Enable/disable termination protection on a stack. |

**SDK:** `boto3` (already installed, uses `cloudformation` client)

---

## Connector 4: Pulumi

**Credential fields:** `api_token` (password, required — Pulumi Cloud access token), `organization` (string, required)

**Auth pattern:** `Authorization: token {api_token}` to `https://api.pulumi.com/api/`

| action_id | type | description |
|-----------|------|-------------|
| `discover_stacks` | ingest | All stacks: project, name, last update, resource count, update status |
| `discover_stack_resources` | ingest | Resources in stack state: type, name, URN, provider |
| `discover_stack_history` | ingest | Update history: timestamp, author, resource changes, duration |
| `cancel_update` | change | Cancel an in-progress stack update. |
| `import_resource` | change | Import an existing resource into Pulumi state. |
| `refresh_stack` | change | Refresh stack state from cloud provider. |

**SDK:** `requests`

---

## Connector 5: Helm

**Credential fields:** Same as Kubernetes connector (`kubeconfig`, `context`, `namespace`) — uses Kubernetes API to read Helm release secrets.

**Note:** Helm stores release state as Kubernetes secrets (`helm.sh/release.v1` type). No separate Helm API — reads via kubernetes Python client.

| action_id | type | description |
|-----------|------|-------------|
| `discover_releases` | ingest | All Helm releases: name, namespace, chart, version, status, last deployed |
| `discover_release_history` | ingest | Release upgrade/rollback history per release |
| `rollback_release` | change | Roll back a Helm release to a previous revision (via `helm rollback` subprocess with `--timeout`). |
| `uninstall_release` | change | Uninstall a Helm release. |

**SDK:** `kubernetes>=28.1` + subprocess for helm CLI (optional: if helm binary present)

---

## Connector 6: Azure Bicep

**Credential fields:** Same as Azure connector (`tenant_id`, `client_id`, `client_secret`, `subscription_id`).

| action_id | type | description |
|-----------|------|-------------|
| `discover_deployments` | ingest | All resource group and subscription deployments: name, mode, template hash, status |
| `discover_deployment_operations` | ingest | Operations within a deployment: resource type, state, error |
| `validate_template` | change | Validate a Bicep/ARM template without deploying (what-if operation). |
| `create_deployment` | change | Deploy a Bicep/ARM template to a resource group. Rollback: delete deployment + restoreoriginal state snapshot. |
| `delete_deployment` | change | Delete a deployment record (does not delete resources). |
| `cancel_deployment` | change | Cancel an in-progress deployment. |

**SDK:** `azure-mgmt-resource` (already installed from Azure connector)

---

## Connector 7: Checkov

**Credential fields:** `repo_path` (string, required — local filesystem path to IaC code checked out on server), `framework` (string, optional — `terraform`, `cloudformation`, `kubernetes`, `arm`, `all`)

**Auth pattern:** subprocess — runs `checkov` CLI locally

| action_id | type | description |
|-----------|------|-------------|
| `scan_iac` | ingest | Run Checkov against repo_path, return findings: check ID, severity, resource, file, guideline — enriches assets/creates security signals |
| `scan_secrets` | ingest | Run Checkov secrets detection (`--enable-secret-scan-all-files`). |
| `get_compliance_summary` | ingest | Return pass/fail counts by compliance framework (CIS, NIST, PCI-DSS). |

**SDK:** subprocess + `checkov` CLI (installed via pip in requirements.txt as `checkov`)

---

## Connector 8: SaltStack

**Credential fields:** `api_url` (string, required — e.g. `https://salt-master.example.com:8080`), `username` (string, required), `password` (password, required), `eauth` (string, default `pam` — auth backend)

**Auth pattern:** POST `/login` with credentials → get token → include token in subsequent requests

| action_id | type | description |
|-----------|------|-------------|
| `discover_minions` | ingest | All connected minions: ID, OS, kernel, IP, grains |
| `discover_jobs` | ingest | Recent job history: function, target, started, return data |
| `run_state` | change | Apply a Salt state to target minions. Returns job ID. |
| `run_function` | change | Execute a Salt execution module function on target (allowlisted: `sys.doc`, `test.ping`, `pkg.list_pkgs`, `service.status`). |
| `sync_minion` | change | Force a minion to sync modules and states from master. |
| `accept_key` | change | Accept a pending minion key. |
| `reject_key` | change | Reject/delete a minion key. |

**SDK:** `requests`

---

## Connector 9: Chef InSpec

**Credential fields:** `automate_url` (string, required — Chef Automate URL), `api_token` (password, required — Chef Automate API token)

**Auth pattern:** `api-token: {api_token}` header to `{automate_url}/api/v0/`

| action_id | type | description |
|-----------|------|-------------|
| `discover_nodes` | ingest | All Chef-managed nodes: name, environment, platform, run list, last check-in |
| `discover_compliance_profiles` | ingest | Installed InSpec compliance profiles: name, version, controls count |
| `discover_compliance_results` | ingest | Compliance scan results per node: profile, control, status (passed/failed/skipped) — enriches assets with compliance-fail tags |
| `run_compliance_scan` | change | Trigger an InSpec compliance scan on target nodes. |
| `assign_profile` | change | Assign a compliance profile to a node or node group. |

**SDK:** `requests`

---

## Section 10: New Files Summary

**Migration:** `backend/alembic/versions/012_add_new_connector_types_6d.py`

**New ConnectorType values:** `terraform`, `ansible`, `cloudformation`, `pulumi`, `helm`, `bicep`, `checkov`, `saltstack`, `chef_inspec`

**Catalogs:** 9 new JSON files

**Executors:** 9 new directories

**Requirements additions:**
```
checkov>=3.2.0
```
(All other SDKs use `requests`, `boto3`, `azure-mgmt-resource`, `kubernetes` — all already installed)
