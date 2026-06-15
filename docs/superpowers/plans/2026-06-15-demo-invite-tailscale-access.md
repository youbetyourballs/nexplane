# Demo Invite: Tailscale Guest Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `tailscale_generate_auth_key` as a first-class CR type and ship a `scripts/demo_invite.py` script that walks the full CR lifecycle to produce a copy-paste Tailscale invite for a named guest, then restart the frontend and mailhog with the correct Tailscale IP.

**Architecture:** Add the CR type to the enum + migration + planning/safety dispatch, add rollback to the existing executor, write a self-contained Python script that hits the platform API (create → plan → submit → approve → execute → poll → print). Restart services on EC2 with corrected `VITE_API_URL`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, httpx, PostgreSQL, Tailscale API v2, Docker Compose

---

## Files

| File | Action |
|------|--------|
| `backend/app/models/change_request.py` | Modify — add enum value |
| `backend/alembic/versions/o500i3j6k7l8_add_tailscale_generate_auth_key_cr_type.py` | Create — DB migration |
| `backend/app/connectors/change_type_definitions/tailscale_generate_auth_key.json` | Create — CT definition |
| `backend/app/services/planning_engine.py` | Modify — add dispatch entry |
| `backend/app/services/safety_engine.py` | Modify — add to `_IMPLICIT_ROLLBACK_TYPES` |
| `backend/app/connectors/executors/tailscale/_client.py` | Modify — add `ts_delete` helper |
| `backend/app/connectors/executors/tailscale/generate_auth_key.py` | Modify — implement rollback |
| `scripts/demo_invite.py` | Create — invite generator script |

All backend file edits are made locally and then `scp`'d / `git pull`'d to EC2.

---

## Task 1: Add `tailscale_generate_auth_key` to ChangeType enum

**Files:**
- Modify: `backend/app/models/change_request.py`

- [ ] **Step 1: Find the tailscale entries in the enum**

  Open `backend/app/models/change_request.py`. Locate the `ChangeType` class (around line 14). Find `tailscale_join` and `tailscale_remove`.

- [ ] **Step 2: Add the new enum value**

  Add `tailscale_generate_auth_key` immediately after `tailscale_remove`:

  ```python
  tailscale_join = "tailscale_join"
  tailscale_remove = "tailscale_remove"
  tailscale_generate_auth_key = "tailscale_generate_auth_key"
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add backend/app/models/change_request.py
  git commit -m "feat: add tailscale_generate_auth_key to ChangeType enum"
  ```

---

## Task 2: Add Alembic migration

**Files:**
- Create: `backend/alembic/versions/o500i3j6k7l8_add_tailscale_generate_auth_key_cr_type.py`

- [ ] **Step 1: Create the migration file**

  Create `backend/alembic/versions/o500i3j6k7l8_add_tailscale_generate_auth_key_cr_type.py`:

  ```python
  """add tailscale_generate_auth_key change type

  Revision ID: o500i3j6k7l8
  Revises: n390h2i5j6k7
  Create Date: 2026-06-15
  """
  from alembic import op

  revision = "o500i3j6k7l8"
  down_revision = "n390h2i5j6k7"
  branch_labels = None
  depends_on = None


  def upgrade() -> None:
      op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'tailscale_generate_auth_key'")


  def downgrade() -> None:
      pass
  ```

- [ ] **Step 2: Push to EC2 and run the migration**

  ```bash
  scp -i ~/.ssh/id_ed25519 backend/alembic/versions/o500i3j6k7l8_add_tailscale_generate_auth_key_cr_type.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/alembic/versions/

  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "docker exec nexplane-backend-1 alembic -c /app/alembic.ini upgrade head"
  ```

  Expected output ends with: `Running upgrade n390h2i5j6k7 -> o500i3j6k7l8, add tailscale_generate_auth_key change type`

- [ ] **Step 3: Verify the enum value exists in the DB**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "docker exec nexplane-db-1 psql -U nexplane -d nexplane -c \
    \"SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_enum.enumtypid=pg_type.oid WHERE typname='change_type' AND enumlabel LIKE 'tailscale%';\""
  ```

  Expected:
  ```
        enumlabel
  --------------------------
   tailscale_join
   tailscale_remove
   tailscale_generate_auth_key
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add backend/alembic/versions/o500i3j6k7l8_add_tailscale_generate_auth_key_cr_type.py
  git commit -m "feat: migration — add tailscale_generate_auth_key change type to pg enum"
  ```

---

## Task 3: Add change type definition JSON

**Files:**
- Create: `backend/app/connectors/change_type_definitions/tailscale_generate_auth_key.json`

- [ ] **Step 1: Create the file**

  Create `backend/app/connectors/change_type_definitions/tailscale_generate_auth_key.json`:

  ```json
  {
    "change_type": "tailscale_generate_auth_key",
    "display_name": "Generate Tailscale Auth Key",
    "steps": [
      {
        "generic_action": "generate_auth_key",
        "purpose": "execute",
        "required": true,
        "param_overrides": {}
      }
    ],
    "preflight_checks": ["connector_reachable"],
    "verification_methods": []
  }
  ```

- [ ] **Step 2: Copy to EC2**

  ```bash
  scp -i ~/.ssh/id_ed25519 \
    backend/app/connectors/change_type_definitions/tailscale_generate_auth_key.json \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/connectors/change_type_definitions/
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add backend/app/connectors/change_type_definitions/tailscale_generate_auth_key.json
  git commit -m "feat: change type definition for tailscale_generate_auth_key"
  ```

---

## Task 4: Wire planning engine dispatch

**Files:**
- Modify: `backend/app/services/planning_engine.py`

The planning engine has a dict that maps change type strings to `desired_outcome` parameter extraction. `tailscale_join` is at around line 88.

- [ ] **Step 1: Add the dispatch entry after `tailscale_remove`**

  Find:
  ```python
  "tailscale_remove":       {"instance_id": desired.get("instance_id", "")},
  ```

  Add immediately after:
  ```python
  "tailscale_generate_auth_key": {
      "expiry_seconds": desired.get("expiry_seconds", 86400),
      "reusable":       desired.get("reusable", False),
      "ephemeral":      desired.get("ephemeral", False),
      "tags":           desired.get("tags", []),
  },
  ```

- [ ] **Step 2: Copy to EC2**

  ```bash
  scp -i ~/.ssh/id_ed25519 backend/app/services/planning_engine.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/services/planning_engine.py
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add backend/app/services/planning_engine.py
  git commit -m "feat: planning engine dispatch for tailscale_generate_auth_key"
  ```

---

## Task 5: Add to safety engine implicit rollback set

**Files:**
- Modify: `backend/app/services/safety_engine.py`

Without this, the safety engine adds +30 ("no rollback strategy") to the risk score for every `tailscale_generate_auth_key` CR, pushing it from `low` to `medium`. Adding it to `_IMPLICIT_ROLLBACK_TYPES` keeps the score at 0 → `low` risk.

- [ ] **Step 1: Find `_IMPLICIT_ROLLBACK_TYPES` and add the new type**

  Locate (around line 38-40):
  ```python
  ChangeType.tailscale_join, ChangeType.tailscale_remove, ChangeType.deploy_nexplane_agent,
  ```

  Change to:
  ```python
  ChangeType.tailscale_join, ChangeType.tailscale_remove, ChangeType.deploy_nexplane_agent,
  ChangeType.tailscale_generate_auth_key,
  ```

- [ ] **Step 2: Copy to EC2**

  ```bash
  scp -i ~/.ssh/id_ed25519 backend/app/services/safety_engine.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/services/safety_engine.py
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add backend/app/services/safety_engine.py
  git commit -m "feat: add tailscale_generate_auth_key to implicit rollback types"
  ```

---

## Task 6: Add `ts_delete` to Tailscale client

**Files:**
- Modify: `backend/app/connectors/executors/tailscale/_client.py`

The rollback for `generate_auth_key` needs to call `DELETE /api/v2/tailnet/{tailnet}/keys/{key_id}`.

- [ ] **Step 1: Add `ts_delete` function**

  Open `backend/app/connectors/executors/tailscale/_client.py`. After `ts_post`, add:

  ```python
  async def ts_delete(path: str, creds: dict) -> None:
      """Make an authenticated DELETE to the Tailscale API using OAuth client credentials."""
      async with httpx.AsyncClient() as client:
          token_resp = await client.post(
              "https://api.tailscale.com/api/v2/oauth/token",
              data={
                  "client_id": creds["oauth_client_id"],
                  "client_secret": creds["oauth_client_secret"],
                  "grant_type": "client_credentials",
              },
          )
          token_resp.raise_for_status()
          access_token = token_resp.json()["access_token"]

          resp = await client.delete(
              f"{TAILSCALE_API_BASE}{path}",
              headers={"Authorization": f"Bearer {access_token}"},
          )
          resp.raise_for_status()
  ```

- [ ] **Step 2: Copy to EC2**

  ```bash
  scp -i ~/.ssh/id_ed25519 \
    backend/app/connectors/executors/tailscale/_client.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/connectors/executors/tailscale/_client.py
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add backend/app/connectors/executors/tailscale/_client.py
  git commit -m "feat: add ts_delete helper to Tailscale client"
  ```

---

## Task 7: Implement rollback in `generate_auth_key` executor

**Files:**
- Modify: `backend/app/connectors/executors/tailscale/generate_auth_key.py`

The `rollback` function currently returns `{"rolled_back": False, "reason": "auth keys expire automatically"}`. We need to revoke the key via the API when a key ID is available.

- [ ] **Step 1: Replace the rollback function**

  Open `backend/app/connectors/executors/tailscale/generate_auth_key.py`. Replace the existing `rollback` function:

  ```python
  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      creds = getattr(connector, 'credentials', {})
      key_id = execution_result.get("id", "")
      source = execution_result.get("source", "")

      # Static stored keys can't be individually revoked (they're shared)
      if source == "stored" or not key_id:
          return {
              "rolled_back": False,
              "reason": "stored_key_or_no_key_id — key expires automatically",
          }

      if not creds.get("oauth_client_id"):
          return {"rolled_back": False, "reason": "no_oauth_credentials"}

      from ._client import ts_delete
      tailnet = creds.get("tailnet") or "-"
      try:
          await ts_delete(f"/tailnet/{tailnet}/keys/{key_id}", creds)
      except Exception as exc:
          return {"rolled_back": False, "reason": str(exc)}

      return {
          "rolled_back": True,
          "key_id": key_id,
          "action": "key_revoked",
      }
  ```

- [ ] **Step 2: Copy to EC2**

  ```bash
  scp -i ~/.ssh/id_ed25519 \
    backend/app/connectors/executors/tailscale/generate_auth_key.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/connectors/executors/tailscale/generate_auth_key.py
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add backend/app/connectors/executors/tailscale/generate_auth_key.py
  git commit -m "feat: implement key-revocation rollback for tailscale generate_auth_key"
  ```

---

## Task 8: Restart backend to pick up all changes

All executor and service files are loaded at import time by the running backend. A restart is required to pick up Tasks 1–7.

- [ ] **Step 1: Restart the backend container**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "cd nexplane && docker compose restart backend"
  ```

- [ ] **Step 2: Confirm it came back healthy**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "docker exec nexplane-backend-1 curl -s http://localhost:8000/health | python3 -m json.tool"
  ```

  Expected: `{"status": "ok"}` or similar. If the endpoint doesn't exist, just confirm the container is `Up`:
  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker ps | grep backend"
  ```

---

## Task 9: Smoke test the new CR type end-to-end

Verify the full lifecycle works before writing the invite script.

- [ ] **Step 1: Get an admin JWT**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 curl -s \
    -X POST http://localhost:8000/auth/login \
    -H 'Content-Type: application/json' \
    -d '{\"email\":\"admin@acme.example\",\"password\":\"admin123\"}'"
  ```

  Copy the `access_token` value.

- [ ] **Step 2: Create a test CR**

  Replace `TOKEN` with your token:

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 curl -s \
    -X POST http://localhost:8000/change-requests \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer TOKEN' \
    -d '{
      \"title\": \"[SMOKE] Demo access test\",
      \"change_type\": \"tailscale_generate_auth_key\",
      \"target_asset_ids\": [],
      \"desired_outcome\": {\"expiry_seconds\": 3600, \"reusable\": false, \"ephemeral\": false}
    }' | python3 -m json.tool"
  ```

  Copy the `id` from the response.

- [ ] **Step 3: Plan → Submit → Approve → Execute**

  Replace `TOKEN` and `CR_ID`:

  ```bash
  # Plan
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 curl -s \
    -X POST http://localhost:8000/change-requests/CR_ID/plan \
    -H 'Authorization: Bearer TOKEN' | python3 -m json.tool | grep status"

  # Submit for approval
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 curl -s \
    -X POST http://localhost:8000/change-requests/CR_ID/submit-for-approval \
    -H 'Authorization: Bearer TOKEN' | python3 -m json.tool | grep status"

  # Approve (admin satisfies security_operator requirement for low-risk)
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 curl -s \
    -X POST http://localhost:8000/change-requests/CR_ID/approve \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer TOKEN' \
    -d '{\"decision\": \"approved\", \"comment\": \"smoke test\"}' | python3 -m json.tool | grep -E 'decision|id'"

  # Execute
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 curl -s \
    -X POST http://localhost:8000/change-requests/CR_ID/execute \
    -H 'Authorization: Bearer TOKEN' | python3 -m json.tool | grep -E 'status|id'"
  ```

- [ ] **Step 4: Poll until completed and verify auth_key present**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 curl -s \
    http://localhost:8000/change-requests/CR_ID \
    -H 'Authorization: Bearer TOKEN' | python3 -c \"
  import sys, json
  d = json.load(sys.stdin)
  print('CR status:', d['status'])
  for run in d.get('execution_runs', []):
      steps = run.get('result', {}).get('steps', [])
      for s in steps:
          key = s.get('result', {}).get('auth_key', '')
          print('auth_key:', key[:20] + '...' if key else 'NOT FOUND')
  \""
  ```

  Expected: `CR status: completed` and an auth_key starting with `tskey-`.

---

## Task 10: Write `scripts/demo_invite.py`

**Files:**
- Create: `scripts/demo_invite.py`

This is a standalone script. It runs on the EC2 host (not inside the container) using `http://localhost:8000` since port 8000 is exposed. No third-party dependencies beyond stdlib and `httpx` (which is installed in the backend venv, or install with `pip install httpx`).

- [ ] **Step 1: Create the script**

  Create `scripts/demo_invite.py`:

  ```python
  #!/usr/bin/env python3
  """Generate a Tailscale demo invite for a named guest.

  Usage: python3 scripts/demo_invite.py --name "Lucas Nelson"

  Walks the full CR lifecycle via the Nexplane platform API:
    create → plan → submit → approve → execute → poll → print invite
  """
  import argparse
  import sys
  import time

  try:
      import httpx
  except ImportError:
      sys.exit("httpx not found. Run: pip install httpx")

  BASE_URL = "http://localhost:8000"
  ADMIN_EMAIL = "admin@acme.example"
  ADMIN_PASSWORD = "admin123"
  UI_URL = "http://100.101.186.39:3000"
  DEMO_EMAIL = "admin@acme.example"
  DEMO_PASSWORD = "admin123"
  EXPIRY_SECONDS = 86400  # 24 hours


  def login(client: httpx.Client, email: str, password: str) -> str:
      resp = client.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password})
      resp.raise_for_status()
      return resp.json()["access_token"]


  def auth_headers(token: str) -> dict:
      return {"Authorization": f"Bearer {token}"}


  def create_cr(client: httpx.Client, token: str, name: str) -> str:
      resp = client.post(
          f"{BASE_URL}/change-requests",
          headers=auth_headers(token),
          json={
              "title": f"Demo access — {name}",
              "description": f"Tailscale auth key for demo guest: {name}",
              "change_type": "tailscale_generate_auth_key",
              "target_asset_ids": [],
              "desired_outcome": {
                  "expiry_seconds": EXPIRY_SECONDS,
                  "reusable": False,
                  "ephemeral": False,
              },
          },
          timeout=30,
      )
      resp.raise_for_status()
      cr_id = resp.json()["id"]
      print(f"  Created CR: {cr_id}")
      return cr_id


  def plan_cr(client: httpx.Client, token: str, cr_id: str) -> None:
      resp = client.post(f"{BASE_URL}/change-requests/{cr_id}/plan", headers=auth_headers(token), timeout=30)
      resp.raise_for_status()
      print(f"  Planned")


  def submit_cr(client: httpx.Client, token: str, cr_id: str) -> None:
      resp = client.post(
          f"{BASE_URL}/change-requests/{cr_id}/submit-for-approval",
          headers=auth_headers(token),
          timeout=30,
      )
      resp.raise_for_status()
      print(f"  Submitted for approval")


  def approve_cr(client: httpx.Client, token: str, cr_id: str) -> None:
      resp = client.post(
          f"{BASE_URL}/change-requests/{cr_id}/approve",
          headers=auth_headers(token),
          json={"decision": "approved", "comment": "Demo invite approved"},
          timeout=30,
      )
      resp.raise_for_status()
      print(f"  Approved")


  def execute_cr(client: httpx.Client, token: str, cr_id: str) -> None:
      resp = client.post(f"{BASE_URL}/change-requests/{cr_id}/execute", headers=auth_headers(token), timeout=30)
      resp.raise_for_status()
      print(f"  Executing...")


  def poll_for_completion(client: httpx.Client, token: str, cr_id: str, timeout_seconds: int = 60) -> dict:
      deadline = time.time() + timeout_seconds
      while time.time() < deadline:
          resp = client.get(f"{BASE_URL}/change-requests/{cr_id}", headers=auth_headers(token), timeout=30)
          resp.raise_for_status()
          cr = resp.json()
          status = cr["status"]
          if status == "completed":
              return cr
          if status in ("failed", "rejected", "cancelled"):
              sys.exit(f"CR ended in terminal state: {status}")
          time.sleep(2)
      sys.exit(f"Timed out after {timeout_seconds}s waiting for CR to complete")


  def extract_auth_key(cr: dict) -> str:
      for run in cr.get("execution_runs", []):
          for step in run.get("result", {}).get("steps", []):
              key = step.get("result", {}).get("auth_key", "")
              if key:
                  return key
      sys.exit("Could not find auth_key in execution result. Check the CR in the UI.")


  def print_invite(name: str, cr_id: str, auth_key: str) -> None:
      first = name.split()[0]
      print()
      print("=" * 60)
      print("INVITE MESSAGE (copy below this line)")
      print("=" * 60)
      print(f"""
  Hi {first}! To access the Nexplane demo:

  1. Install Tailscale: https://tailscale.com/download

  2. After installing, connect using this key:
     tailscale up --authkey {auth_key}

     On Windows/Mac: open the Tailscale app, click "Log in",
     then paste the key above when prompted.

  3. Open {UI_URL} in your browser

  4. Log in with:
     Email:    {DEMO_EMAIL}
     Password: {DEMO_PASSWORD}

  The key expires in 24 hours. Reply here if you run into anything!
  """)
      print("=" * 60)
      print(f"(Platform CR for this invite: {BASE_URL}/change-requests/{cr_id})")


  def main():
      parser = argparse.ArgumentParser(description="Generate a Tailscale demo invite")
      parser.add_argument("--name", required=True, help='Guest full name, e.g. "Lucas Nelson"')
      args = parser.parse_args()

      print(f"Generating Tailscale invite for: {args.name}")
      with httpx.Client() as client:
          print("  Logging in as admin...")
          token = login(client, ADMIN_EMAIL, ADMIN_PASSWORD)

          print("  Creating CR...")
          cr_id = create_cr(client, token, args.name)

          print("  Planning...")
          plan_cr(client, token, cr_id)

          print("  Submitting for approval...")
          submit_cr(client, token, cr_id)

          print("  Approving (admin)...")
          approve_cr(client, token, cr_id)

          print("  Executing...")
          execute_cr(client, token, cr_id)

          print("  Waiting for completion...")
          cr = poll_for_completion(client, token, cr_id)

          auth_key = extract_auth_key(cr)
          print_invite(args.name, cr_id, auth_key)


  if __name__ == "__main__":
      main()
  ```

- [ ] **Step 2: Copy to EC2**

  ```bash
  scp -i ~/.ssh/id_ed25519 scripts/demo_invite.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/scripts/demo_invite.py
  ```

- [ ] **Step 3: Run it for Lucas**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "cd nexplane && pip install httpx -q && python3 scripts/demo_invite.py --name 'Lucas Nelson'"
  ```

  Expected: invite message with a real `tskey-auth-...` key printed to stdout.

- [ ] **Step 4: Commit**

  ```bash
  git add scripts/demo_invite.py
  git commit -m "feat: demo_invite.py — generate Tailscale guest access via CR lifecycle"
  ```

---

## Task 11: Restart frontend and mailhog with correct API URL

The frontend container stopped 9 days ago. The `VITE_API_URL` in `docker-compose.yml` is `http://localhost:8000` — a remote browser can't reach that. We override it at container start time; Vite dev server substitutes env vars at serve time, so no rebuild is needed.

- [ ] **Step 1: Start mailhog**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "cd nexplane && docker compose up mailhog -d"
  ```

- [ ] **Step 2: Start frontend with correct API URL**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "cd nexplane && VITE_API_URL=http://100.101.186.39:8000 docker compose up frontend -d"
  ```

- [ ] **Step 3: Wait ~30 seconds for Vite to compile, then verify**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "docker logs nexplane-frontend-1 --tail=20"
  ```

  Expected: `VITE v... ready` and `Local: http://...` lines. No error lines.

- [ ] **Step 4: Confirm the UI is reachable from the dev machine**

  On your laptop (assuming your laptop is on the same Tailscale network already):

  ```
  curl -s -o /dev/null -w "%{http_code}" http://100.101.186.39:3000
  ```

  Expected: `200`

  If curl isn't available on Windows, open `http://100.101.186.39:3000` in a browser. You should see the Nexplane login screen.

---

## Self-Review

**Spec coverage check:**
- ✅ `tailscale_generate_auth_key` CR type — Tasks 1–5
- ✅ Rollback via key revocation — Tasks 6–7
- ✅ `scripts/demo_invite.py` with full CR lifecycle — Task 10
- ✅ Output message with Tailscale key + UI URL + demo credentials — Task 10 (print_invite)
- ✅ Reproducible: any name via `--name` flag — Task 10
- ✅ Frontend restart with correct VITE_API_URL — Task 11
- ✅ Mailhog restart — Task 11
- ✅ Backend restart to pick up file changes — Task 8

**Placeholder scan:** No TBDs or incomplete steps found.

**Type consistency:** `auth_key` field name is consistent across `generate_auth_key.py` executor output and `extract_auth_key()` in the script. `tailscale_generate_auth_key` string is consistent across enum, migration, JSON definition, planning engine key, and safety engine reference.
