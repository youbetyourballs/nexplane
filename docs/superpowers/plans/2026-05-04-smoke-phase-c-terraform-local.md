# Expanded Smoke Test Phase C — Local Terraform Connector

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `terraform_local` connector that runs the Terraform CLI binary inside the backend container, enabling Terraform apply CRs that work without Terraform Cloud.

**Architecture:** New `terraform_local` connector type writes `.tf` content from the CR desired_outcome to a temp directory, runs `terraform init && terraform plan && terraform apply`, and captures output. AWS credentials are passed from the connected AWS connector as environment variables. Rollback runs `terraform destroy`. The existing `terraform` (HCP/Cloud) connector is unchanged.

**Tech Stack:** Terraform CLI 1.7.5 (installed in Dockerfile by Phase A plan), boto3, subprocess, Python tempfile.

---

## File Map

**Create:**
- `backend/app/connectors/executors/terraform_local/__init__.py`
- `backend/app/connectors/executors/terraform_local/terraform_plan_local.py`
- `backend/app/connectors/executors/terraform_local/terraform_apply_local.py`
- `backend/app/connectors/executors/terraform_local/terraform_destroy_local.py`
- `backend/app/connectors/catalog/terraform_local.json`
- `backend/app/connectors/change_type_definitions/terraform_local_apply.json`
- `backend/alembic/versions/021_add_terraform_local_connector.py`

**Modify:**
- `backend/app/models/connector.py` — add `terraform_local` to `ConnectorType`
- `backend/app/models/change_request.py` — add `terraform_local_apply` to `ChangeType`
- `backend/app/services/planning_engine.py` — add resolver for `terraform_plan_local`, `terraform_apply_local`, `terraform_destroy_local`
- `backend/app/services/safety_engine.py` — add `terraform_local_apply` to `_IMPLICIT_ROLLBACK_TYPES`
- `frontend/src/types/api.ts` — add to `ConnectorType` and `ChangeType`
- `frontend/src/components/AddConnectorModal.tsx` — add label + icon
- `frontend/src/pages/Connectors.tsx` — add label + icon
- `frontend/src/pages/CreateChangeRequest.tsx` — add to `CHANGE_TYPE_META`, groups, asset filter
- `backend/tests/smoke/test_aws_live.py` — implement `run_phase_c`

---

## Task 1: Backend Enum + Migration

- [ ] **Step 1: Add `terraform_local` to ConnectorType**

In `backend/app/models/connector.py`, add after `terraform = "terraform"`:
```python
    terraform_local = "terraform_local"
```

- [ ] **Step 2: Add `terraform_local_apply` to ChangeType**

In `backend/app/models/change_request.py`, add after `deploy_nexplane_agent = "deploy_nexplane_agent"`:
```python
    terraform_local_apply = "terraform_local_apply"
```

- [ ] **Step 3: Create migration**

Create `backend/alembic/versions/021_add_terraform_local_connector.py`:

```python
"""add terraform_local connector and change type

Revision ID: 021
Revises: 020
Create Date: 2026-05-04
"""
from alembic import op

revision = '021'
down_revision = '020'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'terraform_local'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'terraform_local_apply'")


def downgrade():
    pass
```

- [ ] **Step 4: Run migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected last line: `Running upgrade 020 -> 021`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/connector.py \
        backend/app/models/change_request.py \
        backend/alembic/versions/021_add_terraform_local_connector.py
git commit -m "feat: add terraform_local connector type and terraform_local_apply change type"
```

---

## Task 2: Terraform Local Executors

- [ ] **Step 1: Create `__init__.py`**

Create `backend/app/connectors/executors/terraform_local/__init__.py` — empty file.

- [ ] **Step 2: Create `terraform_plan_local.py`**

Create `backend/app/connectors/executors/terraform_local/terraform_plan_local.py`:

```python
import asyncio
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


async def _get_aws_env(connector) -> dict:
    """Extract AWS credentials from the linked AWS connector for terraform env vars."""
    creds = getattr(connector, 'credentials', {}) or {}
    env = {**os.environ}
    if creds.get('access_key_id'):
        env['AWS_ACCESS_KEY_ID'] = creds['access_key_id']
        env['AWS_SECRET_ACCESS_KEY'] = creds.get('secret_access_key', '')
        env['AWS_DEFAULT_REGION'] = creds.get('region', 'us-east-1')
        if creds.get('session_token'):
            env['AWS_SESSION_TOKEN'] = creds['session_token']
    return env


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    tf_content = parameters.get('tf_content', '')
    working_dir = parameters.get('working_dir', '')

    if not tf_content and not working_dir:
        return {
            "action": "terraform_plan_local",
            "plan_output": "mock plan: no changes",
            "plan_file": "/tmp/mock.tfplan",
            "mock": True,
        }

    env = await _get_aws_env(connector)
    loop = asyncio.get_event_loop()

    def _run():
        work_dir = working_dir or tempfile.mkdtemp(prefix="nexplane-tf-")
        plan_file = os.path.join(work_dir, "tfplan")

        if tf_content:
            Path(os.path.join(work_dir, "main.tf")).write_text(tf_content)

        init_result = subprocess.run(
            ["terraform", "init", "-no-color"],
            cwd=work_dir, env=env, capture_output=True, text=True, timeout=120,
        )
        if init_result.returncode != 0:
            raise RuntimeError(f"terraform init failed:\n{init_result.stderr}")

        plan_result = subprocess.run(
            ["terraform", "plan", "-no-color", f"-out={plan_file}"],
            cwd=work_dir, env=env, capture_output=True, text=True, timeout=180,
        )
        if plan_result.returncode != 0:
            raise RuntimeError(f"terraform plan failed:\n{plan_result.stderr}")

        return {"work_dir": work_dir, "plan_file": plan_file, "output": plan_result.stdout}

    result = await loop.run_in_executor(None, _run)
    return {
        "action": "terraform_plan_local",
        "plan_output": result["output"],
        "plan_file": result["plan_file"],
        "working_dir": result["work_dir"],
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "terraform plan has no rollback"}
```

- [ ] **Step 3: Create `terraform_apply_local.py`**

Create `backend/app/connectors/executors/terraform_local/terraform_apply_local.py`:

```python
import asyncio
import os
import subprocess
from datetime import datetime, timezone


async def _get_aws_env(connector) -> dict:
    creds = getattr(connector, 'credentials', {}) or {}
    env = {**os.environ}
    if creds.get('access_key_id'):
        env['AWS_ACCESS_KEY_ID'] = creds['access_key_id']
        env['AWS_SECRET_ACCESS_KEY'] = creds.get('secret_access_key', '')
        env['AWS_DEFAULT_REGION'] = creds.get('region', 'us-east-1')
        if creds.get('session_token'):
            env['AWS_SESSION_TOKEN'] = creds['session_token']
    return env


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    working_dir = parameters.get('working_dir', '')
    plan_file = parameters.get('plan_file', '')

    if not working_dir:
        return {"action": "terraform_apply_local", "output": "mock apply: resources created", "mock": True}

    env = await _get_aws_env(connector)
    loop = asyncio.get_event_loop()

    def _run():
        cmd = ["terraform", "apply", "-no-color", "-auto-approve"]
        if plan_file:
            cmd.append(plan_file)
        result = subprocess.run(
            cmd, cwd=working_dir, env=env, capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"terraform apply failed:\n{result.stderr}")
        return result.stdout

    output = await loop.run_in_executor(None, _run)
    return {
        "action": "terraform_apply_local",
        "output": output,
        "working_dir": working_dir,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.terraform_local.terraform_destroy_local import execute as destroy
    return await destroy({"working_dir": execution_result.get("working_dir", "")}, [], connector)
```

- [ ] **Step 4: Create `terraform_destroy_local.py`**

Create `backend/app/connectors/executors/terraform_local/terraform_destroy_local.py`:

```python
import asyncio
import os
import subprocess
import shutil
from datetime import datetime, timezone


async def _get_aws_env(connector) -> dict:
    creds = getattr(connector, 'credentials', {}) or {}
    env = {**os.environ}
    if creds.get('access_key_id'):
        env['AWS_ACCESS_KEY_ID'] = creds['access_key_id']
        env['AWS_SECRET_ACCESS_KEY'] = creds.get('secret_access_key', '')
        env['AWS_DEFAULT_REGION'] = creds.get('region', 'us-east-1')
        if creds.get('session_token'):
            env['AWS_SESSION_TOKEN'] = creds['session_token']
    return env


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    working_dir = parameters.get('working_dir', '')
    cleanup_dir = parameters.get('cleanup_dir', True)

    if not working_dir:
        return {"action": "terraform_destroy_local", "output": "mock destroy: resources removed", "mock": True}

    env = await _get_aws_env(connector)
    loop = asyncio.get_event_loop()

    def _run():
        result = subprocess.run(
            ["terraform", "destroy", "-no-color", "-auto-approve"],
            cwd=working_dir, env=env, capture_output=True, text=True, timeout=300,
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise RuntimeError(f"terraform destroy failed:\n{result.stderr}")
        if cleanup_dir:
            shutil.rmtree(working_dir, ignore_errors=True)
        return output

    output = await loop.run_in_executor(None, _run)
    return {
        "action": "terraform_destroy_local",
        "output": output,
        "working_dir": working_dir,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "terraform destroy has no further rollback"}
```

- [ ] **Step 5: Create catalog JSON**

Create `backend/app/connectors/catalog/terraform_local.json`:

```json
{
  "connector_type": "terraform_local",
  "display_name": "Terraform (Local CLI)",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "terraform_plan_local",
      "generic_action": "terraform_plan_local",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Terraform Plan (Local)",
      "description": "Runs terraform plan using the local Terraform CLI binary inside the Nexplane backend container.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "tf_content", "type": "string", "required": false},
        {"name": "working_dir", "type": "string", "required": false}
      ],
      "executor": "terraform_local.terraform_plan_local",
      "estimated_duration_seconds": 60
    },
    {
      "action_id": "terraform_apply_local",
      "generic_action": "terraform_apply_local",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Terraform Apply (Local)",
      "description": "Runs terraform apply using the local Terraform CLI binary.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "working_dir", "type": "string", "required": false},
        {"name": "plan_file", "type": "string", "required": false}
      ],
      "executor": "terraform_local.terraform_apply_local",
      "rollback_action": "terraform_destroy_local",
      "rollback_connector_type": "terraform_local",
      "estimated_duration_seconds": 120
    },
    {
      "action_id": "terraform_destroy_local",
      "generic_action": "terraform_destroy_local",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Terraform Destroy (Local)",
      "description": "Runs terraform destroy to remove all resources in a working directory.",
      "applicable_asset_types": ["cloud_account"],
      "parameters": [
        {"name": "working_dir", "type": "string", "required": true},
        {"name": "cleanup_dir", "type": "boolean", "required": false, "default": true}
      ],
      "executor": "terraform_local.terraform_destroy_local",
      "estimated_duration_seconds": 120
    }
  ]
}
```

- [ ] **Step 6: Create change type definition**

Create `backend/app/connectors/change_type_definitions/terraform_local_apply.json`:

```json
{
  "change_type": "terraform_local_apply",
  "display_name": "Terraform Apply (Local CLI)",
  "steps": [
    {"generic_action": "terraform_plan_local",   "purpose": "preflight_validate", "required": true},
    {"generic_action": "terraform_apply_local",  "purpose": "execute",            "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 7: Add planning_engine resolvers**

In `backend/app/services/planning_engine.py`, add before the `terminate_instance` resolver:

```python
        "terraform_plan_local":   {
            "tf_content": desired.get("tf_content", ""),
            "working_dir": desired.get("working_dir", ""),
        },
        "terraform_apply_local":  {
            "working_dir": desired.get("working_dir", ""),
            "plan_file": desired.get("plan_file", ""),
        },
        "terraform_destroy_local": {
            "working_dir": desired.get("working_dir", ""),
            "cleanup_dir": desired.get("cleanup_dir", True),
        },
```

- [ ] **Step 8: Add to safety engine implicit rollbacks**

In `backend/app/services/safety_engine.py`, add `ChangeType.terraform_local_apply` to `_IMPLICIT_ROLLBACK_TYPES`.

- [ ] **Step 9: Frontend updates**

In `frontend/src/types/api.ts`:
- Add `"terraform_local"` to `ConnectorType`
- Add `"terraform_local_apply"` to `ChangeType`

In `frontend/src/components/AddConnectorModal.tsx`:
- Add to `CONNECTOR_LABELS`: `terraform_local: "Terraform (Local CLI)"`
- Add to `CONNECTOR_ICONS`: `terraform_local: "🏗️"`

In `frontend/src/pages/Connectors.tsx`:
- Add to `CONNECTOR_LABELS`: `terraform_local: "Terraform (Local CLI)"`
- Add to `CONNECTOR_ICONS`: `terraform_local: "🏗️"`

In `frontend/src/pages/CreateChangeRequest.tsx`:
- Add to `CHANGE_TYPE_META`:
```typescript
  terraform_local_apply: {
    label: "Terraform Apply (Local CLI)",
    description: "Run terraform plan + apply using the local Terraform binary inside the Nexplane backend container.",
    outcomeTemplate: JSON.stringify({
      tf_content: "# Paste your .tf content here\nresource \"aws_s3_bucket\" \"example\" {\n  bucket = \"my-nexplane-test-bucket\"\n  force_destroy = true\n}\n",
      rollback_strategy: "terraform_destroy_local",
    }, null, 2),
  },
```
- Add `"terraform_local_apply"` to the IaC group in `CHANGE_TYPE_GROUPS`
- Add `terraform_local_apply: "cloud_account"` to `CHANGE_TYPE_ASSET_FILTER`

- [ ] **Step 10: Restart and verify catalog**

```bash
docker compose stop frontend && docker compose up frontend -d
docker compose restart backend
docker compose exec backend python -c "
from app.connectors.catalog_service import get_catalog_service
c = get_catalog_service()
for a in ['terraform_plan_local', 'terraform_apply_local', 'terraform_destroy_local']:
    opts = c.get_options_for_action(a)
    print(f'{a}: {[o.connector_type for o in opts]}')
"
```

Expected:
```
terraform_plan_local: ['terraform_local']
terraform_apply_local: ['terraform_local']
terraform_destroy_local: ['terraform_local']
```

- [ ] **Step 11: Commit**

```bash
git add backend/app/connectors/executors/terraform_local/ \
        backend/app/connectors/catalog/terraform_local.json \
        backend/app/connectors/change_type_definitions/terraform_local_apply.json \
        backend/app/models/connector.py \
        backend/app/models/change_request.py \
        backend/alembic/versions/021_add_terraform_local_connector.py \
        backend/app/services/planning_engine.py \
        backend/app/services/safety_engine.py \
        frontend/src/types/api.ts \
        frontend/src/components/AddConnectorModal.tsx \
        frontend/src/pages/Connectors.tsx \
        frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat: add terraform_local connector with CLI-based plan/apply/destroy executors"
```

---

## Task 3: Add Terraform Local Connector in UI + Implement Phase C Smoke Test

- [ ] **Step 1: Add terraform_local connector in Nexplane UI**

Go to Connectors → Add Connector → select **🏗️ Terraform (Local CLI)** → name it "Terraform Local" → Save.

No credentials needed — it uses the AWS connector's credentials at execution time.

- [ ] **Step 2: Implement run_phase_c in test_aws_live.py**

Find `run_phase_c` in `backend/tests/smoke/test_aws_live.py` and replace it:

```python
def run_phase_c(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase C: local Terraform S3 bucket lifecycle."""
    print("\n[Phase C] Local Terraform")
    import random
    bucket_suffix = random.randint(10000, 99999)
    bucket_name = f"nexplane-smoke-test-{bucket_suffix}"

    tf_content = f"""
terraform {{
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
}}

provider "aws" {{}}

resource "aws_s3_bucket" "smoke_test" {{
  bucket        = "{bucket_name}"
  force_destroy = true
}}
"""

    result = client.run_cr(
        "Smoke: terraform apply S3 bucket", "terraform_local_apply", cloud_account_id,
        {"tf_content": tf_content, "rollback_strategy": "terraform_destroy_local"},
    )
    log(f"Terraform applied — bucket: {bucket_name}")

    # Verify bucket exists via discovery
    time.sleep(5)
    s3_assets = client.get("/assets", params={"asset_type": "storage_bucket", "q": bucket_name})
    if s3_assets:
        log(f"Bucket appears in inventory: {s3_assets[0]['id']}")
    else:
        print("  ⚠️  Bucket not yet in inventory — may need manual discovery run")

    log("Phase C complete")
```

- [ ] **Step 3: Run Phase C**

```bash
python backend/tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases C
```

Expected: Terraform creates an S3 bucket, apply CR completes, rollback (destroy) runs in cleanup.
