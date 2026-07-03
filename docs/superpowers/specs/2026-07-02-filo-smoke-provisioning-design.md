# FILO Smoke Self-Contained Provisioning — Design

**Goal:** Make `test_smoke_filo_rollback.py` provision its own fresh EC2 instance, deploy the Nexplane agent via the CR lifecycle, run all 5 FILO phases against it, and terminate it only on full pass — leaving it alive for debugging on any failure.

**Architecture:** Add `PHASE_0_provision` to the existing smoke file. It provisions via the CR lifecycle (same httpx + JWT pattern already used in the file for rollback endpoints), registers a pytest finalizer for conditional teardown, and stores the asset UUID in `_STATE`. Existing phases read `asset_id` from `_STATE` instead of the `ASSET_ID` env var. `ASSET_ID` env var remains as an escape hatch: if set, PHASE_0 skips provisioning entirely.

**Tech Stack:** Python/asyncio, httpx, boto3, pytest-asyncio, Nexplane CR lifecycle (`ec2_launch`, `deploy_nexplane_agent`). No Tailscale — both the backend container and the new instance are in the same VPC; the agent phones home via the backend's private VPC IP.

---

## Global Constraints

- NEVER use `from __future__ import annotations`.
- Maximum two hosts for smoke infrastructure — PHASE_0 provisions exactly one EC2 instance.
- All provisioning goes through the Nexplane CR lifecycle (create → plan → approve → execute), not direct boto3 calls.
- Teardown uses boto3 `ec2.terminate_instances()` directly (same pattern as `test_agent_live.py`) — no rollback CR for teardown, because the instance may be mid-failure.
- Provisioning helpers are duplicated inline in the smoke file — no import from `test_agent_live.py`.
- Instance type: `t3.small`, OS: `amazon_linux` (Amazon Linux 2023), IAM profile: `NexplaneEC2TestProfile`.
- No Tailscale dependency — VPC-internal connectivity only.

---

## PHASE_0: Provision

### Environment variables

| Var | Required | Purpose |
|---|---|---|
| `API_TOKEN` | always | nxp_... token for JWT derivation and CR auth |
| `ASSET_ID` | optional | If set, skip provisioning and use this asset UUID |
| `CLOUD_ACCOUNT_ID` | optional | AWS connector asset UUID; if unset, discovered via `GET /assets?asset_type=cloud_account` (first result) |

### Provisioning flow (when `ASSET_ID` not set)

1. **Resolve backend private IP** — query the EC2 instance metadata endpoint from inside the container:
   ```
   GET http://169.254.169.254/latest/meta-data/local-ipv4  (timeout=2s)
   ```
   If that fails (metadata not reachable from Docker), fall back to boto3:
   ```python
   own_id = requests.get("http://169.254.169.254/latest/meta-data/instance-id", timeout=2).text
   ec2 = boto3.client("ec2")
   ec2.describe_instances(InstanceIds=[own_id])["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
   ```
   Store as `_STATE["backend_private_ip"]`. Used as `nexplane_url = f"http://{backend_private_ip}:8000"`.

2. **Discover cloud account** — `GET /assets?asset_type=cloud_account` → first result; fail with clear message if none found.

3. **Get agent secret** — `GET /settings/agent-secret` → store in `_STATE["agent_secret"]`.

4. **`ec2_launch` CR** — create on the cloud account asset:
   ```json
   {
     "name": "nexplane-smoke-filo",
     "os": "amazon_linux",
     "instance_type": "t3.small",
     "iam_instance_profile": "NexplaneEC2TestProfile",
     "rollback_strategy": "terminate_instance"
   }
   ```
   Poll until `completed`. Extract `instance_id` and EC2 asset UUID from CR result.

5. **Sleep 180s** — wait for SSM agent to register on the new instance (same duration as `test_agent_live.py`).

6. **`deploy_nexplane_agent` CR** — create on the EC2 asset:
   ```json
   {
     "instance_id": "<instance_id>",
     "nexplane_url": "http://<backend_private_ip>:8000",
     "nexplane_secret": "<agent_secret>"
   }
   ```
   Poll until `completed`.

7. **Poll for agent asset registration** — `GET /assets?q=nexplane-smoke-filo&asset_type=server` every 15s, up to 120s. Fail if not registered. Store the registered asset UUID as `_STATE["asset_id"]`.

8. Store `instance_id`, `provisioned=True` in `_STATE`.

### Escape hatch (when `ASSET_ID` is set)

Store `_STATE["asset_id"] = ASSET_ID`, `_STATE["provisioned"] = False`. No provisioning. No teardown.

### Finalizer registration

Registered via `request.addfinalizer(_teardown)` in PHASE_0. The finalizer:
- If `_STATE.get("provisioned")` is False: no-op.
- If `_STATE.get("failed")` is True: print instance ID and private IP to stdout; leave running for debugging.
- Otherwise: call `_terminate_instance(_STATE["instance_id"])` and `_delete_smoke_asset(_STATE["asset_id"])`.

### Failure flag

A module-level `pytest_runtest_logreport` hook sets `_STATE["failed"] = True` whenever `report.failed` is True and `report.when == "call"`. This covers any of the 5 existing phases failing.

---

## Changes to Existing Phases

- Replace all `_env("ASSET_ID")` calls with `_STATE["asset_id"]`.
- `token` continues to come from `_env("API_TOKEN")`.
- No other changes to PHASE_1–5 logic.

---

## Teardown Helpers (duplicated inline)

```python
def _terminate_instance(instance_id: str) -> None:
    import boto3
    ec2 = boto3.client("ec2")
    ec2.terminate_instances(InstanceIds=[instance_id])

async def _delete_smoke_asset(asset_id: str) -> None:
    jwt = await _get_jwt(_env("API_TOKEN"))
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        await client.delete(f"/assets/{asset_id}",
                            headers={"Authorization": f"Bearer {jwt}"})
```

Note: `_terminate_instance` is sync (boto3 is synchronous); called from the sync finalizer. `_delete_smoke_asset` is async; the finalizer runs it via `asyncio.get_event_loop().run_until_complete(...)`.

---

## CR Polling Helper

`_wait_for_cr(cr_id, jwt, timeout=600)` — polls `GET /change-requests/{cr_id}` every 10s until `status == "completed"` or timeout. Raises `pytest.fail(...)` on timeout or `failed` status.

---

## Run Commands

Fresh instance (no env vars needed beyond token):
```bash
API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_filo_rollback.py -v -s
```

Reuse existing asset (skip provisioning):
```bash
ASSET_ID=<uuid> API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_filo_rollback.py -v -s
```

With explicit cloud account:
```bash
CLOUD_ACCOUNT_ID=<uuid> API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_filo_rollback.py -v -s
```
