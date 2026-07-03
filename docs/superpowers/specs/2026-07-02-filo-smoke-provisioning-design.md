# FILO Smoke Self-Contained Provisioning — Design

**Goal:** Make `test_smoke_filo_rollback.py` provision its own fresh EC2 instance, deploy the Nexplane agent via the CR lifecycle, run all 5 FILO phases against it, and terminate it only on full pass — leaving it alive for debugging on any failure.

**Architecture:** Add `PHASE_0_provision` to the existing smoke file. It provisions via the CR lifecycle (same httpx + JWT pattern already used in the file for rollback endpoints), registers a pytest finalizer for conditional teardown, and stores the asset UUID in `_STATE`. Existing phases read `asset_id` from `_STATE` instead of the `ASSET_ID` env var. `ASSET_ID` env var remains as an escape hatch: if set, PHASE_0 skips provisioning entirely.

**Tech Stack:** Python/asyncio, httpx, boto3, pytest-asyncio, Nexplane CR lifecycle (`ec2_launch`, `tailscale_join`, `deploy_nexplane_agent`).

---

## Global Constraints

- NEVER use `from __future__ import annotations`.
- Maximum two hosts for smoke infrastructure — PHASE_0 provisions exactly one EC2 instance.
- All provisioning goes through the Nexplane CR lifecycle (create → plan → approve → execute), not direct boto3 calls.
- Teardown uses boto3 `ec2.terminate_instances()` directly (same pattern as `test_agent_live.py`) — no rollback CR for teardown, because the instance may be mid-failure.
- Provisioning helpers are duplicated inline in the smoke file — no import from `test_agent_live.py`.
- Instance type: `t3.small`, OS: `amazon_linux` (Amazon Linux 2023), IAM profile: `NexplaneEC2TestProfile`.

---

## PHASE_0: Provision

### Environment variables

| Var | Required | Purpose |
|---|---|---|
| `API_TOKEN` | always | nxp_... token for JWT derivation and CR auth |
| `ASSET_ID` | optional | If set, skip provisioning and use this asset UUID |
| `CLOUD_ACCOUNT_ID` | optional | AWS connector asset UUID; if unset, discovered via `GET /assets?asset_type=cloud_account&q=aws` (first result) |

### Provisioning flow (when `ASSET_ID` not set)

1. **Discover cloud account** — `GET /assets?asset_type=cloud_account` filtered to first AWS connector; fail with clear message if none found.
2. **Get agent secret** — `GET /settings/agent-secret` → store in `_STATE["agent_secret"]`.
3. **Determine backend URL** — hardcoded to `http://100.101.186.39:8000` (the EC2 Tailscale IP). This is where the provisioned instance's agent phones home.
4. **Generate hostname** — `nexplane-smoke-filo` (fixed; simple, no random suffix — only one instance ever runs for this suite).
5. **`ec2_launch` CR** — create on the cloud account asset:
   ```json
   {
     "name": "nexplane-smoke-filo",
     "os": "amazon_linux",
     "instance_type": "t3.small",
     "iam_instance_profile": "NexplaneEC2TestProfile",
     "rollback_strategy": "terminate_instance"
   }
   ```
   Poll until `completed`. Extract `instance_id` and EC2 asset UUID from CR output.
6. **Sleep 180s** — wait for SSM agent to register (same duration as `test_agent_live.py`).
7. **`tailscale_join` CR** — create on the EC2 asset:
   ```json
   {
     "instance_id": "<instance_id>",
     "auth_key": "<from GET /settings/tailscale-auth-key>",
     "hostname": "nexplane-smoke-filo"
   }
   ```
   Poll until `completed`.
8. **`deploy_nexplane_agent` CR** — create on the EC2 asset:
   ```json
   {
     "instance_id": "<instance_id>",
     "nexplane_url": "http://100.101.186.39:8000",
     "nexplane_secret": "<agent_secret>"
   }
   ```
   Poll until `completed`.
9. **Poll for agent asset registration** — `GET /assets?q=nexplane-smoke-filo&asset_type=server` every 15s, up to 120s. Fail if not registered.
10. Store `asset_id`, `instance_id`, `provisioned=True` in `_STATE`.

### Escape hatch (when `ASSET_ID` is set)

Store `_STATE["asset_id"] = ASSET_ID`, `_STATE["provisioned"] = False`. No provisioning. No teardown.

### Finalizer registration

Registered via `request.addfinalizer(_teardown)` in PHASE_0. The finalizer:
- If `_STATE.get("provisioned")` is False: no-op.
- If `_STATE.get("failed")` is True: print instance ID and Tailscale IP (`nexplane-smoke-filo`) to stdout; leave running.
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
async def _terminate_instance(instance_id: str) -> None:
    import boto3
    ec2 = boto3.client("ec2")
    ec2.terminate_instances(InstanceIds=[instance_id])

async def _delete_smoke_asset(asset_id: str) -> None:
    jwt = await _get_jwt(_env("API_TOKEN"))
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        await client.delete(f"/assets/{asset_id}",
                            headers={"Authorization": f"Bearer {jwt}"})
```

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
