# Expanded Smoke Test Phase D — Local Ansible Connector

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `ansible_local` connector that runs `ansible-playbook` inside the backend container using AWS SSM as the connection transport, enabling Ansible playbook CRs without needing AWX/Tower.

**Architecture:** New `ansible_local` connector writes an inventory file and playbook to a temp directory, then runs `ansible-playbook` with `connection=aws_ssm`. The `community.aws` collection's SSM connection plugin handles SSH-less execution via AWS SSM Session Manager — no open ports or SSH keys needed. The existing `ansible` (AWX) connector is unchanged.

**Tech Stack:** Ansible 2.16+, community.aws collection, amazon.aws collection, AWS SSM connection plugin (installed in Dockerfile by Phase A plan).

---

## File Map

**Create:**
- `backend/app/connectors/executors/ansible_local/__init__.py`
- `backend/app/connectors/executors/ansible_local/ansible_run_local.py`
- `backend/app/connectors/executors/ansible_local/ansible_check_local.py`
- `backend/app/connectors/catalog/ansible_local.json`
- `backend/app/connectors/change_type_definitions/ansible_local_playbook.json`
- `backend/alembic/versions/022_add_ansible_local_connector.py`

**Modify:**
- `backend/app/models/connector.py` — add `ansible_local`
- `backend/app/models/change_request.py` — add `ansible_local_playbook`
- `backend/app/services/planning_engine.py` — add resolvers
- `backend/app/services/safety_engine.py` — add to implicit rollback types
- `frontend/src/types/api.ts` — extend unions
- `frontend/src/components/AddConnectorModal.tsx` — label + icon
- `frontend/src/pages/Connectors.tsx` — label + icon
- `frontend/src/pages/CreateChangeRequest.tsx` — meta, groups, filter
- `backend/tests/smoke/test_aws_live.py` — implement `run_phase_d`

---

## Task 1: Backend Enum + Migration

- [ ] **Step 1: Add `ansible_local` to ConnectorType**

In `backend/app/models/connector.py`, add after `ansible = "ansible"`:
```python
    ansible_local = "ansible_local"
```

- [ ] **Step 2: Add `ansible_local_playbook` to ChangeType**

In `backend/app/models/change_request.py`, add after `terraform_local_apply = "terraform_local_apply"`:
```python
    ansible_local_playbook = "ansible_local_playbook"
```

- [ ] **Step 3: Create migration**

Create `backend/alembic/versions/022_add_ansible_local_connector.py`:

```python
"""add ansible_local connector and change type

Revision ID: 022
Revises: 021
Create Date: 2026-05-04
"""
from alembic import op

revision = '022'
down_revision = '021'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'ansible_local'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ansible_local_playbook'")


def downgrade():
    pass
```

- [ ] **Step 4: Run migration**

```bash
docker compose exec backend alembic upgrade head
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/connector.py \
        backend/app/models/change_request.py \
        backend/alembic/versions/022_add_ansible_local_connector.py
git commit -m "feat: add ansible_local connector type and ansible_local_playbook change type"
```

---

## Task 2: Ansible Local Executors

- [ ] **Step 1: Create `__init__.py`**

Create `backend/app/connectors/executors/ansible_local/__init__.py` — empty file.

- [ ] **Step 2: Create shared helper `_runner.py`**

Create `backend/app/connectors/executors/ansible_local/_runner.py`:

```python
import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _build_ssm_inventory(instance_id: str, region: str) -> str:
    return f"""[targets]
{instance_id}

[targets:vars]
ansible_connection=community.aws.aws_ssm
ansible_aws_ssm_region={region}
ansible_aws_ssm_timeout=60
"""


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


async def run_playbook(
    instance_id: str,
    playbook_content: str,
    connector,
    check_mode: bool = False,
    extra_vars: dict | None = None,
) -> dict:
    creds = getattr(connector, 'credentials', {}) or {}
    region = creds.get('region', 'us-east-1') if creds else 'us-east-1'

    if not creds:
        return {
            "stdout": "mock ansible run — no credentials",
            "rc": 0,
            "mock": True,
        }

    env = await _get_aws_env(connector)
    loop = asyncio.get_event_loop()

    def _run():
        work_dir = tempfile.mkdtemp(prefix="nexplane-ansible-")
        try:
            inventory_path = os.path.join(work_dir, "inventory.ini")
            playbook_path = os.path.join(work_dir, "playbook.yml")
            Path(inventory_path).write_text(_build_ssm_inventory(instance_id, region))
            Path(playbook_path).write_text(playbook_content)

            cmd = [
                "ansible-playbook",
                "-i", inventory_path,
                playbook_path,
                "--timeout", "60",
            ]
            if check_mode:
                cmd.append("--check")
            if extra_vars:
                import json
                cmd += ["--extra-vars", json.dumps(extra_vars)]

            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True, timeout=300,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "rc": result.returncode,
            }
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    result = await loop.run_in_executor(None, _run)
    if result["rc"] != 0:
        raise RuntimeError(f"ansible-playbook failed (rc={result['rc']}):\n{result['stderr']}")
    return result
```

- [ ] **Step 3: Create `ansible_run_local.py`**

Create `backend/app/connectors/executors/ansible_local/ansible_run_local.py`:

```python
from datetime import datetime, timezone
from ._runner import run_playbook


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    instance_id = parameters.get('instance_id', '')
    playbook_content = parameters.get('playbook_content', '')
    extra_vars = parameters.get('extra_vars', {})

    if not playbook_content:
        return {"action": "ansible_run_local", "stdout": "mock: no playbook content", "mock": True}

    result = await run_playbook(instance_id, playbook_content, connector, check_mode=False, extra_vars=extra_vars)
    return {
        "action": "ansible_run_local",
        "instance_id": instance_id,
        "stdout": result["stdout"],
        "rc": result["rc"],
        "mock": result.get("mock", False),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ansible_run_local has no automatic rollback — write an undo playbook"}
```

- [ ] **Step 4: Create `ansible_check_local.py`**

Create `backend/app/connectors/executors/ansible_local/ansible_check_local.py`:

```python
from datetime import datetime, timezone
from ._runner import run_playbook


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    instance_id = parameters.get('instance_id', '')
    playbook_content = parameters.get('playbook_content', '')
    extra_vars = parameters.get('extra_vars', {})

    if not playbook_content:
        return {"action": "ansible_check_local", "stdout": "mock: no playbook content", "mock": True}

    result = await run_playbook(instance_id, playbook_content, connector, check_mode=True, extra_vars=extra_vars)
    return {
        "action": "ansible_check_local",
        "instance_id": instance_id,
        "stdout": result["stdout"],
        "rc": result["rc"],
        "mock": result.get("mock", False),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ansible check mode has no rollback"}
```

- [ ] **Step 5: Create catalog JSON**

Create `backend/app/connectors/catalog/ansible_local.json`:

```json
{
  "connector_type": "ansible_local",
  "display_name": "Ansible (Local CLI)",
  "credential_fields": [],
  "actions": [
    {
      "action_id": "ansible_check_local",
      "generic_action": "ansible_check_local",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Ansible Check (Local)",
      "description": "Runs ansible-playbook --check using the local Ansible binary with SSM transport.",
      "applicable_asset_types": ["server", "endpoint"],
      "parameters": [
        {"name": "instance_id", "type": "string", "required": true},
        {"name": "playbook_content", "type": "string", "required": true},
        {"name": "extra_vars", "type": "object", "required": false}
      ],
      "executor": "ansible_local.ansible_check_local",
      "estimated_duration_seconds": 60
    },
    {
      "action_id": "ansible_run_local",
      "generic_action": "ansible_run_local",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Ansible Run (Local)",
      "description": "Runs ansible-playbook using the local Ansible binary with AWS SSM as the connection transport.",
      "applicable_asset_types": ["server", "endpoint"],
      "parameters": [
        {"name": "instance_id", "type": "string", "required": true},
        {"name": "playbook_content", "type": "string", "required": true},
        {"name": "extra_vars", "type": "object", "required": false}
      ],
      "executor": "ansible_local.ansible_run_local",
      "estimated_duration_seconds": 120
    }
  ]
}
```

- [ ] **Step 6: Create change type definition**

Create `backend/app/connectors/change_type_definitions/ansible_local_playbook.json`:

```json
{
  "change_type": "ansible_local_playbook",
  "display_name": "Ansible Playbook (Local CLI)",
  "steps": [
    {"generic_action": "ansible_check_local", "purpose": "preflight_validate", "required": true},
    {"generic_action": "ansible_run_local",   "purpose": "execute",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 7: Add planning_engine resolvers**

In `backend/app/services/planning_engine.py`, add before `terminate_instance`:

```python
        "ansible_check_local":   {
            "instance_id": desired.get("instance_id", ""),
            "playbook_content": desired.get("playbook_content", ""),
            "extra_vars": desired.get("extra_vars", {}),
        },
        "ansible_run_local":     {
            "instance_id": desired.get("instance_id", ""),
            "playbook_content": desired.get("playbook_content", ""),
            "extra_vars": desired.get("extra_vars", {}),
        },
```

- [ ] **Step 8: Add to safety engine implicit rollbacks**

In `backend/app/services/safety_engine.py`, add `ChangeType.ansible_local_playbook` to `_IMPLICIT_ROLLBACK_TYPES`.

- [ ] **Step 9: Frontend updates**

In `frontend/src/types/api.ts`:
- Add `"ansible_local"` to `ConnectorType`
- Add `"ansible_local_playbook"` to `ChangeType`

In `frontend/src/components/AddConnectorModal.tsx`:
- Add to `CONNECTOR_LABELS`: `ansible_local: "Ansible (Local CLI)"`
- Add to `CONNECTOR_ICONS`: `ansible_local: "⚙️"`

In `frontend/src/pages/Connectors.tsx`:
- Add to `CONNECTOR_LABELS`: `ansible_local: "Ansible (Local CLI)"`
- Add to `CONNECTOR_ICONS`: `ansible_local: "⚙️"`

In `frontend/src/pages/CreateChangeRequest.tsx`:
- Add to `CHANGE_TYPE_META`:
```typescript
  ansible_local_playbook: {
    label: "Ansible Playbook (Local CLI)",
    description: "Run an Ansible playbook via the local Ansible binary using SSM as the connection transport — no SSH or AWX needed.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      playbook_content: "---\n- name: Example playbook\n  hosts: all\n  gather_facts: yes\n  tasks:\n    - name: Print hostname\n      ansible.builtin.debug:\n        msg: \"{{ ansible_hostname }}\"\n",
      extra_vars: {},
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
```
- Add `"ansible_local_playbook"` to the IaC group in `CHANGE_TYPE_GROUPS`
- Add `ansible_local_playbook: "server"` to `CHANGE_TYPE_ASSET_FILTER`

- [ ] **Step 10: Restart and verify catalog**

```bash
docker compose stop frontend && docker compose up frontend -d
docker compose restart backend
docker compose exec backend python -c "
from app.connectors.catalog_service import get_catalog_service
c = get_catalog_service()
for a in ['ansible_check_local', 'ansible_run_local']:
    opts = c.get_options_for_action(a)
    print(f'{a}: {[o.connector_type for o in opts]}')
"
```

Expected:
```
ansible_check_local: ['ansible_local']
ansible_run_local: ['ansible_local']
```

- [ ] **Step 11: Commit**

```bash
git add backend/app/connectors/executors/ansible_local/ \
        backend/app/connectors/catalog/ansible_local.json \
        backend/app/connectors/change_type_definitions/ansible_local_playbook.json \
        backend/app/models/connector.py \
        backend/app/models/change_request.py \
        backend/alembic/versions/022_add_ansible_local_connector.py \
        backend/app/services/planning_engine.py \
        backend/app/services/safety_engine.py \
        frontend/src/types/api.ts \
        frontend/src/components/AddConnectorModal.tsx \
        frontend/src/pages/Connectors.tsx \
        frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat: add ansible_local connector with SSM-transport playbook executor"
```

---

## Task 3: Add Ansible Local Connector + Implement Phase D Smoke Test

- [ ] **Step 1: Add ansible_local connector in Nexplane UI**

Go to Connectors → Add Connector → select **⚙️ Ansible (Local CLI)** → name it "Ansible Local" → Save.

No credentials needed — uses AWS connector credentials at execution time.

- [ ] **Step 2: Implement run_phase_d in test_aws_live.py**

Find `run_phase_d` in `backend/tests/smoke/test_aws_live.py` and replace it:

```python
def run_phase_d(client: NexplaneClient, phase_a_result: Optional[dict]) -> None:
    """Phase D: local Ansible playbook via SSM transport."""
    print("\n[Phase D] Local Ansible")

    if phase_a_result is None:
        fail("Phase D requires Phase A to have run first (needs a running EC2 instance)")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    INSTALL_PLAYBOOK = """---
- name: Smoke test — install htop
  hosts: all
  gather_facts: yes
  become: yes
  tasks:
    - name: Install htop
      ansible.builtin.package:
        name: htop
        state: present

    - name: Verify htop installed
      ansible.builtin.command: htop --version
      register: htop_out
      changed_when: false

    - name: Report
      ansible.builtin.debug:
        msg: "htop installed: {{ htop_out.stdout }}"
"""

    REMOVE_PLAYBOOK = """---
- name: Smoke test — remove htop
  hosts: all
  gather_facts: yes
  become: yes
  tasks:
    - name: Remove htop
      ansible.builtin.package:
        name: htop
        state: absent
"""

    # Dry run first
    client.run_cr(
        "Smoke: ansible check (htop install)", "ansible_local_playbook", instance_asset["id"],
        {"instance_id": instance_id, "playbook_content": INSTALL_PLAYBOOK,
         "rollback_strategy": "rollback_unavailable"},
    )
    log("Ansible check mode passed")

    # Apply
    client.run_cr(
        "Smoke: ansible run (install htop)", "ansible_local_playbook", instance_asset["id"],
        {"instance_id": instance_id, "playbook_content": INSTALL_PLAYBOOK,
         "rollback_strategy": "rollback_unavailable"},
    )

    # Verify via SSM
    client.run_cr(
        "Smoke: verify htop", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "htop --version", "rollback_strategy": "rollback_unavailable"},
    )
    log("htop verified via SSM")

    # Remove
    client.run_cr(
        "Smoke: ansible run (remove htop)", "ansible_local_playbook", instance_asset["id"],
        {"instance_id": instance_id, "playbook_content": REMOVE_PLAYBOOK,
         "rollback_strategy": "rollback_unavailable"},
    )
    log("htop removed")

    log("Phase D complete")
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: implement Phase D smoke test (local Ansible via SSM transport)"
```

- [ ] **Step 4: Run Phase D (requires Phase A running first)**

```bash
python backend/tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases A,D
```

Expected: EC2 launches, Ansible installs htop, SSM verifies it, Ansible removes it, cleanup runs.

- [ ] **Step 5: Run all phases end-to-end**

```bash
python backend/tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases A,B,C,D
```

Expected: All ✅, total runtime ~15–20 minutes.
