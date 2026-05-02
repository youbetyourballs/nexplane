# Connectors 6e: Workflow & Observability Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7 workflow and observability connectors: Jira, PagerDuty, ServiceNow, Splunk, Datadog, Zscaler, Google Workspace.

**Architecture:** Single Alembic migration (013) adds 7 new enum values. All use REST APIs with token/OAuth. Real-if-credentials/mock-if-not pattern.

**Tech Stack:** `requests`/`httpx` (all), `google-api-python-client>=2.120`, `google-auth>=2.27`

---

## File Map

**Backend:**
- Create: `backend/alembic/versions/013_add_new_connector_types_6e.py`
- Modify: `backend/app/models/connector.py`
- Create 7 catalogs and 7 executor directories
- Modify: `backend/requirements.txt`

**Frontend:**
- Modify: `frontend/src/types/api.ts`, `Connectors.tsx`, `AddConnectorModal.tsx`

---

### Task 1: Database Migration + Model Update

- [ ] **Step 1: Create migration 013**

```python
# backend/alembic/versions/013_add_new_connector_types_6e.py
"""add workflow/observability connector types: jira, pagerduty, servicenow, splunk, datadog, zscaler, google_workspace

Revision ID: 013
Revises: 012
Create Date: 2026-05-01
"""
from alembic import op

revision = '013'
down_revision = '012'
branch_labels = None
depends_on = None


def upgrade():
    for val in ['jira', 'pagerduty', 'servicenow', 'splunk', 'datadog', 'zscaler', 'google_workspace']:
        op.execute(f"ALTER TYPE connector_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
```

- [ ] **Step 2: Run migration**

```bash
docker compose exec backend alembic upgrade 013
```

- [ ] **Step 3: Update ConnectorType in models/connector.py**

```python
jira = "jira"
pagerduty = "pagerduty"
servicenow = "servicenow"
splunk = "splunk"
datadog = "datadog"
zscaler = "zscaler"
google_workspace = "google_workspace"
```

- [ ] **Step 4: Add requirements**

Add to `backend/requirements.txt`:
```
google-api-python-client>=2.120.0
google-auth>=2.27.0
```

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/013_add_new_connector_types_6e.py backend/app/models/connector.py backend/requirements.txt
git commit -m "feat: add migration 013 for workflow/observability connector types (jira, pagerduty, servicenow, splunk, datadog, zscaler, google_workspace)"
```

---

### Task 2: Jira Connector

- [ ] **Step 1: Create catalog jira.json**

```json
{
  "connector_type": "jira",
  "display_name": "Jira",
  "credential_fields": [
    {"name": "base_url", "label": "Jira Base URL", "type": "string", "required": true, "placeholder": "https://acme.atlassian.net"},
    {"name": "email", "label": "Email", "type": "string", "required": true},
    {"name": "api_token", "label": "API Token", "type": "password", "required": true}
  ],
  "actions": [
    {"action_id": "discover_projects", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Projects", "description": "All accessible Jira projects: key, name, type, lead", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "jira.discover_projects", "estimated_duration_seconds": 15},
    {"action_id": "discover_issues", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Issues", "description": "Open security issues: summary, priority, status, assignee, components", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "project_key", "type": "string", "required": false}, {"name": "jql", "type": "string", "required": false, "description": "Custom JQL query"}], "executor": "jira.discover_issues", "estimated_duration_seconds": 30},
    {"action_id": "create_issue", "generic_action": "create_ticket", "action_type": "change", "execution_tier": 2, "display_name": "Create Issue", "description": "Create a Jira issue with summary, description, priority, labels, assignee. Returns issue key.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "project_key", "type": "string", "required": true}, {"name": "issue_type", "type": "string", "required": true, "description": "Bug, Task, Story"}, {"name": "summary", "type": "string", "required": true}, {"name": "description", "type": "string", "required": false}, {"name": "priority", "type": "string", "required": false, "description": "Highest, High, Medium, Low, Lowest"}, {"name": "labels", "type": "array", "required": false}, {"name": "assignee_id", "type": "string", "required": false}], "executor": "jira.create_issue", "estimated_duration_seconds": 5},
    {"action_id": "transition_issue", "generic_action": "update_ticket", "action_type": "change", "execution_tier": 2, "display_name": "Transition Issue", "description": "Transition an issue to a new status.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "issue_key", "type": "string", "required": true}, {"name": "transition_name", "type": "string", "required": true, "description": "e.g. In Progress, Done, Cancelled"}], "executor": "jira.transition_issue", "estimated_duration_seconds": 5},
    {"action_id": "add_comment", "generic_action": "add_comment", "action_type": "change", "execution_tier": 2, "display_name": "Add Comment", "description": "Add a comment to an existing issue.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "issue_key", "type": "string", "required": true}, {"name": "comment", "type": "string", "required": true}], "executor": "jira.add_comment", "estimated_duration_seconds": 5},
    {"action_id": "link_issues", "generic_action": "link_tickets", "action_type": "change", "execution_tier": 2, "display_name": "Link Issues", "description": "Link two Jira issues (blocks/is blocked by/relates to).", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "inward_issue_key", "type": "string", "required": true}, {"name": "outward_issue_key", "type": "string", "required": true}, {"name": "link_type", "type": "string", "required": true, "description": "Blocks, Relates, Clones"}], "executor": "jira.link_issues", "estimated_duration_seconds": 5},
    {"action_id": "assign_issue", "generic_action": "assign_ticket", "action_type": "change", "execution_tier": 2, "display_name": "Assign Issue", "description": "Assign an issue to a user.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "issue_key", "type": "string", "required": true}, {"name": "assignee_id", "type": "string", "required": true}], "executor": "jira.assign_issue", "estimated_duration_seconds": 5}
  ]
}
```

- [ ] **Step 2: Create _client.py**

```python
# backend/app/connectors/executors/jira/_client.py
import httpx
import base64


def get_client(creds: dict) -> httpx.AsyncClient:
    auth = base64.b64encode(f"{creds['email']}:{creds['api_token']}".encode()).decode()
    base = creds["base_url"].rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{base}/rest/api/3",
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json", "Content-Type": "application/json"},
        timeout=30.0,
    )
```

```python
# backend/app/connectors/executors/jira/__init__.py
```

- [ ] **Step 3: Create executor files**

```python
# backend/app/connectors/executors/jira/discover_projects.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_projects", "projects": [
            {"key": "SEC", "name": "Security", "type": "software", "lead": "alice"}
        ], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/project/search", params={"maxResults": 200})
        resp.raise_for_status()
        projects = [{"key": p["key"], "name": p["name"], "type": p.get("projectTypeKey"), "lead": p.get("lead", {}).get("displayName")} for p in resp.json().get("values", [])]
    return {"action": "discover_projects", "projects": projects, "count": len(projects)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/jira/discover_issues.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_issues", "issues": [
            {"key": "SEC-1", "summary": "Mock security issue", "priority": "High", "status": "In Progress", "assignee": "alice"}
        ], "count": 1}
    from ._client import get_client
    jql = parameters.get("jql")
    if not jql:
        project = parameters.get("project_key", "")
        jql = f"project = {project} AND status != Done ORDER BY priority DESC" if project else "status != Done ORDER BY priority DESC"
    async with get_client(creds) as client:
        resp = await client.post("/search", json={"jql": jql, "maxResults": 100, "fields": ["summary", "priority", "status", "assignee", "components"]})
        resp.raise_for_status()
        issues = [{"key": i["key"], "summary": i["fields"]["summary"], "priority": i["fields"].get("priority", {}).get("name"), "status": i["fields"]["status"]["name"], "assignee": i["fields"].get("assignee", {}).get("displayName") if i["fields"].get("assignee") else None} for i in resp.json().get("issues", [])]
    return {"action": "discover_issues", "issues": issues, "count": len(issues)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/jira/create_issue.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "create_issue", "issue_key": "SEC-MOCK-1", "id": "mock-issue-id"}
    from ._client import get_client
    fields = {
        "project": {"key": parameters["project_key"]},
        "issuetype": {"name": parameters["issue_type"]},
        "summary": parameters["summary"],
    }
    if parameters.get("description"):
        fields["description"] = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": parameters["description"]}]}]}
    if parameters.get("priority"):
        fields["priority"] = {"name": parameters["priority"]}
    if parameters.get("labels"):
        fields["labels"] = parameters["labels"]
    if parameters.get("assignee_id"):
        fields["assignee"] = {"id": parameters["assignee_id"]}
    async with get_client(creds) as client:
        resp = await client.post("/issue", json={"fields": fields})
        resp.raise_for_status()
        issue = resp.json()
    return {"action": "create_issue", "issue_key": issue["key"], "id": issue["id"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    issue_key = execution_result.get("issue_key")
    if not issue_key:
        return {"rolled_back": False, "reason": "no issue_key in result"}
    return await (lambda p: __import__('app.connectors.executors.jira.transition_issue', fromlist=['execute']).execute({"issue_key": issue_key, "transition_name": "Cancelled"}, [], p))(connector)
```

```python
# backend/app/connectors/executors/jira/transition_issue.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    issue_key = parameters["issue_key"]
    transition_name = parameters["transition_name"]
    if not creds:
        return {"action": "transition_issue", "issue_key": issue_key, "new_status": transition_name}
    from ._client import get_client
    async with get_client(creds) as client:
        # Get available transitions
        resp = await client.get(f"/issue/{issue_key}/transitions")
        resp.raise_for_status()
        transitions = {t["name"]: t["id"] for t in resp.json().get("transitions", [])}
        transition_id = transitions.get(transition_name)
        if not transition_id:
            return {"action": "transition_issue", "error": f"Transition '{transition_name}' not found. Available: {list(transitions.keys())}"}
        resp2 = await client.post(f"/issue/{issue_key}/transitions", json={"transition": {"id": transition_id}})
        resp2.raise_for_status()
    return {"action": "transition_issue", "issue_key": issue_key, "new_status": transition_name}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous transition state not captured"}
```

```python
# backend/app/connectors/executors/jira/add_comment.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    issue_key = parameters["issue_key"]
    if not creds:
        return {"action": "add_comment", "issue_key": issue_key, "comment_id": "mock-comment-id"}
    from ._client import get_client
    body = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": parameters["comment"]}]}]}
    async with get_client(creds) as client:
        resp = await client.post(f"/issue/{issue_key}/comment", json={"body": body})
        resp.raise_for_status()
        comment = resp.json()
    return {"action": "add_comment", "issue_key": issue_key, "comment_id": comment["id"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "comments cannot be automatically deleted"}
```

```python
# backend/app/connectors/executors/jira/link_issues.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "link_issues", "inward": parameters.get("inward_issue_key"), "outward": parameters.get("outward_issue_key"), "linked": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/issueLink", json={"type": {"name": parameters["link_type"]}, "inwardIssue": {"key": parameters["inward_issue_key"]}, "outwardIssue": {"key": parameters["outward_issue_key"]}})
        resp.raise_for_status()
    return {"action": "link_issues", "inward": parameters["inward_issue_key"], "outward": parameters["outward_issue_key"], "linked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "link removal requires knowing the link ID — remove manually"}
```

```python
# backend/app/connectors/executors/jira/assign_issue.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    issue_key = parameters["issue_key"]
    assignee_id = parameters["assignee_id"]
    if not creds:
        return {"action": "assign_issue", "issue_key": issue_key, "assignee_id": assignee_id, "assigned": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.put(f"/issue/{issue_key}/assignee", json={"accountId": assignee_id})
        resp.raise_for_status()
    return {"action": "assign_issue", "issue_key": issue_key, "assignee_id": assignee_id, "assigned": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous assignee not captured — re-assign manually"}
```

- [ ] **Step 4: Commit Jira connector**

```bash
git add backend/app/connectors/catalog/jira.json backend/app/connectors/executors/jira/
git commit -m "feat: add Jira connector with 7 actions (discover projects/issues, create issue, transition, comment, link, assign)"
```

---

### Task 3: PagerDuty Connector

- [ ] **Step 1: Create catalog pagerduty.json**

```json
{
  "connector_type": "pagerduty",
  "display_name": "PagerDuty",
  "credential_fields": [
    {"name": "api_key", "label": "API Token", "type": "password", "required": true},
    {"name": "from_email", "label": "From Email", "type": "string", "required": true, "description": "Email address for incident creation attribution"}
  ],
  "actions": [
    {"action_id": "discover_services", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Services", "description": "PagerDuty services: name, escalation policy, on-call team, status", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "pagerduty.discover_services", "estimated_duration_seconds": 15},
    {"action_id": "discover_incidents", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Incidents", "description": "Active incidents: title, severity, service, assigned to, created at", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "pagerduty.discover_incidents", "estimated_duration_seconds": 15},
    {"action_id": "discover_oncall", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover On-Call", "description": "Current on-call schedule: who is on call per service", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "pagerduty.discover_oncall", "estimated_duration_seconds": 15},
    {"action_id": "create_incident", "generic_action": "create_incident", "action_type": "change", "execution_tier": 2, "display_name": "Create Incident", "description": "Create a PagerDuty incident on a service. Returns incident ID. Rollback: resolve incident.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "title", "type": "string", "required": true}, {"name": "service_id", "type": "string", "required": true}, {"name": "urgency", "type": "string", "required": false, "description": "high or low"}, {"name": "body", "type": "string", "required": false}], "executor": "pagerduty.create_incident", "estimated_duration_seconds": 5},
    {"action_id": "resolve_incident", "generic_action": "resolve_incident", "action_type": "change", "execution_tier": 2, "display_name": "Resolve Incident", "description": "Resolve an incident.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "incident_id", "type": "string", "required": true}], "executor": "pagerduty.resolve_incident", "estimated_duration_seconds": 5},
    {"action_id": "acknowledge_incident", "generic_action": "acknowledge_incident", "action_type": "change", "execution_tier": 2, "display_name": "Acknowledge Incident", "description": "Acknowledge an incident.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "incident_id", "type": "string", "required": true}], "executor": "pagerduty.acknowledge_incident", "estimated_duration_seconds": 5},
    {"action_id": "trigger_webhook", "generic_action": "send_event", "action_type": "change", "execution_tier": 2, "display_name": "Trigger Webhook", "description": "Send a custom event to PagerDuty Events API.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "routing_key", "type": "string", "required": true}, {"name": "event_action", "type": "string", "required": true, "description": "trigger, acknowledge, or resolve"}, {"name": "summary", "type": "string", "required": true}, {"name": "severity", "type": "string", "required": false, "description": "critical, error, warning, info"}, {"name": "source", "type": "string", "required": false}], "executor": "pagerduty.trigger_webhook", "estimated_duration_seconds": 5},
    {"action_id": "get_incident_notes", "generic_action": "get_notes", "action_type": "change", "execution_tier": 1, "display_name": "Get Incident Notes", "description": "Retrieve notes/timeline for an incident.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "incident_id", "type": "string", "required": true}], "executor": "pagerduty.get_incident_notes", "estimated_duration_seconds": 5}
  ]
}
```

- [ ] **Step 2: Create _client.py and __init__.py**

```python
# backend/app/connectors/executors/pagerduty/_client.py
import httpx

def get_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.pagerduty.com",
        headers={"Authorization": f"Token token={creds['api_key']}", "Accept": "application/vnd.pagerduty+json;version=2", "Content-Type": "application/json", "From": creds["from_email"]},
        timeout=30.0,
    )
```

```python
# backend/app/connectors/executors/pagerduty/__init__.py
```

- [ ] **Step 3: Create all 8 executor files**

```python
# backend/app/connectors/executors/pagerduty/discover_services.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_services", "services": [{"id": "P0001", "name": "Production API", "status": "active"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/services", params={"limit": 100})
        resp.raise_for_status()
        services = [{"id": s["id"], "name": s["name"], "status": s.get("status")} for s in resp.json().get("services", [])]
    return {"action": "discover_services", "services": services, "count": len(services)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/pagerduty/discover_incidents.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_incidents", "incidents": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/incidents", params={"statuses[]": ["triggered", "acknowledged"], "limit": 100})
        resp.raise_for_status()
        incidents = [{"id": i["id"], "title": i["title"], "status": i["status"], "urgency": i.get("urgency"), "service_id": i.get("service", {}).get("id")} for i in resp.json().get("incidents", [])]
    return {"action": "discover_incidents", "incidents": incidents, "count": len(incidents)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/pagerduty/discover_oncall.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_oncall", "oncall": [{"schedule": "Primary", "user": "alice@example.com"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/oncalls", params={"limit": 100})
        resp.raise_for_status()
        oncall = [{"schedule": o.get("schedule", {}).get("summary"), "user": o.get("user", {}).get("email"), "start": o.get("start"), "end": o.get("end")} for o in resp.json().get("oncalls", [])]
    return {"action": "discover_oncall", "oncall": oncall, "count": len(oncall)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/pagerduty/create_incident.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "create_incident", "incident_id": "Q0001MOCK", "title": parameters.get("title"), "status": "triggered"}
    from ._client import get_client
    payload = {"incident": {"type": "incident", "title": parameters["title"], "service": {"id": parameters["service_id"], "type": "service_reference"}, "urgency": parameters.get("urgency", "high")}}
    if parameters.get("body"):
        payload["incident"]["body"] = {"type": "incident_body", "details": parameters["body"]}
    async with get_client(creds) as client:
        resp = await client.post("/incidents", json=payload)
        resp.raise_for_status()
        inc = resp.json()["incident"]
    return {"action": "create_incident", "incident_id": inc["id"], "title": inc["title"], "status": inc["status"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    incident_id = execution_result.get("incident_id")
    if not incident_id:
        return {"rolled_back": False, "reason": "no incident_id in result"}
    from app.connectors.executors.pagerduty.resolve_incident import execute as resolve
    return await resolve({"incident_id": incident_id}, [], connector)
```

```python
# backend/app/connectors/executors/pagerduty/resolve_incident.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    incident_id = parameters["incident_id"]
    if not creds:
        return {"action": "resolve_incident", "incident_id": incident_id, "status": "resolved"}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.put(f"/incidents/{incident_id}", json={"incident": {"type": "incident", "status": "resolved"}})
        resp.raise_for_status()
    return {"action": "resolve_incident", "incident_id": incident_id, "status": "resolved"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot un-resolve an incident"}
```

```python
# backend/app/connectors/executors/pagerduty/acknowledge_incident.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    incident_id = parameters["incident_id"]
    if not creds:
        return {"action": "acknowledge_incident", "incident_id": incident_id, "status": "acknowledged"}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.put(f"/incidents/{incident_id}", json={"incident": {"type": "incident", "status": "acknowledged"}})
        resp.raise_for_status()
    return {"action": "acknowledge_incident", "incident_id": incident_id, "status": "acknowledged"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot un-acknowledge an incident"}
```

```python
# backend/app/connectors/executors/pagerduty/trigger_webhook.py
import httpx

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    routing_key = parameters["routing_key"]
    if not creds:
        return {"action": "trigger_webhook", "status": "success", "dedup_key": "mock-dedup-key"}
    payload = {
        "routing_key": routing_key,
        "event_action": parameters["event_action"],
        "payload": {
            "summary": parameters["summary"],
            "severity": parameters.get("severity", "error"),
            "source": parameters.get("source", "Nexplane"),
        },
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://events.pagerduty.com/v2/enqueue", json=payload, timeout=15.0)
        resp.raise_for_status()
        result = resp.json()
    return {"action": "trigger_webhook", "status": result.get("status"), "dedup_key": result.get("dedup_key")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "webhook events cannot be withdrawn"}
```

```python
# backend/app/connectors/executors/pagerduty/get_incident_notes.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    incident_id = parameters["incident_id"]
    if not creds:
        return {"action": "get_incident_notes", "incident_id": incident_id, "notes": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/incidents/{incident_id}/notes")
        resp.raise_for_status()
        notes = [{"id": n["id"], "content": n["content"], "created_at": n["created_at"], "user": n.get("user", {}).get("email")} for n in resp.json().get("notes", [])]
    return {"action": "get_incident_notes", "incident_id": incident_id, "notes": notes, "count": len(notes)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "status check has no rollback"}
```

- [ ] **Step 4: Commit PagerDuty connector**

```bash
git add backend/app/connectors/catalog/pagerduty.json backend/app/connectors/executors/pagerduty/
git commit -m "feat: add PagerDuty connector with 8 actions"
```

---

### Task 4: ServiceNow, Splunk, Datadog Connectors

These 3 connectors follow the same pattern as above.

- [ ] **Step 1: Create ServiceNow connector**

Catalog `servicenow.json`: credential fields `instance_url` (string), `username` (string), `password` (password). Actions: `discover_incidents`, `discover_change_requests`, `discover_cmdb_cis`, `create_incident`, `create_change_request`, `update_incident`, `resolve_incident`, `sync_cmdb`.

`_client.py`: httpx with Basic auth to `{instance_url}/api/now/v2/table/`.

```python
# backend/app/connectors/executors/servicenow/_client.py
import httpx
import base64

def get_client(creds: dict) -> httpx.AsyncClient:
    auth = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
    url = creds["instance_url"].rstrip("/")
    return httpx.AsyncClient(base_url=f"{url}/api/now/v2/table", headers={"Authorization": f"Basic {auth}", "Accept": "application/json", "Content-Type": "application/json"}, timeout=30.0)
```

Create all 8 executor files with mock data and real API calls.

- [ ] **Step 2: Create Splunk connector**

Catalog `splunk.json`: credential fields `base_url` (string), `token` (password), `hec_url` (string, optional). Actions: `search`, `discover_saved_searches`, `discover_notable_events`, `send_event`, `create_alert`, `suppress_notable`, `run_adaptive_response`.

`_client.py`: httpx with `Authorization: Bearer {token}` for REST API; `Authorization: Splunk {token}` for HEC endpoint.

```python
# backend/app/connectors/executors/splunk/_client.py
import httpx

def get_rest_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=creds["base_url"].rstrip("/"), headers={"Authorization": f"Bearer {creds['token']}"}, timeout=60.0, verify=False)

def get_hec_client(creds: dict) -> httpx.AsyncClient:
    hec_url = creds.get("hec_url", creds["base_url"].replace(":8089", ":8088"))
    return httpx.AsyncClient(base_url=hec_url, headers={"Authorization": f"Splunk {creds['token']}"}, timeout=30.0, verify=False)
```

- [ ] **Step 3: Create Datadog connector**

Catalog `datadog.json`: credential fields `api_key` (string), `app_key` (string), `site` (string, default `datadoghq.com`). Actions: `discover_hosts`, `discover_monitors`, `discover_security_signals`, `discover_logs_indexes`, `mute_host`, `unmute_host`, `create_monitor`, `trigger_synthetics`, `send_event`.

`_client.py`: httpx with `DD-API-KEY` + `DD-APPLICATION-KEY` headers.

```python
# backend/app/connectors/executors/datadog/_client.py
import httpx

def get_client(creds: dict) -> httpx.AsyncClient:
    site = creds.get("site", "datadoghq.com")
    return httpx.AsyncClient(
        base_url=f"https://api.{site}/api/v2",
        headers={"DD-API-KEY": creds["api_key"], "DD-APPLICATION-KEY": creds["app_key"], "Content-Type": "application/json"},
        timeout=30.0,
    )

def get_v1_client(creds: dict) -> httpx.AsyncClient:
    site = creds.get("site", "datadoghq.com")
    return httpx.AsyncClient(
        base_url=f"https://api.{site}/api/v1",
        headers={"DD-API-KEY": creds["api_key"], "DD-APPLICATION-KEY": creds["app_key"]},
        timeout=30.0,
    )
```

- [ ] **Step 4: Create all executor files for ServiceNow, Splunk, Datadog**

For each action in each connector, create executor file with real-if-creds/mock-if-not. ServiceNow: GET/POST to `/table/incident`, `/table/change_request`, `/table/cmdb_ci`. Splunk: POST to `/services/search/jobs` for search, `/services/saved/searches` for discover_saved_searches. Datadog: GET `/hosts`, `/monitors`, `/security_monitoring/signals`, `/logs/indexes`; POST `/downtime` for mute, `/events` for send_event.

- [ ] **Step 5: Commit ServiceNow, Splunk, Datadog**

```bash
git add backend/app/connectors/catalog/servicenow.json backend/app/connectors/catalog/splunk.json backend/app/connectors/catalog/datadog.json
git add backend/app/connectors/executors/servicenow/ backend/app/connectors/executors/splunk/ backend/app/connectors/executors/datadog/
git commit -m "feat: add ServiceNow, Splunk, Datadog connectors"
```

---

### Task 5: Zscaler and Google Workspace Connectors

- [ ] **Step 1: Create Zscaler connector**

Catalog `zscaler.json`: credential fields `cloud` (string, e.g. `zscaler.net`), `api_key` (string), `username` (string), `password` (password). Actions: `discover_users`, `discover_policies`, `discover_locations`, `discover_zpa_applications`, `block_url`, `block_ip`, `suspend_user`, `activate_user`, `update_url_category`.

Auth pattern: Session-based — POST `/api/v1/authenticatedSession` with obfuscated API key to get JSESSIONID cookie.

```python
# backend/app/connectors/executors/zscaler/_client.py
import hashlib
import time
import httpx


def obfuscate_api_key(api_key: str) -> tuple[str, str]:
    """Zscaler API key obfuscation as per their docs."""
    timestamp = str(int(time.time() * 1000))
    high = timestamp[-6:]
    low = str(int(high) >> 1)
    obf = ""
    for c in high:
        obf += api_key[int(c)]
    for c in low.zfill(6):
        obf += api_key[int(c) + 2]
    return obf, timestamp


async def get_session(creds: dict) -> httpx.AsyncClient:
    cloud = creds["cloud"]
    obf_key, ts = obfuscate_api_key(creds["api_key"])
    client = httpx.AsyncClient(base_url=f"https://zsapi.{cloud}/api/v1", timeout=30.0)
    resp = await client.post("/authenticatedSession", json={"apiKey": obf_key, "username": creds["username"], "password": creds["password"], "timestamp": ts})
    resp.raise_for_status()
    return client
```

- [ ] **Step 2: Create Zscaler executor files**

Create executors for all 9 actions. Key actions: `block_url` POSTs to `/urlCategories/{categoryId}` to add URL; `block_ip` adds to custom IP deny category; `suspend_user` PATCHes user with `disabled: true`.

- [ ] **Step 3: Create Google Workspace connector**

Catalog `google_workspace.json`: credential fields `service_account_key_json` (password — service account with domain-wide delegation), `admin_email` (string — for impersonation), `domain` (string). Actions: `discover_users`, `discover_groups`, `discover_devices`, `discover_admin_roles`, `discover_audit_logs`, `suspend_user`, `unsuspend_user`, `reset_password`, `revoke_tokens`, `remove_from_group`, `wipe_device`, `block_device`.

`_client.py`: Uses `google-api-python-client` with service account + domain-wide delegation + impersonation.

```python
# backend/app/connectors/executors/google_workspace/_client.py
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/admin.directory.group",
    "https://www.googleapis.com/auth/admin.directory.device.chromeos",
    "https://www.googleapis.com/auth/admin.directory.device.mobile",
    "https://www.googleapis.com/auth/admin.directory.rolemanagement",
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
]


def get_admin_service(creds: dict, service_name: str, version: str):
    key_info = json.loads(creds["service_account_key_json"])
    credentials = service_account.Credentials.from_service_account_info(key_info, scopes=SCOPES)
    delegated = credentials.with_subject(creds["admin_email"])
    return build(service_name, version, credentials=delegated)
```

Create all 12 executor files: `discover_users` (Admin SDK `/users.list`), `suspend_user` (PATCH `/users/{userId}` with `suspended: true`), `wipe_device` (POST `/mobiledevices/{resourceId}/action` with `{action: 'wipe'}`), etc.

**Note for `wipe_device`:** Non-reversible — executor should log this clearly:

```python
# backend/app/connectors/executors/google_workspace/wipe_device.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_id = parameters["resource_id"]
    confirm = parameters.get("confirm_wipe")
    if not confirm:
        return {"action": "wipe_device", "error": "confirm_wipe must be true — this action is non-reversible"}
    if not creds:
        return {"action": "wipe_device", "resource_id": resource_id, "wiped": True, "warning": "MOCK — device not actually wiped"}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")
    await loop.run_in_executor(None, lambda: service.mobiledevices().action(customerId="my_customer", resourceId=resource_id, body={"action": "wipe"}).execute())
    return {"action": "wipe_device", "resource_id": resource_id, "wiped": True, "warning": "Device wipe initiated — irreversible"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "device wipe is irreversible"}
```

- [ ] **Step 4: Commit Zscaler and Google Workspace**

```bash
git add backend/app/connectors/catalog/zscaler.json backend/app/connectors/catalog/google_workspace.json
git add backend/app/connectors/executors/zscaler/ backend/app/connectors/executors/google_workspace/
git commit -m "feat: add Zscaler and Google Workspace connectors"
```

---

### Task 6: Frontend Updates

- [ ] **Step 1: Update ConnectorType in api.ts**

Add: `| 'jira' | 'pagerduty' | 'servicenow' | 'splunk' | 'datadog' | 'zscaler' | 'google_workspace'`

- [ ] **Step 2: Update CONNECTOR_LABELS in Connectors.tsx**

```typescript
jira: 'Jira',
pagerduty: 'PagerDuty',
servicenow: 'ServiceNow',
splunk: 'Splunk',
datadog: 'Datadog',
zscaler: 'Zscaler',
google_workspace: 'Google Workspace',
```

- [ ] **Step 3: Update icons and AddConnectorModal**

Use `AlertTriangle` or `Bell` for pagerduty, `Database` for servicenow/splunk, `BarChart` for datadog, `Shield` for zscaler, `Globe` or `Mail` for google_workspace, `Ticket` or `Trello` for jira.

- [ ] **Step 4: Verify build**

```bash
docker compose exec frontend npm run build 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/
git commit -m "feat: add 6e workflow/observability connectors to frontend type lists"
```

---

### Task 7: Integration Tests

- [ ] **Step 1: Write tests**

```python
# backend/app/tests/test_connector_6e.py
import pytest


@pytest.mark.asyncio
async def test_jira_create_issue_mock():
    from app.connectors.executors.jira.create_issue import execute
    result = await execute({"project_key": "SEC", "issue_type": "Bug", "summary": "Test issue"}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "create_issue"
    assert "issue_key" in result


@pytest.mark.asyncio
async def test_pagerduty_create_incident_rollback():
    from app.connectors.executors.pagerduty.create_incident import rollback
    result = await rollback({"service_id": "P001"}, {"incident_id": "Q001"}, type("C", (), {"credentials": {}})())
    assert result["action"] == "resolve_incident"


@pytest.mark.asyncio
async def test_datadog_discover_hosts_mock():
    from app.connectors.executors.datadog.discover_hosts import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_hosts"


@pytest.mark.asyncio
async def test_google_workspace_wipe_device_requires_confirm():
    from app.connectors.executors.google_workspace.wipe_device import execute
    result = await execute({"resource_id": "mock-device"}, [], type("C", (), {"credentials": {}})())
    assert "error" in result
    assert "confirm_wipe" in result["error"]


@pytest.mark.asyncio
async def test_saltstack_function_not_in_allowlist():
    from app.connectors.executors.saltstack.run_function import execute
    result = await execute({"function": "cmd.run", "target": "*"}, [], type("C", (), {"credentials": {}})())
    assert "error" in result
```

- [ ] **Step 2: Run all 6e tests**

```bash
docker compose exec backend pytest app/tests/test_connector_6e.py -v
```
Expected: 5 tests pass.

- [ ] **Step 3: Run full test suite**

```bash
docker compose exec backend pytest app/tests/ -v --tb=short 2>&1 | tail -30
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/tests/test_connector_6e.py
git commit -m "test: add integration tests for 6e workflow/observability connectors"
```
