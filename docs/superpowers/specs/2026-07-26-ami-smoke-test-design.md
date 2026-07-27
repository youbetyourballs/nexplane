# AMI Smoke Test — Expanded Coverage Design

## Goal

Expand `packer/smoke_ami.py` from 9 to 14 phases, covering initialization quality, all seeded user accounts, frontend route accessibility, demo vs. non-demo mode state, MCP protocol verification, and systemd resilience. Fix credential inconsistency across seed data, login UI, and AMI defaults. CI pipeline wiring is unchanged — the same `smoke-ami` job runs the script and gates `publish-manifest`.

## Architecture

Single-script expansion of the existing flat-phase pattern in `packer/smoke_ami.py`. One EC2 instance is launched per smoke run; all 14 phases run sequentially against it; the instance is terminated in the `finally` block regardless of outcome. No CI changes required.

SSH interactions are routed through a thin `ssh_run(host, cmd, ...)` helper that accepts either a key-path or username/password for auth — key-pair only today, but the interface supports VMware OVA and other image types that require password auth when those pipelines are built.

## Credential standardization (prerequisite fixes)

Before the smoke test is written, two files must be updated to use consistent credentials across all deployment types:

| File | Field | Old value | New value |
|------|-------|-----------|-----------|
| `backend/seed.py` | `ADMIN_EMAIL` fallback | `admin@acme.example` | `admin@nexplane.local` |
| `backend/seed.py` | `ADMIN_PASSWORD` fallback | `admin123` | `changeme` |
| `frontend/src/pages/Login.tsx:83` | Demo account hint | `admin@acme.example / admin123` | `admin@nexplane.local / changeme` |

`packer/docker-compose.ami.yml` and `README.md` already use `admin@nexplane.local / changeme` and require no change.

## Seeded accounts (DEMO_MODE=true)

| Email | Password | Role |
|-------|----------|------|
| `admin@nexplane.local` | `changeme` | admin |
| `operator@acme.example` | `operator123` | security_operator |
| `approver@acme.example` | `approver123` | approver |
| `auditor@acme.example` | `auditor123` | auditor |

Phases 3 and 8 verify all four accounts.

## Phase map

| Phase | Name | Type | Summary |
|-------|------|------|---------|
| 1 | launch+network | existing | EC2 launch, status OK, port 80 responds |
| 2 | container-health | existing | All 3 containers running via `docker ps` |
| 3 | authentication | existing | Admin login + `/auth/me`; **patch**: also login all 4 seeded accounts |
| 4 | asset-management | existing | Asset CRUD, dependency graph, impact simulation |
| 5 | connector-configuration | existing | Connector create + credential store |
| 6 | cr-lifecycle | existing | CR create → plan → approve → execute |
| 7 | rollback | existing | CR rollback attempt + `rollback_available` field |
| 8 | feature-surface | existing | Endpoint spot-check; **patch**: audit log, second user, 401 rejection, agent download |
| 9 | mcp-server | existing | Agent token + SSE; **patch**: initialize handshake, tools/list, tool call |
| 10 | initialization-quality | new | Alembic at head, zero ERROR logs on clean boot |
| 11 | frontend-routes | new | All known routes return 200 text/html; critical static assets load |
| 12 | demo-mode-on | new | Demo org, assets, connectors present when DEMO_MODE=true |
| 13 | demo-mode-off | new | Reconfigure to DEMO_MODE=false, restart, assert empty state |
| 14 | systemd-resilience | new | `systemctl restart nexplane`, wait for healthy, re-authenticate |

## Phase details

### Phase 3 patch — authentication

Existing: admin login + `/auth/me`.

Add: after verifying admin, loop over all four seeded accounts. For each, `POST /api/auth/login` and assert 200 + token. Then `GET /api/auth/me` and assert `role` matches expected value. All four must pass.

### Phase 8 patch — feature-surface

Existing endpoint loop gains two entries: `GET /api/audit-log` and `GET /api/users`.

After the loop, four new checks:

**Audit log non-empty:** `GET /api/audit-log` response must contain at least one entry (actions from phases 4–7 should have written audit records).

**Second user create + login:** `POST /api/users` with `smoke-user@nexplane.local` / `SmokePass123!`. Then `POST /api/auth/login` with those creds → assert token returned and `role` field present. Token discarded after verification.

**Bad password rejection:** `POST /api/auth/login` with `admin@nexplane.local` / `wrongpassword` → assert 401. Confirms the auth rejection path is functional.

**Agent download URL:** `HEAD https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/latest/linux-amd64/nexplane-agent` using `requests.head()` directly. Assert 200 + `Content-Length` header is present and non-zero.

### Phase 9 patch — mcp-server

Existing: agent token create/list/delete + SSE endpoint connection check.

Add after SSE check:

**`initialize` handshake:** Send MCP JSON-RPC `initialize` message to `/api/mcp` (HTTP POST, `Content-Type: application/json`, `Authorization: Bearer {agent_token}`). Payload:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "clientInfo": {"name": "smoke", "version": "0.0.1"},
    "capabilities": {}
  }
}
```
Assert response contains `result.serverInfo.name` (non-empty string) and `result.capabilities` (dict).

**`tools/list`:** Send `{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}`. Assert `result.tools` is a non-empty array. Catches tool registration failures from import errors or catalog startup failures.

**Lightweight tool call:** Call `list_change_requests` (or `list_assets`) with empty params. Assert response is a valid JSON-RPC result object (has `result` key, not `error` key). Confirms MCP → backend → DB path is fully operational under agent token auth.

### Phase 10 — initialization-quality (new)

**Alembic revision check:** `ssh_run("docker exec nexplane-backend-1 alembic current 2>&1")`. Assert output contains `(head)`. Catches silent migration failures where the DB is running but at a stale revision.

**Error log scan:** `ssh_run("docker logs nexplane-backend-1 2>&1 | grep -c ' ERROR '")`. Assert count is 0. If non-zero, retrieve and log the matching lines for diagnosis, then fail. Alembic migration log lines (which may include the word ERROR in table names) are filtered with `grep -v alembic`.

### Phase 11 — frontend-routes (new)

**Route coverage:** `GET http://{public_ip}{route}` for each of:
```
/    /login    /assets    /change-requests
/connectors    /settings    /users    /audit-log
```
Assert each returns `200` with `Content-Type: text/html`.

**Static asset integrity:** Parse the HTML body from `GET http://{public_ip}/` and extract all `<script src="...">` and `<link rel="stylesheet" href="...">` tags. `HEAD` each referenced path. Assert all return 200. Catches Vite build mismatches where the HTML references a hashed bundle that wasn't written into the image.

### Phase 12 — demo-mode-on (new)

AMI boots with `DEMO_MODE=true` — no reconfiguration needed; runs against the already-running instance.

- `GET /api/assets` → assert response list or `total` field is > 0
- `GET /api/connectors` → assert response list or `total` field is > 0
- `GET /api/organizations` → assert at least 1 org present

Exact response shape (list vs. paginated envelope) confirmed against live API during implementation.

### Phase 13 — demo-mode-off (new)

1. `ssh_run("sudo sed -i 's/DEMO_MODE: \"true\"/DEMO_MODE: \"false\"/' /opt/nexplane/docker-compose.ami.yml")`
2. `ssh_run("sudo docker compose -f /opt/nexplane/docker-compose.ami.yml up -d --force-recreate backend")`
3. Poll `POST /api/auth/login` with admin creds until 200 (120s timeout, 10s interval) — backend re-initializes DB on startup
4. Assert `GET /api/assets` returns empty (list length 0 or `total: 0`)
5. Assert `GET /api/connectors` returns empty
6. Assert `GET /api/organizations` returns exactly 1 org

Restore before phase 14:
7. `ssh_run("sudo sed -i 's/DEMO_MODE: \"false\"/DEMO_MODE: \"true\"/' /opt/nexplane/docker-compose.ami.yml")`
8. `ssh_run("sudo docker compose -f /opt/nexplane/docker-compose.ami.yml up -d --force-recreate backend")`
9. Poll login until 200 (60s timeout)

### Phase 14 — systemd-resilience (new)

1. `ssh_run("sudo systemctl restart nexplane")`
2. Poll `GET http://{public_ip}/` until 200 (180s timeout, 10s interval)
3. `POST /api/auth/login` with admin creds → assert token returned

Confirms the systemd unit correctly restarts all containers and the platform returns to a healthy state — the primary recovery path after an instance reboot or failed platform update.

## SSH helper abstraction

All SSH interactions route through a single `ssh_run(host, cmd, key_path=None, username="ubuntu", password=None, timeout=30)` helper. Today it always uses key-pair auth. The signature accepts `password` for future image types (VMware OVA, bare-metal) that require password auth instead of a key. The main loop passes `key_path=args.key_name + ".pem"` everywhere; no call site encodes the auth method directly.

## Error handling

- Each phase raises `RuntimeError` on failure; the outer `try/finally` catches and prints the error, then terminates the instance before exiting 1.
- Phase 13 restore steps run in a nested `try/finally` so a failed assertion in demo-mode-off doesn't leave the instance in a broken state for phase 14.
- SSH failures (non-zero exit code) always log the stderr output before raising.

## CI impact

No changes to `.github/workflows/release.yml`. The `smoke-ami` job already runs `packer/smoke_ami.py` and gates `publish-manifest` on exit code 0. The expanded script is a drop-in replacement.

## Out of scope

- VMware OVA build pipeline (the SSH helper interface supports it; the pipeline does not exist yet)
- Performance benchmarks or load testing
- Multi-region AMI validation
- Connector live execution (fake credentials are intentional in phase 6)
