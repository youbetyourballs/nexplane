# MCP Deep Parity Design

**Date:** 2026-07-02  
**Status:** Approved  
**Scope:** Three new MCP tool modules bringing the MCP interface to full parity with the UI and enabling AI-driven infrastructure project orchestration.

---

## Problem

The current MCP server has 49 tools across 6 domains. It is sufficient for reactive security workflows (findings, CRs, identity) but insufficient for AI-driven infrastructure projects. Three gaps block the "containerize and migrate 5 hosts" class of task:

1. **No host intelligence** — the agent has 80+ discovery executors (processes, cron jobs, users, kernel, security policies) but none are exposed as MCP tools. An AI cannot plan a migration it cannot see.
2. **No project orchestration** — project creation, phase execution, success criteria, and AI planning chat are REST-only. An AI client cannot drive a project end-to-end through MCP.
3. **No planning context** — no fleet-wide queries, no historical behavior, no migration precedents. AI planning is blind to what has been done before.

---

## Approval model

Unchanged. MCP does not exempt any operation from the platform's existing approval machinery. The same CR approval gates that apply to human operators apply to AI agents calling MCP tools. Higher-trust CR types or elevated connector permissions can auto-approve — that is a platform policy decision, not an MCP concern.

---

## Architecture: two behavioral classes

All new tools fall into one of two classes:

**Synchronous-read tools** — hide the CR machinery entirely. The tool fires an agent audit executor internally (auto-approved, read-only), polls until complete (30s timeout), caches the result for 5 minutes per asset, and returns structured data. The AI sees a simple function call and a return value.

**CR-lifecycle tools** — the full lifecycle is visible to the AI: create → submit → approve → execute → poll → rollback. The AI receives `status: awaiting_approval` and must wait. This is where human gates live. These are write operations or project state mutations.

---

## Sub-project 1: Host Intelligence (`mcp_tools/host_intelligence.py`)

16 synchronous-read tools. Each wraps one or more existing agent audit executors. Results are cached per `(asset_id, tool)` for 5 minutes.

### Tools

| Tool | Agent executor | Returns |
|------|---------------|---------|
| `get_kernel_info(asset_id)` | `deep_discover` | version, arch, eol_date, supported bool |
| `get_running_processes(asset_id)` | `deep_discover` | pid, name, user, cmdline, open_ports per process |
| `get_cron_jobs(asset_id)` | `audit_scheduled_tasks` | schedule, command, owner_user, source (crontab/systemd) |
| `get_local_users(asset_id)` | `audit_users_and_groups` | username, uid, gid, groups, shell, last_login, locked bool |
| `get_installed_packages(asset_id)` | `audit_software_inventory` | name, version, source, install_date |
| `get_running_services(asset_id)` | `deep_discover` | name, state, enabled, running_as_user |
| `get_open_ports(asset_id)` | `deep_discover` | port, protocol, process_name, process_user, listening_on |
| `get_security_posture(asset_id)` | `audit_os_security_posture` | selinux_mode, apparmor_enabled, seccomp_default, firewall_rules, last_audit_at |
| `get_seccomp_policy(asset_id)` | `audit_ebpf_posture` | active profiles, profile_per_process map, unconfined_processes |
| `get_apparmor_profiles(asset_id)` | `audit_os_security_posture` | profile_name, mode (enforce/complain), confined_processes |
| `get_selinux_policy(asset_id)` | `audit_os_security_posture` | mode, policy_name, recent_denials count and samples |
| `get_sudoers(asset_id)` | `sudoers_audit` | rules list, risky_entries flagged (NOPASSWD, ALL) |
| `get_authorized_keys(asset_id)` | `authorized_keys_audit` | key_fingerprint, user, last_used, stale bool |
| `get_ssl_certs(asset_id)` | `ssl_cert_inspect` | subject, expiry, days_remaining, issuer, chain_valid bool |
| `get_patch_status(asset_id)` | `audit_patch_status` | pending_patches count, cves_addressed, last_patched_at, critical_pending list |
| `get_host_full_context(asset_id)` | all of the above in parallel | Single bundle: all 15 tool results merged. Primary entry point for AI migration planning. |

### Implementation notes

- Each tool creates an audit CR against the asset's connector, waits for `status=completed`, extracts the relevant field from `execution_result`.
- Audit CRs created by host intelligence tools are tagged `source=mcp_intelligence` and `auto_approved=true` — they appear in the asset timeline but do not enter the approval queue.
- `get_host_full_context` fires all 15 underlying audits concurrently using `asyncio.gather`.
- Cache key: `(org_id, asset_id, tool_name)`. TTL 300 seconds. Invalidated on any write CR completing against the same asset.
- On timeout (30s), returns partial result with `timed_out: true` and whatever completed.

---

## Sub-project 2: Project Orchestration (`mcp_tools/projects.py`)

14 tools. Full parity with the Projects UI. CR-lifecycle tools follow the existing approval machinery.

### Tools

| Tool | Class | What it does |
|------|-------|-------------|
| `create_project(name, goal, description, template?)` | CR-lifecycle | Creates project in draft state. Returns project_id. |
| `get_project(project_id)` | read | Full state: all CRs with status, dependencies, pending approvals, blocking items, next executable step. |
| `list_projects(status?, limit?)` | read | Project list with name, status, CR count, completion %. |
| `chat_with_project(project_id, message)` | CR-lifecycle | Multi-turn AI planning chat. Returns AI reply + proposed_crs (typed, with asset_ids and parameters resolved). Appends to project conversation history. |
| `add_cr_to_project(project_id, cr_id, depends_on?, sequence_order?)` | CR-lifecycle | Adds existing CR to project. Wires dependency graph. Detects cycles. |
| `remove_cr_from_project(project_id, cr_id)` | CR-lifecycle | Removes CR. Updates dependency graph. |
| `reorder_project_crs(project_id, ordered_cr_ids)` | CR-lifecycle | Resequences execution order. |
| `get_project_status(project_id)` | read | Current phase, pending approvals with approver names, blocking CRs, next_executable_cr_ids. |
| `execute_project_phase(project_id, cr_ids?)` | CR-lifecycle | Executes next ready CRs (dependencies satisfied). Optional explicit CR list to execute a specific subset. |
| `get_project_timeline(project_id)` | read | Full ordered history: CR id, type, executed_at, outcome, rolled_back bool, executor. |
| `rollback_project(project_id, to_cr_id?)` | CR-lifecycle | Triggers FILO unwind from current applied state, or from a specific CR backward. |
| `materialize_project_plan(project_id, proposed_crs)` | CR-lifecycle | Batch convenience: creates each CR from a `proposed_crs` list (as returned by `chat_with_project`) and adds them to the project in one call. Avoids N×2 tool calls for large plans. |
| `define_success_criteria(project_id, criteria)` | CR-lifecycle | Registers criteria list. Each criterion: `type` (cr_completed \| host_state_check \| service_check \| port_check \| manual) + assertion parameters. Returns criteria_ids. |
| `check_success_criteria(project_id)` | read | Evaluates each criterion against live state. Returns pass/fail per criterion, overall verdict, failing criteria detail. |
| `estimate_project_risk(project_id)` | read | Aggregate blast radius, rollback_coverage_pct, estimated_duration_minutes, highest_risk_step with reason. |

### Success criteria types

| Type | Assertion parameters | How evaluated |
|------|---------------------|---------------|
| `cr_completed` | `cr_id` | Check CR status = completed |
| `host_state_check` | `asset_id`, `tool` (from host intelligence), `field`, `operator`, `value` | Run the host intelligence tool, evaluate the field |
| `service_check` | `asset_id`, `service_name`, `expected_state` | `get_running_services` → check state |
| `port_check` | `asset_id`, `port`, `expected_open` bool | `get_open_ports` → check presence |
| `manual` | `description`, `instructions` | Returns `pending_manual_verification` — human must confirm |

### `chat_with_project` flow

The AI sends a free-form message ("containerize these 5 hosts and migrate to current Linux"). The tool:
1. Appends the message to project.ai_context
2. Assembles a prompt including: project goal, all org assets with type/environment, CR manifest vocabulary (413 types), current project CRs and their status
3. Calls Claude via the existing AIService
4. Parses the response for structured `proposed_crs` blocks
5. Resolves asset names to IDs
6. Returns: `{reply: string, proposed_crs: [{change_type, asset_id, parameters, depends_on, rationale}]}`

The AI client can then call `create_change_request` + `add_cr_to_project` for each proposed CR to materialize the plan.

---

## Sub-project 3: Planning Context (`mcp_tools/planning_context.py`)

8 read tools. All synchronous. Provide fleet-wide and historical intelligence to ground AI planning.

### Tools

| Tool | What it returns |
|------|----------------|
| `get_asset_history(asset_id, since?, cr_types?)` | Every CR ever run on this asset — type, outcome, rollback_status, approver, notes, duration. Answers "what has been done to this host." |
| `get_fleet_context(filters)` | All assets matching OS, kernel_version_lt, environment, tag, connector_type. Returns key attributes per asset: kernel, OS, app_list, last_cr_at, open_findings_count. Answers "show me all hosts on kernel < 5.x in production." |
| `find_similar_assets(asset_id, limit?)` | Assets with matching OS, app profile, and environment. Returns similarity_score and key diffs. Answers "what do other hosts like this look like." |
| `get_migration_precedents(change_type, asset_type?)` | Past executions of this CR type: success_rate, avg_duration_minutes, common_failure_modes, rollback_frequency, sample_cr_ids. Answers "has this worked before and what went wrong." |
| `get_cross_host_dependency_map(asset_ids)` | How a set of hosts relate: shared_certs, shared_services, network_paths, identity_dependencies. Answers "if I migrate these 5, what else is affected." |
| `get_kernel_eol_status(asset_ids?)` | Per host: kernel_version, eol_date, supported bool, days_until_eol. Answers "which of these boxes have unsupported kernels." |
| `get_environment_diff(asset_ids)` | Side-by-side comparison across a set of hosts: packages present on some but not others, user differences, service differences, policy differences. Answers "what is different between these hosts before I apply a uniform change." |
| `get_project_precedents(goal, limit?)` | Past projects with similar goal text (pg_trgm trigram similarity, threshold 0.3): their CR sequences, total_crs, success_rate, avg_duration_days, what_rolled_back. Answers "has anyone done a migration like this before." |

### `get_fleet_context` filters

```
os: string          # "Amazon Linux 2023", "Ubuntu 22.04", etc.
kernel_version_lt: string   # semver — returns hosts with kernel < this
kernel_version_gt: string
environment: string         # "production", "staging", etc.
tag: {key: string, value: string}
connector_type: string      # "nexplane_agent", "aws", etc.
has_open_findings: bool
finding_severity: string    # filter to assets with findings of this severity+
```

---

## Data model additions

| Addition | Purpose |
|----------|---------|
| `mcp_intelligence_cache` table | Caches host intelligence tool results. Columns: org_id, asset_id, tool_name, result (JSONB), cached_at, ttl_seconds. Index on (org_id, asset_id, tool_name). |
| `project_success_criteria` table | Stores criteria per project. Columns: id, project_id, type, assertion (JSONB), last_checked_at, last_result (pass/fail/pending), last_result_detail. |
| `projects.last_chat_summary` | TEXT column on projects table — rolling AI-maintained summary of the planning conversation for context compression on long projects. |

---

## Testing

Each sub-project requires:
- Unit tests for all tool functions (mocked agent executor responses)
- Integration tests for cache invalidation logic
- Smoke phase `MCP_PROJECT_ORCHESTRATION_SMOKE` — end-to-end: create project via MCP, use `chat_with_project` to generate a plan, add proposed CRs, execute, check success criteria, rollback
- `MCP_HOST_INTELLIGENCE_SMOKE` — call `get_host_full_context` on a real enrolled agent, verify all 15 sub-tools return structured data

---

## Sequence dependency

1. Sub-project 1 (host intelligence) — independent, can build first
2. Sub-project 2 (project orchestration) — independent of 1, but `check_success_criteria` uses host intelligence tools for `host_state_check` criteria
3. Sub-project 3 (planning context) — independent, but `get_project_precedents` requires project data to exist

Recommended build order: 1 → 3 → 2 (host intelligence first so success criteria work fully when project orchestration ships).
