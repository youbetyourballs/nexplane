# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

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
        must_clauses: List[Dict] = [
            {"range": {"@timestamp": {"gte": start_time, "lte": end_time}}}
        ]
        if min_severity and min_severity.lower() in {"low", "medium", "high", "critical"}:
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
