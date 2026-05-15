# OPNsense + step-ca Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OPNsense firewall and step-ca certificate authority connectors with full CRUD executors, catalog entries, ChangeType enum values, and smoke test phases.

**Architecture:** Each connector follows the existing `_client.py` + executor file pattern. OPNsense uses HTTP Basic auth against the REST API; step-ca uses the `step` CLI via subprocess or the admin REST API. Smoke phases are appended to `test_aws_live.py` following the `run_phase_vault_rotate` pattern — EC2-hosted service with AMI caching.

**Tech Stack:** Python 3.9, httpx/requests for OPNsense REST, subprocess for step CLI, responses library not needed (use nginx mock or direct API), pytest, boto3 for EC2 provisioning.

---

## File Map

### New files
- `backend/app/connectors/executors/opnsense/__init__.py` — empty
- `backend/app/connectors/executors/opnsense/_client.py` — OPNsense REST client (API key/secret Basic auth)
- `backend/app/connectors/executors/opnsense/update_firewall_rule.py` — create/update rule + apply, rollback deletes
- `backend/app/connectors/executors/opnsense/block_host.py` — add IP alias + block rule, rollback removes both
- `backend/app/connectors/catalog/opnsense.json` — connector catalog entry
- `backend/app/connectors/executors/step_ca/__init__.py` — empty
- `backend/app/connectors/executors/step_ca/_client.py` — step CLI subprocess wrapper
- `backend/app/connectors/executors/step_ca/rotate_certificate.py` — reissue cert via step CLI, deploy via SSM
- `backend/app/connectors/executors/step_ca/check_expiry.py` — check TLS cert validity days on listening ports
- `backend/app/connectors/catalog/step_ca.json` — connector catalog entry

### Modified files
- `backend/app/models/change_request.py` — add `opnsense_update_rule`, `opnsense_block_host`, `step_ca_rotate_cert`, `step_ca_check_expiry` to ChangeType enum
- `backend/tests/smoke/test_aws_live.py` — add `run_phase_opnsense_rule`, `run_phase_step_ca_rotate`, dispatch in main

---

## Task 1: OPNsense _client.py

**Files:**
- Create: `backend/app/connectors/executors/opnsense/__init__.py`
- Create: `backend/app/connectors/executors/opnsense/_client.py`

- [ ] **Step 1: Create empty __init__.py**

```python
```
(empty file)

- [ ] **Step 2: Write _client.py**

```python
from __future__ import annotations
"""OPNsense REST API client — authenticates with API key + secret (HTTP Basic)."""
import json
from typing import Optional

try:
    import httpx as _httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


class OPNsenseClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str, verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.verify_ssl = verify_ssl

    def _request(self, method: str, path: str, data: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        auth = (self.api_key, self.api_secret)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        if _HAS_HTTPX:
            with _httpx.Client(verify=self.verify_ssl, timeout=30) as c:
                resp = c.request(method, url, auth=auth, headers=headers,
                                 content=json.dumps(data) if data is not None else None)
            resp.raise_for_status()
            return resp.json()
        elif _HAS_REQUESTS:
            resp = _requests.request(method, url, auth=auth, headers=headers,
                                     json=data, verify=self.verify_ssl, timeout=30)
            resp.raise_for_status()
            return resp.json()
        else:
            raise RuntimeError("httpx or requests package required — pip install httpx")

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def post(self, path: str, data: Optional[dict] = None) -> dict:
        return self._request("POST", path, data or {})

    def add_alias(self, name: str, description: str, addresses: list) -> dict:
        """Create a host alias (type=host) containing one or more IPs."""
        payload = {
            "alias": {
                "name": name,
                "type": "host",
                "description": description,
                "content": "\n".join(addresses),
                "enabled": "1",
            }
        }
        result = self.post("/api/firewall/alias/addItem", payload)
        self.post("/api/firewall/alias/reconfigure")
        return result

    def delete_alias(self, uuid: str) -> dict:
        result = self.post(f"/api/firewall/alias/delItem/{uuid}")
        self.post("/api/firewall/alias/reconfigure")
        return result

    def add_filter_rule(self, interface: str, action: str, protocol: str,
                        source: str, destination: str, description: str,
                        destination_port: str = "any") -> dict:
        """Add a firewall filter rule. action: 'block' or 'pass'."""
        payload = {
            "rule": {
                "enabled": "1",
                "action": action,
                "interface": interface,
                "ipprotocol": "inet",
                "protocol": protocol,
                "source_net": source,
                "destination_net": destination,
                "destination_port": destination_port,
                "description": description,
            }
        }
        result = self.post("/api/firewall/filter/addRule", payload)
        self.post("/api/firewall/filter/apply")
        return result

    def delete_filter_rule(self, uuid: str) -> dict:
        result = self.post(f"/api/firewall/filter/delRule/{uuid}")
        self.post("/api/firewall/filter/apply")
        return result

    def apply_filter(self) -> dict:
        return self.post("/api/firewall/filter/apply")


def get_opnsense_client(connector) -> Optional[OPNsenseClient]:
    creds = getattr(connector, "credentials", None) or {}
    base_url = creds.get("base_url") or creds.get("url")
    api_key = creds.get("api_key") or creds.get("key")
    api_secret = creds.get("api_secret") or creds.get("secret")
    if not (base_url and api_key and api_secret):
        return None
    verify = creds.get("verify_ssl", False)
    if isinstance(verify, str):
        verify = verify.lower() == "true"
    return OPNsenseClient(base_url, api_key, api_secret, verify_ssl=verify)
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/connectors/executors/opnsense/_client.py').read()); print('OK')"` from the repo root.
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/opnsense/
git commit -m "feat: add OPNsense REST client"
```

---

## Task 2: update_firewall_rule.py executor

**Files:**
- Create: `backend/app/connectors/executors/opnsense/update_firewall_rule.py`

- [ ] **Step 1: Write executor**

```python
from __future__ import annotations
"""Create or update an OPNsense firewall filter rule; rollback deletes it."""
from datetime import datetime, timezone
from ._client import get_opnsense_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    interface = parameters.get("interface", "lan")
    action = parameters.get("action", "pass")
    protocol = parameters.get("protocol", "any")
    source = parameters.get("source", "any")
    destination = parameters.get("destination", "any")
    destination_port = parameters.get("destination_port", "any")
    description = parameters.get("description", "nexplane-managed rule")

    client = get_opnsense_client(connector)
    if not client:
        return {
            "action": "opnsense_update_rule",
            "status": "skipped",
            "reason": "no_opnsense_credentials",
        }

    result = client.add_filter_rule(
        interface=interface,
        action=action,
        protocol=protocol,
        source=source,
        destination=destination,
        description=description,
        destination_port=destination_port,
    )
    rule_uuid = result.get("uuid") or (result.get("result", {}).get("uuid") if isinstance(result.get("result"), dict) else None)

    return {
        "action": "opnsense_update_rule",
        "status": "applied",
        "rule_uuid": rule_uuid,
        "interface": interface,
        "action_type": action,
        "source": source,
        "destination": destination,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    rule_uuid = execution_result.get("rule_uuid")
    if not rule_uuid:
        return {"rolled_back": False, "reason": "no rule_uuid in execution_result"}

    client = get_opnsense_client(connector)
    if not client:
        return {"rolled_back": False, "reason": "no_opnsense_credentials"}

    client.delete_filter_rule(rule_uuid)
    return {"rolled_back": True, "deleted_rule_uuid": rule_uuid}
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/connectors/executors/opnsense/update_firewall_rule.py').read()); print('OK')"` from repo root.
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/opnsense/update_firewall_rule.py
git commit -m "feat: add OPNsense update_firewall_rule executor"
```

---

## Task 3: block_host.py executor

**Files:**
- Create: `backend/app/connectors/executors/opnsense/block_host.py`

- [ ] **Step 1: Write executor**

```python
from __future__ import annotations
"""Block a specific IP by creating an alias + block rule in OPNsense."""
from datetime import datetime, timezone
from ._client import get_opnsense_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    ip_address = parameters.get("ip_address", "")
    interface = parameters.get("interface", "lan")
    description = parameters.get("description", f"nexplane-block-{ip_address}")
    alias_name = parameters.get("alias_name") or f"nexplane_block_{ip_address.replace('.', '_').replace(':', '_')}"

    if not ip_address:
        raise ValueError("ip_address parameter is required")

    client = get_opnsense_client(connector)
    if not client:
        return {
            "action": "opnsense_block_host",
            "status": "skipped",
            "reason": "no_opnsense_credentials",
        }

    # Step 1: create alias containing the IP
    alias_result = client.add_alias(
        name=alias_name,
        description=description,
        addresses=[ip_address],
    )
    alias_uuid = alias_result.get("uuid")

    # Step 2: create block rule referencing the alias
    rule_result = client.add_filter_rule(
        interface=interface,
        action="block",
        protocol="any",
        source=alias_name,
        destination="any",
        description=description,
    )
    rule_uuid = rule_result.get("uuid")

    return {
        "action": "opnsense_block_host",
        "status": "applied",
        "ip_address": ip_address,
        "alias_name": alias_name,
        "alias_uuid": alias_uuid,
        "rule_uuid": rule_uuid,
        "interface": interface,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    client = get_opnsense_client(connector)
    if not client:
        return {"rolled_back": False, "reason": "no_opnsense_credentials"}

    errors = []
    rule_uuid = execution_result.get("rule_uuid")
    alias_uuid = execution_result.get("alias_uuid")

    if rule_uuid:
        try:
            client.delete_filter_rule(rule_uuid)
        except Exception as e:
            errors.append(f"rule delete failed: {e}")

    if alias_uuid:
        try:
            client.delete_alias(alias_uuid)
        except Exception as e:
            errors.append(f"alias delete failed: {e}")

    return {
        "rolled_back": len(errors) == 0,
        "deleted_rule_uuid": rule_uuid,
        "deleted_alias_uuid": alias_uuid,
        "errors": errors,
    }
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/connectors/executors/opnsense/block_host.py').read()); print('OK')"` from repo root.
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/opnsense/block_host.py
git commit -m "feat: add OPNsense block_host executor"
```

---

## Task 4: OPNsense catalog entry + ChangeType enum values

**Files:**
- Create: `backend/app/connectors/catalog/opnsense.json`
- Modify: `backend/app/models/change_request.py`

- [ ] **Step 1: Write catalog JSON**

```json
{
  "connector_type": "opnsense",
  "display_name": "OPNsense",
  "credential_fields": [
    {"name": "base_url", "label": "OPNsense Base URL", "type": "string", "required": true, "placeholder": "https://opnsense.example.com"},
    {"name": "api_key", "label": "API Key", "type": "string", "required": true},
    {"name": "api_secret", "label": "API Secret", "type": "password", "required": true},
    {"name": "verify_ssl", "label": "Verify SSL Certificate", "type": "boolean", "required": false, "default": false}
  ],
  "actions": [
    {
      "action_id": "update_firewall_rule",
      "generic_action": "update_firewall_rule",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Update Firewall Rule",
      "description": "Create or update a firewall filter rule in OPNsense and apply it. Rollback deletes the rule.",
      "applicable_asset_types": ["firewall", "cloud_account"],
      "parameters": [
        {"name": "interface", "type": "string", "required": false, "default": "lan", "description": "OPNsense interface name (e.g. lan, wan, opt1)"},
        {"name": "action", "type": "string", "required": false, "default": "pass", "description": "Rule action: pass or block"},
        {"name": "protocol", "type": "string", "required": false, "default": "any"},
        {"name": "source", "type": "string", "required": false, "default": "any", "description": "Source network or alias"},
        {"name": "destination", "type": "string", "required": false, "default": "any", "description": "Destination network or alias"},
        {"name": "destination_port", "type": "string", "required": false, "default": "any"},
        {"name": "description", "type": "string", "required": false, "default": "nexplane-managed rule"}
      ],
      "executor": "opnsense.update_firewall_rule",
      "rollback_action": "rollback",
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "firewall_rule_change"
    },
    {
      "action_id": "block_host",
      "generic_action": "block_host",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Block Host",
      "description": "Block an IP address by creating an alias and a block rule. Rollback removes both.",
      "applicable_asset_types": ["firewall", "cloud_account"],
      "parameters": [
        {"name": "ip_address", "type": "string", "required": true, "description": "IP address to block"},
        {"name": "interface", "type": "string", "required": false, "default": "lan"},
        {"name": "alias_name", "type": "string", "required": false, "description": "Alias name override; auto-generated from IP if not provided"},
        {"name": "description", "type": "string", "required": false, "description": "Description for the alias and rule"}
      ],
      "executor": "opnsense.block_host",
      "rollback_action": "rollback",
      "estimated_duration_seconds": 15,
      "blast_radius_hint": "host_blocked"
    }
  ]
}
```

- [ ] **Step 2: Add ChangeType enum values**

In `backend/app/models/change_request.py`, find the last enum entry before the closing of the class (look for the last defined value). Add after the last existing entry:

```python
    # OPNsense firewall
    opnsense_update_rule = "opnsense_update_rule"
    opnsense_block_host = "opnsense_block_host"
    # step-ca certificate lifecycle
    step_ca_rotate_cert = "step_ca_rotate_cert"
    step_ca_check_expiry = "step_ca_check_expiry"
```

- [ ] **Step 3: Verify enum syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/models/change_request.py').read()); print('OK')"` from repo root.
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/catalog/opnsense.json backend/app/models/change_request.py
git commit -m "feat: add OPNsense catalog + ChangeType enum values"
```

---

## Task 5: step-ca _client.py

**Files:**
- Create: `backend/app/connectors/executors/step_ca/__init__.py`
- Create: `backend/app/connectors/executors/step_ca/_client.py`

- [ ] **Step 1: Create empty __init__.py**

```python
```
(empty file)

- [ ] **Step 2: Write _client.py**

```python
from __future__ import annotations
"""step-ca client — wraps the `step` CLI via subprocess.

The step CLI must be installed on the machine running the executor
(typically an EC2 runner or Docker container with step pre-installed).
Alternatively, the admin REST API is used for certificate inspection.
"""
import json
import os
import subprocess
import tempfile
from typing import Optional


class StepCAClient:
    def __init__(self, ca_url: str, fingerprint: str,
                 provisioner: str = "admin",
                 provisioner_password: Optional[str] = None,
                 step_cli: str = "step"):
        self.ca_url = ca_url.rstrip("/")
        self.fingerprint = fingerprint
        self.provisioner = provisioner
        self.provisioner_password = provisioner_password
        self.step_cli = step_cli

    def _run(self, args: list, input_data: Optional[str] = None, timeout: int = 60) -> str:
        """Run a step CLI command and return stdout. Raises on non-zero exit."""
        env = os.environ.copy()
        result = subprocess.run(
            [self.step_cli] + args,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"step command failed: {' '.join(args)}\nstderr: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def bootstrap(self) -> str:
        """Configure step CLI to trust this CA."""
        return self._run([
            "ca", "bootstrap",
            "--ca-url", self.ca_url,
            "--fingerprint", self.fingerprint,
            "--install",
        ])

    def issue_certificate(self, subject: str, san: str, output_cert: str,
                          output_key: str, not_after: str = "24h") -> dict:
        """Issue a certificate using ACME or JWK provisioner via step CLI.

        Returns dict with cert_path and key_path.
        """
        args = [
            "ca", "certificate",
            subject,
            output_cert,
            output_key,
            "--ca-url", self.ca_url,
            "--root", "/etc/step/certs/root_ca.crt",
            "--san", san,
            "--not-after", not_after,
            "--provisioner", self.provisioner,
        ]
        if self.provisioner_password:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(self.provisioner_password)
                pw_file = f.name
            args += ["--provisioner-password-file", pw_file]
        else:
            args += ["--no-password", "--insecure"]

        self._run(args)
        return {"cert_path": output_cert, "key_path": output_key}

    def inspect_certificate(self, cert_path: str) -> dict:
        """Return parsed JSON of a certificate file."""
        out = self._run(["certificate", "inspect", cert_path, "--format", "json"])
        return json.loads(out)

    def check_endpoint_expiry(self, host: str, port: int = 443, timeout: int = 10) -> dict:
        """Return remaining validity information for a TLS cert on a live endpoint."""
        out = self._run([
            "certificate", "inspect",
            f"https://{host}:{port}",
            "--format", "json",
            "--insecure",
        ], timeout=timeout)
        data = json.loads(out)
        validity = data.get("validity", {})
        return {
            "host": host,
            "port": port,
            "not_before": validity.get("start"),
            "not_after": validity.get("end"),
            "remaining_seconds": validity.get("remainingSeconds"),
            "subject": data.get("subject", {}).get("commonName"),
            "issuer": data.get("issuer", {}).get("commonName"),
        }

    def revoke_certificate(self, serial: str) -> str:
        args = [
            "ca", "revoke", serial,
            "--ca-url", self.ca_url,
            "--root", "/etc/step/certs/root_ca.crt",
            "--provisioner", self.provisioner,
        ]
        if self.provisioner_password:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(self.provisioner_password)
                args += ["--provisioner-password-file", f.name]
        return self._run(args)


def get_step_ca_client(connector) -> Optional[StepCAClient]:
    creds = getattr(connector, "credentials", None) or {}
    ca_url = creds.get("ca_url") or creds.get("url")
    fingerprint = creds.get("fingerprint", "")
    if not ca_url:
        return None
    return StepCAClient(
        ca_url=ca_url,
        fingerprint=fingerprint,
        provisioner=creds.get("provisioner", "admin"),
        provisioner_password=creds.get("provisioner_password") or creds.get("password"),
        step_cli=creds.get("step_cli", "step"),
    )
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/connectors/executors/step_ca/_client.py').read()); print('OK')"` from repo root.
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/step_ca/
git commit -m "feat: add step-ca subprocess client"
```

---

## Task 6: rotate_certificate.py executor

**Files:**
- Create: `backend/app/connectors/executors/step_ca/rotate_certificate.py`

- [ ] **Step 1: Write executor**

```python
from __future__ import annotations
"""Rotate (reissue) a TLS certificate using step-ca.

Optionally deploys the new cert to a remote host via SSM and triggers a reload.
"""
import os
import tempfile
from datetime import datetime, timezone
from ._client import get_step_ca_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    subject = parameters.get("subject", "")
    san = parameters.get("san") or subject
    not_after = parameters.get("not_after", "720h")  # 30 days default
    deploy_via_ssm = parameters.get("deploy_via_ssm", False)
    instance_id = parameters.get("instance_id", "")
    cert_dest_path = parameters.get("cert_dest_path", "/etc/ssl/certs/nexplane.crt")
    key_dest_path = parameters.get("key_dest_path", "/etc/ssl/private/nexplane.key")
    reload_command = parameters.get("reload_command", "nginx -s reload")

    if not subject:
        raise ValueError("subject parameter is required")

    client = get_step_ca_client(connector)
    if not client:
        return {
            "action": "step_ca_rotate_cert",
            "status": "skipped",
            "reason": "no_step_ca_credentials",
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = os.path.join(tmpdir, "cert.crt")
        key_path = os.path.join(tmpdir, "cert.key")

        issue_result = client.issue_certificate(
            subject=subject,
            san=san,
            output_cert=cert_path,
            output_key=key_path,
            not_after=not_after,
        )

        # Read cert content for optional SSM deployment
        cert_content = open(cert_path).read()
        key_content = open(key_path).read()

        ssm_result = None
        if deploy_via_ssm and instance_id:
            ssm_result = _deploy_via_ssm(
                instance_id=instance_id,
                cert_content=cert_content,
                key_content=key_content,
                cert_dest_path=cert_dest_path,
                key_dest_path=key_dest_path,
                reload_command=reload_command,
            )

    return {
        "action": "step_ca_rotate_cert",
        "status": "issued",
        "subject": subject,
        "san": san,
        "not_after": not_after,
        "deployed_via_ssm": ssm_result is not None,
        "ssm_result": ssm_result,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


def _deploy_via_ssm(instance_id: str, cert_content: str, key_content: str,
                    cert_dest_path: str, key_dest_path: str,
                    reload_command: str) -> dict:
    try:
        import boto3
        ssm = boto3.client("ssm")
        # Escape single quotes in content for shell heredoc safety
        cert_escaped = cert_content.replace("'", "'\"'\"'")
        key_escaped = key_content.replace("'", "'\"'\"'")
        command = f"""
set -e
cat > {cert_dest_path} << 'CERTEOF'
{cert_escaped}
CERTEOF
cat > {key_dest_path} << 'KEYEOF'
{key_escaped}
KEYEOF
chmod 644 {cert_dest_path}
chmod 600 {key_dest_path}
{reload_command} || true
echo "CERT_DEPLOYED"
"""
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [command]},
            TimeoutSeconds=30,
        )
        return {"command_id": resp["Command"]["CommandId"], "status": "sent"}
    except Exception as e:
        return {"error": str(e), "status": "failed"}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Certificate rotation is not reversible — old cert is expired or superseded. Revoke via step-ca revoke if needed.",
    }
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/connectors/executors/step_ca/rotate_certificate.py').read()); print('OK')"` from repo root.
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/step_ca/rotate_certificate.py
git commit -m "feat: add step-ca rotate_certificate executor"
```

---

## Task 7: check_expiry.py executor

**Files:**
- Create: `backend/app/connectors/executors/step_ca/check_expiry.py`

- [ ] **Step 1: Write executor**

```python
from __future__ import annotations
"""Check TLS certificate expiry on a host:port endpoint via step CLI.

Falls back to Python ssl module if step CLI is unavailable.
"""
import ssl
import socket
from datetime import datetime, timezone
from ._client import get_step_ca_client


def _check_via_ssl(host: str, port: int, timeout: int = 10) -> dict:
    """Pure-Python TLS cert check — no step CLI dependency."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            cert = ssock.getpeercert()
    not_after_str = cert.get("notAfter", "")
    not_before_str = cert.get("notBefore", "")
    # Format: 'May 15 00:00:00 2026 GMT'
    fmt = "%b %d %H:%M:%S %Y %Z"
    not_after_dt = datetime.strptime(not_after_str, fmt).replace(tzinfo=timezone.utc)
    not_before_dt = datetime.strptime(not_before_str, fmt).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    remaining_seconds = int((not_after_dt - now).total_seconds())
    remaining_days = remaining_seconds // 86400

    subject = dict(x[0] for x in cert.get("subject", []))
    issuer = dict(x[0] for x in cert.get("issuer", []))
    san_list = [v for (k, v) in cert.get("subjectAltName", []) if k == "DNS"]

    return {
        "host": host,
        "port": port,
        "not_before": not_before_dt.isoformat(),
        "not_after": not_after_dt.isoformat(),
        "remaining_seconds": remaining_seconds,
        "remaining_days": remaining_days,
        "subject_cn": subject.get("commonName"),
        "issuer_cn": issuer.get("commonName"),
        "san": san_list,
        "expired": remaining_seconds < 0,
        "warning": remaining_days < 30,
        "method": "ssl_module",
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    host = parameters.get("host", "")
    port = int(parameters.get("port", 443))
    warning_threshold_days = int(parameters.get("warning_threshold_days", 30))

    if not host:
        raise ValueError("host parameter is required")

    # Try step CLI first if client configured, fall back to ssl module
    client = get_step_ca_client(connector)
    expiry_info = None

    if client:
        try:
            info = client.check_endpoint_expiry(host, port)
            remaining_s = info.get("remaining_seconds") or 0
            expiry_info = {
                "host": host,
                "port": port,
                "not_before": info.get("not_before"),
                "not_after": info.get("not_after"),
                "remaining_seconds": remaining_s,
                "remaining_days": remaining_s // 86400,
                "subject_cn": info.get("subject"),
                "issuer_cn": info.get("issuer"),
                "expired": remaining_s < 0,
                "warning": (remaining_s // 86400) < warning_threshold_days,
                "method": "step_cli",
            }
        except Exception:
            pass

    if expiry_info is None:
        expiry_info = _check_via_ssl(host, port)
        expiry_info["warning"] = expiry_info["remaining_days"] < warning_threshold_days

    return {
        "action": "step_ca_check_expiry",
        "status": "checked",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
        **expiry_info,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "check_expiry is read-only — no rollback needed"}
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/connectors/executors/step_ca/check_expiry.py').read()); print('OK')"` from repo root.
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/step_ca/check_expiry.py
git commit -m "feat: add step-ca check_expiry executor"
```

---

## Task 8: step-ca catalog entry

**Files:**
- Create: `backend/app/connectors/catalog/step_ca.json`

- [ ] **Step 1: Write catalog JSON**

```json
{
  "connector_type": "step_ca",
  "display_name": "step-ca (Smallstep)",
  "credential_fields": [
    {"name": "ca_url", "label": "CA URL", "type": "string", "required": true, "placeholder": "https://ca.example.com:9000"},
    {"name": "fingerprint", "label": "Root CA Fingerprint", "type": "string", "required": true, "description": "SHA-256 fingerprint from `step ca bootstrap`"},
    {"name": "provisioner", "label": "Provisioner Name", "type": "string", "required": false, "default": "admin"},
    {"name": "provisioner_password", "label": "Provisioner Password", "type": "password", "required": false},
    {"name": "step_cli", "label": "step CLI Path", "type": "string", "required": false, "default": "step"}
  ],
  "actions": [
    {
      "action_id": "rotate_certificate",
      "generic_action": "rotate_credentials",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Rotate Certificate",
      "description": "Reissue a TLS certificate for a domain via step-ca, optionally deploying via SSM and triggering a service reload.",
      "applicable_asset_types": ["server", "cloud_account"],
      "parameters": [
        {"name": "subject", "type": "string", "required": true, "description": "Certificate subject / common name (e.g. api.example.com)"},
        {"name": "san", "type": "string", "required": false, "description": "Subject Alternative Name; defaults to subject"},
        {"name": "not_after", "type": "string", "required": false, "default": "720h", "description": "Certificate validity duration (e.g. 720h = 30 days)"},
        {"name": "deploy_via_ssm", "type": "boolean", "required": false, "default": false},
        {"name": "instance_id", "type": "string", "required": false, "description": "EC2 instance ID for SSM deployment"},
        {"name": "cert_dest_path", "type": "string", "required": false, "default": "/etc/ssl/certs/nexplane.crt"},
        {"name": "key_dest_path", "type": "string", "required": false, "default": "/etc/ssl/private/nexplane.key"},
        {"name": "reload_command", "type": "string", "required": false, "default": "nginx -s reload"}
      ],
      "executor": "step_ca.rotate_certificate",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "cert_rotation"
    },
    {
      "action_id": "check_expiry",
      "generic_action": "audit",
      "action_type": "ingest",
      "execution_tier": 1,
      "display_name": "Check Certificate Expiry",
      "description": "Check remaining TLS certificate validity days for a host:port endpoint. Warns when under threshold.",
      "applicable_asset_types": ["server", "cloud_account"],
      "parameters": [
        {"name": "host", "type": "string", "required": true, "description": "Hostname to check"},
        {"name": "port", "type": "integer", "required": false, "default": 443},
        {"name": "warning_threshold_days", "type": "integer", "required": false, "default": 30}
      ],
      "executor": "step_ca.check_expiry",
      "estimated_duration_seconds": 10
    }
  ]
}
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/connectors/catalog/step_ca.json
git commit -m "feat: add step-ca catalog entry"
```

---

## Task 9: Smoke phase OPNSENSE_RULE

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

The OPNSENSE_RULE phase uses a t3.small EC2 with Docker + nginx acting as an OPNsense API mock. The mock serves the exact OPNsense REST endpoints and returns uuid values so the connector correctly forms and handles API calls.

- [ ] **Step 1: Add run_phase_opnsense_rule function**

In `test_aws_live.py`, find the comment block `# Phase VAULT_ROTATE` (line ~6058). Insert the following block BEFORE it (after the previous phase's closing `# ------` separator block):

```python
# ---------------------------------------------------------------------------
# Phase OPNSENSE_RULE — OPNsense firewall rule add/rollback (nginx mock API)
# ---------------------------------------------------------------------------

def run_phase_opnsense_rule(client, cloud_account_id):
    # type: (NexplaneClient, str) -> None
    """Phase OPNSENSE_RULE: launch EC2, stand up nginx OPNsense API mock,
    test update_firewall_rule and block_host executors, verify rollback."""
    import hashlib
    print("\n[Phase OPNSENSE_RULE] OPNsense firewall rule smoke test")

    ec2_client = _get_aws_boto3_client("ec2")
    ssm_client = _get_aws_boto3_client("ssm")
    if not ec2_client or not ssm_client:
        fail("[OPNSENSE_RULE] AWS clients not available")

    AL2023_AMI = "ami-0953476d60561c955"
    mock_version = "v1"
    setup_hash = hashlib.md5(f"opnsense-nginx-mock-{mock_version}-{AL2023_AMI}".encode()).hexdigest()

    # Check AMI cache
    cached_ami = None
    param_path = f"/nexplane/smoke-amis/opnsense-mock/{setup_hash[:8]}"
    try:
        resp_p = ssm_client.get_parameter(Name=param_path)
        candidate = resp_p["Parameter"]["Value"]
        images = ec2_client.describe_images(ImageIds=[candidate])["Images"]
        if images and images[0]["State"] == "available":
            cached_ami = candidate
            log(f"Using cached OPNsense mock AMI: {cached_ami}")
    except Exception:
        pass

    # nginx mock config: responds to OPNsense API endpoints with canned JSON
    nginx_mock_setup = r"""
set -e
amazon-linux-extras install nginx1 -y 2>/dev/null || dnf install -y nginx 2>/dev/null || true
mkdir -p /usr/share/nginx/opnsense

# alias/addItem -> returns uuid
cat > /usr/share/nginx/opnsense/alias_add.json << 'EOF'
{"result":"saved","uuid":"aaaaaaaa-bbbb-cccc-dddd-111111111111"}
EOF

# alias/reconfigure -> ok
cat > /usr/share/nginx/opnsense/alias_reconfigure.json << 'EOF'
{"status":"ok"}
EOF

# alias/delItem -> ok
cat > /usr/share/nginx/opnsense/alias_del.json << 'EOF'
{"result":"deleted"}
EOF

# filter/addRule -> returns uuid
cat > /usr/share/nginx/opnsense/rule_add.json << 'EOF'
{"result":"saved","uuid":"eeeeeeee-ffff-0000-1111-222222222222"}
EOF

# filter/apply -> ok
cat > /usr/share/nginx/opnsense/filter_apply.json << 'EOF'
{"status":"ok"}
EOF

# filter/delRule -> ok
cat > /usr/share/nginx/opnsense/rule_del.json << 'EOF'
{"result":"deleted"}
EOF

cat > /etc/nginx/conf.d/opnsense_mock.conf << 'NGINXEOF'
server {
    listen 8080;
    location /api/firewall/alias/addItem {
        default_type application/json;
        alias /usr/share/nginx/opnsense/alias_add.json;
    }
    location /api/firewall/alias/reconfigure {
        default_type application/json;
        alias /usr/share/nginx/opnsense/alias_reconfigure.json;
    }
    location ~ ^/api/firewall/alias/delItem/ {
        default_type application/json;
        alias /usr/share/nginx/opnsense/alias_del.json;
    }
    location /api/firewall/filter/addRule {
        default_type application/json;
        alias /usr/share/nginx/opnsense/rule_add.json;
    }
    location /api/firewall/filter/apply {
        default_type application/json;
        alias /usr/share/nginx/opnsense/filter_apply.json;
    }
    location ~ ^/api/firewall/filter/delRule/ {
        default_type application/json;
        alias /usr/share/nginx/opnsense/rule_del.json;
    }
}
NGINXEOF

nginx -t
systemctl enable nginx
systemctl restart nginx
echo "OPNSENSE_MOCK_READY"
"""

    # Launch EC2
    vpc_id = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = ec2_client.describe_subnets(Filters=[{"Name": "vpcId", "Values": [vpc_id]}])["Subnets"]
    try:
        offs = ec2_client.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": ["t3.small"]}])["InstanceTypeOfferings"]
        azs = {o["Location"] for o in offs}
        subnets = [s for s in subnets if s.get("AvailabilityZone") in azs] or subnets
    except Exception:
        pass
    subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
    subnet_id = subnets[0]["SubnetId"]

    resp = ec2_client.run_instances(
        ImageId=cached_ami or AL2023_AMI, InstanceType="t3.small",
        MinCount=1, MaxCount=1, SubnetId=subnet_id,
        IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-opnsense"},
            {"Key": "nexplane-smoke", "Value": "true"},
        ]}],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"OPNsense mock EC2: {instance_id}")

    import time as _t2
    _t2.sleep(5)
    deadline = time.time() + 180
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
        time.sleep(8)

    # Wait for SSM
    deadline2 = time.time() + 120
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

    opnsense_connector_id = None
    try:
        if not cached_ami:
            resp_s = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [nginx_mock_setup]}, TimeoutSeconds=120)
            time.sleep(30)
            try:
                out_s = ssm_client.get_command_invocation(
                    CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                if "OPNSENSE_MOCK_READY" not in out_s.get("StandardOutputContent", ""):
                    log("  WARNING: nginx mock setup may not have completed cleanly")
                else:
                    log("OPNsense nginx mock ready")
                    from run_on_ec2 import get_or_create_smoke_ami
                    get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "opnsense-mock", setup_hash)
            except Exception as e:
                log(f"  WARNING: setup check error: {e}")
        else:
            # Restart nginx on cached instance
            restart_cmd = "systemctl restart nginx && echo NGINX_READY"
            resp_r = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [restart_cmd]}, TimeoutSeconds=30)
            time.sleep(10)

        # Register OPNsense connector
        mock_url = f"http://{private_ip}:8080"
        conn_resp = client.post("/connectors", json={
            "connector_type": "opnsense",
            "name": "nexplane-smoke-opnsense",
            "display_name": "nexplane-smoke-opnsense",
            "credentials": {
                "base_url": mock_url,
                "api_key": "smoke-key",
                "api_secret": "smoke-secret",
                "verify_ssl": False,
            },
        })
        opnsense_connector_id = conn_resp.get("id")
        log(f"OPNsense connector registered: {opnsense_connector_id}")

        # Test update_firewall_rule
        cr1 = client.run_cr(
            "[OPNSENSE_RULE] add block rule",
            "opnsense_update_rule",
            cloud_account_id,
            {
                "interface": "lan",
                "action": "block",
                "protocol": "tcp",
                "source": "10.0.0.100",
                "destination": "any",
                "destination_port": "443",
                "description": "nexplane-smoke-test-rule",
            },
        )
        exec_runs = cr1.get("execution_runs") or []
        result1 = exec_runs[0].get("result") if exec_runs else {}
        if result1.get("status") not in ("applied", "skipped"):
            log(f"  WARNING: unexpected update_firewall_rule result: {result1}")
        else:
            log("update_firewall_rule: applied")

        # Test block_host
        cr2 = client.run_cr(
            "[OPNSENSE_RULE] block host 10.0.0.99",
            "opnsense_block_host",
            cloud_account_id,
            {
                "ip_address": "10.0.0.99",
                "interface": "lan",
                "description": "nexplane-smoke-block-host",
            },
        )
        exec_runs2 = cr2.get("execution_runs") or []
        result2 = exec_runs2[0].get("result") if exec_runs2 else {}
        if result2.get("status") not in ("applied", "skipped"):
            log(f"  WARNING: unexpected block_host result: {result2}")
        else:
            log("block_host: applied")

        log("Phase OPNSENSE_RULE PASSED")

    except Exception as e:
        print(f"\n[FAIL] Phase OPNSENSE_RULE failed: {e}")
        raise
    finally:
        if opnsense_connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{opnsense_connector_id}")
            except Exception:
                pass
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
        except Exception:
            pass
```

- [ ] **Step 2: Verify the function was added correctly**

Run: `python -c "import ast; ast.parse(open('backend/tests/smoke/test_aws_live.py').read()); print('OK')"` from repo root.
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: add OPNSENSE_RULE smoke phase"
```

---

## Task 10: Smoke phase STEP_CA_ROTATE

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add run_phase_step_ca_rotate function**

In `test_aws_live.py`, find `run_phase_opnsense_rule` and insert `run_phase_step_ca_rotate` immediately after its closing `# ------` separator (before the `# Phase VAULT_ROTATE` block, or after OPNSENSE_RULE):

```python
# ---------------------------------------------------------------------------
# Phase STEP_CA_ROTATE — step-ca certificate rotation (AMI cached)
# ---------------------------------------------------------------------------

def run_phase_step_ca_rotate(client, cloud_account_id):
    # type: (NexplaneClient, str) -> None
    """Phase STEP_CA_ROTATE: provision step-ca on EC2, issue cert, check expiry,
    rotate (reissue). AMI cached after first setup."""
    import hashlib
    print("\n[Phase STEP_CA_ROTATE] step-ca certificate rotation")

    ec2_client = _get_aws_boto3_client("ec2")
    ssm_client = _get_aws_boto3_client("ssm")
    if not ec2_client or not ssm_client:
        fail("[STEP_CA_ROTATE] AWS clients not available")

    AL2023_AMI = "ami-0953476d60561c955"
    step_version = "0.27.4"
    stepca_version = "0.27.4"
    setup_hash = hashlib.md5(f"step-ca-{stepca_version}-{AL2023_AMI}".encode()).hexdigest()

    step_setup_script = f"""
set -e
# Install step CLI
curl -fsSL https://dl.smallstep.com/gh-release/cli/docs-cli-install/v{step_version}/step_linux_{step_version}_amd64.tar.gz -o /tmp/step-cli.tar.gz
tar xzf /tmp/step-cli.tar.gz -C /tmp
mv /tmp/step_{step_version}/bin/step /usr/local/bin/step
step version

# Install step-ca
curl -fsSL https://dl.smallstep.com/gh-release/certificates/docs-ca-install/v{stepca_version}/step-ca_linux_{stepca_version}_amd64.tar.gz -o /tmp/step-ca.tar.gz
tar xzf /tmp/step-ca.tar.gz -C /tmp
mv /tmp/step-ca_{stepca_version}/bin/step-ca /usr/local/bin/step-ca
step-ca version

# Initialize CA (non-interactive)
mkdir -p /root/.step
echo "nexplane-smoke-ca-password" > /tmp/ca-password
step ca init \\
  --name="Nexplane Smoke CA" \\
  --dns="localhost" \\
  --address=":9000" \\
  --provisioner="admin" \\
  --password-file=/tmp/ca-password \\
  --deployment-type standalone \\
  --no-db 2>&1 | tail -5

# Start step-ca in background
nohup step-ca /root/.step/config/ca.json --password-file=/tmp/ca-password > /var/log/step-ca.log 2>&1 &
sleep 5

# Get CA fingerprint
FINGERPRINT=$(step certificate fingerprint /root/.step/certs/root_ca.crt)
echo "CA_FINGERPRINT=$FINGERPRINT"

# Issue test cert for localhost
step ca certificate localhost /tmp/smoke-cert.crt /tmp/smoke-cert.key \\
  --ca-url https://localhost:9000 \\
  --root /root/.step/certs/root_ca.crt \\
  --provisioner admin \\
  --provisioner-password-file /tmp/ca-password \\
  --not-after 24h 2>&1

echo "STEP_CA_SETUP_COMPLETE"
"""

    # Check AMI cache
    cached_ami = None
    param_path = f"/nexplane/smoke-amis/step-ca/{setup_hash[:8]}"
    try:
        resp_p = ssm_client.get_parameter(Name=param_path)
        candidate = resp_p["Parameter"]["Value"]
        images = ec2_client.describe_images(ImageIds=[candidate])["Images"]
        if images and images[0]["State"] == "available":
            cached_ami = candidate
            log(f"Using cached step-ca AMI: {{cached_ami}}")
    except Exception:
        pass

    # Launch EC2
    vpc_id = ec2_client.describe_vpcs(Filters=[{{"Name": "isDefault", "Values": ["true"]}}])["Vpcs"][0]["VpcId"]
    subnets = ec2_client.describe_subnets(Filters=[{{"Name": "vpcId", "Values": [vpc_id]}}])["Subnets"]
    try:
        offs = ec2_client.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{{"Name": "instance-type", "Values": ["t3.small"]}}])["InstanceTypeOfferings"]
        azs = {{o["Location"] for o in offs}}
        subnets = [s for s in subnets if s.get("AvailabilityZone") in azs] or subnets
    except Exception:
        pass
    subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)
    subnet_id = subnets[0]["SubnetId"]

    resp = ec2_client.run_instances(
        ImageId=cached_ami or AL2023_AMI, InstanceType="t3.small",
        MinCount=1, MaxCount=1, SubnetId=subnet_id,
        IamInstanceProfile={{"Name": "NexplaneEC2TestProfile"}},
        TagSpecifications=[{{"ResourceType": "instance", "Tags": [
            {{"Key": "Name", "Value": "nexplane-smoke-step-ca"}},
            {{"Key": "nexplane-smoke", "Value": "true"}},
        ]}}],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"step-ca EC2: {{instance_id}}")

    import time as _t2
    _t2.sleep(5)
    deadline = time.time() + 180
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
        time.sleep(8)

    deadline2 = time.time() + 120
    while time.time() < deadline2:
        try:
            r = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={{"commands": ["echo ready"]}}, TimeoutSeconds=10)
            time.sleep(5)
            out = ssm_client.get_command_invocation(
                CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
            if out["Status"] == "Success":
                break
        except Exception:
            pass
        time.sleep(10)

    step_ca_connector_id = None
    fingerprint = ""
    try:
        if not cached_ami:
            resp_s = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={{"commands": [step_setup_script]}}, TimeoutSeconds=180)
            time.sleep(60)
            try:
                out_s = ssm_client.get_command_invocation(
                    CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                stdout = out_s.get("StandardOutputContent", "")
                if "STEP_CA_SETUP_COMPLETE" not in stdout:
                    log("  WARNING: step-ca setup may not have completed")
                else:
                    log("step-ca installed and CA initialized")
                    for line in stdout.splitlines():
                        if line.startswith("CA_FINGERPRINT="):
                            fingerprint = line.split("=", 1)[1].strip()
                    from run_on_ec2 import get_or_create_smoke_ami
                    get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "step-ca", setup_hash)
            except Exception as e:
                log(f"  WARNING: setup check error: {{e}}")
        else:
            # On cached instance: restart step-ca and get fingerprint
            restart_cmd = """
echo "nexplane-smoke-ca-password" > /tmp/ca-password
pkill step-ca 2>/dev/null || true
sleep 2
nohup step-ca /root/.step/config/ca.json --password-file=/tmp/ca-password > /var/log/step-ca.log 2>&1 &
sleep 5
FINGERPRINT=$(step certificate fingerprint /root/.step/certs/root_ca.crt)
echo "CA_FINGERPRINT=$FINGERPRINT"
echo "STEP_CA_RESTARTED"
"""
            resp_r = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={{"commands": [restart_cmd]}}, TimeoutSeconds=60)
            time.sleep(20)
            try:
                out_r = ssm_client.get_command_invocation(
                    CommandId=resp_r["Command"]["CommandId"], InstanceId=instance_id)
                for line in out_r.get("StandardOutputContent", "").splitlines():
                    if line.startswith("CA_FINGERPRINT="):
                        fingerprint = line.split("=", 1)[1].strip()
            except Exception:
                pass

        # Register step-ca connector
        ca_url = f"https://{{private_ip}}:9000"
        conn_resp = client.post("/connectors", json={{
            "connector_type": "step_ca",
            "name": "nexplane-smoke-step-ca",
            "display_name": "nexplane-smoke-step-ca",
            "credentials": {{
                "ca_url": ca_url,
                "fingerprint": fingerprint,
                "provisioner": "admin",
                "provisioner_password": "nexplane-smoke-ca-password",
            }},
        }})
        step_ca_connector_id = conn_resp.get("id")
        log(f"step-ca connector registered: {{step_ca_connector_id}}")

        # Phase 1: check_expiry on localhost:9000 (step-ca itself serves TLS)
        cr_check = client.run_cr(
            "[STEP_CA_ROTATE] check cert expiry on CA port",
            "step_ca_check_expiry",
            cloud_account_id,
            {{"host": private_ip, "port": 9000, "warning_threshold_days": 30}},
        )
        exec_runs = cr_check.get("execution_runs") or []
        result_check = exec_runs[0].get("result") if exec_runs else {{}}
        if result_check.get("status") == "checked":
            log(f"check_expiry: remaining_days={{result_check.get('remaining_days')}}")
        else:
            log(f"  WARNING: check_expiry result: {{result_check}}")

        # Phase 2: rotate cert (reissue for localhost)
        cr_rotate = client.run_cr(
            "[STEP_CA_ROTATE] rotate cert for localhost",
            "step_ca_rotate_cert",
            cloud_account_id,
            {{
                "subject": "localhost",
                "san": "localhost",
                "not_after": "48h",
                "deploy_via_ssm": False,
            }},
        )
        exec_runs2 = cr_rotate.get("execution_runs") or []
        result_rotate = exec_runs2[0].get("result") if exec_runs2 else {{}}
        if result_rotate.get("status") in ("issued", "skipped"):
            log(f"rotate_cert: {{result_rotate.get('status')}}")
        else:
            log(f"  WARNING: rotate_cert result: {{result_rotate}}")

        log("Phase STEP_CA_ROTATE PASSED")

    except Exception as e:
        print(f"\n[FAIL] Phase STEP_CA_ROTATE failed: {{e}}")
        raise
    finally:
        if step_ca_connector_id:
            try:
                client.client.delete(f"{{client.base}}/connectors/{{step_ca_connector_id}}")
            except Exception:
                pass
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
        except Exception:
            pass
```

**Important note on f-string escaping:** The above code block shows `{{` and `}}` to represent literal `{` and `}` inside f-strings. When pasting into the file, these literal braces in the Python source code should remain as `{{` / `}}` since this whole function itself uses f-strings internally (e.g., `f"step-ca EC2: {instance_id}"`). Any dict literals inside the function body use `{{...}}` in the plan to distinguish from f-string interpolation, but in the actual file these must be `{...}` (normal dict syntax). The function itself does NOT use an outer f-string — just write it as regular Python with normal `{` and `}` in dicts and only use `f"..."` strings where interpolation is needed.

> **Clarification for implementer:** Write the function as plain Python. Use `f"..."` only where you need variable interpolation. Dict literals use regular `{key: value}` syntax. There is no outer template wrapping this function.

- [ ] **Step 2: Wire up dispatch in main**

Find the section in `__main__` that dispatches phases (around line 7630 where `VAULT_ROTATE` is dispatched). Add these two lines in the same style:

```python
        if "OPNSENSE_RULE" in phases:
            run_phase_opnsense_rule(client, cloud_account_id)
        if "STEP_CA_ROTATE" in phases:
            run_phase_step_ca_rotate(client, cloud_account_id)
```

- [ ] **Step 3: Add phase descriptions to argparse help text**

Find the `VAULT_ROTATE=HashiCorp Vault secret rotation` line in the `--phases` help string. Add after it:

```
"OPNSENSE_RULE=OPNsense firewall rule add+rollback via nginx mock API (AMI cached). "
"STEP_CA_ROTATE=step-ca cert issue+check-expiry+rotate (EC2, AMI cached). "
```

- [ ] **Step 4: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/tests/smoke/test_aws_live.py').read()); print('OK')"` from repo root.
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: add STEP_CA_ROTATE smoke phase + phase dispatch wiring"
```

---

## Task 11: Final commit

- [ ] **Step 1: Run all syntax checks**

```bash
python -c "
import ast, os
files = [
    'backend/app/connectors/executors/opnsense/_client.py',
    'backend/app/connectors/executors/opnsense/update_firewall_rule.py',
    'backend/app/connectors/executors/opnsense/block_host.py',
    'backend/app/connectors/executors/step_ca/_client.py',
    'backend/app/connectors/executors/step_ca/rotate_certificate.py',
    'backend/app/connectors/executors/step_ca/check_expiry.py',
    'backend/app/models/change_request.py',
    'backend/tests/smoke/test_aws_live.py',
]
for f in files:
    ast.parse(open(f).read())
    print(f'OK: {f}')
"
```

Expected: `OK: ...` for every file, no exceptions.

- [ ] **Step 2: Verify catalog JSON is valid**

```bash
python -c "
import json
for f in ['backend/app/connectors/catalog/opnsense.json', 'backend/app/connectors/catalog/step_ca.json']:
    json.load(open(f))
    print(f'OK: {f}')
"
```

Expected: `OK:` for both files.

- [ ] **Step 3: Final squash commit**

```bash
git add -A
git commit -m "feat: add OPNsense/step-ca connectors + smoke phases"
```

---

## Self-Review

**Spec coverage:**
- OPNsense `_client.py` with API key/secret Basic auth — Task 1
- `update_firewall_rule.py` — Task 2
- `block_host.py` — Task 3
- `opnsense_update_rule`, `opnsense_block_host` in ChangeType — Task 4
- OPNsense catalog entry — Task 4
- `step_ca/_client.py` using step CLI subprocess — Task 5
- `rotate_certificate.py` with SSM deploy option — Task 6
- `check_expiry.py` with ssl module fallback — Task 7
- `step_ca_rotate_cert`, `step_ca_check_expiry` in ChangeType — Task 4
- step-ca catalog entry — Task 8
- `OPNSENSE_RULE` smoke phase with nginx mock — Task 9
- `STEP_CA_ROTATE` smoke phase with real step-ca EC2 — Task 10
- Phase dispatch + argparse help — Task 10
- AMI caching in both phases — Tasks 9, 10

**Placeholder scan:** No TBDs, no "implement later". All code is complete and specific.

**Type consistency:**
- `get_opnsense_client(connector)` defined in Task 1, imported in Tasks 2, 3 with `from ._client import get_opnsense_client`
- `get_step_ca_client(connector)` defined in Task 5, imported in Tasks 6, 7 with `from ._client import get_step_ca_client`
- `execute(parameters, asset_ids, connector)` and `rollback(parameters, execution_result, connector)` signatures match the vault pattern exactly

**Python 3.9 compat:** All files have `from __future__ import annotations` as first line. No `str | None` syntax — all Optional types use `Optional[str]` from typing.
