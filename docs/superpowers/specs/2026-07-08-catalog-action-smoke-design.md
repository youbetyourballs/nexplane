# Catalog Action Smoke — Commercial Wizard Phases Design

**Goal:** Live-verify the commercial onboarding wizard (`create_customer` → `provision_instance` → `generate_setup_token` → rollback) against real AWS infrastructure, and wire the existing 6 core `CATALOG_ACTION` phases into `run_on_ec2.py`.

**Architecture:** Two deliverables. (1) Wire existing 6 `CATALOG_ACTION` phases in `test_catalog_action_live.py` into `run_on_ec2.py`'s routing so they run on EC2 runners. (2) A new `nexplane-commercial` private repo holds the `ops_provision` catalog JSON and 4 executor modules; a new `COMMERCIAL_WIZARD` smoke phase in `test_catalog_action_live.py` clones that repo onto the runner, injects it into the backend, and runs a full `catalog_workflow` CR lifecycle including FILO rollback verification.

**Tech Stack:** Python 3.12, boto3, httpx, FastAPI, SQLAlchemy 2.x async, Docker Compose, Tailscale, `catalog_workflow` CR type

---

## Global Constraints

- All assertions run against live AWS infrastructure — no mocks, no stubs
- Commercial phases gated behind `RUN_COMMERCIAL_PHASES=true` env var; omitting it skips them cleanly
- Smoke follows existing file conventions: `phase_*` naming, `fail()` for assertion failures, `--email`/`--password` CLI args
- FILO rollback order must be explicitly verified: token revoked → instance terminated → client deleted
- `smoke_verified` in `ops_provision.json` starts `false`; the smoke phase sets it to `true` on pass as the final gate step
- No new backend code changes — commercial loading infrastructure already exists (`_load_commercial_executor`, `NEXPLANE_COMMERCIAL_CATALOG_PATH`)
- EC2 is the live filesystem; all runner changes happen on the runner instance, not locally

---

## Deliverable 1: Wire Existing CATALOG_ACTION Phases

**File to modify:** `backend/tests/smoke/run_on_ec2.py`

Add `"CATALOG_ACTION"` (and its 6 sub-phases: `CATALOG_DISCOVERY`, `CATALOG_CR_LIFECYCLE`, `CATALOG_ACTION_ERRORS`, `CATALOG_ROLLBACK`, `CATALOG_WORKFLOW`, `CATALOG_WORKFLOW_PARTIAL_FAILURE`) to the routing table so they install dependencies and dispatch `test_catalog_action_live.py` on the runner EC2.

No changes to `test_catalog_action_live.py` — all 6 phases are already fully implemented there.

---

## Deliverable 2: `nexplane-commercial` Repo

**Repo:** `github.com/youbetyourballs/nexplane-commercial` (private)

**Structure:**
```
catalog/
  ops_provision/
    ops_provision.json       # catalog metadata
    create_customer.py
    provision_instance.py
    generate_setup_token.py
    terminate_instance.py
```

### `ops_provision.json`

```json
{
  "connector_type": "ops_provision",
  "domain": "commercial",
  "actions": [
    {
      "name": "create_customer",
      "read_only": false,
      "smoke_verified": false,
      "module": "create_customer",
      "description": "Create a new customer record in the platform database"
    },
    {
      "name": "provision_instance",
      "read_only": false,
      "smoke_verified": false,
      "module": "provision_instance",
      "description": "Launch a Nexplane EC2 instance for the customer"
    },
    {
      "name": "generate_setup_token",
      "read_only": false,
      "smoke_verified": false,
      "module": "generate_setup_token",
      "description": "Generate a one-time setup token for the customer instance"
    },
    {
      "name": "terminate_instance",
      "read_only": false,
      "smoke_verified": false,
      "module": "terminate_instance",
      "description": "Terminate a customer EC2 instance"
    }
  ]
}
```

`smoke_verified` is updated to `true` for all 4 actions after the `COMMERCIAL_WIZARD` smoke phase passes.

### Executor: `create_customer.py`

```python
import uuid
from app.db import get_db_session
from app.models.client import Client

async def execute(params: dict, context: dict) -> dict:
    client_id = str(uuid.uuid4())
    slug = params["company_name"].lower().replace(" ", "-")
    async with get_db_session() as db:
        client = Client(client_id=client_id, slug=slug, name=params["company_name"], status="provisioning")
        db.add(client)
        await db.commit()
    return {"client_id": client_id, "slug": slug}

async def rollback(result: dict, context: dict) -> None:
    async with get_db_session() as db:
        await db.execute("DELETE FROM clients WHERE client_id = :id", {"id": result["client_id"]})
        await db.commit()
```

### Executor: `provision_instance.py`

- Launches `t3.small` EC2 in platform VPC, Name tag `np-{slug}`, AWS tags `ManagedBy=nexplane`, `ClientId={client_id}`
- User-data: pulls bootstrap script from S3/SSM, installs Docker + Nexplane stack, registers Tailscale with pre-generated auth key from SSM `/nexplane/smoke/tailscale_auth_key`
- Polls `http://{private_ip}:8000/health` every 15s, up to 20 min
- Returns `{instance_id, private_ip, tailscale_ip}`
- Rollback: delegates to `terminate_instance.execute({instance_id})`

### Executor: `generate_setup_token.py`

- Inserts row into `setup_tokens` table: `token` (UUID), `client_id`, `expires_at` (72h from now), `used=false`
- Returns `{token, expires_at}`
- Rollback: `DELETE FROM setup_tokens WHERE token = :token`

### Executor: `terminate_instance.py`

- Calls `ec2.terminate_instances([instance_id])`
- Polls until instance state = `terminated` (up to 10 min)
- Returns `{terminated: true}`
- Rollback: no-op (termination is final; platform reconstitution policy applies if needed)

---

## Deliverable 3: `COMMERCIAL_WIZARD` Smoke Phase

**File to modify:** `backend/tests/smoke/test_catalog_action_live.py`

**File to modify:** `backend/tests/smoke/run_on_ec2.py`

### Phase Setup (in `run_on_ec2.py`, before dispatching `test_catalog_action_live.py`)

When `COMMERCIAL_WIZARD` phase is requested:
1. Clone `nexplane-commercial` to `/tmp/nexplane-commercial` on the runner
2. SSH into platform EC2 or use `docker exec` to set `NEXPLANE_COMMERCIAL_CATALOG_PATH=/tmp/nexplane-commercial/catalog` in the backend container env and restart it
3. Poll `GET /health` until backend confirms commercial catalog loaded (check for `ops_provision` in `/catalog/actions` response)

Teardown (after phase completes):
1. Unset `NEXPLANE_COMMERCIAL_CATALOG_PATH`, restart backend

### Phase `COMMERCIAL_WIZARD` (in `test_catalog_action_live.py`)

```python
async def phase_commercial_wizard(client, log, fail):
    # 1. Assert commercial catalog loaded
    r = await client.get("/catalog/actions")
    actions = r.json()
    op_names = [a["name"] for a in actions if a.get("connector_type") == "ops_provision"]
    if not all(n in op_names for n in ["create_customer", "provision_instance", "generate_setup_token"]):
        fail(f"Commercial catalog not loaded. Actions: {op_names}")

    # 2. Create catalog_workflow CR
    cr_payload = {
        "cr_type": "catalog_workflow",
        "title": "Smoke: Commercial Wizard",
        "steps": [
            {"action": "ops_provision.create_customer", "params": {"company_name": "Smoke Test Co"}},
            {"action": "ops_provision.provision_instance", "params": {}},
            {"action": "ops_provision.generate_setup_token", "params": {}},
        ]
    }
    r = await client.post("/change-requests", json=cr_payload)
    cr_id = r.json()["id"]

    # 3. Plan + approve
    await client.post(f"/change-requests/{cr_id}/plan")
    await client.post(f"/change-requests/{cr_id}/approve")

    # 4. Execute — poll up to 25 min
    await client.post(f"/change-requests/{cr_id}/execute")
    cr = await poll_until(lambda: client.get(f"/change-requests/{cr_id}"),
                          lambda r: r.json()["status"] in ("COMPLETED", "FAILED"),
                          timeout=1500, interval=15)
    if cr["status"] != "COMPLETED":
        fail(f"CR execution failed: {cr}")

    # 5. Assert assets exist
    result = cr["execution_result"]
    client_id = result["steps"][0]["result"]["client_id"]
    token = result["steps"][2]["result"]["token"]
    instance_id = result["steps"][1]["result"]["instance_id"]
    private_ip = result["steps"][1]["result"]["private_ip"]

    # DB assertions via platform API
    r = await client.get(f"/clients/{client_id}")
    if r.status_code != 200:
        fail(f"Client record not found: {client_id}")

    r = await client.get(f"/setup-tokens/{token}")
    if r.status_code != 200:
        fail(f"Setup token not found: {token}")

    # Instance health
    import httpx as _httpx
    try:
        hr = _httpx.get(f"http://{private_ip}:8000/health", timeout=10)
        if hr.status_code != 200:
            fail(f"Provisioned instance unhealthy: {hr.status_code}")
    except Exception as e:
        fail(f"Provisioned instance unreachable: {e}")

    # 6. Rollback
    await client.post(f"/change-requests/{cr_id}/rollback")
    cr = await poll_until(lambda: client.get(f"/change-requests/{cr_id}"),
                          lambda r: r.json()["status"] in ("ROLLED_BACK", "ROLLBACK_FAILED"),
                          timeout=900, interval=15)
    if cr["status"] != "ROLLED_BACK":
        fail(f"Rollback failed: {cr}")

    # 7. Verify FILO order from rollback log
    rollback_steps = cr["rollback_log"]
    step_order = [s["action"] for s in rollback_steps]
    expected = ["ops_provision.generate_setup_token", "ops_provision.provision_instance", "ops_provision.create_customer"]
    if step_order != expected:
        fail(f"Rollback order wrong. Got: {step_order}")

    # 8. Assert assets gone
    r = await client.get(f"/clients/{client_id}")
    if r.status_code != 404:
        fail(f"Client record not deleted after rollback")

    r = await client.get(f"/setup-tokens/{token}")
    if r.status_code != 404:
        fail(f"Setup token not deleted after rollback")

    import boto3 as _boto3
    ec2 = _boto3.client("ec2", region_name="us-east-1")
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    state = resp["Reservations"][0]["Instances"][0]["State"]["Name"]
    if state not in ("terminated", "shutting-down"):
        fail(f"EC2 instance not terminated after rollback: {state}")

    log("COMMERCIAL_WIZARD: all assertions passed")
```

---

## Error Handling

**Instance boot timeout:** `provision_instance` raises after 20 min poll. CR enters `FAILED`. Rollback must still terminate the EC2 (instance exists even if unhealthy). `terminate_instance` rollback runs unconditionally.

**Partial workflow failure:** If step 2 fails, only step 1 rolls back (FILO from completed steps only). This is handled by the existing `catalog_workflow` CR machinery — no new executor logic needed.

**Commercial catalog not loaded:** Smoke setup step asserts `ops_provision` appears in `/catalog/actions` before attempting CR creation. Fails fast with clear message.

**`smoke_verified: false` blocking load:** Backend startup skips commercial mutating actions where `smoke_verified=false`. Smoke setup step will catch this via the catalog assertion — fix is to set `smoke_verified=true` after smoke passes.

---

## Sequence of Delivery

1. Create `nexplane-commercial` repo with all 4 executors + `ops_provision.json`
2. Wire 6 existing `CATALOG_ACTION` phases into `run_on_ec2.py`
3. Add `COMMERCIAL_WIZARD` phase to `test_catalog_action_live.py`
4. Add commercial phase routing to `run_on_ec2.py` (clone repo, inject env, restart backend, teardown)
5. Run smoke — on pass, update `smoke_verified: true` in `ops_provision.json`
