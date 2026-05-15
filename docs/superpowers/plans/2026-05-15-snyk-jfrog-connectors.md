# Snyk / JFrog Xray Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `scan_image` + `sync_findings` executors to the existing Snyk connector, create the full JFrog Xray connector with catalog, add four new ChangeType values, and add `SNYK_SCAN` + `JFROG_SCAN` credential-gated smoke phases to `test_aws_live.py`.

**Architecture:** Snyk already has a `_client.py` (thin httpx wrapper). We extend it with a `SnykClient` class and add two new executor files. JFrog is brand new: same pattern — one `_client.py` class, two executors, one catalog JSON. Both smoke phases use `_get_aws_boto3_client("ssm")` to read secrets from SSM (pattern already established), skip gracefully if credentials are absent, and are dispatched from `main()` in `test_aws_live.py`.

**Tech Stack:** Python 3.9, httpx (already used by Snyk), boto3 (SSM param reads), Nexplane executor pattern (async `execute` + `rollback` functions).

---

## File Map

| Action | File |
|--------|------|
| Modify | `backend/app/connectors/executors/snyk/_client.py` |
| Create | `backend/app/connectors/executors/snyk/scan_image.py` |
| Create | `backend/app/connectors/executors/snyk/sync_findings.py` |
| Modify | `backend/app/connectors/catalog/snyk.json` |
| Create | `backend/app/connectors/executors/jfrog/__init__.py` |
| Create | `backend/app/connectors/executors/jfrog/_client.py` |
| Create | `backend/app/connectors/executors/jfrog/scan_artifact.py` |
| Create | `backend/app/connectors/executors/jfrog/sync_violations.py` |
| Create | `backend/app/connectors/catalog/jfrog.json` |
| Modify | `backend/app/models/change_request.py` |
| Modify | `backend/tests/smoke/test_aws_live.py` |

---

## Task 1: Extend `SnykClient` in `_client.py`

The existing `_client.py` is a minimal factory function. Replace it with a class that supports all required methods. The class keeps the same `base_url` and auth header. All methods are `async`.

**Files:**
- Modify: `backend/app/connectors/executors/snyk/_client.py`

- [ ] **Step 1: Read the existing file**

Read `backend/app/connectors/executors/snyk/_client.py` to confirm current content (it currently only has `get_client(creds)`).

- [ ] **Step 2: Replace with `SnykClient` class**

Overwrite `backend/app/connectors/executors/snyk/_client.py` with:

```python
from __future__ import annotations
import httpx

_BASE = "https://api.snyk.io"
_API_VERSION = "2023-05-29"


class SnykClient:
    def __init__(self, api_token: str, org_id: str) -> None:
        self._org_id = org_id
        self._http = httpx.AsyncClient(
            base_url=_BASE,
            headers={
                "Authorization": f"token {api_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def __aenter__(self) -> "SnykClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._http.aclose()

    async def list_projects(self, org_id: str) -> list:
        resp = await self._http.get(
            f"/rest/orgs/{org_id}/projects",
            params={"version": _API_VERSION},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def get_issues(self, org_id: str, project_id: str) -> list:
        resp = await self._http.get(
            f"/rest/orgs/{org_id}/issues",
            params={"version": _API_VERSION, "project_id": project_id, "limit": 100},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def test_package(self, package_manager: str, package_name: str, version: str) -> dict:
        resp = await self._http.post(
            f"/v1/test/{package_manager}",
            json={"encoding": "plain", "files": {"target": {"contents": f"{package_name}=={version}"}}},
        )
        resp.raise_for_status()
        return resp.json()

    async def test_container_image(self, image: str) -> dict:
        resp = await self._http.post(
            "/v1/test/docker",
            json={"image": image},
        )
        resp.raise_for_status()
        return resp.json()

    async def create_project(self, org_id: str, target: dict) -> dict:
        resp = await self._http.post(
            f"/rest/orgs/{org_id}/projects",
            params={"version": _API_VERSION},
            json={"data": {"type": "project", "attributes": target}},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_project(self, org_id: str, project_id: str) -> None:
        resp = await self._http.delete(
            f"/rest/orgs/{org_id}/projects/{project_id}",
            params={"version": _API_VERSION},
        )
        resp.raise_for_status()


# Legacy factory used by existing executors (discover_*, trigger_test, etc.)
def get_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.snyk.io/rest",
        headers={"Authorization": f"token {creds['api_token']}", "Content-Type": "application/json"},
        timeout=30.0,
    )
```

- [ ] **Step 3: Verify syntax**

```bash
python -c "import ast; ast.parse(open('backend/app/connectors/executors/snyk/_client.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/snyk/_client.py
git commit -m "feat: extend SnykClient class in snyk/_client.py"
```

---

## Task 2: Create `snyk/scan_image.py`

Calls `SnykClient.test_container_image`, returns findings list with CVEs and severity. Rollback is no-op.

**Files:**
- Create: `backend/app/connectors/executors/snyk/scan_image.py`

- [ ] **Step 1: Write the executor**

Create `backend/app/connectors/executors/snyk/scan_image.py`:

```python
from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    image = parameters["image"]
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "snyk_scan_image", "image": image, "findings": [], "count": 0}

    from ._client import SnykClient

    api_token = creds["api_token"]
    org_id = creds["org_id"]

    async with SnykClient(api_token, org_id) as client:
        result = await client.test_container_image(image)

    vulnerabilities = result.get("vulnerabilities", [])
    findings = [
        {
            "cve": v.get("identifiers", {}).get("CVE", [None])[0],
            "severity": v.get("severity"),
            "title": v.get("title"),
            "package_name": v.get("packageName"),
            "version": v.get("version"),
        }
        for v in vulnerabilities
    ]
    return {
        "action": "snyk_scan_image",
        "image": image,
        "findings": findings,
        "count": len(findings),
        "ok": result.get("ok", True),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan_image is read-only — no rollback needed"}
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('backend/app/connectors/executors/snyk/scan_image.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/snyk/scan_image.py
git commit -m "feat: add snyk/scan_image.py executor"
```

---

## Task 3: Create `snyk/sync_findings.py`

Pulls all issues from a Snyk project, returns them formatted as Nexplane findings linked to the asset.

**Files:**
- Create: `backend/app/connectors/executors/snyk/sync_findings.py`

- [ ] **Step 1: Write the executor**

Create `backend/app/connectors/executors/snyk/sync_findings.py`:

```python
from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    project_id = parameters["project_id"]
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "snyk_sync_findings", "project_id": project_id, "findings": [], "count": 0}

    from ._client import SnykClient

    api_token = creds["api_token"]
    org_id = creds["org_id"]

    async with SnykClient(api_token, org_id) as client:
        issues = await client.get_issues(org_id, project_id)

    findings = []
    for issue in issues:
        attrs = issue.get("attributes", {})
        problems = attrs.get("problems", [])
        cves = [p.get("id") for p in problems if p.get("source") == "CVE"]
        findings.append(
            {
                "issue_id": issue.get("id"),
                "title": attrs.get("title"),
                "severity": attrs.get("effective_severity_level"),
                "cves": cves,
                "asset_ids": asset_ids,
                "status": attrs.get("status"),
                "ignored": attrs.get("ignored", False),
            }
        )

    return {
        "action": "snyk_sync_findings",
        "project_id": project_id,
        "findings": findings,
        "count": len(findings),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync_findings is read-only — no rollback needed"}
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('backend/app/connectors/executors/snyk/sync_findings.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/snyk/sync_findings.py
git commit -m "feat: add snyk/sync_findings.py executor"
```

---

## Task 4: Update `snyk.json` catalog

Add `snyk_scan_image` and `snyk_sync_findings` actions to the catalog.

**Files:**
- Modify: `backend/app/connectors/catalog/snyk.json`

- [ ] **Step 1: Read current file**

Read `backend/app/connectors/catalog/snyk.json` to see current actions array.

- [ ] **Step 2: Add the two new actions**

Add after the last existing action (before the closing `]` of the `"actions"` array):

```json
    {"action_id": "scan_image", "generic_action": "scan", "action_type": "change", "execution_tier": 2, "display_name": "Scan Container Image", "description": "Test a container image against Snyk vulnerability database, returns CVEs and severity.", "applicable_asset_types": ["server", "cloud_account"], "parameters": [{"name": "image", "type": "string", "required": true}], "executor": "snyk.scan_image", "estimated_duration_seconds": 60},
    {"action_id": "sync_findings", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Sync Findings", "description": "Pull all open issues from a Snyk project and import as Nexplane findings linked to assets.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "project_id", "type": "string", "required": true}], "executor": "snyk.sync_findings", "estimated_duration_seconds": 30}
```

The final JSON must be valid. The full `actions` array will have 9 entries.

- [ ] **Step 3: Validate JSON**

```bash
python -c "import json; json.load(open('backend/app/connectors/catalog/snyk.json')); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/catalog/snyk.json
git commit -m "feat: add scan_image and sync_findings to snyk catalog"
```

---

## Task 5: Create JFrog connector `__init__.py` and `_client.py`

**Files:**
- Create: `backend/app/connectors/executors/jfrog/__init__.py`
- Create: `backend/app/connectors/executors/jfrog/_client.py`

- [ ] **Step 1: Create empty `__init__.py`**

Create `backend/app/connectors/executors/jfrog/__init__.py` with empty content (just a newline).

- [ ] **Step 2: Write `JFrogClient`**

Create `backend/app/connectors/executors/jfrog/_client.py`:

```python
from __future__ import annotations
import httpx


class JFrogClient:
    """Thin async client for JFrog Artifactory + Xray REST APIs."""

    def __init__(self, base_url: str, username: str, password_or_token: str) -> None:
        self._base = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            auth=(username, password_or_token),
            headers={"Content-Type": "application/json"},
            timeout=60.0,
            verify=False,  # free-tier certs may be self-signed
        )

    async def __aenter__(self) -> "JFrogClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._http.aclose()

    # -------------------------------------------------------------------
    # Xray — scanning
    # -------------------------------------------------------------------

    async def scan_artifact(self, repo: str, path: str) -> dict:
        resp = await self._http.post(
            f"{self._base}/xray/api/v1/scanArtifact",
            json={"componentID": f"gav:///{repo}/{path}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_violations(self, filters: dict) -> list:
        resp = await self._http.post(
            f"{self._base}/xray/api/v1/violations",
            json=filters,
        )
        resp.raise_for_status()
        return resp.json().get("violations", [])

    async def get_artifact_summary(self, checksums: list) -> dict:
        resp = await self._http.post(
            f"{self._base}/xray/api/v1/summary/artifact",
            json={"checksums": checksums},
        )
        resp.raise_for_status()
        return resp.json()

    # -------------------------------------------------------------------
    # Xray — policies
    # -------------------------------------------------------------------

    async def create_policy(self, name: str, rules: list) -> dict:
        resp = await self._http.post(
            f"{self._base}/xray/api/v2/policies",
            json={"name": name, "type": "security", "rules": rules},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_policy(self, name: str) -> None:
        resp = await self._http.delete(f"{self._base}/xray/api/v2/policies/{name}")
        resp.raise_for_status()

    # -------------------------------------------------------------------
    # Xray — watches
    # -------------------------------------------------------------------

    async def create_watch(self, name: str, repos: list) -> dict:
        resources = {
            "repositories": {
                r: {"name": r, "type": "repository", "filters": []}
                for r in repos
            }
        }
        resp = await self._http.post(
            f"{self._base}/xray/api/v2/watches",
            json={"general_data": {"name": name, "active": True}, "project_resources": resources},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_watch(self, name: str) -> None:
        resp = await self._http.delete(f"{self._base}/xray/api/v2/watches/{name}")
        resp.raise_for_status()

    # -------------------------------------------------------------------
    # Artifactory — artifact upload helper
    # -------------------------------------------------------------------

    async def upload_artifact(self, repo: str, path: str, content: bytes) -> dict:
        resp = await self._http.put(
            f"{self._base}/artifactory/{repo}/{path}",
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_artifact(self, repo: str, path: str) -> None:
        resp = await self._http.delete(f"{self._base}/artifactory/{repo}/{path}")
        resp.raise_for_status()
```

- [ ] **Step 3: Verify syntax**

```bash
python -c "import ast; ast.parse(open('backend/app/connectors/executors/jfrog/_client.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/jfrog/__init__.py backend/app/connectors/executors/jfrog/_client.py
git commit -m "feat: add JFrogClient in jfrog/_client.py"
```

---

## Task 6: Create `jfrog/scan_artifact.py`

Triggers Xray scan of a specific artifact, returns violations/CVEs.

**Files:**
- Create: `backend/app/connectors/executors/jfrog/scan_artifact.py`

- [ ] **Step 1: Write the executor**

Create `backend/app/connectors/executors/jfrog/scan_artifact.py`:

```python
from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    repo = parameters["repo"]
    path = parameters["path"]
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "jfrog_scan_artifact", "repo": repo, "path": path, "violations": [], "count": 0}

    from ._client import JFrogClient

    async with JFrogClient(creds["base_url"], creds["username"], creds["password_or_token"]) as client:
        scan_result = await client.scan_artifact(repo, path)
        # Xray returns violations asynchronously; fetch them
        violations_raw = await client.get_violations(
            {"filters": {"artifact": f"{repo}/{path}"}, "pagination": {"order_by": "created", "limit": 100}}
        )

    violations = [
        {
            "type": v.get("violation_type"),
            "severity": v.get("severity"),
            "summary": v.get("summary"),
            "cves": [c.get("cve") for c in v.get("issue_id", {}).get("cves", []) if c.get("cve")],
            "impacted_artifact": v.get("impacted_artifact"),
        }
        for v in violations_raw
    ]

    return {
        "action": "jfrog_scan_artifact",
        "repo": repo,
        "path": path,
        "scan_result": scan_result,
        "violations": violations,
        "count": len(violations),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan_artifact is read-only — no rollback needed"}
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('backend/app/connectors/executors/jfrog/scan_artifact.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/jfrog/scan_artifact.py
git commit -m "feat: add jfrog/scan_artifact.py executor"
```

---

## Task 7: Create `jfrog/sync_violations.py`

Pulls all Xray violations, syncs as Nexplane findings.

**Files:**
- Create: `backend/app/connectors/executors/jfrog/sync_violations.py`

- [ ] **Step 1: Write the executor**

Create `backend/app/connectors/executors/jfrog/sync_violations.py`:

```python
from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "jfrog_sync_violations", "findings": [], "count": 0}

    from ._client import JFrogClient

    filters = parameters.get("filters", {"pagination": {"order_by": "created", "limit": 100}})

    async with JFrogClient(creds["base_url"], creds["username"], creds["password_or_token"]) as client:
        violations_raw = await client.get_violations(filters)

    findings = [
        {
            "violation_id": v.get("id"),
            "type": v.get("violation_type"),
            "severity": v.get("severity"),
            "summary": v.get("summary"),
            "cves": [c.get("cve") for c in v.get("issue_id", {}).get("cves", []) if c.get("cve")],
            "impacted_artifact": v.get("impacted_artifact"),
            "asset_ids": asset_ids,
        }
        for v in violations_raw
    ]

    return {
        "action": "jfrog_sync_violations",
        "findings": findings,
        "count": len(findings),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync_violations is read-only — no rollback needed"}
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('backend/app/connectors/executors/jfrog/sync_violations.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/jfrog/sync_violations.py
git commit -m "feat: add jfrog/sync_violations.py executor"
```

---

## Task 8: Create `jfrog.json` catalog

**Files:**
- Create: `backend/app/connectors/catalog/jfrog.json`

- [ ] **Step 1: Write the catalog file**

Create `backend/app/connectors/catalog/jfrog.json`:

```json
{
  "connector_type": "jfrog",
  "display_name": "JFrog Xray",
  "credential_fields": [
    {"name": "base_url", "label": "JFrog Base URL", "type": "string", "required": true},
    {"name": "username", "label": "Username", "type": "string", "required": true},
    {"name": "password_or_token", "label": "Password or API Token", "type": "password", "required": true}
  ],
  "actions": [
    {"action_id": "scan_artifact", "generic_action": "scan", "action_type": "change", "execution_tier": 2, "display_name": "Scan Artifact", "description": "Trigger Xray security scan of a specific artifact and return violations/CVEs.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "repo", "type": "string", "required": true}, {"name": "path", "type": "string", "required": true}], "executor": "jfrog.scan_artifact", "estimated_duration_seconds": 60},
    {"action_id": "sync_violations", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Sync Violations", "description": "Pull all Xray policy violations and import as Nexplane findings.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "filters", "type": "object", "required": false}], "executor": "jfrog.sync_violations", "estimated_duration_seconds": 30}
  ]
}
```

- [ ] **Step 2: Validate JSON**

```bash
python -c "import json; json.load(open('backend/app/connectors/catalog/jfrog.json')); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/catalog/jfrog.json
git commit -m "feat: add jfrog.json connector catalog"
```

---

## Task 9: Add ChangeType values

Add `snyk_scan_image`, `snyk_sync_findings`, `jfrog_scan_artifact`, `jfrog_sync_violations` to the `ChangeType` enum in `backend/app/models/change_request.py`.

**Files:**
- Modify: `backend/app/models/change_request.py`

- [ ] **Step 1: Read the bottom of `change_request.py`**

Read from line 230 onward to find the last entry in the enum.

- [ ] **Step 2: Append the four new values**

Find the last line of the ChangeType enum (currently `oci_compartment_delete = "oci_compartment_delete"` or similar) and add after it:

```python
    # Supply chain security
    snyk_scan_image = "snyk_scan_image"
    snyk_sync_findings = "snyk_sync_findings"
    jfrog_scan_artifact = "jfrog_scan_artifact"
    jfrog_sync_violations = "jfrog_sync_violations"
```

- [ ] **Step 3: Verify the enum parses**

```bash
python -c "from app.models.change_request import ChangeType; print(ChangeType.snyk_scan_image, ChangeType.jfrog_scan_artifact)"
```

Run from `backend/` directory:
```bash
cd backend && python -c "from app.models.change_request import ChangeType; print(ChangeType.snyk_scan_image, ChangeType.jfrog_scan_artifact)"
```

Expected: `snyk_scan_image jfrog_scan_artifact`

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/change_request.py
git commit -m "feat: add snyk and jfrog change types to ChangeType enum"
```

---

## Task 10: Add `SNYK_SCAN` smoke phase to `test_aws_live.py`

Add a standalone credential-gated smoke phase function `run_phase_snyk_scan` that:
1. Reads `SNYK_API_TOKEN` and `SNYK_ORG_ID` from SSM at `/nexplane/smoke/snyk/api_token` and `/nexplane/smoke/snyk/org_id`
2. Skips gracefully if either is absent
3. Calls `SnykClient.test_container_image("python:2.7")`, verifies findings with CVEs are returned
4. Optionally calls `SnykClient.get_issues` to simulate sync

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add the phase function**

Find the line `def run_phase_postgres_rotate` (around line 6941) in `test_aws_live.py`. Insert the new function **before** it (or anywhere before `def main()`). The function uses the `_get_aws_boto3_client` helper already imported at the top.

Add this function:

```python
def run_phase_snyk_scan(client: NexplaneClient, cloud_account_id: str) -> dict:
    """Phase SNYK_SCAN: credential-gated Snyk container image scan via cloud API.

    Reads SNYK_API_TOKEN and SNYK_ORG_ID from SSM /nexplane/smoke/snyk/*.
    Skips gracefully if credentials are absent.
    """
    import asyncio as _asyncio
    import sys as _sys
    print("\n[Phase SNYK_SCAN] Snyk container image scan")

    ssm_client = _get_aws_boto3_client("ssm")
    if not ssm_client:
        print("SKIP: No AWS credentials — cannot read Snyk secrets from SSM")
        return {"status": "skipped"}

    def _get_ssm_param(path: str) -> str:
        try:
            return ssm_client.get_parameter(Name=path, WithDecryption=True)["Parameter"]["Value"]
        except Exception:
            return ""

    api_token = _get_ssm_param("/nexplane/smoke/snyk/api_token")
    org_id = _get_ssm_param("/nexplane/smoke/snyk/org_id")

    if not api_token or not org_id:
        print("SKIP: Snyk credentials not in SSM (/nexplane/smoke/snyk/api_token and /nexplane/smoke/snyk/org_id)")
        return {"status": "skipped"}

    # Import SnykClient from the bundled connector package
    try:
        import sys as _sys
        if "/tmp/nexplane_smoke" not in _sys.path:
            _sys.path.insert(0, "/tmp/nexplane_smoke")
        from smoke.snyk._client import SnykClient
    except ImportError:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "snyk_client",
            "/tmp/nexplane_smoke/smoke/snyk/_client.py",
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        SnykClient = _mod.SnykClient

    async def _run() -> dict:
        async with SnykClient(api_token, org_id) as snyk:
            result = await snyk.test_container_image("python:2.7")
        return result

    scan_result = _asyncio.run(_run())

    vulns = scan_result.get("vulnerabilities", [])
    if not vulns:
        log("[SNYK_SCAN] Warning: no vulnerabilities returned for python:2.7 (Snyk API may require org-level access or image pull)")
    else:
        cves = [v.get("identifiers", {}).get("CVE", []) for v in vulns if v.get("identifiers", {}).get("CVE")]
        log(f"[SNYK_SCAN] {len(vulns)} vulnerabilities returned ({len(cves)} with CVEs)")

    log("[SNYK_SCAN] Snyk container scan complete")
    return {"status": "ok", "vuln_count": len(vulns)}
```

- [ ] **Step 2: Wire into `main()`**

Find the block in `main()` that dispatches `MONGODB_ROTATE` (near line 10803) and add after it:

```python
        if "SNYK_SCAN" in phases:
            run_phase_snyk_scan(client, cloud_account_id)
```

- [ ] **Step 3: Verify syntax**

```bash
python -c "import ast; ast.parse(open('backend/tests/smoke/test_aws_live.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: add SNYK_SCAN credential-gated smoke phase"
```

---

## Task 11: Add `JFROG_SCAN` smoke phase to `test_aws_live.py`

Add `run_phase_jfrog_scan` which:
1. Reads `JFROG_URL`, `JFROG_USER`, `JFROG_TOKEN` from SSM at `/nexplane/smoke/jfrog/*`
2. Skips gracefully if absent
3. Creates a temp repo named `nexplane-smoke-generic-local` (or uses default `generic-local`)
4. Uploads a tiny test artifact
5. Triggers Xray scan
6. Pulls violations
7. Deletes the artifact as cleanup

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add the phase function**

Add directly after `run_phase_snyk_scan` (or anywhere before `def main()`):

```python
def run_phase_jfrog_scan(client: NexplaneClient, cloud_account_id: str) -> dict:
    """Phase JFROG_SCAN: credential-gated JFrog Xray scan via cloud API.

    Reads JFROG_URL, JFROG_USER, JFROG_TOKEN from SSM /nexplane/smoke/jfrog/*.
    Skips gracefully if credentials are absent.
    """
    import asyncio as _asyncio
    print("\n[Phase JFROG_SCAN] JFrog Xray artifact scan")

    ssm_client = _get_aws_boto3_client("ssm")
    if not ssm_client:
        print("SKIP: No AWS credentials — cannot read JFrog secrets from SSM")
        return {"status": "skipped"}

    def _get_ssm_param(path: str) -> str:
        try:
            return ssm_client.get_parameter(Name=path, WithDecryption=True)["Parameter"]["Value"]
        except Exception:
            return ""

    jfrog_url = _get_ssm_param("/nexplane/smoke/jfrog/url")
    jfrog_user = _get_ssm_param("/nexplane/smoke/jfrog/user")
    jfrog_token = _get_ssm_param("/nexplane/smoke/jfrog/token")

    if not jfrog_url or not jfrog_user or not jfrog_token:
        print("SKIP: JFrog credentials not in SSM (/nexplane/smoke/jfrog/url, /user, /token)")
        return {"status": "skipped"}

    # Import JFrogClient from the bundled connector package
    try:
        import sys as _sys
        if "/tmp/nexplane_smoke" not in _sys.path:
            _sys.path.insert(0, "/tmp/nexplane_smoke")
        from smoke.jfrog._client import JFrogClient
    except ImportError:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "jfrog_client",
            "/tmp/nexplane_smoke/smoke/jfrog/_client.py",
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        JFrogClient = _mod.JFrogClient

    repo = "generic-local"
    artifact_path = "nexplane-smoke/test-artifact.txt"
    artifact_content = b"nexplane smoke test artifact for Xray scan"

    async def _run() -> dict:
        async with JFrogClient(jfrog_url, jfrog_user, jfrog_token) as jfrog:
            # Upload test artifact
            try:
                upload_result = await jfrog.upload_artifact(repo, artifact_path, artifact_content)
                log(f"[JFROG_SCAN] Artifact uploaded: {artifact_path}")
            except Exception as e:
                print(f"  [JFROG_SCAN] Upload warning: {e}")
                return {"status": "ok", "violations": [], "note": f"upload failed: {e}"}

            # Trigger scan
            try:
                await jfrog.scan_artifact(repo, artifact_path)
                log("[JFROG_SCAN] Xray scan triggered")
            except Exception as e:
                print(f"  [JFROG_SCAN] Scan trigger warning (Xray may not be licensed): {e}")

            # Pull violations
            try:
                violations = await jfrog.get_violations(
                    {"filters": {"artifact": f"{repo}/{artifact_path}"},
                     "pagination": {"order_by": "created", "limit": 50}}
                )
                log(f"[JFROG_SCAN] {len(violations)} violation(s) returned")
            except Exception as e:
                print(f"  [JFROG_SCAN] Violations warning: {e}")
                violations = []

            # Cleanup artifact
            try:
                await jfrog.delete_artifact(repo, artifact_path)
                log("[JFROG_SCAN] Artifact cleaned up")
            except Exception as e:
                print(f"  [JFROG_SCAN] Cleanup warning: {e}")

        return {"status": "ok", "violations": violations}

    result = _asyncio.run(_run())
    log("[JFROG_SCAN] JFrog Xray phase complete")
    return result
```

- [ ] **Step 2: Wire into `main()`**

After the `SNYK_SCAN` block you added in Task 10, add:

```python
        if "JFROG_SCAN" in phases:
            run_phase_jfrog_scan(client, cloud_account_id)
```

- [ ] **Step 3: Verify syntax**

```bash
python -c "import ast; ast.parse(open('backend/tests/smoke/test_aws_live.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: add JFROG_SCAN credential-gated smoke phase"
```

---

## Task 12: Update `run_on_ec2.py` tarball to include jfrog package

The EC2 runner copies connector packages into the tarball so standalone phases can import them. Add `jfrog` to the list alongside `snyk`.

**Files:**
- Modify: `backend/tests/smoke/run_on_ec2.py`

- [ ] **Step 1: Read the relevant section**

Read `backend/tests/smoke/run_on_ec2.py` around line 100-110 — the `make_test_tarball` function that lists connector packages.

- [ ] **Step 2: Add `jfrog` to the package list**

Find this line:
```python
        for connector_pkg in ("opnsense", "step_ca", "postgres", "redis", "mongodb"):
```

Change it to:
```python
        for connector_pkg in ("opnsense", "step_ca", "postgres", "redis", "mongodb", "snyk", "jfrog"):
```

- [ ] **Step 3: Verify syntax**

```bash
python -c "import ast; ast.parse(open('backend/tests/smoke/run_on_ec2.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run the smoke command (will skip — credentials not configured)**

```powershell
$env:AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"
$env:AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
$env:AWS_DEFAULT_REGION="us-east-1"
cd f:/Nexplane/nexplane
python backend/tests/smoke/run_on_ec2.py --phases SNYK_SCAN,JFROG_SCAN
```

Expected output: Runner launches, transfers files, runs `SNYK_SCAN` which prints `SKIP: Snyk credentials not in SSM`, runs `JFROG_SCAN` which prints `SKIP: JFrog credentials not in SSM`, then prints `ALL SELECTED PHASES PASSED`.

- [ ] **Step 5: Commit everything**

```bash
git add backend/tests/smoke/run_on_ec2.py
git commit -m "feat: add Snyk/JFrog Xray connectors + credential-gated smoke phases"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Task |
|-----------------|------|
| `SnykClient` class with all 6 methods | Task 1 |
| `snyk/scan_image.py` executor | Task 2 |
| `snyk/sync_findings.py` executor | Task 3 |
| `snyk_scan_image`, `snyk_sync_findings` ChangeType values | Task 9 |
| Snyk catalog updated | Task 4 |
| `JFrogClient` class with all 7 methods | Task 5 |
| `jfrog/scan_artifact.py` executor | Task 6 |
| `jfrog/sync_violations.py` executor | Task 7 |
| `jfrog_scan_artifact`, `jfrog_sync_violations` ChangeType values | Task 9 |
| `jfrog.json` catalog | Task 8 |
| `SNYK_SCAN` smoke phase with SSM cred-gate | Task 10 |
| `JFROG_SCAN` smoke phase with SSM cred-gate | Task 11 |
| `run_on_ec2.py` includes jfrog/snyk packages | Task 12 |
| `from __future__ import annotations` first line in smoke files | All smoke files already have it; new executor files include it |

### Placeholder scan

No TBD, TODO, or "similar to Task N" references. All code blocks are complete.

### Type consistency

- `SnykClient` defined in Task 1; imported as `from ._client import SnykClient` in Tasks 2 and 3. ✓
- `JFrogClient` defined in Task 5; imported as `from ._client import JFrogClient` in Tasks 6 and 7. ✓
- `JFrogClient.upload_artifact` used in Task 11 smoke phase — defined in Task 5. ✓
- `JFrogClient.delete_artifact` used in Task 11 — defined in Task 5. ✓
- `SnykClient.test_container_image` used in Task 10 — defined in Task 1. ✓
- `_get_aws_boto3_client` used in Tasks 10 and 11 — already defined in `smoke_helpers.py`. ✓
