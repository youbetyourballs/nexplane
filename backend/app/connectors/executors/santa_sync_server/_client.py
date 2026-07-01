# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx
from typing import Optional


_MOROZ_RULE_TYPE_MAP = {1: "allowlist", 2: "denylist", 3: "silent_blocklist"}
_ZENTRAL_POLICY_MAP = {"ALLOWLIST": "allowlist", "BLOCKLIST": "denylist", "SILENT_BLOCKLIST": "silent_blocklist"}
_TO_MOROZ_POLICY = {"allowlist": 1, "denylist": 2, "silent_blocklist": 3}
_TO_ZENTRAL_POLICY = {"allowlist": "ALLOWLIST", "denylist": "BLOCKLIST", "silent_blocklist": "SILENT_BLOCKLIST"}
_ZENTRAL_TARGET_TYPE_MAP = {"binary": "BINARY", "certificate": "CERTIFICATE", "teamid": "TEAM_ID", "signingid": "SIGNING_ID"}
_ZENTRAL_FIELD_NAME = {"binary": "sha256", "certificate": "sha256", "teamid": "team_id", "signingid": "signing_id"}


class SantaSyncClient:
    def __init__(self, creds: dict):
        self._creds = creds
        url = creds["sync_server_url"].rstrip("/")
        token = creds["auth_token"]
        verify = creds.get("tls_verify", True)
        self._is_zentral = "zentral" in url.lower() or creds.get("server_flavor") == "zentral"
        self._http = httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": f"Token {token}"},
            verify=verify,
            timeout=30.0,
        )

    async def aclose(self):
        await self._http.aclose()

    async def get_rules(self, machine_group: Optional[str]) -> list[dict]:
        if self._is_zentral:
            params = {}
            if machine_group:
                params["configuration"] = machine_group
            resp = await self._http.get("/api/santa/rules/", params=params)
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("results", data) if isinstance(data, dict) else data
            return [self._normalize_zentral_rule(r) for r in raw]
        else:
            resp = await self._http.get("/rules")
            resp.raise_for_status()
            raw = resp.json()
            return [self._normalize_moroz_rule(r) for r in (raw if isinstance(raw, list) else raw.get("rules", []))]

    async def push_rules(self, machine_group: Optional[str], rules: list[dict], mode: str = "merge") -> dict:
        if self._is_zentral:
            pushed = 0
            for rule in rules:
                field_name = _ZENTRAL_FIELD_NAME.get(rule["identifier_type"], "sha256")
                payload = {
                    "target": {
                        "type": _ZENTRAL_TARGET_TYPE_MAP.get(rule["identifier_type"], "BINARY"),
                        field_name: rule["identifier"],
                    },
                    "policy": _TO_ZENTRAL_POLICY.get(rule["rule_type"], "BLOCKLIST"),
                }
                if rule.get("custom_message"):
                    payload["custom_message"] = rule["custom_message"]
                resp = await self._http.post("/api/santa/rules/", json=payload)
                resp.raise_for_status()
                pushed += 1
            return {"pushed": pushed, "machine_group": machine_group, "mode": mode}
        else:
            payload = {"rules": [
                {
                    "sha256": rule["identifier"],
                    "policy": _TO_MOROZ_POLICY.get(rule["rule_type"], 2),
                    "rule_type": 1 if rule["identifier_type"] == "binary" else 2,
                    **({"custom_message": rule["custom_message"]} if rule.get("custom_message") else {}),
                }
                for rule in rules
            ]}
            if mode == "replace":
                payload["clean_sync"] = True
            resp = await self._http.post("/rules", json=payload)
            resp.raise_for_status()
            return {"pushed": len(rules), "machine_group": machine_group, "mode": mode}

    async def list_machines(self, machine_group: Optional[str]) -> list[dict]:
        if self._is_zentral:
            params = {}
            if machine_group:
                params["configuration"] = machine_group
            resp = await self._http.get("/api/santa/enrolled-machines/", params=params)
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("results", data) if isinstance(data, dict) else data
            return [self._normalize_zentral_machine(m) for m in raw]
        else:
            resp = await self._http.get("/machines")
            resp.raise_for_status()
            raw = resp.json()
            machines = raw if isinstance(raw, list) else raw.get("machines", [])
            return [self._normalize_moroz_machine(m) for m in machines]

    async def assign_machine_group(self, machine_id: str, target_group: str) -> dict:
        if self._is_zentral:
            resp = await self._http.patch(f"/api/santa/enrolled-machines/{machine_id}/", json={"configuration": target_group})
            resp.raise_for_status()
            return resp.json()
        else:
            resp = await self._http.put(f"/machines/{machine_id}", json={"machine_group": target_group})
            resp.raise_for_status()
            return resp.json()

    def _normalize_moroz_rule(self, r: dict) -> dict:
        return {
            "identifier": r.get("sha256", ""),
            "rule_type": _MOROZ_RULE_TYPE_MAP.get(r.get("policy", 2), "denylist"),
            "identifier_type": "binary" if r.get("rule_type", 1) == 1 else "certificate",
            "custom_message": r.get("custom_message", ""),
        }

    def _normalize_zentral_rule(self, r: dict) -> dict:
        target = r.get("target", {})
        id_type_raw = target.get("type", "BINARY").lower()
        id_type = {"binary": "binary", "certificate": "certificate", "team_id": "teamid", "signing_id": "signingid"}.get(id_type_raw, "binary")
        identifier = target.get("sha256") or target.get("signing_id") or target.get("team_id") or ""
        return {
            "identifier": identifier,
            "rule_type": _ZENTRAL_POLICY_MAP.get(r.get("policy", "BLOCKLIST"), "denylist"),
            "identifier_type": id_type,
            "custom_message": r.get("custom_message", ""),
        }

    def _normalize_moroz_machine(self, m: dict) -> dict:
        return {
            "machine_id": m.get("machine_id", m.get("hardware_uuid", "")),
            "hostname": m.get("hostname", ""),
            "os_version": m.get("os_version", ""),
            "santa_version": m.get("santa_version", ""),
            "last_sync": m.get("last_preflight_at", ""),
            "rule_count": m.get("rule_count", 0),
            "machine_group": m.get("machine_group", ""),
        }

    def _normalize_zentral_machine(self, m: dict) -> dict:
        return {
            "machine_id": m.get("hardware_uuid", m.get("serial_number", "")),
            "hostname": m.get("serial_number", ""),
            "os_version": m.get("os_version", ""),
            "santa_version": m.get("client_version", ""),
            "last_sync": m.get("last_seen", ""),
            "rule_count": m.get("rule_count", 0),
            "machine_group": m.get("configuration", ""),
        }
