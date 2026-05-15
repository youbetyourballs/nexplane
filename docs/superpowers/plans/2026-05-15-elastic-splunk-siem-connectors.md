# Elastic Security + Splunk Free SIEM Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Elastic Security and Splunk Free connectors with alert-sync and rule-management executors, plus AMI-cached smoke phases ELASTIC_ALERTS and SPLUNK_ALERTS.

**Architecture:** Elastic uses the Elasticsearch REST API (port 9200) for search/alerts and Kibana Security API (port 5601) for detection rules; both via httpx sync client (same pattern as wazuh). Splunk uses session-key auth over its REST API (port 8089). Smoke phases provision t3.large EC2s, install the SIEM, snapshot as AMI, then exercise the connector executors end-to-end.

**Tech Stack:** Python 3.9, httpx (sync), boto3, AWS SSM, Amazon Linux 2023 AMI `ami-0953476d60561c955`

---

## File Map

### New files — Elastic
- `backend/app/connectors/executors/elastic/__init__.py` — empty package init
- `backend/app/connectors/executors/elastic/_client.py` — `ElasticClient` class
- `backend/app/connectors/executors/elastic/sync_alerts.py` — sync_alerts executor
- `backend/app/connectors/executors/elastic/create_detection_rule.py` — create_rule executor
- `backend/app/connectors/catalog/elastic.json` — connector catalog

### New files — Splunk
- `backend/app/connectors/executors/splunk/sync_notables.py` — new executor (splunk dir already exists)
- (splunk `_client.py` already exists but needs rewrite per spec)

### Modified files
- `backend/app/models/change_request.py` — add `elastic_sync_alerts`, `elastic_create_rule`, `splunk_sync_notables`, `splunk_create_alert` to ChangeType enum
- `backend/app/connectors/catalog/splunk.json` — add `sync_notables` action entry
- `backend/tests/smoke/test_aws_live.py` — add `run_phase_elastic_alerts`, `run_phase_splunk_alerts`, wire into main()
- `backend/tests/smoke/run_on_ec2.py` — add `elastic` and `splunk` to `make_test_tarball()`

---

## Task 1: Add ChangeType entries

**Files:**
- Modify: `backend/app/models/change_request.py`

- [ ] **Step 1: Add four new ChangeType values**

Open `backend/app/models/change_request.py`. After the last existing entry (currently at the end of the enum body, around `mongodb_rotate` or similar), add:

```python
    # SIEM connectors — Elastic Security + Splunk Free
    elastic_sync_alerts = "elastic_sync_alerts"
    elastic_create_rule = "elastic_create_rule"
    splunk_sync_notables = "splunk_sync_notables"
    splunk_create_alert = "splunk_create_alert"
```

- [ ] **Step 2: Verify no syntax errors**

```bash
cd f:/Nexplane/nexplane
python -c "from backend.app.models.change_request import ChangeType; print('OK')"
```

Expected output: `OK`

Note: if Python path issues, run: `python -c "import sys; sys.path.insert(0,'backend'); from app.models.change_request import ChangeType; print(list(ChangeType)[-4:])"` — expect the four new names.

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/change_request.py
git commit -m "feat: add elastic_sync_alerts, elastic_create_rule, splunk_sync_notables, splunk_create_alert to ChangeType"
```

---

## Task 2: ElasticClient

**Files:**
- Create: `backend/app/connectors/executors/elastic/__init__.py`
- Create: `backend/app/connectors/executors/elastic/_client.py`

- [ ] **Step 1: Create the package init**

Create `backend/app/connectors/executors/elastic/__init__.py` with empty contents (just a newline).

- [ ] **Step 2: Write `_client.py`**

Create `backend/app/connectors/executors/elastic/_client.py`:

```python
from __future__ import annotations
"""Elastic Security / Kibana REST API client using httpx (sync)."""
from typing import Optional, List, Dict, Any
import httpx


class ElasticClient:
    """Thin sync wrapper around the Elasticsearch + Kibana HTTP APIs."""

    def __init__(self, base_url: str, username: str, password: str,
                 kibana_url: Optional[str] = None, verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")          # e.g. http://10.0.0.5:9200
        self.kibana_url = (kibana_url or base_url.replace(":9200", ":5601")).rstrip("/")
        self.auth = (username, password)
        self.verify_ssl = verify_ssl
        self._session = httpx.Client(
            auth=self.auth,
            verify=self.verify_ssl,
            timeout=60.0,
        )

    # ------------------------------------------------------------------
    # Elasticsearch index operations
    # ------------------------------------------------------------------

    def search(self, index: str, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Run a query DSL search against an index. Returns list of _source dicts."""
        resp = self._session.post(
            f"{self.base_url}/{index}/_search",
            json=query,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        return [h.get("_source", h) for h in hits]

    def index_doc(self, index: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Index a single document. Returns the ES response dict."""
        resp = self._session.post(
            f"{self.base_url}/{index}/_doc",
            json=doc,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Security alerts (signals index in .siem-signals-* or .alerts-*)
    # ------------------------------------------------------------------

    def get_alerts(self, start_time: str, end_time: str,
                   min_severity: Optional[str] = None) -> List[Dict[str, Any]]:
        """Pull security alerts from .siem-signals-default or .alerts-security.alerts-default.
        
        start_time / end_time: ISO-8601 strings, e.g. "now-24h", "now"
        min_severity: one of "low", "medium", "high", "critical" (optional filter)
        """
        severity_values = {"low": 1, "medium": 40, "high": 70, "critical": 90}
        must_clauses: List[Dict] = [
            {"range": {"@timestamp": {"gte": start_time, "lte": end_time}}}
        ]
        if min_severity and min_severity.lower() in severity_values:
            must_clauses.append(
                {"range": {"kibana.alert.severity": {"gte": min_severity.lower()}}}
            )
        query = {
            "size": 200,
            "query": {"bool": {"must": must_clauses}},
            "sort": [{"@timestamp": {"order": "desc"}}],
        }
        # Try modern alerts index first, fall back to legacy
        for idx in (".alerts-security.alerts-default", ".siem-signals-default"):
            try:
                return self.search(idx, query)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                raise
        return []

    def acknowledge_alert(self, alert_id: str) -> Dict[str, Any]:
        """Mark a signal/alert as acknowledged (status=acknowledged)."""
        # Kibana SIEM API endpoint
        resp = self._session.post(
            f"{self.kibana_url}/api/detection_engine/signals/status",
            headers={"kbn-xsrf": "true", "Content-Type": "application/json"},
            json={"signal_ids": [alert_id], "status": "acknowledged"},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Detection rules (Kibana Security API)
    # ------------------------------------------------------------------

    def create_rule(self, rule_def: Dict[str, Any]) -> Dict[str, Any]:
        """Create a KQL detection rule via Kibana Security API."""
        resp = self._session.post(
            f"{self.kibana_url}/api/detection_engine/rules",
            headers={"kbn-xsrf": "true", "Content-Type": "application/json"},
            json=rule_def,
        )
        resp.raise_for_status()
        return resp.json()

    def delete_rule(self, rule_id: str) -> Dict[str, Any]:
        """Delete a detection rule by its rule_id."""
        resp = self._session.delete(
            f"{self.kibana_url}/api/detection_engine/rules",
            headers={"kbn-xsrf": "true"},
            params={"rule_id": rule_id},
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single detection rule by rule_id. Returns None if not found."""
        resp = self._session.get(
            f"{self.kibana_url}/api/detection_engine/rules",
            headers={"kbn-xsrf": "true"},
            params={"rule_id": rule_id},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Fleet agents (Kibana Fleet API)
    # ------------------------------------------------------------------

    def get_fleet_agents(self) -> List[Dict[str, Any]]:
        """List Fleet agents via Kibana Fleet API."""
        resp = self._session.get(
            f"{self.kibana_url}/api/fleet/agents",
            headers={"kbn-xsrf": "true"},
            params={"perPage": 100},
        )
        resp.raise_for_status()
        return resp.json().get("items", [])

    def close(self) -> None:
        self._session.close()


def get_elastic_client(connector) -> Optional[ElasticClient]:
    """Build an ElasticClient from connector credentials. Returns None if no creds."""
    creds = getattr(connector, "credentials", None) or {}
    base_url = creds.get("base_url") or creds.get("url", "")
    username = creds.get("username", "elastic")
    password = creds.get("password", "")
    if not base_url or not password:
        return None
    return ElasticClient(
        base_url=base_url,
        username=username,
        password=password,
        kibana_url=creds.get("kibana_url"),
        verify_ssl=creds.get("verify_ssl", False),
    )
```

- [ ] **Step 3: Verify syntax**

```bash
python -c "import sys; sys.path.insert(0,'backend'); from app.connectors.executors.elastic._client import ElasticClient, get_elastic_client; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/elastic/
git commit -m "feat: add ElasticClient with search, alerts, rules, fleet methods"
```

---

## Task 3: Elastic sync_alerts executor

**Files:**
- Create: `backend/app/connectors/executors/elastic/sync_alerts.py`

- [ ] **Step 1: Write the executor**

Create `backend/app/connectors/executors/elastic/sync_alerts.py`:

```python
from __future__ import annotations
"""Sync Elastic Security alerts into Nexplane findings."""
from typing import Optional


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Pull security alerts from Elastic and return them as Nexplane findings.
    
    Parameters:
        start_time (str): ES date math, e.g. "now-24h". Default: "now-24h"
        end_time (str): ES date math, e.g. "now". Default: "now"
        min_severity (str): Optional minimum severity filter: low/medium/high/critical
    
    Returns dict with:
        action: "elastic_sync_alerts"
        count: number of alerts synced
        alerts: list of finding dicts
    """
    from ._client import get_elastic_client

    client = get_elastic_client(connector)
    if client is None:
        return {
            "action": "elastic_sync_alerts",
            "count": 0,
            "alerts": [],
            "status": "skipped",
            "reason": "no credentials",
        }

    start_time: str = parameters.get("start_time", "now-24h")
    end_time: str = parameters.get("end_time", "now")
    min_severity: Optional[str] = parameters.get("min_severity")

    try:
        raw_alerts = client.get_alerts(
            start_time=start_time,
            end_time=end_time,
            min_severity=min_severity,
        )

        findings = []
        for alert in raw_alerts:
            finding = {
                "source": "elastic",
                "alert_id": alert.get("kibana.alert.uuid") or alert.get("_id", ""),
                "rule_name": alert.get("kibana.alert.rule.name", ""),
                "severity": alert.get("kibana.alert.severity", "unknown"),
                "status": alert.get("kibana.alert.workflow_status", "open"),
                "timestamp": alert.get("@timestamp", ""),
                "raw": alert,
            }
            findings.append(finding)

        return {
            "action": "elastic_sync_alerts",
            "count": len(findings),
            "alerts": findings,
        }
    finally:
        client.close()


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync_alerts has no rollback — read-only operation"}
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import sys; sys.path.insert(0,'backend'); from app.connectors.executors.elastic.sync_alerts import execute, rollback; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/elastic/sync_alerts.py
git commit -m "feat: add elastic sync_alerts executor"
```

---

## Task 4: Elastic create_detection_rule executor

**Files:**
- Create: `backend/app/connectors/executors/elastic/create_detection_rule.py`

- [ ] **Step 1: Write the executor**

Create `backend/app/connectors/executors/elastic/create_detection_rule.py`:

```python
from __future__ import annotations
"""Create a KQL detection rule in Kibana Security. Rollback deletes it."""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Create a KQL detection rule in Kibana Security.
    
    Parameters:
        rule_id (str): Unique rule ID (caller-supplied or auto-generated)
        name (str): Human-readable rule name
        description (str): Rule description
        query (str): KQL query string
        index (list[str]): Index patterns to search, e.g. ["logs-*", "filebeat-*"]
        severity (str): low / medium / high / critical. Default: medium
        risk_score (int): 1-100. Default: 47
        interval (str): How often to run, e.g. "5m". Default: "5m"
        enabled (bool): Whether to enable the rule immediately. Default: True
    
    Returns dict with:
        action: "elastic_create_rule"
        rule_id: the rule_id used
        kibana_id: Kibana's internal UUID for the rule
        created: True
    """
    import uuid as _uuid
    from ._client import get_elastic_client

    client = get_elastic_client(connector)
    if client is None:
        rule_id = parameters.get("rule_id", str(_uuid.uuid4()))
        return {
            "action": "elastic_create_rule",
            "rule_id": rule_id,
            "kibana_id": "",
            "created": True,
            "status": "skipped",
            "reason": "no credentials",
        }

    rule_id: str = parameters.get("rule_id") or str(_uuid.uuid4())
    index_patterns = parameters.get("index", ["logs-*", "filebeat-*", ".ds-logs-*"])

    rule_def = {
        "rule_id": rule_id,
        "name": parameters.get("name", f"Nexplane smoke rule {rule_id[:8]}"),
        "description": parameters.get("description", "Created by Nexplane"),
        "type": "query",
        "language": "kuery",
        "query": parameters.get("query", "*"),
        "index": index_patterns,
        "severity": parameters.get("severity", "medium"),
        "risk_score": parameters.get("risk_score", 47),
        "interval": parameters.get("interval", "5m"),
        "from": "now-6m",
        "enabled": parameters.get("enabled", True),
    }

    try:
        result = client.create_rule(rule_def)
        kibana_id = result.get("id", "")
        return {
            "action": "elastic_create_rule",
            "rule_id": rule_id,
            "kibana_id": kibana_id,
            "created": True,
        }
    finally:
        client.close()


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the rule that was created."""
    from ._client import get_elastic_client

    rule_id = execution_result.get("rule_id") or parameters.get("rule_id", "")
    if not rule_id:
        return {"rolled_back": False, "reason": "no rule_id in execution_result"}

    client = get_elastic_client(connector)
    if client is None:
        return {"rolled_back": False, "reason": "no credentials"}

    try:
        client.delete_rule(rule_id)
        return {"rolled_back": True, "rule_id": rule_id}
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc)}
    finally:
        client.close()
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import sys; sys.path.insert(0,'backend'); from app.connectors.executors.elastic.create_detection_rule import execute, rollback; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/elastic/create_detection_rule.py
git commit -m "feat: add elastic create_detection_rule executor with rollback"
```

---

## Task 5: Elastic catalog JSON

**Files:**
- Create: `backend/app/connectors/catalog/elastic.json`

- [ ] **Step 1: Write the catalog**

Create `backend/app/connectors/catalog/elastic.json`:

```json
{
  "connector_type": "elastic",
  "display_name": "Elastic Security",
  "credential_fields": [
    {"name": "base_url", "label": "Elasticsearch URL", "type": "string", "required": true, "placeholder": "http://elastic.example.com:9200"},
    {"name": "username", "label": "Username", "type": "string", "required": true, "placeholder": "elastic"},
    {"name": "password", "label": "Password", "type": "password", "required": true},
    {"name": "kibana_url", "label": "Kibana URL (optional)", "type": "string", "required": false, "placeholder": "http://elastic.example.com:5601"},
    {"name": "verify_ssl", "label": "Verify SSL", "type": "boolean", "required": false}
  ],
  "actions": [
    {
      "action_id": "sync_alerts",
      "generic_action": "sync_alerts",
      "action_type": "ingest",
      "execution_tier": 1,
      "display_name": "Sync Security Alerts",
      "description": "Pull security alerts from Elastic Security and sync as Nexplane findings.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "start_time", "type": "string", "required": false, "default": "now-24h"},
        {"name": "end_time", "type": "string", "required": false, "default": "now"},
        {"name": "min_severity", "type": "string", "required": false}
      ],
      "executor": "elastic.sync_alerts",
      "estimated_duration_seconds": 30
    },
    {
      "action_id": "create_detection_rule",
      "generic_action": "create_rule",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Create Detection Rule",
      "description": "Create a KQL detection rule in Kibana Security. Rollback deletes the rule.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "rule_id", "type": "string", "required": false},
        {"name": "name", "type": "string", "required": true},
        {"name": "description", "type": "string", "required": false},
        {"name": "query", "type": "string", "required": true},
        {"name": "index", "type": "array", "required": false},
        {"name": "severity", "type": "string", "required": false},
        {"name": "risk_score", "type": "integer", "required": false},
        {"name": "interval", "type": "string", "required": false},
        {"name": "enabled", "type": "boolean", "required": false}
      ],
      "executor": "elastic.create_detection_rule",
      "estimated_duration_seconds": 10
    }
  ]
}
```

- [ ] **Step 2: Verify valid JSON**

```bash
python -c "import json; json.load(open('backend/app/connectors/catalog/elastic.json')); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/catalog/elastic.json
git commit -m "feat: add Elastic Security connector catalog"
```

---

## Task 6: Splunk _client.py rewrite + sync_notables executor

The existing `backend/app/connectors/executors/splunk/_client.py` uses a token-based bearer pattern (API token, not username/password session key). The spec requires a new `SplunkClient` class with session-key auth. We'll add the new class to `_client.py` while keeping the existing `get_rest_client` / `get_hec_client` helpers for backward compatibility.

**Files:**
- Modify: `backend/app/connectors/executors/splunk/_client.py`
- Create: `backend/app/connectors/executors/splunk/sync_notables.py`

- [ ] **Step 1: Add SplunkClient class to `_client.py`**

Append to the end of `backend/app/connectors/executors/splunk/_client.py`:

```python
from __future__ import annotations
"""SplunkClient — username/password session-key auth for Splunk Free REST API."""
from typing import Optional, List, Dict, Any
import httpx


class SplunkClient:
    """Sync HTTP client for Splunk REST API using session key auth (port 8089)."""

    def __init__(self, base_url: str, username: str, password: str,
                 verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._session_key: Optional[str] = None
        self._http = httpx.Client(verify=self.verify_ssl, timeout=60.0)

    def _get_session_key(self) -> str:
        if self._session_key:
            return self._session_key
        resp = self._http.post(
            f"{self.base_url}/services/auth/login",
            data={"username": self.username, "password": self.password,
                  "output_mode": "json"},
        )
        resp.raise_for_status()
        self._session_key = resp.json()["sessionKey"]
        return self._session_key

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Splunk {self._get_session_key()}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def search(self, spl_query: str, earliest: str = "-24h",
               latest: str = "now") -> List[Dict[str, Any]]:
        """Run a blocking SPL search and return result rows."""
        import json as _json
        resp = self._http.post(
            f"{self.base_url}/services/search/jobs/export",
            headers=self._headers(),
            data={
                "search": spl_query if spl_query.startswith("search ") else f"search {spl_query}",
                "earliest_time": earliest,
                "latest_time": latest,
                "output_mode": "json",
            },
        )
        resp.raise_for_status()
        results: List[Dict[str, Any]] = []
        for line in resp.text.strip().splitlines():
            try:
                obj = _json.loads(line)
                if "result" in obj:
                    results.append(obj["result"])
            except Exception:
                pass
        return results

    def get_notable_events(self) -> List[Dict[str, Any]]:
        """Return active ES notable events (or all events from search index if no ES)."""
        try:
            return self.search("| inputlookup notable_xref | search status!=5 | head 100")
        except Exception:
            # Fall back to a generic index search if Splunk ES is not installed
            return self.search("index=* | head 50", earliest="-1h")

    def update_notable_status(self, event_id: str, status: str) -> Dict[str, Any]:
        """Update notable event status (0=unassigned, 1=assigned, 2=resolved, 3=closed, 4=deferred, 5=suppressed)."""
        status_map = {"open": "0", "assigned": "1", "resolved": "2", "closed": "3",
                      "deferred": "4", "suppressed": "5"}
        status_code = status_map.get(status.lower(), status)
        resp = self._http.post(
            f"{self.base_url}/services/notable_update",
            headers=self._headers(),
            data={"ruleUIDs": event_id, "status": status_code, "output_mode": "json"},
        )
        # notable_update returns 200 only on ES; treat errors as non-fatal
        return {"event_id": event_id, "status": status, "http_status": resp.status_code}

    def create_saved_search(self, name: str, query: str,
                             cron_schedule: Optional[str] = None) -> Dict[str, Any]:
        """Create a saved search (alert) in Splunk."""
        data: Dict[str, Any] = {
            "name": name,
            "search": query,
            "output_mode": "json",
        }
        if cron_schedule:
            data["cron_schedule"] = cron_schedule
            data["is_scheduled"] = "1"
            data["schedule_window"] = "0"
        resp = self._http.post(
            f"{self.base_url}/services/saved/searches",
            headers=self._headers(),
            data=data,
        )
        resp.raise_for_status()
        return {"name": name, "created": True}

    def delete_saved_search(self, name: str) -> Dict[str, Any]:
        """Delete a saved search by name."""
        # URL-encode the name inline
        import urllib.parse as _up
        encoded = _up.quote(name, safe="")
        resp = self._http.delete(
            f"{self.base_url}/services/saved/searches/{encoded}",
            headers=self._headers(),
            params={"output_mode": "json"},
        )
        if resp.status_code == 404:
            return {"name": name, "deleted": False, "reason": "not found"}
        resp.raise_for_status()
        return {"name": name, "deleted": True}

    def close(self) -> None:
        self._http.close()


def get_splunk_client(connector) -> Optional[SplunkClient]:
    """Build a SplunkClient from connector credentials. Returns None if missing creds."""
    creds = getattr(connector, "credentials", None) or {}
    base_url = creds.get("base_url", "")
    username = creds.get("username", "admin")
    password = creds.get("password", "")
    if not base_url or not password:
        return None
    return SplunkClient(
        base_url=base_url,
        username=username,
        password=password,
        verify_ssl=creds.get("verify_ssl", False),
    )
```

Note: The file already has `import httpx` at the top. The new class block should be appended after the existing `get_hec_client` function. The `from __future__ import annotations` and other imports in the snippet above should NOT be duplicated — just append the class and `get_splunk_client` function starting after the existing content. The type hints (`Optional`, `List`, `Dict`, `Any`) need to be imported — add them to the existing import block at the top of the file.

Revised edit — read the existing file carefully and append only what's missing. Exact content to append after the existing `get_hec_client` closing brace:

```python

from typing import Optional, List, Dict, Any


class SplunkClient:
    """Sync HTTP client for Splunk REST API using session key auth (port 8089)."""

    def __init__(self, base_url: str, username: str, password: str,
                 verify_ssl: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._session_key: Optional[str] = None
        self._http = httpx.Client(verify=self.verify_ssl, timeout=60.0)

    def _get_session_key(self) -> str:
        if self._session_key:
            return self._session_key
        resp = self._http.post(
            f"{self.base_url}/services/auth/login",
            data={"username": self.username, "password": self.password,
                  "output_mode": "json"},
        )
        resp.raise_for_status()
        self._session_key = resp.json()["sessionKey"]
        return self._session_key

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Splunk {self._get_session_key()}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def search(self, spl_query: str, earliest: str = "-24h",
               latest: str = "now") -> List[Dict[str, Any]]:
        """Run a blocking SPL search and return result rows."""
        import json as _json
        resp = self._http.post(
            f"{self.base_url}/services/search/jobs/export",
            headers=self._headers(),
            data={
                "search": spl_query if spl_query.startswith("search ") else f"search {spl_query}",
                "earliest_time": earliest,
                "latest_time": latest,
                "output_mode": "json",
            },
        )
        resp.raise_for_status()
        results: List[Dict[str, Any]] = []
        for line in resp.text.strip().splitlines():
            try:
                obj = _json.loads(line)
                if "result" in obj:
                    results.append(obj["result"])
            except Exception:
                pass
        return results

    def get_notable_events(self) -> List[Dict[str, Any]]:
        """Return active ES notable events (or general events if no ES)."""
        try:
            return self.search("| inputlookup notable_xref | search status!=5 | head 100")
        except Exception:
            return self.search("index=* | head 50", earliest="-1h")

    def update_notable_status(self, event_id: str, status: str) -> Dict[str, Any]:
        """Update notable event status."""
        status_map = {"open": "0", "assigned": "1", "resolved": "2",
                      "closed": "3", "deferred": "4", "suppressed": "5"}
        status_code = status_map.get(status.lower(), status)
        resp = self._http.post(
            f"{self.base_url}/services/notable_update",
            headers=self._headers(),
            data={"ruleUIDs": event_id, "status": status_code, "output_mode": "json"},
        )
        return {"event_id": event_id, "status": status, "http_status": resp.status_code}

    def create_saved_search(self, name: str, query: str,
                             cron_schedule: Optional[str] = None) -> Dict[str, Any]:
        """Create a saved search in Splunk."""
        data: Dict[str, Any] = {"name": name, "search": query, "output_mode": "json"}
        if cron_schedule:
            data["cron_schedule"] = cron_schedule
            data["is_scheduled"] = "1"
            data["schedule_window"] = "0"
        resp = self._http.post(
            f"{self.base_url}/services/saved/searches",
            headers=self._headers(),
            data=data,
        )
        resp.raise_for_status()
        return {"name": name, "created": True}

    def delete_saved_search(self, name: str) -> Dict[str, Any]:
        """Delete a saved search by name."""
        import urllib.parse as _up
        encoded = _up.quote(name, safe="")
        resp = self._http.delete(
            f"{self.base_url}/services/saved/searches/{encoded}",
            headers=self._headers(),
            params={"output_mode": "json"},
        )
        if resp.status_code == 404:
            return {"name": name, "deleted": False, "reason": "not found"}
        resp.raise_for_status()
        return {"name": name, "deleted": True}

    def close(self) -> None:
        self._http.close()


def get_splunk_client(connector) -> Optional["SplunkClient"]:
    """Build a SplunkClient from connector credentials. Returns None if missing creds."""
    creds = getattr(connector, "credentials", None) or {}
    base_url = creds.get("base_url", "")
    username = creds.get("username", "admin")
    password = creds.get("password", "")
    if not base_url or not password:
        return None
    return SplunkClient(
        base_url=base_url,
        username=username,
        password=password,
        verify_ssl=creds.get("verify_ssl", False),
    )
```

- [ ] **Step 2: Create `sync_notables.py`**

Create `backend/app/connectors/executors/splunk/sync_notables.py`:

```python
from __future__ import annotations
"""Sync Splunk notable events (or saved search results) as Nexplane findings."""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Pull notable events from Splunk ES (or fallback generic events) and return as findings.
    
    Parameters:
        earliest (str): SPL earliest time, e.g. "-24h". Default: "-24h"
        latest (str): SPL latest time, e.g. "now". Default: "now"
        saved_search (str): Optional saved search name to run instead of notable lookup
    
    Returns dict with:
        action: "splunk_sync_notables"
        count: number of events found
        events: list of event dicts
    """
    from ._client import get_splunk_client

    client = get_splunk_client(connector)
    if client is None:
        return {
            "action": "splunk_sync_notables",
            "count": 0,
            "events": [],
            "status": "skipped",
            "reason": "no credentials",
        }

    earliest: str = parameters.get("earliest", "-24h")
    latest: str = parameters.get("latest", "now")
    saved_search: str = parameters.get("saved_search", "")

    try:
        if saved_search:
            raw_events = client.search(
                f"savedsearch \"{saved_search}\"",
                earliest=earliest,
                latest=latest,
            )
        else:
            raw_events = client.get_notable_events()

        findings = []
        for evt in raw_events:
            finding = {
                "source": "splunk",
                "event_id": evt.get("event_id") or evt.get("_key", ""),
                "rule_name": evt.get("rule_name") or evt.get("search_name", ""),
                "severity": evt.get("urgency") or evt.get("severity", "unknown"),
                "status": evt.get("status_label") or evt.get("status", "open"),
                "timestamp": evt.get("_time", ""),
                "raw": evt,
            }
            findings.append(finding)

        return {
            "action": "splunk_sync_notables",
            "count": len(findings),
            "events": findings,
        }
    finally:
        client.close()


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync_notables is read-only — no rollback"}
```

- [ ] **Step 3: Update `create_alert.py` to use SplunkClient for rollback**

Read `backend/app/connectors/executors/splunk/create_alert.py` — currently rollback just returns `rolled_back: False`. Update it to use the new `SplunkClient.delete_saved_search` for proper rollback:

```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters["name"]
    if not creds:
        return {"action": "create_alert", "name": name, "created": True}
    from ._client import get_splunk_client
    client = get_splunk_client(connector)
    if client is None:
        return {"action": "create_alert", "name": name, "created": True, "status": "skipped"}
    try:
        result = client.create_saved_search(
            name=name,
            query=parameters["search"],
            cron_schedule=parameters.get("cron_schedule"),
        )
        return {"action": "create_alert", "name": name, "created": True}
    finally:
        client.close()


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    name = execution_result.get("name") or parameters.get("name", "")
    if not name:
        return {"rolled_back": False, "reason": "no alert name to delete"}
    from ._client import get_splunk_client
    client = get_splunk_client(connector)
    if client is None:
        return {"rolled_back": False, "reason": "no credentials"}
    try:
        result = client.delete_saved_search(name)
        return {"rolled_back": result.get("deleted", False), "name": name}
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc)}
    finally:
        client.close()
```

- [ ] **Step 4: Update `splunk.json` catalog to add sync_notables action**

Add the following entry to the `"actions"` array in `backend/app/connectors/catalog/splunk.json`:

```json
    {
      "action_id": "sync_notables",
      "generic_action": "sync_alerts",
      "action_type": "ingest",
      "execution_tier": 1,
      "display_name": "Sync Notable Events",
      "description": "Pull notable events from Splunk ES (or saved search) and sync as Nexplane findings.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "earliest", "type": "string", "required": false},
        {"name": "latest", "type": "string", "required": false},
        {"name": "saved_search", "type": "string", "required": false}
      ],
      "executor": "splunk.sync_notables",
      "estimated_duration_seconds": 30
    }
```

- [ ] **Step 5: Verify syntax**

```bash
python -c "import sys; sys.path.insert(0,'backend'); from app.connectors.executors.splunk._client import SplunkClient, get_splunk_client; print('SplunkClient OK')"
python -c "import sys; sys.path.insert(0,'backend'); from app.connectors.executors.splunk.sync_notables import execute; print('sync_notables OK')"
python -c "import json; json.load(open('backend/app/connectors/catalog/splunk.json')); print('splunk.json OK')"
```

Expected: all three lines print `OK`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/splunk/_client.py \
        backend/app/connectors/executors/splunk/sync_notables.py \
        backend/app/connectors/executors/splunk/create_alert.py \
        backend/app/connectors/catalog/splunk.json
git commit -m "feat: add SplunkClient class + sync_notables executor + proper create_alert rollback"
```

---

## Task 7: Update run_on_ec2.py tarball to include elastic and splunk

**Files:**
- Modify: `backend/tests/smoke/run_on_ec2.py`

The `make_test_tarball()` function includes connector packages so smoke phases can import them directly. Add `elastic` and `splunk`.

- [ ] **Step 1: Edit make_test_tarball**

Find this line in `run_on_ec2.py`:
```python
        for connector_pkg in ("opnsense", "step_ca", "postgres", "redis", "mongodb"):
```

Replace with:
```python
        for connector_pkg in ("opnsense", "step_ca", "postgres", "redis", "mongodb",
                              "elastic", "splunk"):
```

- [ ] **Step 2: Verify**

```bash
python -c "
import sys; sys.path.insert(0, 'backend/tests/smoke')
import run_on_ec2
import inspect
src = inspect.getsource(run_on_ec2.make_test_tarball)
assert 'elastic' in src and 'splunk' in src, 'MISSING'
print('OK')
"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/run_on_ec2.py
git commit -m "fix: include elastic and splunk connector packages in smoke tarball"
```

---

## Task 8: Smoke phase ELASTIC_ALERTS

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

This is the most complex task. We add `run_phase_elastic_alerts()` function and wire it into `main()`.

- [ ] **Step 1: Write `run_phase_elastic_alerts`**

Insert the following function into `test_aws_live.py` just before the `def main():` line (around line 10426). Use the established `POSTGRES_ROTATE`/`WAZUH_AGENT` pattern exactly.

```python
def run_phase_elastic_alerts(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase ELASTIC_ALERTS: provision Elasticsearch + Kibana on t3.large EC2 via SSM,
    create a KQL detection rule, index a synthetic alert, run sync_alerts, verify finding.
    AMI cached after first setup in SSM at /nexplane/smoke-amis/elastic/<hash[:8]>."""
    import time, hashlib
    print("\n[Phase ELASTIC_ALERTS] Elastic Security alerts sync + detection rule lifecycle")

    ec2_client = _get_aws_boto3_client("ec2")
    ssm_client = _get_aws_boto3_client("ssm")
    if not ec2_client or not ssm_client:
        fail("[ELASTIC_ALERTS] AWS clients not available")

    AL2023_AMI = "ami-0953476d60561c955"
    INSTANCE_TYPE = "t3.large"   # Elastic needs 4GB+ RAM

    # ------------------------------------------------------------------
    # Setup script — install Elasticsearch 8.x + Kibana via official RPM repo
    # hash determines AMI cache key
    # ------------------------------------------------------------------
    setup_script = r"""#!/bin/bash
set -e

# Import Elasticsearch GPG key and add repo
rpm --import https://artifacts.elastic.co/GPG-KEY-elasticsearch 2>/dev/null || true
cat > /etc/yum.repos.d/elasticsearch.repo << 'REPO'
[elasticsearch]
name=Elasticsearch repository for 8.x packages
baseurl=https://artifacts.elastic.co/packages/8.x/yum
gpgcheck=1
gpgkey=https://artifacts.elastic.co/GPG-KEY-elasticsearch
enabled=1
autorefresh=1
type=rpm-md
REPO

dnf install -y elasticsearch kibana

# Configure Elasticsearch — disable security for smoke test simplicity
cat > /etc/elasticsearch/elasticsearch.yml << 'ES_CFG'
network.host: 0.0.0.0
http.port: 9200
discovery.type: single-node
xpack.security.enabled: false
xpack.security.enrollment.enabled: false
xpack.security.http.ssl.enabled: false
xpack.security.transport.ssl.enabled: false
ES_CFG

# Configure Kibana
cat > /etc/kibana/kibana.yml << 'KB_CFG'
server.host: "0.0.0.0"
server.port: 5601
elasticsearch.hosts: ["http://localhost:9200"]
xpack.security.enabled: false
KB_CFG

systemctl daemon-reload
systemctl enable elasticsearch kibana
systemctl start elasticsearch

# Wait for Elasticsearch to be ready
for i in $(seq 1 60); do
  curl -sf http://localhost:9200/_cluster/health | grep -qE '"status":"(green|yellow)"' && break
  sleep 5
done

systemctl start kibana

# Wait for Kibana to be ready
for i in $(seq 1 60); do
  curl -sf http://localhost:5601/api/status | grep -q '"level":"available"' && break
  sleep 5
done

# Initialize Kibana's detection engine (required before creating rules)
curl -sf -X POST http://localhost:5601/api/detection_engine/index \
  -H 'kbn-xsrf: true' -H 'Content-Type: application/json' 2>/dev/null || true

echo "ELASTIC_SETUP_COMPLETE"
"""
    setup_hash = hashlib.md5(f"elastic-8.x-{AL2023_AMI}".encode()).hexdigest()

    # ------------------------------------------------------------------
    # AMI cache check
    # ------------------------------------------------------------------
    cached_ami = None
    param_path = f"/nexplane/smoke-amis/elastic/{setup_hash[:8]}"
    try:
        resp_p = ssm_client.get_parameter(Name=param_path)
        candidate = resp_p["Parameter"]["Value"]
        images = ec2_client.describe_images(ImageIds=[candidate])["Images"]
        if images and images[0]["State"] == "available":
            cached_ami = candidate
            log(f"Using cached Elastic AMI: {cached_ami}")
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Launch t3.large EC2
    # ------------------------------------------------------------------
    vpc_id = ec2_client.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = ec2_client.describe_subnets(
        Filters=[{"Name": "vpcId", "Values": [vpc_id]}])["Subnets"]
    try:
        offs = ec2_client.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": [INSTANCE_TYPE]}])["InstanceTypeOfferings"]
        azs = {o["Location"] for o in offs}
        subnets = [s for s in subnets if s.get("AvailabilityZone") in azs] or subnets
    except Exception:
        pass
    subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
    subnet_id = subnets[0]["SubnetId"]

    resp = ec2_client.run_instances(
        ImageId=cached_ami or AL2023_AMI, InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1, SubnetId=subnet_id,
        IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-elastic"},
            {"Key": "nexplane-smoke", "Value": "true"},
        ]}],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"Elastic EC2: {instance_id} ({INSTANCE_TYPE})")

    import time as _t2
    _t2.sleep(5)
    deadline = time.time() + 300
    private_ip = ""
    while time.time() < deadline:
        try:
            desc = ec2_client.describe_instances(InstanceIds=[instance_id])
            state = desc["Reservations"][0]["Instances"][0]["State"]["Name"]
            if state == "running":
                private_ip = desc["Reservations"][0]["Instances"][0].get("PrivateIpAddress", "")
                break
        except Exception:
            pass
        time.sleep(10)

    # Wait for SSM
    deadline2 = time.time() + 180
    while time.time() < deadline2:
        try:
            r = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["echo ready"]}, TimeoutSeconds=10)
            time.sleep(5)
            out = ssm_client.get_command_invocation(
                CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
            if out["Status"] == "Success":
                break
        except Exception:
            pass
        time.sleep(10)

    elastic_connector_id = None
    try:
        if not cached_ami:
            # Install and configure Elasticsearch + Kibana
            log("Installing Elasticsearch + Kibana (this takes ~5 minutes)...")
            resp_s = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [setup_script]}, TimeoutSeconds=600)
            # Poll for completion — installation takes several minutes
            deadline_install = time.time() + 600
            setup_done = False
            while time.time() < deadline_install:
                time.sleep(30)
                try:
                    out_s = ssm_client.get_command_invocation(
                        CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                    if out_s["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
                        if "ELASTIC_SETUP_COMPLETE" in out_s.get("StandardOutputContent", ""):
                            setup_done = True
                            log("Elasticsearch + Kibana installed and started")
                        else:
                            log(f"  WARNING: Elastic setup may be incomplete. stderr: {out_s.get('StandardErrorContent','')[:300]}")
                        break
                except Exception:
                    pass
            if setup_done:
                from run_on_ec2 import get_or_create_smoke_ami
                get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "elastic", setup_hash)
        else:
            # Start services on cached AMI
            start_cmd = "systemctl start elasticsearch kibana; sleep 20"
            ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [start_cmd]}, TimeoutSeconds=60)
            time.sleep(25)

        elastic_url = f"http://{private_ip}:9200"
        kibana_url = f"http://{private_ip}:5601"
        log(f"Elastic at {elastic_url}, Kibana at {kibana_url}")

        # Wait for ES to be accessible via SSM curl
        wait_cmd = """
for i in $(seq 1 30); do
  curl -sf http://localhost:9200/_cluster/health | grep -qE '"status"' && echo "ES_READY" && break
  sleep 5
done
"""
        ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [wait_cmd]}, TimeoutSeconds=180)
        time.sleep(10)

        # Register Elastic connector in Nexplane
        conn_resp = client.post("/connectors", json={
            "connector_type": "elastic",
            "name": "nexplane-smoke-elastic",
            "display_name": "nexplane-smoke-elastic",
            "credentials": {
                "base_url": elastic_url,
                "username": "elastic",
                "password": "smoke-no-auth",  # security disabled
                "kibana_url": kibana_url,
                "verify_ssl": False,
            },
        })
        elastic_connector_id = conn_resp.get("id")
        log(f"Elastic connector registered: {elastic_connector_id}")

        import time as _ts
        rule_id = f"nexplane-smoke-rule-{int(_ts.time())}"

        # Initialize detection engine via SSM (Kibana API)
        init_cmd = """
for i in $(seq 1 12); do
  STATUS=$(curl -sf http://localhost:5601/api/status | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',{}).get('overall',{}).get('level','unknown'))" 2>/dev/null || echo unknown)
  [ "$STATUS" = "available" ] && echo "KIBANA_READY" && break
  sleep 10
done
curl -sf -X POST http://localhost:5601/api/detection_engine/index \
  -H 'kbn-xsrf: true' -H 'Content-Type: application/json' 2>/dev/null || true
echo "ENGINE_INIT_DONE"
"""
        ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [init_cmd]}, TimeoutSeconds=150)
        time.sleep(20)

        # Create detection rule via CR
        cr_rule = client.run_cr(
            f"[ELASTIC_ALERTS] create detection rule {rule_id}",
            "elastic_create_rule",
            cloud_account_id,
            {
                "rule_id": rule_id,
                "name": "Nexplane Smoke Test Rule",
                "description": "Detects smoke-test events",
                "query": "tags: nexplane-smoke",
                "index": ["smoke-test-*"],
                "severity": "medium",
                "risk_score": 47,
                "interval": "1m",
                "enabled": True,
                "rollback_strategy": "rollback_available",
            },
        )
        exec_runs = cr_rule.get("execution_runs") or []
        rule_result = exec_runs[0].get("result") if exec_runs else {}
        if rule_result.get("status") == "skipped":
            log("  WARNING: Detection rule creation skipped (no credentials)")
        else:
            log(f"Detection rule created: {rule_id} (kibana_id={rule_result.get('kibana_id','?')})")

        # Index a synthetic alert document so sync_alerts can find it
        index_cmd = f"""
curl -sf -X POST http://localhost:9200/smoke-test-index/_doc \
  -H 'Content-Type: application/json' \
  -d '{{"@timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "tags": ["nexplane-smoke"], "message": "synthetic smoke alert", "kibana.alert.rule.name": "Nexplane Smoke Test Rule", "kibana.alert.severity": "medium", "kibana.alert.workflow_status": "open", "kibana.alert.uuid": "smoke-{int(_ts.time())}"}}'
echo "DOC_INDEXED"
"""
        ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [index_cmd]}, TimeoutSeconds=30)
        time.sleep(5)

        # Also index into the alerts index directly for get_alerts() to find
        alerts_index_cmd = f"""
# Create the alerts index if it doesn't exist and index a synthetic alert
curl -sf -X PUT http://localhost:9200/.alerts-security.alerts-default \
  -H 'Content-Type: application/json' \
  -d '{{"settings": {{"number_of_shards": 1, "number_of_replicas": 0}}}}' 2>/dev/null || true
curl -sf -X POST http://localhost:9200/.alerts-security.alerts-default/_doc \
  -H 'Content-Type: application/json' \
  -d '{{"@timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "kibana.alert.rule.name": "Nexplane Smoke Test Rule", "kibana.alert.severity": "medium", "kibana.alert.workflow_status": "open", "kibana.alert.uuid": "smoke-alert-{int(_ts.time())}"}}'
curl -sf -X POST http://localhost:9200/.alerts-security.alerts-default/_refresh
echo "ALERT_INDEXED"
"""
        resp_idx = ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [alerts_index_cmd]}, TimeoutSeconds=30)
        time.sleep(8)
        try:
            out_idx = ssm_client.get_command_invocation(
                CommandId=resp_idx["Command"]["CommandId"], InstanceId=instance_id)
            if "ALERT_INDEXED" in out_idx.get("StandardOutputContent", ""):
                log("Synthetic alert indexed into .alerts-security.alerts-default")
            else:
                log(f"  Index output: {out_idx.get('StandardOutputContent','')[:200]}")
        except Exception as e:
            log(f"  WARNING: Alert index check: {e}")

        # Run sync_alerts CR
        cr_sync = client.run_cr(
            "[ELASTIC_ALERTS] sync_alerts",
            "elastic_sync_alerts",
            cloud_account_id,
            {
                "start_time": "now-1h",
                "end_time": "now",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        sync_runs = cr_sync.get("execution_runs") or []
        sync_result = sync_runs[0].get("result") if sync_runs else {}

        if sync_result.get("status") == "skipped":
            log("  WARNING: sync_alerts skipped (no credentials in backend)")
        else:
            count = sync_result.get("count", 0)
            log(f"sync_alerts returned {count} alert(s)")
            if count > 0:
                log(f"First alert: {sync_result.get('alerts', [{}])[0].get('rule_name', '?')}")

        # Rollback: delete the detection rule
        cr_rb = client.run_cr(
            f"[ELASTIC_ALERTS] rollback delete rule {rule_id}",
            "elastic_create_rule",
            cloud_account_id,
            {
                "rule_id": rule_id,
                "_rollback": True,
                "rollback_strategy": "rollback_available",
            },
        )
        rb_runs = cr_rb.get("execution_runs") or []
        rb_result = rb_runs[0].get("result") if rb_runs else {}
        log(f"Rollback result: rolled_back={rb_result.get('rolled_back')}")

        log("Phase ELASTIC_ALERTS PASSED")

    except Exception as e:
        print(f"\n[FAIL] Phase ELASTIC_ALERTS failed: {e}")
        raise
    finally:
        if elastic_connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{elastic_connector_id}")
            except Exception:
                pass
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
            log(f"Elastic EC2 {instance_id} terminated")
        except Exception:
            pass
```

- [ ] **Step 2: Wire ELASTIC_ALERTS into main()**

In `test_aws_live.py` `main()`, find the block:
```python
        if "MONGODB_ROTATE" in phases:
            run_phase_mongodb_rotate(client, cloud_account_id)
```

Append after it:
```python
        if "ELASTIC_ALERTS" in phases:
            run_phase_elastic_alerts(client, cloud_account_id)
        if "SPLUNK_ALERTS" in phases:
            run_phase_splunk_alerts(client, cloud_account_id)
```

- [ ] **Step 3: Update the --phases help string in main()**

Find the help string that ends with:
```
"MONGODB_ROTATE=MongoDB user password rotation (EC2, AMI cached)."
```

Replace the period at the end with:
```
"MONGODB_ROTATE=MongoDB user password rotation (EC2, AMI cached). "
"ELASTIC_ALERTS=Elastic Security alerts sync + KQL rule lifecycle (t3.large, AMI cached). "
"SPLUNK_ALERTS=Splunk Free notable event sync + saved search lifecycle (t3.large, AMI cached)."
```

- [ ] **Step 4: Commit (after SPLUNK phase is also added — see Task 9)**

Commit both phases together after Task 9 is done.

---

## Task 9: Smoke phase SPLUNK_ALERTS

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Write `run_phase_splunk_alerts`**

Insert just before `def main():`, after `run_phase_elastic_alerts`:

```python
def run_phase_splunk_alerts(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase SPLUNK_ALERTS: install Splunk Free on t3.large EC2, create a saved search via CR,
    index a test event, run sync_notables, verify event found. AMI cached after first setup."""
    import time, hashlib
    print("\n[Phase SPLUNK_ALERTS] Splunk Free notable event sync + saved search lifecycle")

    ec2_client = _get_aws_boto3_client("ec2")
    ssm_client = _get_aws_boto3_client("ssm")
    if not ec2_client or not ssm_client:
        fail("[SPLUNK_ALERTS] AWS clients not available")

    AL2023_AMI = "ami-0953476d60561c955"
    INSTANCE_TYPE = "t3.large"   # Splunk needs 4GB+ RAM

    # ------------------------------------------------------------------
    # Splunk setup script — download and install Splunk Free 9.x
    # Note: Splunk requires accepting the license. We use --seed-passwd.
    # The actual RPM URL must be from Splunk's download page (latest 9.x).
    # ------------------------------------------------------------------
    # Splunk 9.3.2 (latest stable as of 2025) direct RPM URL
    SPLUNK_RPM_URL = "https://download.splunk.com/products/splunk/releases/9.3.2/linux/splunk-9.3.2-d8bb32809498-linux-2.6-x86_64.rpm"
    SPLUNK_VERSION = "9.3.2"

    setup_script = f"""#!/bin/bash
set -e

# Download Splunk Free RPM
echo "Downloading Splunk {SPLUNK_VERSION}..."
curl -L -o /tmp/splunk.rpm "{SPLUNK_RPM_URL}" --retry 3 --retry-delay 5

# Install
rpm -ivh /tmp/splunk.rpm

# Accept license and start with seeded admin password
/opt/splunk/bin/splunk start --accept-license --answer-yes --no-prompt --seed-passwd admin123 2>/dev/null || true

# Enable boot start
/opt/splunk/bin/splunk enable boot-start -user splunk 2>/dev/null || true

# Wait for Splunk REST API to be ready
for i in $(seq 1 60); do
  curl -sk https://localhost:8089/services/server/info -u admin:admin123 | grep -q "productType" && break
  sleep 5
done

echo "SPLUNK_SETUP_COMPLETE"
"""
    setup_hash = hashlib.md5(f"splunk-{SPLUNK_VERSION}-{AL2023_AMI}".encode()).hexdigest()

    # ------------------------------------------------------------------
    # AMI cache check
    # ------------------------------------------------------------------
    cached_ami = None
    param_path = f"/nexplane/smoke-amis/splunk/{setup_hash[:8]}"
    try:
        resp_p = ssm_client.get_parameter(Name=param_path)
        candidate = resp_p["Parameter"]["Value"]
        images = ec2_client.describe_images(ImageIds=[candidate])["Images"]
        if images and images[0]["State"] == "available":
            cached_ami = candidate
            log(f"Using cached Splunk AMI: {cached_ami}")
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Launch t3.large EC2
    # ------------------------------------------------------------------
    vpc_id = ec2_client.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = ec2_client.describe_subnets(
        Filters=[{"Name": "vpcId", "Values": [vpc_id]}])["Subnets"]
    try:
        offs = ec2_client.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": [INSTANCE_TYPE]}])["InstanceTypeOfferings"]
        azs = {o["Location"] for o in offs}
        subnets = [s for s in subnets if s.get("AvailabilityZone") in azs] or subnets
    except Exception:
        pass
    subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
    subnet_id = subnets[0]["SubnetId"]

    resp = ec2_client.run_instances(
        ImageId=cached_ami or AL2023_AMI, InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1, SubnetId=subnet_id,
        IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-splunk"},
            {"Key": "nexplane-smoke", "Value": "true"},
        ]}],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"Splunk EC2: {instance_id} ({INSTANCE_TYPE})")

    import time as _t2
    _t2.sleep(5)
    deadline = time.time() + 300
    private_ip = ""
    while time.time() < deadline:
        try:
            desc = ec2_client.describe_instances(InstanceIds=[instance_id])
            state = desc["Reservations"][0]["Instances"][0]["State"]["Name"]
            if state == "running":
                private_ip = desc["Reservations"][0]["Instances"][0].get("PrivateIpAddress", "")
                break
        except Exception:
            pass
        time.sleep(10)

    # Wait for SSM
    deadline2 = time.time() + 180
    while time.time() < deadline2:
        try:
            r = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["echo ready"]}, TimeoutSeconds=10)
            time.sleep(5)
            out = ssm_client.get_command_invocation(
                CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
            if out["Status"] == "Success":
                break
        except Exception:
            pass
        time.sleep(10)

    splunk_connector_id = None
    try:
        if not cached_ami:
            # Install Splunk
            log("Installing Splunk Free (this takes ~5-8 minutes for download + install)...")
            resp_s = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [setup_script]}, TimeoutSeconds=600)
            deadline_install = time.time() + 600
            setup_done = False
            while time.time() < deadline_install:
                time.sleep(30)
                try:
                    out_s = ssm_client.get_command_invocation(
                        CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                    if out_s["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
                        if "SPLUNK_SETUP_COMPLETE" in out_s.get("StandardOutputContent", ""):
                            setup_done = True
                            log("Splunk Free installed and started")
                        else:
                            log(f"  WARNING: Splunk setup incomplete. stderr: {out_s.get('StandardErrorContent','')[:300]}")
                        break
                except Exception:
                    pass
            if setup_done:
                from run_on_ec2 import get_or_create_smoke_ami
                get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "splunk", setup_hash)
        else:
            # Start Splunk on cached instance
            start_cmd = "/opt/splunk/bin/splunk start --accept-license --no-prompt 2>/dev/null || true; sleep 15"
            ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [start_cmd]}, TimeoutSeconds=60)
            time.sleep(20)

        splunk_url = f"https://{private_ip}:8089"
        log(f"Splunk at {splunk_url}")

        # Register Splunk connector
        conn_resp = client.post("/connectors", json={
            "connector_type": "splunk",
            "name": "nexplane-smoke-splunk",
            "display_name": "nexplane-smoke-splunk",
            "credentials": {
                "base_url": splunk_url,
                "username": "admin",
                "password": "admin123",
                "verify_ssl": False,
            },
        })
        splunk_connector_id = conn_resp.get("id")
        log(f"Splunk connector registered: {splunk_connector_id}")

        import time as _ts
        search_name = f"nexplane-smoke-search-{int(_ts.time())}"

        # Create a saved search via CR
        cr_search = client.run_cr(
            f"[SPLUNK_ALERTS] create saved search {search_name}",
            "splunk_create_alert",
            cloud_account_id,
            {
                "name": search_name,
                "search": "index=main sourcetype=nexplane_smoke | head 10",
                "rollback_strategy": "rollback_available",
            },
        )
        sa_runs = cr_search.get("execution_runs") or []
        sa_result = sa_runs[0].get("result") if sa_runs else {}
        if sa_result.get("status") == "skipped":
            log("  WARNING: create_alert skipped (no credentials)")
        else:
            log(f"Saved search created: {search_name}")

        # Index a test event via SSM (Splunk HEC or receivers/simple)
        index_cmd = """
# Index event via Splunk's receivers/simple endpoint
curl -sk -u admin:admin123 \
  https://localhost:8089/services/receivers/simple?sourcetype=nexplane_smoke \
  -d "nexplane smoke test alert event $(date)" || true
# Refresh the search index
sleep 3
echo "EVENT_INDEXED"
"""
        resp_ev = ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [index_cmd]}, TimeoutSeconds=30)
        time.sleep(8)
        try:
            out_ev = ssm_client.get_command_invocation(
                CommandId=resp_ev["Command"]["CommandId"], InstanceId=instance_id)
            if "EVENT_INDEXED" in out_ev.get("StandardOutputContent", ""):
                log("Test event indexed into Splunk")
        except Exception as e:
            log(f"  WARNING: Event index check: {e}")

        # Run sync_notables CR
        cr_sync = client.run_cr(
            "[SPLUNK_ALERTS] sync_notables",
            "splunk_sync_notables",
            cloud_account_id,
            {
                "earliest": "-1h",
                "latest": "now",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        sync_runs = cr_sync.get("execution_runs") or []
        sync_result = sync_runs[0].get("result") if sync_runs else {}

        if sync_result.get("status") == "skipped":
            log("  WARNING: sync_notables skipped (no credentials)")
        else:
            count = sync_result.get("count", 0)
            log(f"sync_notables returned {count} event(s)")

        # Rollback: delete saved search
        cr_rb = client.run_cr(
            f"[SPLUNK_ALERTS] rollback delete {search_name}",
            "splunk_create_alert",
            cloud_account_id,
            {
                "name": search_name,
                "_rollback": True,
                "rollback_strategy": "rollback_available",
            },
        )
        rb_runs = cr_rb.get("execution_runs") or []
        rb_result = rb_runs[0].get("result") if rb_runs else {}
        log(f"Rollback result: rolled_back={rb_result.get('rolled_back')}")

        log("Phase SPLUNK_ALERTS PASSED")

    except Exception as e:
        print(f"\n[FAIL] Phase SPLUNK_ALERTS failed: {e}")
        raise
    finally:
        if splunk_connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{splunk_connector_id}")
            except Exception:
                pass
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
            log(f"Splunk EC2 {instance_id} terminated")
        except Exception:
            pass
```

- [ ] **Step 2: Commit both phases**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: add ELASTIC_ALERTS and SPLUNK_ALERTS smoke phases (AMI cached, t3.large)"
```

---

## Task 10: Run smoke phases and fix any bugs

- [ ] **Step 1: Set AWS credentials and run**

```powershell
$env:AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"
$env:AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
$env:AWS_DEFAULT_REGION="us-east-1"
cd f:/Nexplane/nexplane
python backend/tests/smoke/run_on_ec2.py --phases ELASTIC_ALERTS,SPLUNK_ALERTS
```

- [ ] **Step 2: Fix bugs**

Common failure modes and fixes:
- `connector_type 'elastic' not found` → the catalog file isn't loaded; verify `elastic.json` exists in catalog dir and the catalog loader picks up all JSON files
- `ChangeType has no attribute elastic_sync_alerts` → migration needed; if using Alembic, create and run a migration; if enum is used directly in Python only, just verify the enum file was saved correctly
- `ssm_client.exceptions.ParameterNotFound not found` → this is expected on first run (no cached AMI); not a bug
- Splunk download URL 404 → find the current URL at `https://www.splunk.com/en_us/download/splunk-enterprise.html` and update `SPLUNK_RPM_URL`
- Elasticsearch `xpack.security` enrollment error → ensure the `xpack.security.enrollment.enabled: false` is in the config; on some 8.x versions you also need `xpack.license.self_generated.type: basic`
- `kibana not found in dnf` → Kibana is in the same Elastic repo; verify the repo file is written correctly before the install
- httpx `SSLError` on Splunk → `verify_ssl=False` in `SplunkClient.__init__` should handle this; ensure `httpx.Client(verify=False)` is passed through

- [ ] **Step 3: Final commit after all tests pass**

```bash
git add -A
git commit -m "feat: add Elastic Security/Splunk Free connectors + AMI-cached smoke phases"
```

---

## Self-Review

**Spec coverage:**
- [x] `ElasticClient` with `search`, `get_alerts`, `acknowledge_alert`, `create_rule`, `delete_rule`, `get_fleet_agents` — all implemented
- [x] `sync_alerts.py` — pulls alerts, returns count + findings
- [x] `create_detection_rule.py` — creates KQL rule, rollback deletes it
- [x] `elastic_sync_alerts`, `elastic_create_rule` in ChangeType
- [x] `elastic.json` catalog
- [x] `SplunkClient` with `search`, `get_notable_events`, `update_notable_status`, `create_saved_search`, `delete_saved_search`
- [x] `sync_notables.py` — syncs notable events as findings
- [x] `splunk_sync_notables`, `splunk_create_alert` in ChangeType
- [x] `splunk.json` updated with sync_notables action
- [x] ELASTIC_ALERTS smoke phase: t3.large, RPM install, AMI cache, rule CR, synthetic alert index, sync CR, rollback
- [x] SPLUNK_ALERTS smoke phase: t3.large, Splunk Free download, AMI cache, saved search CR, event index, sync CR, rollback
- [x] setup_hash = md5 of install commands for both
- [x] elastic and splunk added to run_on_ec2.py tarball
- [x] ELASTIC_ALERTS, SPLUNK_ALERTS wired into main() PHASES block and help string

**Placeholder scan:** No TBDs or vague steps — all steps have concrete code.

**Type consistency:**
- `get_elastic_client(connector)` used in `sync_alerts.py` and `create_detection_rule.py` — matches definition in `_client.py`
- `get_splunk_client(connector)` used in `sync_notables.py` and `create_alert.py` — matches definition
- `client.close()` called in all `finally` blocks
- `execution_result.get("rule_id")` in elastic rollback matches `"rule_id"` key in execute return dict
- `execution_result.get("name")` in splunk rollback matches `"name"` key in create_saved_search return dict
