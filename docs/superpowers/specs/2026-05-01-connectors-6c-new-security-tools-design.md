# Sub-project 6c: New Security Tool Connectors — Design Spec

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Seven new security tool connectors: SentinelOne (EDR), Microsoft Defender for Endpoint (EDR), HashiCorp Vault (secrets), GitHub (source security), Kubernetes (container orchestration), Snyk (developer security), Qualys (vulnerability management).

---

## Design Decisions

- New connector types: `sentinelone`, `defender_endpoint`, `hashicorp_vault`, `github`, `kubernetes`, `snyk`, `qualys`
- All follow real-if-credentials, mock-if-not pattern
- Single migration (011) adds all 7 enum values
- SDKs: `requests` for most REST APIs; `kubernetes` official Python client for K8s; `hvac` for Vault

---

## Connector 1: SentinelOne

**Credential fields:** `management_url` (string, required — e.g. `https://usea1.sentinelone.net`), `api_token` (password, required)

**Auth pattern:** `Authorization: ApiToken {api_token}` header

| action_id | type | description |
|-----------|------|-------------|
| `discover_agents` | ingest | All SentinelOne agents: hostname, OS, version, status, group, threat count, online status |
| `discover_threats` | ingest | Active threats: severity, classification, agent, file, status — enriches assets with threat tags |
| `discover_groups` | ingest | Agent groups and site hierarchy |
| `isolate_endpoint` | change | Network-isolate an endpoint. Rollback: reconnect. |
| `reconnect_endpoint` | change | Lift network isolation. |
| `kill_process` | change | Terminate a specific process on endpoint via SentinelOne RCE. |
| `quarantine_file` | change | Quarantine a specific file hash on an endpoint. |
| `initiate_scan` | change | Trigger a full disk scan on an endpoint. |
| `rollback_threat` | change | Rollback threat-related changes using SentinelOne's Storyline. |
| `update_policy` | change | Update agent group policy settings (detection mode, protection level). |

**SDK:** `requests`

---

## Connector 2: Microsoft Defender for Endpoint

**Credential fields:** `tenant_id` (string, required), `client_id` (string, required), `client_secret` (password, required)

**Auth pattern:** MSAL client credentials → Microsoft 365 Defender API `https://api.securitycenter.microsoft.com/api/`

| action_id | type | description |
|-----------|------|-------------|
| `discover_machines` | ingest | All managed machines: hostname, OS, health status, risk level, exposure level, last seen |
| `discover_alerts` | ingest | Active security alerts: severity, title, machine, status, assigned to — enriches assets |
| `discover_vulnerabilities` | ingest | Vulnerability findings per machine via Defender TVM |
| `discover_software` | ingest | Installed software with vulnerability counts |
| `isolate_machine` | change | Network isolate a machine. Rollback: unisolate. |
| `unisolate_machine` | change | Remove network isolation. |
| `restrict_app_execution` | change | Restrict code execution to Microsoft-signed binaries only. |
| `run_antivirus_scan` | change | Trigger a full antivirus scan. |
| `initiate_investigation` | change | Create automated investigation on a machine. |
| `get_machine_actions` | change | Retrieve pending/running machine actions. |

**SDK:** `msal>=1.28`, `requests`

---

## Connector 3: HashiCorp Vault

**Credential fields:** `vault_addr` (string, required — e.g. `https://vault.example.com:8200`), `token` (password, optional — for token auth), `role_id` (string, optional — for AppRole), `secret_id` (password, optional — for AppRole), `namespace` (string, optional — for Vault Enterprise)

**Auth pattern:** Token auth (`X-Vault-Token` header) or AppRole auth (exchange role_id + secret_id for token)

| action_id | type | description |
|-----------|------|-------------|
| `discover_secret_engines` | ingest | Enabled secret engines: path, type, config |
| `discover_auth_methods` | ingest | Enabled auth methods and their configs |
| `discover_policies` | ingest | Vault ACL policies |
| `discover_leases` | ingest | Active leases: secret path, TTL, renewable |
| `rotate_secret` | change | Rotate a secret at a given path (write new value). |
| `revoke_lease` | change | Revoke a specific secret lease. |
| `revoke_all_leases` | change | Revoke all leases for a given secret path prefix. |
| `create_policy` | change | Create or update a Vault ACL policy. |
| `delete_policy` | change | Delete a Vault ACL policy. |
| `seal_vault` | change | Seal the Vault (emergency lockdown). Rollback: unseal (requires unseal keys — out of band). |
| `enable_audit_device` | change | Enable an audit log device (file or syslog). |

**SDK:** `hvac>=2.1` (official Python Vault client)

---

## Connector 4: GitHub

**Credential fields:** `token` (password, required — GitHub PAT or GitHub App token), `org` (string, required — GitHub org name)

**Auth pattern:** `Authorization: Bearer {token}` header to `https://api.github.com`

| action_id | type | description |
|-----------|------|-------------|
| `discover_repositories` | ingest | All org repos: visibility, branch protection, last commit, topics, language |
| `discover_secret_scanning_alerts` | ingest | Secret scanning alerts: secret type, location, state — enriches assets with leaked-secret tag |
| `discover_code_scanning_alerts` | ingest | Code scanning (SAST) findings: rule, severity, location |
| `discover_dependabot_alerts` | ingest | Dependabot dependency vulnerability alerts: CVE, severity, package |
| `discover_outside_collaborators` | ingest | External users with org repo access |
| `discover_org_members` | ingest | Org members: role, 2FA status, last active |
| `enable_branch_protection` | change | Enable branch protection on a repo: require reviews, status checks, signed commits. |
| `revoke_oauth_token` | change | Revoke a specific OAuth application token. |
| `suspend_org_member` | change | Suspend a GitHub org member. Rollback: unsuspend. |
| `dismiss_secret_alert` | change | Dismiss a secret scanning alert with reason. |
| `enable_secret_scanning` | change | Enable secret scanning on a repository. |
| `enable_dependabot` | change | Enable Dependabot security updates on a repo. |

**SDK:** `PyGithub>=2.1` or `requests`

---

## Connector 5: Kubernetes

**Credential fields:** `kubeconfig` (password, required — base64-encoded kubeconfig YAML), `context` (string, optional — kubeconfig context name), `namespace` (string, optional — default: all namespaces)

**Auth pattern:** Load kubeconfig from base64-decoded content using `kubernetes.config.load_kube_config_from_dict()`

| action_id | type | description |
|-----------|------|-------------|
| `discover_nodes` | ingest | Cluster nodes: name, role, OS, kernel, container runtime, resource capacity, conditions |
| `discover_namespaces` | ingest | Namespaces: status, labels, resource quotas |
| `discover_workloads` | ingest | Deployments, DaemonSets, StatefulSets: replicas, images, service account |
| `discover_pods` | ingest | Running pods: namespace, node, images, security context, host network/PID |
| `discover_rbac` | ingest | ClusterRoles, Roles, RoleBindings, ClusterRoleBindings — flag overpermissive bindings |
| `discover_network_policies` | ingest | Network policies per namespace |
| `discover_service_accounts` | ingest | Service accounts and their bound roles |
| `discover_secrets` | ingest | Kubernetes secrets inventory (names/types only, not values) |
| `delete_pod` | change | Delete a pod (forces restart/eviction). |
| `cordon_node` | change | Mark node as unschedulable. Rollback: uncordon. |
| `uncordon_node` | change | Re-enable scheduling on a node. |
| `drain_node` | change | Drain all pods from a node (cordon + evict). |
| `create_network_policy` | change | Apply a network policy to namespace. Rollback: delete. |
| `delete_network_policy` | change | Remove a network policy. |
| `patch_deployment` | change | Patch a deployment (e.g. update image, set resource limits). |

**SDK:** `kubernetes>=28.1` (official Python client)

---

## Connector 6: Snyk

**Credential fields:** `api_token` (password, required), `org_id` (string, required — Snyk org ID)

**Auth pattern:** `Authorization: token {api_token}` header to `https://api.snyk.io/rest/`

| action_id | type | description |
|-----------|------|-------------|
| `discover_projects` | ingest | All Snyk projects: type (npm/docker/terraform/etc), repo, issue counts by severity |
| `discover_issues` | ingest | Open issues: CVE, severity, exploit maturity, fix available, affected package — enriches assets |
| `discover_dependencies` | ingest | Direct and transitive dependencies with known vulnerabilities |
| `discover_container_images` | ingest | Container image scan results: base image vulns, fixable count |
| `trigger_test` | change | Trigger a Snyk test on a project. |
| `ignore_issue` | change | Ignore a Snyk issue with reason and expiry date. |
| `mark_fixed` | change | Mark an issue as fixed (after remediation). |

**SDK:** `requests`

---

## Connector 7: Qualys

**Credential fields:** `api_url` (string, required — e.g. `https://qualysapi.qualys.com`), `username` (string, required), `password` (password, required)

**Auth pattern:** HTTP Basic auth to Qualys API (`/api/2.0/fo/`)

| action_id | type | description |
|-----------|------|-------------|
| `discover_hosts` | ingest | All scanned hosts: IP, DNS, OS, last scan, vulnerability counts by severity |
| `discover_vulnerabilities` | ingest | Vulnerability findings: QID, CVE, severity, CVSS, host, status — enriches assets |
| `discover_scan_schedules` | ingest | Configured scan schedules: target, frequency, next scan time |
| `discover_asset_groups` | ingest | Asset groups and their IP ranges |
| `launch_scan` | change | Launch a vulnerability scan against a target IP list or asset group. |
| `launch_compliance_scan` | change | Launch a compliance/configuration audit scan. |
| `verify_remediation` | change | Re-scan a specific host to confirm vulnerability closure. |
| `patch_vulnerability` | change | Trigger remediation workflow via Qualys Patch Management (if configured). |

**SDK:** `requests` (Qualys XML API — parse responses with `xml.etree.ElementTree`)

---

## Section 8: New Files Summary

**Migration:** `backend/alembic/versions/011_add_new_connector_types_6c.py`

**New ConnectorType values:** `sentinelone`, `defender_endpoint`, `hashicorp_vault`, `github`, `kubernetes`, `snyk`, `qualys`

**Catalogs:** 7 new JSON files

**Executors:** 7 new directories with `_client.py` + executor files

**Requirements additions:**
```
hvac>=2.1.0
PyGithub>=2.1.0
kubernetes>=28.1.0
```

(SentinelOne, Defender, Snyk, Qualys, RunZero use `requests` which is already installed)
