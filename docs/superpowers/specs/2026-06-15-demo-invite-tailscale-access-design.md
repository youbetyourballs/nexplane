# Demo Invite: Tailscale Access for Guests

**Date:** 2026-06-15
**Status:** Approved

## Problem

We need a repeatable way to invite demo guests (e.g. "Lucas Nelson") to join the Tailscale network and access the Nexplane UI. The invite must be copyable into a text message or email. The flow must go through the platform's CR lifecycle for dogfooding.

## Constraints

- Use live Tailscale OAuth credentials already in the platform DB (connector id: `f38d95f0-e4ab-4ede-9790-86963bdee53f`)
- Guests use existing demo accounts (`admin@acme.example` / `admin123`) — no per-guest platform user
- The `generate_auth_key` executor already exists but is not wired as a CR type — that gap must be closed
- Frontend is stopped and has `VITE_API_URL=http://localhost:8000` which breaks for remote browsers — must be fixed

---

## Part 1: New CR Type — `tailscale_generate_auth_key`

### ChangeType enum (`backend/app/models/change_request.py`)
Add: `tailscale_generate_auth_key = "tailscale_generate_auth_key"`

### Change type definition (`backend/app/connectors/change_type_definitions/tailscale_generate_auth_key.json`)
```json
{
  "change_type": "tailscale_generate_auth_key",
  "display_name": "Generate Tailscale Auth Key",
  "steps": [
    {"generic_action": "generate_auth_key", "purpose": "execute", "required": true, "param_overrides": {}}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": []
}
```

### Planning engine (`backend/app/services/planning_engine.py`)
Add to the `desired_outcome → plan params` dispatch map:
```python
"tailscale_generate_auth_key": {
    "expiry_seconds": desired.get("expiry_seconds", 86400),
    "reusable":       desired.get("reusable", False),
    "ephemeral":      desired.get("ephemeral", False),
    "tags":           desired.get("tags", []),
},
```

### Safety engine (`backend/app/services/safety_engine.py`)
Add `ChangeType.tailscale_generate_auth_key` to the low-risk / no-approval-required set (alongside `tailscale_join`, `tailscale_remove`).

### Executor rollback (`backend/app/connectors/executors/tailscale/generate_auth_key.py`)
Add a `DELETE /api/v2/tailnet/{tailnet}/keys/{key_id}` rollback call. The execution result already includes `id` (the key ID). If `source == "stored"` (pre-generated static key), rollback is a no-op. Add a `ts_delete` helper to `_client.py`.

---

## Part 2: `scripts/demo_invite.py`

Standalone script — no imports beyond stdlib + `httpx`. Runs against `http://localhost:8000` from inside the container, or against `http://100.101.186.39:8000` from the EC2 host.

### Usage
```
python3 scripts/demo_invite.py --name "Lucas Nelson"
```

### Flow
1. `POST /auth/login` as `admin@acme.example` → JWT
2. `POST /change-requests` — title: `Demo access — {name}`, type: `tailscale_generate_auth_key`, `desired_outcome`: `{expiry_seconds: 86400, reusable: false, ephemeral: false}`, `target_asset_ids: []`
3. `POST /change-requests/{id}/plan`
4. `POST /auth/login` as `approver@acme.example` → approver JWT
5. `POST /change-requests/{id}/approve` (approver token)
6. `POST /change-requests/{id}/execute` (admin token)
7. Poll `GET /change-requests/{id}` (1s interval, 60s timeout) until `status == "completed"`
8. Extract `auth_key` from `execution_runs[0].result`
9. Print invite message to stdout

### Output message
```
Hi {first_name}! To access the Nexplane demo:

1. Install Tailscale: https://tailscale.com/download

2. After installing, connect using this key:
   tailscale up --authkey {auth_key}

   On Windows/Mac: open the Tailscale app, click "Log in",
   then paste the key above when prompted.

3. Open http://100.101.186.39:3000 in your browser

4. Log in with:
   Email:    admin@acme.example
   Password: admin123

The key expires in 24 hours. Reply here if you run into anything!
```

The script also prints the CR ID so the invite is traceable in the platform.

---

## Part 3: Frontend + Services Restart

### Problem
`VITE_API_URL=http://localhost:8000` is baked into the running frontend at Vite dev-server serve time. Remote browsers resolve `localhost` as their own machine, not the server.

### Fix
Override `VITE_API_URL` to the Tailscale IP when starting the frontend container:
```
VITE_API_URL=http://100.101.186.39:8000 docker compose up frontend -d
```

This does not require a rebuild — Vite dev server reads the env var at startup and substitutes it into `import.meta.env.VITE_API_URL` for all served files.

### Also start
- `mailhog` (email notifications visible during demo)

---

## Rollback Story

A `tailscale_generate_auth_key` CR can be rolled back to revoke the key early (e.g. if the demo is cancelled). The executor's `rollback` calls `DELETE /api/v2/tailnet/-/keys/{key_id}`. If the key has already expired or been used, the rollback is a no-op.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/models/change_request.py` | Add `tailscale_generate_auth_key` to `ChangeType` enum |
| `backend/app/connectors/change_type_definitions/tailscale_generate_auth_key.json` | New file |
| `backend/app/services/planning_engine.py` | Add dispatch entry |
| `backend/app/services/safety_engine.py` | Add to no-approval set |
| `backend/app/connectors/executors/tailscale/_client.py` | Add `ts_delete` helper |
| `backend/app/connectors/executors/tailscale/generate_auth_key.py` | Implement rollback |
| `scripts/demo_invite.py` | New script |
| EC2 docker compose | Restart frontend + mailhog with correct env |
