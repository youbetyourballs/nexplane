# Nexplane Deploy SP2: Commercial CR Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement seven commercial CR types as catalog JSONs and Python executor files in `nexplane-deploy`, each following the standard plan → approve → execute → rollback lifecycle.

**Architecture:** Catalog JSONs live in `nexplane-deploy/catalog/{cr_type}/{action_id}.json` and are loaded by the ops instance's `ActionCatalogService` via `NEXPLANE_COMMERCIAL_CATALOG_PATH`. Executor modules live in `nexplane-deploy/executors/{cr_type}/execute.py` and are resolved by the three-part `commercial.{cr_type}.execute` loader. Every executor exports `execute` and `rollback` async functions following the standard signature.

**Tech Stack:** Python 3.11+, boto3 (EC2/S3), asyncio, pytest-asyncio, SQLAlchemy (ops DB session for `generate_setup_token`), `secrets` stdlib, `httpx` for instance health checks.

---

## File Structure

Every file to be created:

```
nexplane-deploy/
├── catalog/
│   ├── provision_instance/
│   │   └── provision_instance.json
│   ├── generate_setup_token/
│   │   └── generate_setup_token.json
│   ├── seed_trial/
│   │   └── seed_trial.json
│   ├── terminate_instance/
│   │   └── terminate_instance.json
│   ├── rotate_instance_credentials/
│   │   └── rotate_instance_credentials.json
│   ├── upgrade_instance/
│   │   └── upgrade_instance.json
│   └── reset_instance_auth/
│       └── reset_instance_auth.json
├── executors/
│   ├── __init__.py
│   ├── provision_instance/
│   │   ├── __init__.py
│   │   └── execute.py
│   ├── generate_setup_token/
│   │   ├── __init__.py
│   │   └── execute.py
│   ├── seed_trial/
│   │   ├── __init__.py
│   │   └── execute.py
│   ├── terminate_instance/
│   │   ├── __init__.py
│   │   └── execute.py
│   ├── rotate_instance_credentials/
│   │   ├── __init__.py
│   │   └── execute.py
│   ├── upgrade_instance/
│   │   ├── __init__.py
│   │   └── execute.py
│   └── reset_instance_auth/
│       ├── __init__.py
│       └── execute.py
├── smoke/
│   ├── conftest.py
│   ├── test_provision_instance_live.py
│   ├── test_generate_setup_token_live.py
│   ├── test_seed_trial_live.py
│   ├── test_terminate_instance_live.py
│   ├── test_rotate_instance_credentials_live.py
│   ├── test_upgrade_instance_live.py
│   └── test_reset_instance_auth_live.py
└── tests/
    ├── __init__.py
    ├── test_provision_instance.py
    ├── test_generate_setup_token.py
    ├── test_seed_trial.py
    ├── test_terminate_instance.py
    ├── test_rotate_instance_credentials.py
    ├── test_upgrade_instance.py
    └── test_reset_instance_auth.py
```

---

## Prerequisites

Before starting tasks, verify the skeleton directories exist:

```bash
ls nexplane-deploy/catalog/
ls nexplane-deploy/executors/
ls nexplane-deploy/smoke/
```

If `tests/` and `executors/__init__.py` are missing, create them as part of Task 0.

---

## Task 0: Repo scaffolding and test harness

**Files:** `nexplane-deploy/executors/__init__.py`, `nexplane-deploy/tests/__init__.py`, `nexplane-deploy/tests/conftest.py`

**Why first:** Every subsequent task imports from `executors.*`; the package marker and shared fixtures must exist before any test can run.

- [ ] Verify `nexplane-deploy/executors/` exists; create `executors/__init__.py` (empty)
- [ ] Create `tests/__init__.py` (empty)
- [ ] Create `tests/conftest.py` with a `mock_connector` fixture:

```python
# nexplane-deploy/tests/conftest.py
import pytest

class MockConnector:
    """Minimal connector stub — no credentials, triggers mock path in all executors."""
    credentials = {}
    id = "test-connector-id"

@pytest.fixture
def mock_connector():
    return MockConnector()
```

- [ ] Run `pytest nexplane-deploy/tests/ -q` — expect "no tests ran" (0 errors)
- [ ] Commit: `docs: scaffold nexplane-deploy tests package and mock connector fixture`

---

## Task 1: `provision_instance` — catalog JSON

**Files:** `nexplane-deploy/catalog/provision_instance/provision_instance.json`

- [ ] Write failing test to validate JSON loads and has required fields:

```python
# nexplane-deploy/tests/test_provision_instance.py
import json, pathlib

CATALOG = pathlib.Path(__file__).parent.parent / "catalog" / "provision_instance" / "provision_instance.json"

def test_catalog_loads():
    data = json.loads(CATALOG.read_text())
    assert data["connector_type"] == "commercial"
    actions = {a["action_id"]: a for a in data["actions"]}
    assert "provision_instance" in actions
    a = actions["provision_instance"]
    assert a["executor"] == "commercial.provision_instance.execute"
    param_names = {p["name"] for p in a["parameters"]}
    assert {"client_id", "mode", "artifact_version"}.issubset(param_names)
    assert a.get("rollback_action") == "terminate_instance"
```

- [ ] Run test — expect `FileNotFoundError` or `AssertionError`
- [ ] Create the catalog JSON:

```json
{
  "display_name": "Nexplane Commercial",
  "connector_type": "commercial",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "provision_instance",
      "display_name": "Provision Client Instance",
      "description": "Managed: provision EC2, install Nexplane, register asset. Self-hosted: generate presigned S3 URL and onboarding package.",
      "action_type": "change",
      "execution_tier": 3,
      "executor": "commercial.provision_instance.execute",
      "rollback_action": "terminate_instance",
      "rollback_connector_type": "commercial",
      "applicable_asset_types": ["cloud_account", "ops_instance"],
      "estimated_duration_seconds": 300,
      "parameters": [
        {"name": "client_id",        "type": "string",  "required": true,  "label": "Client ID (slug)"},
        {"name": "mode",             "type": "string",  "required": true,  "label": "Delivery mode (managed|self_hosted)"},
        {"name": "artifact_version", "type": "string",  "required": false, "label": "Image/artifact tag (default: latest)"},
        {"name": "instance_type",    "type": "string",  "required": false, "label": "EC2 instance type (managed mode, default: t3.medium)"},
        {"name": "aws_region",       "type": "string",  "required": false, "label": "AWS region (managed mode, default: us-east-1)"},
        {"name": "ami_id",           "type": "string",  "required": false, "label": "Base AMI override (managed mode)"},
        {"name": "subnet_id",        "type": "string",  "required": false, "label": "Subnet ID (managed mode)"},
        {"name": "security_group_id","type": "string",  "required": false, "label": "Security group ID (managed mode)"},
        {"name": "artifact_format",  "type": "string",  "required": false, "label": "Self-hosted artifact format (compose|ova|vmdk|helm)"},
        {"name": "onboarding_email", "type": "string",  "required": false, "label": "Send onboarding package to this email (self-hosted)"}
      ]
    }
  ]
}
```

- [ ] Run test — expect pass
- [ ] Commit: `feat(catalog): provision_instance catalog JSON`

---

## Task 2: `provision_instance` — executor

**Files:** `nexplane-deploy/executors/provision_instance/__init__.py`, `nexplane-deploy/executors/provision_instance/execute.py`

- [ ] Write failing unit test:

```python
# add to nexplane-deploy/tests/test_provision_instance.py
import asyncio, pytest, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from executors.provision_instance.execute import execute, rollback

@pytest.mark.asyncio
async def test_execute_managed_mock(mock_connector):
    result = await execute(
        {"client_id": "acme", "mode": "managed", "artifact_version": "v0.1.0"},
        [], mock_connector
    )
    assert result["success"] is True
    assert result["mode"] == "managed"
    assert "instance_id" in result
    assert result.get("mock") is True

@pytest.mark.asyncio
async def test_execute_self_hosted_mock(mock_connector):
    result = await execute(
        {"client_id": "acme", "mode": "self_hosted", "artifact_version": "v0.1.0", "artifact_format": "compose"},
        [], mock_connector
    )
    assert result["success"] is True
    assert result["mode"] == "self_hosted"
    assert "presigned_url" in result

@pytest.mark.asyncio
async def test_rollback_managed_mock(mock_connector):
    exec_result = {"success": True, "mode": "managed", "instance_id": "i-mock001", "mock": True}
    result = await rollback({"client_id": "acme", "mode": "managed"}, exec_result, mock_connector)
    assert result["success"] is True
```

- [ ] Run tests — expect `ModuleNotFoundError`
- [ ] Create `executors/provision_instance/__init__.py` (empty)
- [ ] Create `executors/provision_instance/execute.py`:

```python
"""
provision_instance executor
Managed:     EC2 RunInstances → install Docker → pull Nexplane image → start stack.
Self-hosted: generate presigned S3 URL (72h) for delivery artifact + onboarding package.
"""
import asyncio
import secrets
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_instance_id() -> str:
    return "i-mock" + secrets.token_hex(4)


async def _provision_managed(parameters: dict, connector) -> dict:
    """Launch EC2, install Docker, start Nexplane stack via SSM run-command."""
    creds = getattr(connector, "credentials", {})
    import asyncio as _asyncio

    client_id = parameters["client_id"]
    region = parameters.get("aws_region", "us-east-1")
    instance_type = parameters.get("instance_type", "t3.medium")
    artifact_version = parameters.get("artifact_version", "latest")
    ami_id = parameters.get("ami_id")  # caller must supply or we use SSM latest AL2023
    subnet_id = parameters.get("subnet_id")
    sg_id = parameters.get("security_group_id")

    loop = _asyncio.get_event_loop()

    def _run():
        import boto3
        ec2 = boto3.client(
            "ec2",
            region_name=region,
            aws_access_key_id=creds.get("access_key_id"),
            aws_secret_access_key=creds.get("secret_access_key"),
            aws_session_token=creds.get("session_token"),
        )

        # Resolve latest Amazon Linux 2023 AMI if not provided
        resolved_ami = ami_id
        if not resolved_ami:
            ssm = boto3.client(
                "ssm",
                region_name=region,
                aws_access_key_id=creds.get("access_key_id"),
                aws_secret_access_key=creds.get("secret_access_key"),
                aws_session_token=creds.get("session_token"),
            )
            resolved_ami = ssm.get_parameter(
                Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
            )["Parameter"]["Value"]

        user_data = _build_user_data(client_id, artifact_version)

        run_kwargs: dict = dict(
            ImageId=resolved_ami,
            InstanceType=instance_type,
            MinCount=1,
            MaxCount=1,
            UserData=user_data,
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": f"nexplane-{client_id}"},
                    {"Key": "ManagedBy", "Value": "nexplane-ops"},
                    {"Key": "ClientId", "Value": client_id},
                    {"Key": "ArtifactVersion", "Value": artifact_version},
                ],
            }],
        )
        if subnet_id:
            run_kwargs["SubnetId"] = subnet_id
        if sg_id:
            run_kwargs["SecurityGroupIds"] = [sg_id]

        resp = ec2.run_instances(**run_kwargs)
        inst = resp["Instances"][0]
        return inst["InstanceId"], inst.get("PrivateIpAddress", "")

    instance_id, private_ip = await loop.run_in_executor(None, _run)
    return {
        "success": True,
        "mode": "managed",
        "client_id": client_id,
        "instance_id": instance_id,
        "private_ip": private_ip,
        "region": region,
        "artifact_version": artifact_version,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": f"nexplane-{client_id}",
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "client_id": client_id,
                "instance_id": instance_id,
                "private_ip": private_ip,
                "region": region,
                "artifact_version": artifact_version,
                "managed_by": "nexplane-ops",
            },
            "tags": ["nexplane-instance", "managed", client_id],
        },
    }


def _build_user_data(client_id: str, artifact_version: str) -> str:
    """Cloud-init script: install Docker, pull Nexplane image, write .env, start stack."""
    return f"""#!/bin/bash
set -euo pipefail
# Install Docker
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

# Install Docker Compose v2
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Write .env
mkdir -p /opt/nexplane
cat > /opt/nexplane/.env <<ENV
NEXPLANE_EDITION=commercial
CLIENT_ID={client_id}
IMAGE_TAG={artifact_version}
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_hex(16))")
ENV

# Pull and start (compose file fetched from S3 or baked into AMI at /opt/nexplane/docker-compose.prod.yml)
cd /opt/nexplane
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
"""


async def _provision_self_hosted(parameters: dict, connector) -> dict:
    """Generate presigned S3 URL and onboarding package for self-hosted delivery."""
    creds = getattr(connector, "credentials", {})
    import asyncio as _asyncio

    client_id = parameters["client_id"]
    artifact_version = parameters.get("artifact_version", "latest")
    artifact_format = parameters.get("artifact_format", "compose")
    region = parameters.get("aws_region", "us-east-1")

    format_key_map = {
        "compose": f"bundles/{artifact_version}/nexplane-{artifact_version}-compose.tar.gz",
        "ova":     f"bundles/{artifact_version}/nexplane-{artifact_version}.ova",
        "vmdk":    f"bundles/{artifact_version}/nexplane-{artifact_version}.vmdk",
        "helm":    f"helm/nexplane-{artifact_version}.tgz",
    }
    s3_key = format_key_map.get(artifact_format, format_key_map["compose"])
    bucket = "nexplane-dist"

    loop = _asyncio.get_event_loop()

    def _run():
        import boto3
        s3 = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=creds.get("access_key_id"),
            aws_secret_access_key=creds.get("secret_access_key"),
            aws_session_token=creds.get("session_token"),
        )
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=72 * 3600,
        )
        return url

    presigned_url = await loop.run_in_executor(None, _run)

    onboarding = _build_onboarding_package(client_id, artifact_version, artifact_format, presigned_url)

    return {
        "success": True,
        "mode": "self_hosted",
        "client_id": client_id,
        "artifact_format": artifact_format,
        "artifact_version": artifact_version,
        "presigned_url": presigned_url,
        "presigned_url_expires_in_hours": 72,
        "s3_bucket": bucket,
        "s3_key": s3_key,
        "onboarding_package": onboarding,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_onboarding_package(client_id: str, version: str, fmt: str, presigned_url: str) -> dict:
    bootstrap_cmd = (
        f"curl -L '{presigned_url}' | tar xz && "
        f"bash bootstrap.sh --client-id {client_id} --version {version}"
        if fmt == "compose"
        else f"# Download from: {presigned_url}"
    )
    return {
        "download_url": presigned_url,
        "bootstrap_command": bootstrap_cmd,
        "prerequisites": [
            "Docker 24+ and Docker Compose v2 installed",
            "Outbound HTTPS (443) to hub.docker.com and ghcr.io",
            "Inbound TCP 443 from your admin network",
        ],
        "note": "URL expires in 72 hours. Run generate_setup_token after first boot to obtain setup URL.",
    }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    mode = parameters.get("mode", "managed")

    # Mock path: no credentials
    if not creds:
        if mode == "self_hosted":
            return {
                "success": True,
                "mode": "self_hosted",
                "client_id": parameters.get("client_id", "mock-client"),
                "presigned_url": "https://nexplane-dist.s3.amazonaws.com/bundles/mock/nexplane-mock-compose.tar.gz?X-Amz-Signature=mock",
                "onboarding_package": {"download_url": "https://mock", "bootstrap_command": "echo mock"},
                "mock": True,
            }
        return {
            "success": True,
            "mode": "managed",
            "client_id": parameters.get("client_id", "mock-client"),
            "instance_id": _mock_instance_id(),
            "private_ip": "10.0.1.100",
            "region": parameters.get("aws_region", "us-east-1"),
            "artifact_version": parameters.get("artifact_version", "latest"),
            "mock": True,
        }

    if mode == "self_hosted":
        return await _provision_self_hosted(parameters, connector)
    return await _provision_managed(parameters, connector)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """
    Managed:     terminate the EC2 instance.
    Self-hosted: presigned URL expires naturally; log revocation intent.
    """
    mode = execution_result.get("mode", parameters.get("mode", "managed"))
    client_id = execution_result.get("client_id", parameters.get("client_id", "unknown"))

    if mode == "self_hosted":
        return {
            "success": True,
            "mode": "self_hosted",
            "client_id": client_id,
            "note": "Presigned URL is time-limited; no EC2 to terminate. Setup token should be revoked via generate_setup_token rollback.",
        }

    # Managed: delegate to terminate_instance
    instance_id = execution_result.get("instance_id")
    if not instance_id:
        return {"success": False, "error": "No instance_id in execution_result; cannot terminate."}

    from executors.terminate_instance.execute import execute as terminate
    return await terminate(
        {"client_id": client_id, "instance_id": instance_id, "region": execution_result.get("region", "us-east-1")},
        [],
        connector,
    )
```

- [ ] Run tests — expect pass
- [ ] Commit: `feat(executor): provision_instance executor (managed + self-hosted)`

---

## Task 3: `terminate_instance` — catalog JSON + executor

Terminate is implemented before `generate_setup_token` because `provision_instance` rollback delegates to it.

**Files:**
- `nexplane-deploy/catalog/terminate_instance/terminate_instance.json`
- `nexplane-deploy/executors/terminate_instance/__init__.py`
- `nexplane-deploy/executors/terminate_instance/execute.py`
- `nexplane-deploy/tests/test_terminate_instance.py`

- [ ] Write failing test:

```python
# nexplane-deploy/tests/test_terminate_instance.py
import json, pathlib, asyncio, pytest, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from executors.terminate_instance.execute import execute, rollback

CATALOG = pathlib.Path(__file__).parent.parent / "catalog" / "terminate_instance" / "terminate_instance.json"

def test_catalog_loads():
    data = json.loads(CATALOG.read_text())
    actions = {a["action_id"]: a for a in data["actions"]}
    assert "terminate_instance" in actions
    a = actions["terminate_instance"]
    assert a["executor"] == "commercial.terminate_instance.execute"
    param_names = {p["name"] for p in a["parameters"]}
    assert {"client_id", "instance_id"}.issubset(param_names)

@pytest.mark.asyncio
async def test_execute_mock(mock_connector):
    result = await execute(
        {"client_id": "acme", "instance_id": "i-mock001", "region": "us-east-1"},
        [], mock_connector
    )
    assert result["success"] is True
    assert result["instance_id"] == "i-mock001"
    assert result.get("mock") is True

@pytest.mark.asyncio
async def test_rollback_is_noop(mock_connector):
    result = await rollback({}, {"instance_id": "i-mock001"}, mock_connector)
    assert "note" in result
```

- [ ] Run — expect failures
- [ ] Create catalog JSON:

```json
{
  "display_name": "Nexplane Commercial",
  "connector_type": "commercial",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "terminate_instance",
      "display_name": "Terminate Client Instance",
      "description": "Terminate managed client EC2, remove asset from ops inventory, revoke active tokens.",
      "action_type": "change",
      "execution_tier": 3,
      "executor": "commercial.terminate_instance.execute",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 60,
      "parameters": [
        {"name": "client_id",   "type": "string", "required": true,  "label": "Client ID"},
        {"name": "instance_id", "type": "string", "required": true,  "label": "EC2 instance ID"},
        {"name": "region",      "type": "string", "required": false, "label": "AWS region (default: us-east-1)"}
      ]
    }
  ]
}
```

- [ ] Create `executors/terminate_instance/__init__.py` (empty)
- [ ] Create `executors/terminate_instance/execute.py`:

```python
"""
terminate_instance executor
Terminates a managed client EC2 instance.
Rollback: terminate_instance has no undo — it IS the rollback for provision_instance.
"""
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    client_id = parameters.get("client_id", "unknown")
    instance_id = parameters["instance_id"]
    region = parameters.get("region", "us-east-1")

    if not creds:
        return {
            "success": True,
            "client_id": client_id,
            "instance_id": instance_id,
            "state": "terminated",
            "mock": True,
        }

    loop = asyncio.get_event_loop()

    def _run():
        import boto3
        ec2 = boto3.client(
            "ec2",
            region_name=region,
            aws_access_key_id=creds.get("access_key_id"),
            aws_secret_access_key=creds.get("secret_access_key"),
            aws_session_token=creds.get("session_token"),
        )
        resp = ec2.terminate_instances(InstanceIds=[instance_id])
        state = resp["TerminatingInstances"][0]["CurrentState"]["Name"]
        return state

    state = await loop.run_in_executor(None, _run)
    return {
        "success": True,
        "client_id": client_id,
        "instance_id": instance_id,
        "state": state,
        "region": region,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_remove_asset": {"asset_type": "server", "metadata_key": "instance_id", "metadata_value": instance_id},
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "success": True,
        "note": "terminate_instance is itself a rollback action; no further undo is possible.",
        "instance_id": execution_result.get("instance_id"),
    }
```

- [ ] Run tests — expect pass
- [ ] Commit: `feat(catalog,executor): terminate_instance`

---

## Task 4: `generate_setup_token` — catalog JSON + executor

**Files:**
- `nexplane-deploy/catalog/generate_setup_token/generate_setup_token.json`
- `nexplane-deploy/executors/generate_setup_token/__init__.py`
- `nexplane-deploy/executors/generate_setup_token/execute.py`
- `nexplane-deploy/tests/test_generate_setup_token.py`

**Design note:** This CR runs ON the ops instance. It writes a `setup_tokens` row directly into the target instance's DB via SQLAlchemy. The `connector` carries target instance DB URL in `connector.credentials["db_url"]`. Mock path uses no DB.

- [ ] Write failing test:

```python
# nexplane-deploy/tests/test_generate_setup_token.py
import json, pathlib, asyncio, pytest, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from executors.generate_setup_token.execute import execute, rollback

CATALOG = pathlib.Path(__file__).parent.parent / "catalog" / "generate_setup_token" / "generate_setup_token.json"

def test_catalog_loads():
    data = json.loads(CATALOG.read_text())
    actions = {a["action_id"]: a for a in data["actions"]}
    assert "generate_setup_token" in actions
    a = actions["generate_setup_token"]
    assert a["executor"] == "commercial.generate_setup_token.execute"
    param_names = {p["name"] for p in a["parameters"]}
    assert {"instance_url", "client_id"}.issubset(param_names)

@pytest.mark.asyncio
async def test_execute_mock(mock_connector):
    result = await execute(
        {"instance_url": "https://acme.nexplane.io", "client_id": "acme"},
        [], mock_connector
    )
    assert result["success"] is True
    assert result["setup_url"].startswith("https://acme.nexplane.io/setup?token=")
    assert len(result["token"]) == 64  # 32 bytes hex
    assert result.get("mock") is True

@pytest.mark.asyncio
async def test_rollback_mock(mock_connector):
    exec_result = {"token_id": "mock-uuid", "token": "abc123", "mock": True}
    result = await rollback({"instance_url": "https://acme.nexplane.io"}, exec_result, mock_connector)
    assert result["success"] is True
```

- [ ] Run — expect failures
- [ ] Create catalog JSON:

```json
{
  "display_name": "Nexplane Commercial",
  "connector_type": "commercial",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "generate_setup_token",
      "display_name": "Generate Setup Token",
      "description": "Create a 24h one-time setup token for a client instance, return setup URL.",
      "action_type": "change",
      "execution_tier": 2,
      "executor": "commercial.generate_setup_token.execute",
      "rollback_action": "revoke_setup_token",
      "rollback_connector_type": "commercial",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 5,
      "parameters": [
        {"name": "instance_url", "type": "string", "required": true,  "label": "Target instance base URL"},
        {"name": "client_id",    "type": "string", "required": true,  "label": "Client ID"},
        {"name": "org_id",       "type": "string", "required": false, "label": "Org ID to scope token (if known)"}
      ]
    }
  ]
}
```

- [ ] Create `executors/generate_setup_token/__init__.py` (empty)
- [ ] Create `executors/generate_setup_token/execute.py`:

```python
"""
generate_setup_token executor
Writes a setup_tokens row into the target instance DB via SQLAlchemy.
connector.credentials["db_url"] must be the target instance's Postgres DSN.
Called automatically by provision_instance but also standalone to re-send a link.
"""
import asyncio
import secrets
import hashlib
from datetime import datetime, timezone, timedelta


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_url = parameters["instance_url"].rstrip("/")
    client_id = parameters["client_id"]
    org_id = parameters.get("org_id")

    raw_token = secrets.token_hex(32)  # 64-char hex string
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    setup_url = f"{instance_url}/setup?token={raw_token}"

    if not creds:
        return {
            "success": True,
            "token": raw_token,
            "token_id": "mock-token-id",
            "setup_url": setup_url,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
            "mock": True,
        }

    db_url = creds.get("db_url")
    if not db_url:
        raise ValueError("connector.credentials must contain 'db_url' for generate_setup_token")

    loop = asyncio.get_event_loop()

    def _write_token():
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO setup_tokens (token_hash, instance_url, expires_at, org_id) "
                    "VALUES (:token_hash, :instance_url, :expires_at, :org_id) "
                    "RETURNING id"
                ),
                {
                    "token_hash": token_hash,
                    "instance_url": instance_url,
                    "expires_at": expires_at,
                    "org_id": org_id,
                },
            )
            conn.commit()
            token_id = str(row.fetchone()[0])
        engine.dispose()
        return token_id, expires_at

    token_id, expires_at = await loop.run_in_executor(None, _write_token)

    return {
        "success": True,
        "token": raw_token,
        "token_id": token_id,
        "setup_url": setup_url,
        "instance_url": instance_url,
        "client_id": client_id,
        "expires_at": expires_at.isoformat(),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Mark the token as used so it cannot be consumed."""
    creds = getattr(connector, "credentials", {})
    token_id = execution_result.get("token_id")

    if not token_id:
        return {"success": False, "error": "No token_id in execution_result; cannot revoke."}

    if not creds or execution_result.get("mock"):
        return {"success": True, "token_id": token_id, "revoked": True, "mock": True}

    db_url = creds.get("db_url")
    if not db_url:
        return {"success": False, "error": "No db_url in credentials."}

    loop = asyncio.get_event_loop()

    def _revoke():
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url)
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE setup_tokens SET used_at = NOW() WHERE id = :id AND used_at IS NULL"),
                {"id": token_id},
            )
            conn.commit()
        engine.dispose()

    await loop.run_in_executor(None, _revoke)
    return {
        "success": True,
        "token_id": token_id,
        "revoked": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] Run tests — expect pass
- [ ] Commit: `feat(catalog,executor): generate_setup_token`

---

## Task 5: `seed_trial` — catalog JSON + executor

**Files:**
- `nexplane-deploy/catalog/seed_trial/seed_trial.json`
- `nexplane-deploy/executors/seed_trial/__init__.py`
- `nexplane-deploy/executors/seed_trial/execute.py`
- `nexplane-deploy/tests/test_seed_trial.py`

- [ ] Write failing test:

```python
# nexplane-deploy/tests/test_seed_trial.py
import json, pathlib, asyncio, pytest, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from executors.seed_trial.execute import execute, rollback

CATALOG = pathlib.Path(__file__).parent.parent / "catalog" / "seed_trial" / "seed_trial.json"

def test_catalog_loads():
    data = json.loads(CATALOG.read_text())
    actions = {a["action_id"]: a for a in data["actions"]}
    assert "seed_trial" in actions
    a = actions["seed_trial"]
    param_names = {p["name"] for p in a["parameters"]}
    assert {"instance_url", "seed_mode"}.issubset(param_names)

@pytest.mark.asyncio
async def test_demo_mode_mock(mock_connector):
    result = await execute(
        {"instance_url": "https://acme.nexplane.io", "seed_mode": "demo"},
        [], mock_connector
    )
    assert result["success"] is True
    assert result["seed_mode"] == "demo"
    assert result.get("mock") is True

@pytest.mark.asyncio
async def test_fresh_mode_mock(mock_connector):
    result = await execute(
        {"instance_url": "https://acme.nexplane.io", "seed_mode": "fresh"},
        [], mock_connector
    )
    assert result["success"] is True
    assert result["health_check"] == "ok"

@pytest.mark.asyncio
async def test_rollback_is_informational(mock_connector):
    result = await rollback({}, {"seed_mode": "demo"}, mock_connector)
    assert "note" in result
```

- [ ] Run — expect failures
- [ ] Create catalog JSON:

```json
{
  "display_name": "Nexplane Commercial",
  "connector_type": "commercial",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "seed_trial",
      "display_name": "Seed Trial Instance",
      "description": "Demo mode: insert fixture CRs/connectors/assets. Fresh mode: verify instance health.",
      "action_type": "change",
      "execution_tier": 2,
      "executor": "commercial.seed_trial.execute",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 30,
      "parameters": [
        {"name": "instance_url", "type": "string", "required": true,  "label": "Target instance base URL"},
        {"name": "seed_mode",    "type": "string", "required": true,  "label": "Seed mode (demo|fresh)"},
        {"name": "api_token",    "type": "string", "required": false, "label": "Admin API token for seeding (if already set up)"}
      ]
    }
  ]
}
```

- [ ] Create `executors/seed_trial/__init__.py` (empty)
- [ ] Create `executors/seed_trial/execute.py`:

```python
"""
seed_trial executor
demo: POST fixture CRs, connectors, and assets to instance API.
fresh: health-check only.
Rollback is informational — demo data can be cleared by the client.
"""
import asyncio
from datetime import datetime, timezone


DEMO_FIXTURES = {
    "connectors": [
        {"connector_type": "aws",    "display_name": "Demo AWS Account",  "credentials": {"access_key_id": "DEMO", "secret_access_key": "DEMO", "region": "us-east-1"}},
        {"connector_type": "github", "display_name": "Demo GitHub Org",   "credentials": {"token": "DEMO"}},
    ],
    "assets": [
        {"name": "demo-web-server",  "asset_type": "server",       "environment": "prod", "criticality": "high",   "tags": ["demo"]},
        {"name": "demo-db-server",   "asset_type": "server",       "environment": "prod", "criticality": "high",   "tags": ["demo"]},
        {"name": "demo-s3-bucket",   "asset_type": "data_store",   "environment": "prod", "criticality": "medium", "tags": ["demo"]},
    ],
}


async def _post(session, url: str, payload: dict) -> dict:
    import httpx
    resp = await session.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


async def _health_check(instance_url: str, token: str | None) -> str:
    import httpx
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{instance_url}/api/health", headers=headers)
        resp.raise_for_status()
        return "ok"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_url = parameters["instance_url"].rstrip("/")
    seed_mode = parameters.get("seed_mode", "fresh")
    api_token = parameters.get("api_token") or creds.get("api_token")

    if not creds and not api_token:
        # Mock path
        if seed_mode == "demo":
            return {
                "success": True,
                "seed_mode": "demo",
                "fixtures_loaded": {"connectors": 2, "assets": 3},
                "mock": True,
            }
        return {
            "success": True,
            "seed_mode": "fresh",
            "health_check": "ok",
            "mock": True,
        }

    if seed_mode == "fresh":
        health = await _health_check(instance_url, api_token)
        return {
            "success": True,
            "seed_mode": "fresh",
            "health_check": health,
            "instance_url": instance_url,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    # Demo mode: seed fixtures via REST API
    import httpx
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    created = {"connectors": 0, "assets": 0}

    async with httpx.AsyncClient(base_url=instance_url, headers=headers, timeout=30) as client:
        for conn_payload in DEMO_FIXTURES["connectors"]:
            await _post(client, "/api/connectors", conn_payload)
            created["connectors"] += 1
        for asset_payload in DEMO_FIXTURES["assets"]:
            await _post(client, "/api/assets", asset_payload)
            created["assets"] += 1

    return {
        "success": True,
        "seed_mode": "demo",
        "fixtures_loaded": created,
        "instance_url": instance_url,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "success": True,
        "note": "seed_trial does not auto-rollback fixture data. Demo connectors and assets can be deleted via the instance UI.",
        "seed_mode": execution_result.get("seed_mode"),
    }
```

- [ ] Run tests — expect pass
- [ ] Commit: `feat(catalog,executor): seed_trial`

---

## Task 6: `rotate_instance_credentials` — catalog JSON + executor

**Files:**
- `nexplane-deploy/catalog/rotate_instance_credentials/rotate_instance_credentials.json`
- `nexplane-deploy/executors/rotate_instance_credentials/__init__.py`
- `nexplane-deploy/executors/rotate_instance_credentials/execute.py`
- `nexplane-deploy/tests/test_rotate_instance_credentials.py`

**Design:** Reconstitution pattern — snapshot current `SECRET_KEY` and `POSTGRES_PASSWORD` from the instance's `.env` via SSM `RunCommand` before rotating. On rollback, restore the snapshot. Rotation is done by SSM run-command that rewrites `.env` and restarts the stack.

- [ ] Write failing test:

```python
# nexplane-deploy/tests/test_rotate_instance_credentials.py
import json, pathlib, asyncio, pytest, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from executors.rotate_instance_credentials.execute import execute, rollback

CATALOG = pathlib.Path(__file__).parent.parent / "catalog" / "rotate_instance_credentials" / "rotate_instance_credentials.json"

def test_catalog_loads():
    data = json.loads(CATALOG.read_text())
    actions = {a["action_id"]: a for a in data["actions"]}
    assert "rotate_instance_credentials" in actions
    a = actions["rotate_instance_credentials"]
    assert a["executor"] == "commercial.rotate_instance_credentials.execute"
    param_names = {p["name"] for p in a["parameters"]}
    assert {"client_id", "instance_id"}.issubset(param_names)

@pytest.mark.asyncio
async def test_execute_mock(mock_connector):
    result = await execute(
        {"client_id": "acme", "instance_id": "i-mock001", "region": "us-east-1"},
        [], mock_connector
    )
    assert result["success"] is True
    assert "snapshot_secret_key_hash" in result
    assert result.get("mock") is True

@pytest.mark.asyncio
async def test_rollback_mock(mock_connector):
    exec_result = {
        "success": True,
        "snapshot_secret_key": "old-secret",
        "snapshot_db_password": "old-pass",
        "instance_id": "i-mock001",
        "region": "us-east-1",
        "mock": True,
    }
    result = await rollback({"client_id": "acme"}, exec_result, mock_connector)
    assert result["success"] is True
```

- [ ] Run — expect failures
- [ ] Create catalog JSON:

```json
{
  "display_name": "Nexplane Commercial",
  "connector_type": "commercial",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "rotate_instance_credentials",
      "display_name": "Rotate Instance Credentials",
      "description": "Rotate SECRET_KEY and DB password on a running client instance. Snapshots current credentials before rotating for rollback.",
      "action_type": "change",
      "execution_tier": 3,
      "executor": "commercial.rotate_instance_credentials.execute",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 60,
      "parameters": [
        {"name": "client_id",   "type": "string", "required": true,  "label": "Client ID"},
        {"name": "instance_id", "type": "string", "required": true,  "label": "EC2 instance ID"},
        {"name": "region",      "type": "string", "required": false, "label": "AWS region (default: us-east-1)"}
      ]
    }
  ]
}
```

- [ ] Create `executors/rotate_instance_credentials/__init__.py` (empty)
- [ ] Create `executors/rotate_instance_credentials/execute.py`:

```python
"""
rotate_instance_credentials executor
Reconstitution pattern:
  1. Snapshot current SECRET_KEY and POSTGRES_PASSWORD via SSM run-command (cat .env).
  2. Generate new values.
  3. Write new .env via SSM run-command.
  4. Restart stack.
Rollback: write snapshot values back via SSM and restart.
"""
import asyncio
import hashlib
import secrets
from datetime import datetime, timezone


def _new_secret_key() -> str:
    return secrets.token_hex(32)


def _new_db_password() -> str:
    return secrets.token_hex(16)


async def _ssm_run(creds: dict, instance_id: str, region: str, commands: list[str]) -> str:
    loop = asyncio.get_event_loop()

    def _run():
        import boto3, time
        ssm = boto3.client(
            "ssm",
            region_name=region,
            aws_access_key_id=creds.get("access_key_id"),
            aws_secret_access_key=creds.get("secret_access_key"),
            aws_session_token=creds.get("session_token"),
        )
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands},
        )
        command_id = resp["Command"]["CommandId"]
        for _ in range(30):
            time.sleep(2)
            inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            status = inv["Status"]
            if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                if status != "Success":
                    raise RuntimeError(f"SSM command failed: {inv.get('StandardErrorContent', '')}")
                return inv.get("StandardOutputContent", "")
        raise TimeoutError(f"SSM command {command_id} did not complete in 60s")

    return await loop.run_in_executor(None, _run)


def _parse_env_value(env_output: str, key: str) -> str:
    for line in env_output.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    client_id = parameters["client_id"]
    instance_id = parameters["instance_id"]
    region = parameters.get("region", "us-east-1")

    new_secret_key = _new_secret_key()
    new_db_password = _new_db_password()

    if not creds:
        return {
            "success": True,
            "client_id": client_id,
            "instance_id": instance_id,
            "snapshot_secret_key_hash": hashlib.sha256(b"mock-old-secret").hexdigest(),
            "snapshot_db_password_hash": hashlib.sha256(b"mock-old-pass").hexdigest(),
            "new_secret_key_hash": hashlib.sha256(new_secret_key.encode()).hexdigest(),
            "mock": True,
        }

    # Step 1: snapshot
    env_output = await _ssm_run(creds, instance_id, region, ["cat /opt/nexplane/.env"])
    old_secret_key = _parse_env_value(env_output, "SECRET_KEY")
    old_db_password = _parse_env_value(env_output, "POSTGRES_PASSWORD")

    # Step 2+3: write new values
    rotate_commands = [
        f"sed -i 's|^SECRET_KEY=.*|SECRET_KEY={new_secret_key}|' /opt/nexplane/.env",
        f"sed -i 's|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD={new_db_password}|' /opt/nexplane/.env",
        "cd /opt/nexplane && docker compose -f docker-compose.prod.yml up -d --force-recreate",
    ]
    await _ssm_run(creds, instance_id, region, rotate_commands)

    return {
        "success": True,
        "client_id": client_id,
        "instance_id": instance_id,
        "region": region,
        # Store snapshots in result for rollback — these are passed to rollback()
        "snapshot_secret_key": old_secret_key,
        "snapshot_db_password": old_db_password,
        "snapshot_secret_key_hash": hashlib.sha256(old_secret_key.encode()).hexdigest(),
        "snapshot_db_password_hash": hashlib.sha256(old_db_password.encode()).hexdigest(),
        "new_secret_key_hash": hashlib.sha256(new_secret_key.encode()).hexdigest(),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = execution_result.get("instance_id", parameters.get("instance_id"))
    region = execution_result.get("region", parameters.get("region", "us-east-1"))
    old_secret_key = execution_result.get("snapshot_secret_key")
    old_db_password = execution_result.get("snapshot_db_password")

    if not old_secret_key or not old_db_password:
        return {"success": False, "error": "Snapshot credentials missing from execution_result; cannot rollback."}

    if not creds or execution_result.get("mock"):
        return {"success": True, "rolled_back": True, "mock": True}

    restore_commands = [
        f"sed -i 's|^SECRET_KEY=.*|SECRET_KEY={old_secret_key}|' /opt/nexplane/.env",
        f"sed -i 's|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD={old_db_password}|' /opt/nexplane/.env",
        "cd /opt/nexplane && docker compose -f docker-compose.prod.yml up -d --force-recreate",
    ]
    await _ssm_run(creds, instance_id, region, restore_commands)

    return {
        "success": True,
        "rolled_back": True,
        "instance_id": instance_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] Run tests — expect pass
- [ ] Commit: `feat(catalog,executor): rotate_instance_credentials`

---

## Task 7: `upgrade_instance` — catalog JSON + executor

**Files:**
- `nexplane-deploy/catalog/upgrade_instance/upgrade_instance.json`
- `nexplane-deploy/executors/upgrade_instance/__init__.py`
- `nexplane-deploy/executors/upgrade_instance/execute.py`
- `nexplane-deploy/tests/test_upgrade_instance.py`

**Design:** SSM run-command on the target EC2. Before pulling the new tag, snapshot the current `IMAGE_TAG` from `.env` so rollback can restore it.

- [ ] Write failing test:

```python
# nexplane-deploy/tests/test_upgrade_instance.py
import json, pathlib, asyncio, pytest, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from executors.upgrade_instance.execute import execute, rollback

CATALOG = pathlib.Path(__file__).parent.parent / "catalog" / "upgrade_instance" / "upgrade_instance.json"

def test_catalog_loads():
    data = json.loads(CATALOG.read_text())
    actions = {a["action_id"]: a for a in data["actions"]}
    assert "upgrade_instance" in actions
    a = actions["upgrade_instance"]
    assert a["executor"] == "commercial.upgrade_instance.execute"
    param_names = {p["name"] for p in a["parameters"]}
    assert {"client_id", "instance_id", "target_version"}.issubset(param_names)

@pytest.mark.asyncio
async def test_execute_mock(mock_connector):
    result = await execute(
        {"client_id": "acme", "instance_id": "i-mock001", "target_version": "v0.2.0", "region": "us-east-1"},
        [], mock_connector
    )
    assert result["success"] is True
    assert result["target_version"] == "v0.2.0"
    assert "previous_version" in result

@pytest.mark.asyncio
async def test_rollback_mock(mock_connector):
    exec_result = {"instance_id": "i-mock001", "region": "us-east-1", "previous_version": "v0.1.0", "mock": True}
    result = await rollback({"client_id": "acme"}, exec_result, mock_connector)
    assert result["success"] is True
```

- [ ] Run — expect failures
- [ ] Create catalog JSON:

```json
{
  "display_name": "Nexplane Commercial",
  "connector_type": "commercial",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "upgrade_instance",
      "display_name": "Upgrade Client Instance",
      "description": "Pull new image tag on client EC2, rolling restart. Rollback: restore previous tag.",
      "action_type": "change",
      "execution_tier": 3,
      "executor": "commercial.upgrade_instance.execute",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 120,
      "parameters": [
        {"name": "client_id",      "type": "string", "required": true,  "label": "Client ID"},
        {"name": "instance_id",    "type": "string", "required": true,  "label": "EC2 instance ID"},
        {"name": "target_version", "type": "string", "required": true,  "label": "Target image tag (e.g. v0.2.0)"},
        {"name": "region",         "type": "string", "required": false, "label": "AWS region (default: us-east-1)"}
      ]
    }
  ]
}
```

- [ ] Create `executors/upgrade_instance/__init__.py` (empty)
- [ ] Create `executors/upgrade_instance/execute.py`:

```python
"""
upgrade_instance executor
1. Snapshot current IMAGE_TAG from .env (for rollback).
2. Update IMAGE_TAG in .env to target_version.
3. docker compose pull + up -d (rolling restart).
Rollback: restore previous IMAGE_TAG and restart.
"""
import asyncio
from datetime import datetime, timezone


async def _ssm_run(creds: dict, instance_id: str, region: str, commands: list[str]) -> str:
    """Reuse the same SSM helper pattern."""
    loop = asyncio.get_event_loop()

    def _run():
        import boto3, time
        ssm = boto3.client(
            "ssm", region_name=region,
            aws_access_key_id=creds.get("access_key_id"),
            aws_secret_access_key=creds.get("secret_access_key"),
            aws_session_token=creds.get("session_token"),
        )
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands},
        )
        command_id = resp["Command"]["CommandId"]
        for _ in range(60):
            time.sleep(2)
            inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            status = inv["Status"]
            if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                if status != "Success":
                    raise RuntimeError(f"SSM failed: {inv.get('StandardErrorContent', '')}")
                return inv.get("StandardOutputContent", "")
        raise TimeoutError("SSM command did not complete in 120s")

    return await loop.run_in_executor(None, _run)


def _parse_env_value(env_output: str, key: str) -> str:
    for line in env_output.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return "unknown"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    client_id = parameters["client_id"]
    instance_id = parameters["instance_id"]
    target_version = parameters["target_version"]
    region = parameters.get("region", "us-east-1")

    if not creds:
        return {
            "success": True,
            "client_id": client_id,
            "instance_id": instance_id,
            "previous_version": "v0.1.0",
            "target_version": target_version,
            "mock": True,
        }

    env_output = await _ssm_run(creds, instance_id, region, ["cat /opt/nexplane/.env"])
    previous_version = _parse_env_value(env_output, "IMAGE_TAG")

    upgrade_commands = [
        f"sed -i 's|^IMAGE_TAG=.*|IMAGE_TAG={target_version}|' /opt/nexplane/.env",
        f"cd /opt/nexplane && docker compose -f docker-compose.prod.yml pull",
        f"cd /opt/nexplane && docker compose -f docker-compose.prod.yml up -d",
    ]
    await _ssm_run(creds, instance_id, region, upgrade_commands)

    return {
        "success": True,
        "client_id": client_id,
        "instance_id": instance_id,
        "previous_version": previous_version,
        "target_version": target_version,
        "region": region,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = execution_result.get("instance_id", parameters.get("instance_id"))
    region = execution_result.get("region", parameters.get("region", "us-east-1"))
    previous_version = execution_result.get("previous_version")

    if not previous_version or previous_version == "unknown":
        return {"success": False, "error": "previous_version not available in execution_result."}

    if not creds or execution_result.get("mock"):
        return {"success": True, "rolled_back": True, "restored_version": previous_version, "mock": True}

    restore_commands = [
        f"sed -i 's|^IMAGE_TAG=.*|IMAGE_TAG={previous_version}|' /opt/nexplane/.env",
        f"cd /opt/nexplane && docker compose -f docker-compose.prod.yml pull",
        f"cd /opt/nexplane && docker compose -f docker-compose.prod.yml up -d",
    ]
    await _ssm_run(creds, instance_id, region, restore_commands)

    return {
        "success": True,
        "rolled_back": True,
        "restored_version": previous_version,
        "instance_id": instance_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] Run tests — expect pass
- [ ] Commit: `feat(catalog,executor): upgrade_instance`

---

## Task 8: `reset_instance_auth` — catalog JSON + executor

**Files:**
- `nexplane-deploy/catalog/reset_instance_auth/reset_instance_auth.json`
- `nexplane-deploy/executors/reset_instance_auth/__init__.py`
- `nexplane-deploy/executors/reset_instance_auth/execute.py`
- `nexplane-deploy/tests/test_reset_instance_auth.py`

**Design:** Calls a gated endpoint on the target instance (`POST /api/admin/reset-auth`) that only accepts requests signed by the ops instance's identity (Bearer token from `connector.credentials["ops_signing_token"]`). Requires a support ticket reference. Returns a recovery token.

- [ ] Write failing test:

```python
# nexplane-deploy/tests/test_reset_instance_auth.py
import json, pathlib, asyncio, pytest, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from executors.reset_instance_auth.execute import execute, rollback

CATALOG = pathlib.Path(__file__).parent.parent / "catalog" / "reset_instance_auth" / "reset_instance_auth.json"

def test_catalog_loads():
    data = json.loads(CATALOG.read_text())
    actions = {a["action_id"]: a for a in data["actions"]}
    assert "reset_instance_auth" in actions
    a = actions["reset_instance_auth"]
    assert a["executor"] == "commercial.reset_instance_auth.execute"
    param_names = {p["name"] for p in a["parameters"]}
    assert {"instance_url", "support_ticket_ref", "admin_email"}.issubset(param_names)

@pytest.mark.asyncio
async def test_execute_mock(mock_connector):
    result = await execute(
        {
            "instance_url": "https://acme.nexplane.io",
            "support_ticket_ref": "SUPPORT-123",
            "admin_email": "admin@acme.com",
        },
        [], mock_connector
    )
    assert result["success"] is True
    assert "recovery_token" in result
    assert result.get("mock") is True

@pytest.mark.asyncio
async def test_missing_ticket_raises(mock_connector):
    with pytest.raises(ValueError, match="support_ticket_ref"):
        await execute(
            {"instance_url": "https://acme.nexplane.io", "admin_email": "admin@acme.com"},
            [], mock_connector
        )

@pytest.mark.asyncio
async def test_rollback_informational(mock_connector):
    result = await rollback({}, {"recovery_token": "mock-token"}, mock_connector)
    assert "note" in result
```

- [ ] Run — expect failures
- [ ] Create catalog JSON:

```json
{
  "display_name": "Nexplane Commercial",
  "connector_type": "commercial",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "reset_instance_auth",
      "display_name": "Reset Instance Auth (Support)",
      "description": "Reset auth mode to local, generate recovery token. Requires support ticket reference. Ops instance identity required.",
      "action_type": "change",
      "execution_tier": 3,
      "executor": "commercial.reset_instance_auth.execute",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 15,
      "parameters": [
        {"name": "instance_url",       "type": "string", "required": true,  "label": "Target instance base URL"},
        {"name": "support_ticket_ref", "type": "string", "required": true,  "label": "Support ticket reference (audit trail)"},
        {"name": "admin_email",        "type": "string", "required": true,  "label": "Local admin account to enable/create"},
        {"name": "disable_idp",        "type": "boolean","required": false, "label": "Force-disable IdP auth mode (default: true)"}
      ]
    }
  ]
}
```

- [ ] Create `executors/reset_instance_auth/__init__.py` (empty)
- [ ] Create `executors/reset_instance_auth/execute.py`:

```python
"""
reset_instance_auth executor
Calls POST /api/admin/reset-auth on the target instance.
Request is signed with ops_signing_token from connector credentials.
Requires support_ticket_ref for audit trail.
Rollback is informational — auth reset cannot be undone automatically.
"""
import asyncio
import secrets
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_url = parameters.get("instance_url", "").rstrip("/")
    support_ticket_ref = parameters.get("support_ticket_ref")
    admin_email = parameters["admin_email"]
    disable_idp = parameters.get("disable_idp", True)

    if not support_ticket_ref:
        raise ValueError("support_ticket_ref is required for reset_instance_auth (audit trail)")
    if not instance_url:
        raise ValueError("instance_url is required")

    if not creds:
        recovery_token = secrets.token_hex(24)
        return {
            "success": True,
            "instance_url": instance_url,
            "support_ticket_ref": support_ticket_ref,
            "admin_email": admin_email,
            "recovery_token": recovery_token,
            "auth_mode_reset_to": "local",
            "mock": True,
        }

    ops_signing_token = creds.get("ops_signing_token")
    if not ops_signing_token:
        raise ValueError("connector.credentials must contain 'ops_signing_token' for reset_instance_auth")

    import httpx
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{instance_url}/api/admin/reset-auth",
            headers={
                "Authorization": f"Bearer {ops_signing_token}",
                "X-Ops-Identity": "nexplane-ops",
                "Content-Type": "application/json",
            },
            json={
                "admin_email": admin_email,
                "support_ticket_ref": support_ticket_ref,
                "disable_idp": disable_idp,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "success": True,
        "instance_url": instance_url,
        "support_ticket_ref": support_ticket_ref,
        "admin_email": admin_email,
        "recovery_token": data.get("recovery_token"),
        "auth_mode_reset_to": "local",
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "success": True,
        "note": (
            "reset_instance_auth cannot be automatically rolled back. "
            "To restore IdP auth, re-activate the IdP from the instance admin UI "
            "using the recovery token returned in execution_result."
        ),
        "recovery_token": execution_result.get("recovery_token"),
    }
```

- [ ] Run tests — expect pass
- [ ] Commit: `feat(catalog,executor): reset_instance_auth`

---

## Task 9: Smoke test stubs

**Files:** `nexplane-deploy/smoke/conftest.py` and one stub per CR type.

Smoke tests follow the same phase/watchdog pattern as `test_aws_live.py`. Each file contains:
1. A phase constant marking which smoke phase this covers
2. A `pytest.mark.skipif` guard that skips unless `NEXPLANE_SMOKE=1` is set
3. A stub that will be filled in when the CI test ops instance exists (SP2 smoke is deferred until after bootstrap in SP1 is confirmed working)

- [ ] Create `nexplane-deploy/smoke/conftest.py`:

```python
# nexplane-deploy/smoke/conftest.py
"""
Shared fixtures for commercial CR smoke tests.
All smoke tests require:
  - NEXPLANE_SMOKE=1 env var
  - NEXPLANE_OPS_URL: URL of the CI test ops instance
  - NEXPLANE_OPS_TOKEN: admin API token for the CI test ops instance
  - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY: ops instance IAM role creds
"""
import os
import pytest
import httpx


def ops_url() -> str:
    return os.environ.get("NEXPLANE_OPS_URL", "").rstrip("/")


def ops_token() -> str:
    return os.environ.get("NEXPLANE_OPS_TOKEN", "")


@pytest.fixture(scope="session")
def ops_client():
    """httpx client pre-authenticated to the CI ops instance."""
    url = ops_url()
    token = ops_token()
    if not url or not token:
        pytest.skip("NEXPLANE_OPS_URL and NEXPLANE_OPS_TOKEN required for smoke tests")
    return httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=300)


@pytest.fixture(scope="session")
def aws_creds():
    return {
        "access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", ""),
        "secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    }


def smoke_enabled():
    return os.environ.get("NEXPLANE_SMOKE") == "1"
```

- [ ] Create `nexplane-deploy/smoke/test_provision_instance_live.py`:

```python
"""
PROVISION_INSTANCE smoke phase
Provisions a real EC2, verifies it's reachable, verifies setup token works.
Deferred: runs after SP1 bootstrap and CI test ops instance exist.
"""
import pytest

SMOKE_PHASE = "PROVISION_INSTANCE"

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("NEXPLANE_SMOKE"),
    reason="Set NEXPLANE_SMOKE=1 to run live smoke tests",
)


def test_provision_instance_managed_live(ops_client, aws_creds):
    """
    Phase: PROVISION_INSTANCE (managed)
    1. Create provision_instance CR on ops instance
    2. Approve and execute
    3. Verify instance_id returned and EC2 is running
    4. Verify /api/health on new instance returns 200
    5. Verify setup token generates valid setup URL
    6. Rollback: terminate_instance CR, verify EC2 terminated
    """
    pytest.skip("PROVISION_INSTANCE smoke phase stub — implement after SP1 bootstrap confirmed")


def test_provision_instance_self_hosted_live(ops_client, aws_creds):
    """
    Phase: PROVISION_INSTANCE (self-hosted)
    1. Create provision_instance CR with mode=self_hosted
    2. Verify presigned_url is a valid S3 URL (HEAD request returns 200)
    3. Verify onboarding package structure
    """
    pytest.skip("PROVISION_INSTANCE self-hosted smoke stub — implement after S3 artifacts exist (SP5)")
```

- [ ] Create `nexplane-deploy/smoke/test_generate_setup_token_live.py`:

```python
"""
GENERATE_SETUP_TOKEN smoke phase
"""
import pytest

SMOKE_PHASE = "GENERATE_SETUP_TOKEN"

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("NEXPLANE_SMOKE"),
    reason="Set NEXPLANE_SMOKE=1 to run live smoke tests",
)


def test_generate_setup_token_live(ops_client):
    """
    1. Create generate_setup_token CR for a known client instance
    2. Approve and execute
    3. Verify setup_url is accessible (GET returns redirect to /setup)
    4. Rollback: token is revoked (verify token cannot be re-used)
    """
    pytest.skip("GENERATE_SETUP_TOKEN smoke stub — requires live client instance")
```

- [ ] Create `nexplane-deploy/smoke/test_seed_trial_live.py`:

```python
"""
TRIAL_DEMO_SEED and TRIAL_FRESH smoke phases
"""
import pytest

SMOKE_PHASE = "TRIAL_SEED"

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("NEXPLANE_SMOKE"),
    reason="Set NEXPLANE_SMOKE=1 to run live smoke tests",
)


def test_seed_trial_demo_live(ops_client):
    """
    TRIAL_DEMO_SEED phase:
    1. Seed a freshly provisioned instance in demo mode
    2. Verify /api/connectors returns >= 2 entries
    3. Verify /api/assets returns >= 3 entries
    """
    pytest.skip("TRIAL_DEMO_SEED smoke stub — requires live client instance")


def test_seed_trial_fresh_live(ops_client):
    """
    TRIAL_FRESH phase:
    1. Seed in fresh mode
    2. Verify /api/health returns ok
    3. Verify connectors list is empty (no fixtures)
    """
    pytest.skip("TRIAL_FRESH smoke stub — requires live client instance")
```

- [ ] Create `nexplane-deploy/smoke/test_terminate_instance_live.py`:

```python
"""
TERMINATE_INSTANCE smoke phase
"""
import pytest

SMOKE_PHASE = "TERMINATE_INSTANCE"

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("NEXPLANE_SMOKE"),
    reason="Set NEXPLANE_SMOKE=1 to run live smoke tests",
)


def test_terminate_instance_live(ops_client, aws_creds):
    """
    1. Provision a real EC2 (or use one from PROVISION_INSTANCE phase)
    2. Execute terminate_instance CR
    3. Verify EC2 state is terminated via boto3
    4. Verify asset removed from ops inventory
    """
    pytest.skip("TERMINATE_INSTANCE smoke stub — runs as cleanup in PROVISION_INSTANCE phase")
```

- [ ] Create `nexplane-deploy/smoke/test_rotate_instance_credentials_live.py`:

```python
"""
ROTATE_INSTANCE_CREDENTIALS smoke phase
"""
import pytest

SMOKE_PHASE = "ROTATE_INSTANCE_CREDENTIALS"

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("NEXPLANE_SMOKE"),
    reason="Set NEXPLANE_SMOKE=1 to run live smoke tests",
)


def test_rotate_credentials_live(ops_client, aws_creds):
    """
    ROTATE_INSTANCE_CREDENTIALS phase:
    1. Execute rotate_instance_credentials CR on a running test instance
    2. Verify instance responds on /api/health after restart
    3. Verify old SECRET_KEY no longer works (API call with old token fails)
    4. Rollback: restore snapshot credentials, verify instance healthy again
    """
    pytest.skip("ROTATE_INSTANCE_CREDENTIALS smoke stub — requires running client instance")
```

- [ ] Create `nexplane-deploy/smoke/test_upgrade_instance_live.py`:

```python
"""
UPGRADE_INSTANCE smoke phase
"""
import pytest

SMOKE_PHASE = "UPGRADE_INSTANCE"

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("NEXPLANE_SMOKE"),
    reason="Set NEXPLANE_SMOKE=1 to run live smoke tests",
)


def test_upgrade_instance_live(ops_client, aws_creds):
    """
    UPGRADE_INSTANCE phase:
    1. Execute upgrade_instance CR targeting a known newer tag
    2. Verify /api/health returns ok after restart
    3. Verify IMAGE_TAG in .env matches target_version
    4. Rollback: restore previous tag, verify instance healthy
    """
    pytest.skip("UPGRADE_INSTANCE smoke stub — requires running client instance with two available tags")
```

- [ ] Create `nexplane-deploy/smoke/test_reset_instance_auth_live.py`:

```python
"""
RESET_INSTANCE_AUTH smoke phase
"""
import pytest

SMOKE_PHASE = "RESET_INSTANCE_AUTH"

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("NEXPLANE_SMOKE"),
    reason="Set NEXPLANE_SMOKE=1 to run live smoke tests",
)


def test_reset_instance_auth_live(ops_client):
    """
    RESET_INSTANCE_AUTH phase:
    1. Configure IdP on test instance, switch to IdP auth mode
    2. Execute reset_instance_auth CR with support_ticket_ref
    3. Verify auth mode returned to local
    4. Verify recovery token allows login
    5. Verify rollback result is informational (no auto-undo)
    """
    pytest.skip("RESET_INSTANCE_AUTH smoke stub — requires instance with IdP configured (SP3)")
```

- [ ] Run `pytest nexplane-deploy/smoke/ -q` — all should be skipped (no errors)
- [ ] Commit: `feat(smoke): commercial CR smoke test stubs`

---

## Task 10: Wire commercial catalog loader — verify catalog JSON format

The commercial catalog uses a flat-file layout (`catalog/{cr_type}/{action_id}.json`) rather than one JSON per connector. Verify the `ActionCatalogService` in the core `nexplane` repo can load these files from a nested directory, or document the required format.

**Files:** `nexplane-deploy/catalog/README.md` (documentation only, not code)

- [ ] Run the following to confirm what the ops instance loader expects:

```bash
# In nexplane core repo
grep -n "glob" backend/app/connectors/catalog_service.py
```

The loader calls `catalog_dir.glob("*.json")` — it expects all catalog JSONs in a single flat directory. This means the ops instance must either:
- Mount `nexplane-deploy/catalog/` flat (one JSON per CR type, no subdirs), OR
- The loader needs an update to support nested per-CR-type directories.

**Decision:** Use the flat layout approach — each `catalog/{cr_type}.json` file (not subdirectory). Rename the catalog file structure accordingly.

- [ ] Update file structure: move `catalog/provision_instance/provision_instance.json` → `catalog/provision_instance.json`, and so on for all 7 CR types.
- [ ] Update Task 1-8 catalog paths in the tests to match: `catalog/{cr_type}.json`
- [ ] Verify `connector_type` in all catalog JSONs is `"commercial"` — since all 7 CRs share one connector type, they must be merged into ONE catalog JSON (`catalog/commercial.json`) that lists all actions.

**Revised catalog layout (single file):**

```
nexplane-deploy/catalog/commercial.json   # all 7 CR action definitions
```

Since Tasks 1-8 already create individual JSON files for clarity, the final wiring step is to merge them:

- [ ] Write failing integration test:

```python
# nexplane-deploy/tests/test_catalog_integration.py
import json, pathlib

CATALOG = pathlib.Path(__file__).parent.parent / "catalog" / "commercial.json"

def test_all_actions_present():
    data = json.loads(CATALOG.read_text())
    assert data["connector_type"] == "commercial"
    action_ids = {a["action_id"] for a in data["actions"]}
    expected = {
        "provision_instance", "generate_setup_token", "seed_trial",
        "terminate_instance", "rotate_instance_credentials",
        "upgrade_instance", "reset_instance_auth",
    }
    assert expected == action_ids

def test_all_executors_follow_naming():
    data = json.loads(CATALOG.read_text())
    for action in data["actions"]:
        assert action["executor"].startswith("commercial."), action["action_id"]
        parts = action["executor"].split(".")
        assert len(parts) == 3, f"executor must be commercial.<cr_type>.execute: {action['executor']}"
        assert parts[2] == "execute"
```

- [ ] Run — expect `FileNotFoundError`
- [ ] Create `nexplane-deploy/catalog/commercial.json` merging all 7 action definitions (copy from Tasks 1-8, combine into single `actions` array):

```json
{
  "display_name": "Nexplane Commercial Operations",
  "connector_type": "commercial",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "provision_instance",
      "display_name": "Provision Client Instance",
      "description": "Managed: provision EC2, install Nexplane, register asset. Self-hosted: generate presigned S3 URL and onboarding package.",
      "action_type": "change",
      "execution_tier": 3,
      "executor": "commercial.provision_instance.execute",
      "rollback_action": "terminate_instance",
      "rollback_connector_type": "commercial",
      "applicable_asset_types": ["cloud_account", "ops_instance"],
      "estimated_duration_seconds": 300,
      "parameters": [
        {"name": "client_id",         "type": "string",  "required": true,  "label": "Client ID (slug)"},
        {"name": "mode",              "type": "string",  "required": true,  "label": "Delivery mode (managed|self_hosted)"},
        {"name": "artifact_version",  "type": "string",  "required": false, "label": "Image/artifact tag (default: latest)"},
        {"name": "instance_type",     "type": "string",  "required": false, "label": "EC2 instance type (managed, default: t3.medium)"},
        {"name": "aws_region",        "type": "string",  "required": false, "label": "AWS region (managed, default: us-east-1)"},
        {"name": "ami_id",            "type": "string",  "required": false, "label": "Base AMI override (managed)"},
        {"name": "subnet_id",         "type": "string",  "required": false, "label": "Subnet ID (managed)"},
        {"name": "security_group_id", "type": "string",  "required": false, "label": "Security group ID (managed)"},
        {"name": "artifact_format",   "type": "string",  "required": false, "label": "Self-hosted format (compose|ova|vmdk|helm)"},
        {"name": "onboarding_email",  "type": "string",  "required": false, "label": "Send onboarding package to email (self-hosted)"}
      ]
    },
    {
      "action_id": "generate_setup_token",
      "display_name": "Generate Setup Token",
      "description": "Create a 24h one-time setup token for a client instance, return setup URL.",
      "action_type": "change",
      "execution_tier": 2,
      "executor": "commercial.generate_setup_token.execute",
      "rollback_action": "revoke_setup_token",
      "rollback_connector_type": "commercial",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 5,
      "parameters": [
        {"name": "instance_url", "type": "string", "required": true,  "label": "Target instance base URL"},
        {"name": "client_id",    "type": "string", "required": true,  "label": "Client ID"},
        {"name": "org_id",       "type": "string", "required": false, "label": "Org ID to scope token"}
      ]
    },
    {
      "action_id": "seed_trial",
      "display_name": "Seed Trial Instance",
      "description": "Demo mode: insert fixture CRs/connectors/assets. Fresh mode: verify instance health.",
      "action_type": "change",
      "execution_tier": 2,
      "executor": "commercial.seed_trial.execute",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 30,
      "parameters": [
        {"name": "instance_url", "type": "string", "required": true,  "label": "Target instance base URL"},
        {"name": "seed_mode",    "type": "string", "required": true,  "label": "Seed mode (demo|fresh)"},
        {"name": "api_token",    "type": "string", "required": false, "label": "Admin API token for seeding"}
      ]
    },
    {
      "action_id": "terminate_instance",
      "display_name": "Terminate Client Instance",
      "description": "Terminate managed client EC2, remove asset from ops inventory, revoke active tokens.",
      "action_type": "change",
      "execution_tier": 3,
      "executor": "commercial.terminate_instance.execute",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 60,
      "parameters": [
        {"name": "client_id",   "type": "string", "required": true,  "label": "Client ID"},
        {"name": "instance_id", "type": "string", "required": true,  "label": "EC2 instance ID"},
        {"name": "region",      "type": "string", "required": false, "label": "AWS region (default: us-east-1)"}
      ]
    },
    {
      "action_id": "rotate_instance_credentials",
      "display_name": "Rotate Instance Credentials",
      "description": "Rotate SECRET_KEY and DB password on a running client instance. Snapshots credentials before rotating for rollback.",
      "action_type": "change",
      "execution_tier": 3,
      "executor": "commercial.rotate_instance_credentials.execute",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 60,
      "parameters": [
        {"name": "client_id",   "type": "string", "required": true,  "label": "Client ID"},
        {"name": "instance_id", "type": "string", "required": true,  "label": "EC2 instance ID"},
        {"name": "region",      "type": "string", "required": false, "label": "AWS region (default: us-east-1)"}
      ]
    },
    {
      "action_id": "upgrade_instance",
      "display_name": "Upgrade Client Instance",
      "description": "Pull new image tag on client EC2, rolling restart. Rollback: restore previous tag.",
      "action_type": "change",
      "execution_tier": 3,
      "executor": "commercial.upgrade_instance.execute",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 120,
      "parameters": [
        {"name": "client_id",      "type": "string", "required": true,  "label": "Client ID"},
        {"name": "instance_id",    "type": "string", "required": true,  "label": "EC2 instance ID"},
        {"name": "target_version", "type": "string", "required": true,  "label": "Target image tag (e.g. v0.2.0)"},
        {"name": "region",         "type": "string", "required": false, "label": "AWS region (default: us-east-1)"}
      ]
    },
    {
      "action_id": "reset_instance_auth",
      "display_name": "Reset Instance Auth (Support)",
      "description": "Reset auth mode to local, generate recovery token. Requires support ticket reference.",
      "action_type": "change",
      "execution_tier": 3,
      "executor": "commercial.reset_instance_auth.execute",
      "applicable_asset_types": ["server", "ops_instance"],
      "estimated_duration_seconds": 15,
      "parameters": [
        {"name": "instance_url",       "type": "string",  "required": true,  "label": "Target instance base URL"},
        {"name": "support_ticket_ref", "type": "string",  "required": true,  "label": "Support ticket reference"},
        {"name": "admin_email",        "type": "string",  "required": true,  "label": "Local admin account to enable/create"},
        {"name": "disable_idp",        "type": "boolean", "required": false, "label": "Force-disable IdP auth mode (default: true)"}
      ]
    }
  ]
}
```

- [ ] Run integration test — expect pass
- [ ] Run full test suite: `pytest nexplane-deploy/tests/ -q` — all unit tests pass
- [ ] Commit: `feat(catalog): merged commercial.json with all 7 CR action definitions`

---

## Task 11: Final validation sweep

- [ ] Run `pytest nexplane-deploy/tests/ -v` — all tests pass
- [ ] Run `pytest nexplane-deploy/smoke/ -q` — all tests skipped (0 failures)
- [ ] Verify executor naming: `grep -r "executor" nexplane-deploy/catalog/commercial.json | grep -v "commercial\."` — expect no output
- [ ] Verify every executor module has both `execute` and `rollback` functions:

```bash
for d in provision_instance generate_setup_token seed_trial terminate_instance rotate_instance_credentials upgrade_instance reset_instance_auth; do
  echo "=== $d ===" && grep -n "^async def " nexplane-deploy/executors/$d/execute.py
done
```

- [ ] Commit: `chore: SP2 final validation sweep passes`

---

## Deferred (not in this plan)

- Full live smoke phases (require SP1 CI test ops instance to exist)
- `generate_setup_token` integration with SP3's `setup_tokens` DB table (SP3 creates the table schema)
- `reset_instance_auth` gated endpoint on the core `nexplane` instance (SP3 implements the endpoint)
- Self-hosted artifact URLs (S3 bucket and artifacts are SP5)
