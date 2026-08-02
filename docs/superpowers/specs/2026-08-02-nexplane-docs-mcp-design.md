# Nexplane Docs — MCP Server Documentation Design Spec

## Goal

Add a complete "MCP Server" section to nexplane-docs covering all 108 MCP tools across 11 domains. This section does not exist today. It enables operators and AI agent integrators to understand what tools are available, how to connect a client, and what each tool does.

## Source

All content is derived from `backend/app/mcp_tools/` in the nexplane repo. The modules are:

- `assets.py` — 8 tools
- `change_requests.py` — 10 tools
- `connectors.py` — 6 tools
- `findings.py` — 12 tools
- `host_intelligence.py` — 16 tools
- `identity.py` — 6 tools
- `migration.py` — 6 tools
- `planning_context.py` — 8 tools
- `projects.py` — 14 tools
- `reference_scan.py` — 5 tools
- `runbooks.py` — 4 tools (+ server_instructions.py is internal config, not a tools module)

## Pages to Create

### `docs/mcp/index.md` — MCP Server Overview
- What the MCP server is (Model Context Protocol endpoint for AI agent integration)
- Connection: `http://<host>:8000/mcp` SSE endpoint
- Auth: Bearer token (agent token via `POST /auth/agent-tokens`)
- How to connect from Claude Desktop / Claude Code / any MCP client (show config snippet)
- List of tool domains with brief one-liner per domain
- Link to each domain page

### `docs/mcp/assets-connectors.md` — Assets & Connectors Tools
- Assets domain (8 tools): list_assets, get_asset, get_asset_context, get_asset_history, get_asset_neighbors, get_asset_timeline, get_asset_upstream, search_assets
- Connectors domain (6 tools): list_connectors, get_connector, get_connector_status, list_connector_change_types, list_catalog_actions, test_connector
- Per tool: name, one-line description, key parameters, what it returns

### `docs/mcp/change-requests.md` — Change Request Tools
- 10 tools: list_change_types, get_change_type, list_change_requests, get_change_request, get_change_request_plan, create_change_request, approve_change_request, reject_change_request, execute_change_request, rollback_change_request
- Narrative walkthrough showing how an AI agent creates, approves, and monitors a CR end-to-end (the key workflow)
- Per tool: name, description, key parameters, return shape

### `docs/mcp/findings-identity.md` — Findings & Identity Tools
- Findings domain (12 tools): list_findings, get_finding, list_asset_findings, list_identity_findings, list_finding_change_requests, get_poc_result, trigger_poc_validation, accept_risk, mark_false_positive, challenge_exploitability, assign_finding, update_finding_status
- Identity domain (6 tools): list_identities, get_identity, get_identity_graph, list_access_reviews, get_access_review
- Per tool: name, description, key parameters

### `docs/mcp/host-intelligence.md` — Host Intelligence Tools
- 16 tools: get_host_full_context, get_open_ports, get_running_processes, get_running_services, get_installed_packages, get_authorized_keys, get_sudoers, get_local_users, get_kernel_info, get_kernel_eol_status, get_patch_status, get_cron_jobs, get_ssl_certs, get_security_posture, get_apparmor_profiles, get_seccomp_policy, get_selinux_policy
- Explain that these dispatch live agent jobs (not cached); requires host to have agent installed
- Per tool: name, description, what it returns

### `docs/mcp/projects-planning.md` — Projects, Planning & Migration Tools
- Projects domain (14 tools): list_projects, create_project, get_project, get_project_status, get_project_timeline, chat_with_project, define_success_criteria, check_success_criteria, add_cr_to_project, remove_cr_from_project, reorder_project_crs, execute_project_phase, materialize_project_plan, rollback_project
- Planning Context domain (8 tools): build_asset_context, find_similar_assets, get_fleet_context, get_cross_host_dependency_map, estimate_project_risk, get_environment_diff, get_verification_result
- Migration domain (6 tools): discover_application_profile, get_application_profile, capture_behavioral_baseline, get_migration_precedents, get_project_precedents, verify_against_baseline
- Reference Scan domain (5 tools): scan_for_references, get_scan_results, dismiss_reference_exception, resolve_reference_exception, list_reference_exceptions
- Runbooks domain (4 tools): list_runbooks, get_runbook, execute_runbook, get_runbook_execution_status

## mkdocs.yml Nav Addition

Add after the "Agent" section:

```yaml
- MCP Server:
  - Overview: mcp/index.md
  - Assets & Connectors: mcp/assets-connectors.md
  - Change Requests: mcp/change-requests.md
  - Findings & Identity: mcp/findings-identity.md
  - Host Intelligence: mcp/host-intelligence.md
  - Projects, Planning & Migration: mcp/projects-planning.md
```

## Style Constraints

- Follow existing nexplane-docs style: prose intro paragraph, then tool tables (name | description | key params)
- Code blocks for config snippets (JSON for Claude Desktop config)
- Admonition blocks (`!!! note`) for important caveats (e.g., host intelligence requires agent)
- Do NOT document internal parameters exhaustively — focus on the key parameters an operator would actually use
- Target audience: security engineers and AI agent integrators, not platform developers

## Files

**Create (nexplane-docs repo):**
- `docs/mcp/index.md`
- `docs/mcp/assets-connectors.md`
- `docs/mcp/change-requests.md`
- `docs/mcp/findings-identity.md`
- `docs/mcp/host-intelligence.md`
- `docs/mcp/projects-planning.md`

**Modify (nexplane-docs repo):**
- `mkdocs.yml` — add MCP Server nav section
