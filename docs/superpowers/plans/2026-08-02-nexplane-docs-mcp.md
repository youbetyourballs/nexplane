# Nexplane Docs — MCP Server Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a complete "MCP Server" section to nexplane-docs covering all MCP tools across all domains.

**Architecture:** Six markdown pages (overview + 5 domain pages) in `docs/mcp/`, plus nav wiring in `mkdocs.yml`. All content is derived from the tool docstrings and signatures in `backend/app/mcp_tools/` in the nexplane source repo. No code changes to the platform — docs only.

**Tech Stack:** MkDocs Material, Markdown. Two repos: `f:/Nexplane/nexplane` (source to read) and `f:/Nexplane/nexplane-docs` (docs to write).

## Global Constraints

- All writes go to `f:/Nexplane/nexplane-docs/` — never modify the nexplane source repo
- Follow existing nexplane-docs style: prose intro paragraph, then tool tables with columns `Tool | Description | Key Parameters`
- Use `!!! note` admonition blocks for important operational caveats
- Auth pattern for every tool: `token` parameter is a Nexplane agent token from `POST /auth/agent-tokens`
- All tools return data from the caller's organization only — do not document multi-tenant behavior
- Do not document internal helper functions (`_auth`, `_assert_asset_owned`, etc.) — only `@mcp.tool()` decorated functions
- Code blocks for config snippets use triple-backtick with language tag (e.g., ` ```json `)
- Commit from the nexplane-docs directory: `cd f:/Nexplane/nexplane-docs && git add ... && git commit`

---

### Task 1: MCP Overview Page + Nav Wiring + Assets & Connectors Page

**Files:**
- Create: `f:/Nexplane/nexplane-docs/docs/mcp/index.md`
- Create: `f:/Nexplane/nexplane-docs/docs/mcp/assets-connectors.md`
- Modify: `f:/Nexplane/nexplane-docs/mkdocs.yml`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/mcp_tools/assets.py` — tool docstrings and signatures
- `f:/Nexplane/nexplane/backend/app/mcp_tools/connectors.py` — tool docstrings and signatures
- `f:/Nexplane/nexplane/backend/app/mcp_tools/server_instructions.py` — NEXPLANE_SERVER_INSTRUCTIONS string (explains how the MCP server works)
- `f:/Nexplane/nexplane/backend/app/routers/auth.py` — find the agent token creation endpoint for the "how to connect" section

- [ ] **Step 1: Read source files**

Read all four source files listed above. Extract:
- From `assets.py`: every `@mcp.tool()` function name, its parameters (excluding `token`), and its docstring
- From `connectors.py`: same
- From `server_instructions.py`: the NEXPLANE_SERVER_INSTRUCTIONS string for context on the MCP server's purpose
- From `auth.py`: the endpoint for creating agent tokens (expected: `POST /auth/agent-tokens`)

- [ ] **Step 2: Write `docs/mcp/index.md`**

Write the overview page. Required sections:

```markdown
# MCP Server

Nexplane exposes a Model Context Protocol (MCP) server at `/mcp` that lets AI agents — Claude, Cursor, or any MCP-compatible client — read infrastructure state, create change requests, and monitor execution results. The server implements the MCP SSE (Server-Sent Events) transport.

## Connecting

### Agent Token

All MCP tools require a `token` parameter — a Nexplane agent token. Create one:

```http
POST /auth/agent-tokens
Authorization: Bearer <user-jwt>
Content-Type: application/json

{
  "name": "claude-desktop",
  "scopes": ["read", "write"]
}
```

The response contains the token value. Store it — it is shown only once.

### Claude Desktop

Add Nexplane to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nexplane": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sse", "http://localhost:8000/mcp"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add nexplane --transport sse http://localhost:8000/mcp
```

## Tool Domains

(table listing all 6 domain pages with one-line description of what each covers)

| Domain | Tools | Description |
|--------|-------|-------------|
| Assets & Connectors | X | Enumerate and inspect infrastructure assets and connector status |
| Change Requests | X | Create, approve, execute, and roll back change requests |
| Findings & Identity | X | Manage vulnerability findings and identity access |
| Host Intelligence | X | Live host interrogation via the Nexplane Agent |
| Projects, Planning & Migration | X | Multi-CR project orchestration, migration profiling, reference scanning, runbooks |
```

Fill in actual tool counts from the source files.

- [ ] **Step 3: Write `docs/mcp/assets-connectors.md`**

Required structure:

```markdown
# Assets & Connectors

## Assets

(prose: what these tools do — enumerate and inspect infrastructure assets across connected systems)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_assets` | ... | asset_type, environment, criticality, connector_id, limit |
| `get_asset` | ... | asset_id |
| ... (one row per tool from assets.py) |

## Connectors

(prose: what these tools do — inspect connector health and discover available actions)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_connectors` | ... | (none beyond token) |
| ... (one row per tool from connectors.py) |
```

Use the docstring from each tool as the basis for the Description column. Parameters come from the function signature (exclude `token`).

- [ ] **Step 4: Add MCP section to `mkdocs.yml`**

Find the `- Agent:` section in `mkdocs.yml` and add the MCP section immediately after it:

```yaml
  - MCP Server:
    - Overview: mcp/index.md
    - Assets & Connectors: mcp/assets-connectors.md
    - Change Requests: mcp/change-requests.md
    - Findings & Identity: mcp/findings-identity.md
    - Host Intelligence: mcp/host-intelligence.md
    - Projects, Planning & Migration: mcp/projects-planning.md
```

- [ ] **Step 5: Verify tool count**

Count the `@mcp.tool()` decorated functions in `assets.py` and `connectors.py`. Confirm the number matches the rows in your tables.

- [ ] **Step 6: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/mcp/index.md docs/mcp/assets-connectors.md mkdocs.yml
git commit -m "docs: add MCP server overview, assets & connectors pages"
```

---

### Task 2: Change Requests MCP Page

**Files:**
- Create: `f:/Nexplane/nexplane-docs/docs/mcp/change-requests.md`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/mcp_tools/change_requests.py`

- [ ] **Step 1: Read source file**

Read `change_requests.py`. Extract every `@mcp.tool()` function: name, parameters (excluding `token`), docstring.

- [ ] **Step 2: Write `docs/mcp/change-requests.md`**

Required structure:

```markdown
# Change Requests

(prose: AI agents interact with infrastructure exclusively through change requests — they propose, a human approves, then the platform executes. These tools cover the full CR lifecycle.)

## Creating and Executing a CR — Workflow

(numbered walkthrough showing the typical agent flow):
1. `list_change_types` — discover available CR types
2. `create_change_request` — propose the change (creates a draft CR)
3. `get_change_request_plan` — review the safety analysis and plan
4. `approve_change_request` — approve (operator or agent with write scope)
5. `execute_change_request` — run it
6. `get_change_request` — poll for completion
7. `rollback_change_request` — undo if needed

## Tool Reference

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_change_types` | ... | (none) |
| `get_change_type` | ... | change_type |
| `list_change_requests` | ... | status, asset_id, limit |
| `get_change_request` | ... | cr_id |
| `get_change_request_plan` | ... | cr_id |
| `create_change_request` | ... | change_type, asset_id, title, parameters |
| `approve_change_request` | ... | cr_id |
| `reject_change_request` | ... | cr_id, reason |
| `execute_change_request` | ... | cr_id |
| `rollback_change_request` | ... | cr_id |

!!! note
    `create_change_request` always produces a CR in `draft` state. The platform never executes automatically — approval is always required.
```

Fill descriptions from the actual docstrings in the source file.

- [ ] **Step 3: Verify tool count**

Count `@mcp.tool()` decorators in `change_requests.py`. Confirm the table has one row per tool.

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/mcp/change-requests.md
git commit -m "docs: add MCP change requests page"
```

---

### Task 3: Findings & Identity MCP Page

**Files:**
- Create: `f:/Nexplane/nexplane-docs/docs/mcp/findings-identity.md`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/mcp_tools/findings.py`
- `f:/Nexplane/nexplane/backend/app/mcp_tools/identity.py`

- [ ] **Step 1: Read source files**

Read both files. Extract every `@mcp.tool()` function: name, parameters (excluding `token`), docstring.

- [ ] **Step 2: Write `docs/mcp/findings-identity.md`**

Required structure:

```markdown
# Findings & Identity

## Findings

(prose: vulnerability and security findings surface from connected scanners. These tools let agents triage, assign, and act on findings.)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_findings` | ... | status, severity, asset_id, limit |
| `get_finding` | ... | finding_id |
| ... (one row per tool from findings.py) |

## Identity

(prose: identity tools inspect user access across connected identity systems.)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_identities` | ... | ... |
| ... (one row per tool from identity.py) |
```

- [ ] **Step 3: Verify tool count**

Count `@mcp.tool()` decorators in both files. Confirm table rows match.

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/mcp/findings-identity.md
git commit -m "docs: add MCP findings & identity page"
```

---

### Task 4: Host Intelligence MCP Page

**Files:**
- Create: `f:/Nexplane/nexplane-docs/docs/mcp/host-intelligence.md`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/mcp_tools/host_intelligence.py`

- [ ] **Step 1: Read source file**

Read `host_intelligence.py`. Extract every `@mcp.tool()` function: name, parameters (excluding `token`), docstring. Note: all tools take `asset_id` as a required parameter; `get_host_full_context` aggregates all other tools.

- [ ] **Step 2: Write `docs/mcp/host-intelligence.md`**

Required structure:

```markdown
# Host Intelligence

(prose: these tools dispatch live jobs to the Nexplane Agent running on the target host. Results reflect the current host state — not a cached snapshot.)

!!! note "Agent required"
    Host intelligence tools require the Nexplane Agent to be installed and connected on the target asset. Use `list_assets` to confirm the asset has `agent_connected: true` before calling these tools.

## Full Context

`get_host_full_context` aggregates all host intelligence tools into a single call. Use it when you need a complete picture of a host before planning changes.

**Parameters:** `asset_id`

## Individual Tools

| Tool | Description |
|------|-------------|
| `get_kernel_info` | Kernel version, architecture, boot parameters |
| `get_kernel_eol_status` | Whether the running kernel is past end-of-life |
| `get_running_processes` | Active processes with PID, user, command |
| `get_running_services` | Systemd / Windows service states |
| `get_installed_packages` | Installed package list with versions |
| `get_open_ports` | Listening TCP/UDP ports with owning process |
| `get_authorized_keys` | SSH authorized_keys entries across all users |
| `get_sudoers` | sudoers entries |
| `get_local_users` | Local user accounts |
| `get_cron_jobs` | Cron jobs and scheduled tasks |
| `get_ssl_certs` | TLS certificates with expiry dates |
| `get_patch_status` | Available security updates |
| `get_security_posture` | Combined security posture summary |
| `get_apparmor_profiles` | AppArmor profile enforcement status |
| `get_seccomp_policy` | Active seccomp policy |
| `get_selinux_policy` | SELinux policy and mode |

All tools take `token` and `asset_id` as parameters.
```

Fill tool descriptions from the actual docstrings in the source file.

- [ ] **Step 3: Verify tool count**

Count `@mcp.tool()` decorators in `host_intelligence.py`. Confirm the table accounts for all tools (one is `get_host_full_context` documented in the "Full Context" section; the rest are in the table).

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/mcp/host-intelligence.md
git commit -m "docs: add MCP host intelligence page"
```

---

### Task 5: Projects, Planning & Migration MCP Page

**Files:**
- Create: `f:/Nexplane/nexplane-docs/docs/mcp/projects-planning.md`

**Source files to read (nexplane repo):**
- `f:/Nexplane/nexplane/backend/app/mcp_tools/projects.py`
- `f:/Nexplane/nexplane/backend/app/mcp_tools/planning_context.py`
- `f:/Nexplane/nexplane/backend/app/mcp_tools/migration.py`
- `f:/Nexplane/nexplane/backend/app/mcp_tools/reference_scan.py`
- `f:/Nexplane/nexplane/backend/app/mcp_tools/runbooks.py`

- [ ] **Step 1: Read source files**

Read all five files. Extract every `@mcp.tool()` function: name, parameters (excluding `token`), docstring.

- [ ] **Step 2: Write `docs/mcp/projects-planning.md`**

Required structure — five sections:

```markdown
# Projects, Planning & Migration

## Projects

(prose: projects group related CRs and enforce FILO rollback ordering. Use projects for multi-step operations where rollback sequence matters.)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_projects` | ... | status, limit |
| `create_project` | ... | title, description |
| `get_project` | ... | project_id |
| `get_project_status` | ... | project_id |
| `get_project_timeline` | ... | project_id |
| `chat_with_project` | ... | project_id, message |
| `define_success_criteria` | ... | project_id, criteria |
| `check_success_criteria` | ... | project_id |
| `add_cr_to_project` | ... | project_id, cr_id |
| `remove_cr_from_project` | ... | project_id, cr_id |
| `reorder_project_crs` | ... | project_id, cr_ids (ordered list) |
| `execute_project_phase` | ... | project_id, phase |
| `materialize_project_plan` | ... | project_id |
| `rollback_project` | ... | project_id |

## Planning Context

(prose: planning context tools help agents make better decisions by providing historical and cross-fleet context before proposing changes.)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `get_asset_history` | ... | asset_id |
| `get_fleet_context` | ... | asset_type, environment |
| `find_similar_assets` | ... | asset_id |
| `get_migration_precedents` | ... | asset_id |
| `get_project_precedents` | ... | (query terms) |
| `get_cross_host_dependency_map` | ... | asset_id |
| `get_kernel_eol_status` | ... | asset_id |
| `get_environment_diff` | ... | source_asset_id, target_asset_id |

## Migration Profiling

(prose: before migrating a workload, capture its profile and baseline so verify_against_baseline can confirm the migration succeeded.)

!!! note "Ordered workflow"
    Always call these in order: `discover_application_profile` → `capture_behavioral_baseline` → (perform migration) → `verify_against_baseline`. Skipping `verify_against_baseline` leaves the migration unconfirmed and blocks rollback evidence.

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `discover_application_profile` | ... | asset_id |
| `get_application_profile` | ... | profile_asset_id |
| `capture_behavioral_baseline` | ... | profile_asset_id |
| `verify_against_baseline` | ... | profile_asset_id |
| (any other tools from migration.py) | ... | ... |

## Reference Scanning

(prose: scan infrastructure for references to a value — useful before rotating credentials or changing hostnames, to find all consumers.)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `scan_for_references` | ... | search_terms, connector_ids, migration_context |
| `get_scan_results` | ... | scan_cr_id |
| `list_reference_exceptions` | ... | scan_cr_id |
| `resolve_reference_exception` | ... | exception_id, resolved_params |
| `reattempt_reference_triage` | ... | exception_id, context |
| `dismiss_reference_exception` | ... | exception_id, reason |

## Runbooks

(prose: runbooks are composable multi-step workflows with conditional branching and human checkpoints.)

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `list_runbooks` | ... | (none) |
| `get_runbook` | ... | runbook_id |
| `execute_runbook` | ... | runbook_id, inputs |
| `get_runbook_execution_status` | ... | execution_id |
```

Fill all descriptions from actual docstrings in the source files.

- [ ] **Step 3: Verify tool count**

Count `@mcp.tool()` decorators in all five source files. Confirm all tools appear in the page (either in a table or documented individually).

- [ ] **Step 4: Commit**

```bash
cd f:/Nexplane/nexplane-docs
git add docs/mcp/projects-planning.md
git commit -m "docs: add MCP projects, planning & migration page"
```
