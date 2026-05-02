# Sub-project 6e: Workflow & Observability Connectors — Design Spec

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Seven new connectors completing the incident response and observability loop: Jira, PagerDuty, ServiceNow, Splunk, Datadog, Zscaler, Google Workspace.

---

## Design Decisions

- New connector types: `jira`, `pagerduty`, `servicenow`, `splunk`, `datadog`, `zscaler`, `google_workspace`
- All use REST APIs with token/OAuth authentication
- Single migration (013)
- These connectors close the security operations loop: findings → tickets → incidents → observability

---

## Connector 1: Jira

**Credential fields:** `base_url` (string, required — e.g. `https://acme.atlassian.net`), `email` (string, required), `api_token` (password, required — Atlassian API token)

**Auth pattern:** HTTP Basic auth with `email:api_token` to `{base_url}/rest/api/3/`

| action_id | type | description |
|-----------|------|-------------|
| `discover_projects` | ingest | All accessible Jira projects: key, name, type, lead |
| `discover_issues` | ingest | Open security issues: summary, priority, status, assignee, components — links to Nexplane assets |
| `create_issue` | change | Create a Jira issue (bug/task/story) with summary, description, priority, labels, assignee. Returns issue key. Rollback: transition to "Cancelled". |
| `transition_issue` | change | Transition an issue to a new status (e.g. "In Progress", "Done"). |
| `add_comment` | change | Add a comment to an existing issue. |
| `link_issues` | change | Link two Jira issues (blocks/is blocked by/relates to). |
| `assign_issue` | change | Assign an issue to a user. |

**SDK:** `requests` (Jira REST API v3)

---

## Connector 2: PagerDuty

**Credential fields:** `api_key` (password, required — PagerDuty API token), `from_email` (string, required — email address for incident creation attribution)

**Auth pattern:** `Authorization: Token token={api_key}` header to `https://api.pagerduty.com/`

| action_id | type | description |
|-----------|------|-------------|
| `discover_services` | ingest | PagerDuty services: name, escalation policy, on-call team, status |
| `discover_incidents` | ingest | Active incidents: title, severity, service, assigned to, created at |
| `discover_oncall` | ingest | Current on-call schedule: who is on call per service now |
| `create_incident` | change | Create a PagerDuty incident on a service. Returns incident ID. Rollback: resolve incident. |
| `resolve_incident` | change | Resolve an incident. |
| `acknowledge_incident` | change | Acknowledge an incident. |
| `trigger_webhook` | change | Send a custom event to PagerDuty Events API (for alert integration). |
| `get_incident_notes` | change | Retrieve notes/timeline for an incident. |

**SDK:** `requests` (pdpyras not required — REST is straightforward)

---

## Connector 3: ServiceNow

**Credential fields:** `instance_url` (string, required — e.g. `https://acme.service-now.com`), `username` (string, required), `password` (password, required)

**Auth pattern:** HTTP Basic auth to `{instance_url}/api/now/v2/table/`

| action_id | type | description |
|-----------|------|-------------|
| `discover_incidents` | ingest | Open incidents: number, priority, state, assignment group, CI |
| `discover_change_requests` | ingest | Change requests: number, type, risk, state, scheduled window |
| `discover_cmdb_cis` | ingest | CMDB configuration items — sync to Nexplane asset inventory |
| `create_incident` | change | Create a ServiceNow incident with caller, short description, priority, assignment group. Returns sys_id. Rollback: resolve/cancel. |
| `create_change_request` | change | Create a normal or emergency change request with risk assessment. |
| `update_incident` | change | Update incident fields (state, work notes, assignment). |
| `resolve_incident` | change | Resolve an incident with resolution code and notes. |
| `sync_cmdb` | change | Push Nexplane asset inventory to ServiceNow CMDB (upsert by IP/hostname). |

**SDK:** `requests`

---

## Connector 4: Splunk

**Credential fields:** `base_url` (string, required — e.g. `https://splunk.example.com:8089`), `token` (password, required — Splunk HEC token or API bearer token), `hec_url` (string, optional — HEC endpoint for event ingestion, e.g. `https://splunk.example.com:8088`)

**Auth pattern:** `Authorization: Bearer {token}` or `Authorization: Splunk {token}` depending on endpoint

| action_id | type | description |
|-----------|------|-------------|
| `search` | change | Run a Splunk SPL search query. Returns results. (Read-only operation with change type for gated access.) |
| `discover_saved_searches` | ingest | Saved searches and alerts |
| `discover_notable_events` | ingest | Notable events from Enterprise Security (correlation search results) |
| `send_event` | change | Send a structured event to Splunk via HEC (for Nexplane action logging). |
| `create_alert` | change | Create a saved search with alert action. |
| `suppress_notable` | change | Suppress a notable event in Splunk ES. |
| `run_adaptive_response` | change | Trigger an adaptive response action on a notable event. |

**SDK:** `requests` (Splunk SDK is not required — REST is sufficient)

---

## Connector 5: Datadog

**Credential fields:** `api_key` (string, required), `app_key` (string, required), `site` (string, default `datadoghq.com` — for EU: `datadoghq.eu`)

**Auth pattern:** `DD-API-KEY: {api_key}` + `DD-APPLICATION-KEY: {app_key}` headers to `https://api.{site}/api/v2/`

| action_id | type | description |
|-----------|------|-------------|
| `discover_hosts` | ingest | All Datadog-monitored hosts: name, tags, agent version, last reported, muted |
| `discover_monitors` | ingest | Monitors/alerts: name, type, state, tags, notification channels |
| `discover_security_signals` | ingest | Datadog Security Monitoring signals: severity, rule, source, asset — enriches Nexplane assets |
| `discover_logs_indexes` | ingest | Log indexes: name, retention, filter, daily limit |
| `mute_host` | change | Mute a host for N seconds (suppress alerts during maintenance). Rollback: unmute. |
| `unmute_host` | change | Unmute a previously muted host. |
| `create_monitor` | change | Create a metric/log/APM monitor with thresholds and notification. Rollback: delete. |
| `trigger_synthetics` | change | Trigger Synthetic test run. Returns result URL. |
| `send_event` | change | Post an event to Datadog Events stream (for Nexplane action audit). |

**SDK:** `datadog-api-client>=2.22` (already in requirements from earlier) or `requests`

---

## Connector 6: Zscaler

**Credential fields:** `cloud` (string, required — e.g. `zscloud.net`, `zscaler.net`), `api_key` (string, required), `username` (string, required), `password` (password, required)

**Auth pattern:** Session-based auth — POST `/api/v1/authenticatedSession` with obfuscated API key, get JSESSIONID cookie

| action_id | type | description |
|-----------|------|-------------|
| `discover_users` | ingest | Zscaler Internet Access users: name, department, group, last login |
| `discover_policies` | ingest | ZIA URL filtering and firewall policies |
| `discover_locations` | ingest | Physical locations/branch configs |
| `discover_zpa_applications` | ingest | Zscaler Private Access application segments |
| `block_url` | change | Add a URL to the blocklist. Rollback: remove. |
| `block_ip` | change | Add an IP to the deny list. Rollback: remove. |
| `suspend_user` | change | Suspend a Zscaler user. Rollback: activate. |
| `activate_user` | change | Activate a suspended user. |
| `update_url_category` | change | Add/remove URLs from a custom URL category. |

**SDK:** `requests`

---

## Connector 7: Google Workspace

**Credential fields:** `service_account_key_json` (password, required — service account with domain-wide delegation), `admin_email` (string, required — email of admin for impersonation), `domain` (string, required — Google Workspace domain)

**Auth pattern:** Service account with domain-wide delegation + impersonation using `google-auth` library

| action_id | type | description |
|-----------|------|-------------|
| `discover_users` | ingest | All Workspace users: name, email, admin status, 2SV enrolled, last login, suspended |
| `discover_groups` | ingest | Google Groups: members, managers, owners |
| `discover_devices` | ingest | Managed devices: type, OS, user, compliance status |
| `discover_admin_roles` | ingest | Assigned admin roles and their privileges |
| `discover_audit_logs` | ingest | Recent admin/login/drive audit events — enriches assets with activity signals |
| `suspend_user` | change | Suspend a Workspace user. Rollback: unsuspend. |
| `unsuspend_user` | change | Re-activate a suspended user. |
| `reset_password` | change | Force password reset on next login. |
| `revoke_tokens` | change | Revoke all OAuth tokens for a user. |
| `remove_from_group` | change | Remove a user from a Google Group. |
| `wipe_device` | change | Remote wipe a managed mobile device. Non-reversible — requires explicit confirmation. |
| `block_device` | change | Block a device from accessing Workspace. Rollback: unblock. |

**SDK:** `google-api-python-client>=2.120`, `google-auth>=2.27`

---

## Section 8: New Files Summary

**Migration:** `backend/alembic/versions/013_add_new_connector_types_6e.py`

**New ConnectorType values:** `jira`, `pagerduty`, `servicenow`, `splunk`, `datadog`, `zscaler`, `google_workspace`

**Catalogs:** 7 new JSON files

**Executors:** 7 new directories

**Requirements additions:**
```
google-api-python-client>=2.120.0
google-auth>=2.27.0
```
(Jira, PagerDuty, ServiceNow, Splunk, Zscaler all use `requests` — already installed)
