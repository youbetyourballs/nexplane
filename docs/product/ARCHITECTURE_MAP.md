# Architecture Map

_Onboarding map for a new Staff Engineer. Derived from the repo at `F:\Nexplane\nexplane` on branch `master`, 2026-06-05._

## Authentication

**Files**
- `backend/app/routers/auth.py` — `/auth/login`, `/auth/logout`, `/auth/me`. Login takes email/password, returns a JWT bearer token.
- `backend/app/services/auth_service.py` — `authenticate_user`, `create_access_token`. bcrypt verification, JWT signed with `SECRET_KEY`.
- `backend/app/routers/__init__.py` — `current_user` dependency decodes the bearer token and loads the `User`.
- `backend/app/services/agent_hmac.py` — HMAC-SHA256 signing/verification for agent job payloads.
- `backend/app/models/agent_token.py`, `backend/app/models/api_token.py` — long-lived tokens (agents and MCP/API clients).
- `agent/agenthmac/` — Go side of HMAC sign/verify.

**Flow**
1. UI calls `POST /auth/login` with email/password → JWT.
2. UI sends `Authorization: Bearer <jwt>` on every API call. `current_user` resolves the `User`.
3. The Go agent uses a bearer secret (shared HMAC key generated in Settings) plus per-payload HMAC signatures on `/agent/jobs/next` and `/agent/result`.
4. MCP server (`/mcp`) authenticates via agent tokens (`api_tokens` table).

**Trust boundaries**
- Frontend ↔ backend: JWT only. CORS allow-list configured by `CORS_ORIGINS` env var.
- Agent ↔ backend: bearer secret + HMAC. No inbound connections to agents — agents long-poll outward.
- Backend ↔ Postgres: trusted intra-network (Docker network).
- Backend ↔ cloud APIs: per-connector credentials stored encrypted with Fernet (AES-256), decrypted only at use in `SecretsService`.

## Authorization

**Files**
- `backend/app/models/user.py` — `UserRole = {admin, security_operator, approver, auditor, ir_responder}`.
- `backend/app/routers/__init__.py` — `require_roles(*roles)` dependency factory.
- `backend/app/services/safety_engine.py` — risk scoring, approval requirements, freeze enforcement.
- `backend/app/compliance/freeze.py` — `require_no_active_freeze` dependency.

**Role model**
- **admin** — full control plane, settings, secret/key generation, user management.
- **security_operator** — create/plan change requests, manage connector credentials.
- **approver** — approve CRs (critical requires `approver` + `admin`).
- **auditor** — read-only; audit events, evidence.
- **ir_responder** — can bypass change freeze with mandatory justification; gets expedited IR CR path.

**Authorization decisions** are dependency-injected per route. The safety engine evaluates whether a CR can proceed at plan/approve/execute time based on risk, freeze state, asset criticality, and approved-command-template membership.

## Data Model

Postgres 16 with 38+ Alembic migrations in `backend/alembic/versions/` and 30+ tables. Source of truth in `backend/app/models/`.

**Core entities**
- `organizations` — tenant container. All other rows are scoped by `organization_id`.
- `users` — `UserRole`, hashed_password, FK org.
- `assets` (`asset.py`) — servers, endpoints, cloud accounts, storage buckets, key pairs, identities. Polymorphic via `AssetType` enum; tags; environment + criticality drive risk scoring.
- `connectors` + `connector_credentials` — per-org connector instances; credentials encrypted via Fernet, versioned for rotation.
- `change_requests` (`change_request.py`) — the central table. `ChangeType` enum has 200+ values; status state machine, target_asset_ids (JSON array), risk_level, snapshot_before, finding_ids, batch_id, requester_id.
- `change_plans` + `change_plan_steps` — the planned sequence of steps for a CR. Steps declare `produces`/`consumes` slots for in-memory step-output propagation (`StepOutputStore`).
- `approvals` — per-CR approval decisions.
- `execution_runs` — one row per attempt at executing a CR.
- `audit_events` — immutable history; written by `audit_service.record_event`.
- `projects` + `project_phases` + `project_rollbacks` — grouping for related CRs with dependencies and reverse-traversal rollback.
- `runbooks` (versioned) + `runbook_executions` — composable multi-step workflows.
- `vulnerability_findings` + `remediation_policies` — scanner ingest and auto-CR rules.
- `compliance_baselines`, `compliance_attestations`, `drift_alerts`, `policy_baselines`, `security_policies` — CIS/SOC2/ISO benchmarks, drift, freeze windows.
- `agents` + `agent_tokens` — registered Nexplane agents.
- `forensic_bundles` — IR collection blobs (S3 references).
- `backup_targets`, `patch_campaigns`, `maintenance_windows`, `scheduled_ingest`, `recurring_jobs` — scheduled work.
- `access_reviews` + `access_review_schedules` + `review_campaigns` — periodic access certifications.
- `notifications`, `api_tokens`, `org_settings`, `identity_profiles`, `ir_playbook_templates`, `smoke_test_runs`.

**Key relationships**
- A `Project` has many `ChangeRequest`s and many `ProjectRollback`s.
- A `ChangeRequest` has one `ChangePlan` (which has many `ChangePlanStep`s), many `Approval`s, many `ExecutionRun`s, many `AuditEvent`s.
- A `RunbookExecution` creates `ChangeRequest`s as its `change` steps fire (`runbook_cr_bridge.py`).
- Findings feed CR creation through `vuln_remediation_engine.py`.

## API Surface

Mounted in `backend/app/main.py`. All routers prefix-scoped, all use the JWT `current_user` dependency unless explicitly an agent or webhook route.

| Router file | Prefix | Notes |
|---|---|---|
| `auth.py` | `/auth` | login/logout/me; agent-token CRUD |
| `assets.py` | `/assets` | inventory CRUD, tagging, bulk ingest |
| `connectors.py` | `/connectors` | catalog, instances, credentials (PUT only, see memory note), test, schedule, ingest |
| `change_requests.py` | `/change-requests` | full lifecycle: list/get/create/plan/approve/execute/verify/rollback; batch + bulk-approve |
| `audit.py` | `/audit-events` | immutable event read; per-CR events under `/change-requests/{id}/audit-events` |
| `projects.py` | `/projects` | CRUD, members, AI chat; DELETE; rollback start/get |
| `settings.py` | `/settings` | AI providers, agent secret, org settings |
| `agent.py` | `/agent` | `/register`, `/jobs/next`, `/result` (agent-token auth + HMAC) |
| `vulnerability.py` | `/vulnerability` + `/webhooks` | findings, policies, SLA, CVE blast radius, patch campaigns; HMAC webhook |
| `compliance.py` | `/compliance` | baselines, freeze windows, evidence ZIP |
| `runbooks.py` | `/runbooks`, `/executions` | CRUD, fork, trigger; resume checkpoint, abort |
| `review_campaigns.py` | `/review-campaigns` | access review CRUD |
| `access_reviews.py` | `/access-reviews` | collect, decisions, auto-CR generation |
| `maintenance_windows.py` | `/maintenance-windows` | CRUD, current-status query |
| `ir.py` | `/ir` | playbook templates, execute, forensic bundles |
| `smoke_tests.py`, `smoke_test_runs.py` | `/smoke-tests`, `/smoke-test-runs` | UI-driven live smoke launch + history |
| `notifications.py` | `/notifications` | in-app inbox |
| `policy_generate.py` | `/policy-generate` | LLM-driven policy authoring |
| `drift_alerts.py` | `/drift-alerts` | drift detection results |
| `onboarding.py` | `/onboarding` | first-run setup |
| `credential_discovery.py` | `/credential-discovery` | scan connected systems for credentials |
| `identity.py` | `/identity` | identity graph queries |
| `api_tokens.py` | `/api-tokens` | token CRUD |
| `recurring_jobs.py` | `/recurring-jobs` | cron-scheduled CRs |
| `backup.py` | `/backup` | backup targets |
| `security_policy.py` | `/security-policy` | Linux policy autogen (SELinux/AppArmor/seccomp/eBPF) soak + synthesize |
| `cr_manifest.py` | `/cr-manifest` | inferred planning vocabulary, filterable |
| `asset_timeline.py` | `/assets/{id}/timeline` | per-asset event history |
| `mcp_server.py` (mount) | `/mcp` | MCP tools for agentic clients |

Static `/downloads` is mounted from `/opt/nexplane-downloads` when present (dev only). Health check at `/health`.

## Connector System

**Catalog (`backend/app/connectors/catalog/*.json`)** — one JSON per connector type, declaring credential fields, supported actions, and per-action parameter schemas. 75 catalogs as of writing.

**CR-type definitions (`backend/app/connectors/change_type_definitions/*.json`)** — 396 JSON files, one per `ChangeType` value, declaring inputs, steps, preconditions, effects, rollback strategy, and observability hooks. These are read by `manifest_builder.py` to construct the live CR manifest.

**Executors (`backend/app/connectors/executors/<connector>/`)** — Python modules implementing the actions. Dispatched by `connector_service.py` / `change_execution.py`. Each executor function receives decrypted credentials (via `SecretsService`) and the step params.

**End-to-end CR lifecycle**
1. `POST /change-requests` creates a `ChangeRequest` in `draft`.
2. `POST /change-requests/{id}/plan` invokes `change_plan_service.plan_cr` → `planning_engine` → fills `ChangePlan` with ordered `ChangePlanStep`s based on the CR-type definition; `safety_engine` assigns risk, blocks if rollback missing for prod/critical.
3. Approval gate: `safety_engine.check_approval_requirements` decides 1 vs 2 approvers; freeze check via `require_no_active_freeze`.
4. `POST /change-requests/{id}/execute` enqueues `execute_change_workflow` (asyncio runner today; Temporal-targeted). Each step calls into the executor module; `StepOutputStore` carries credentials in memory between steps.
5. Verification step runs (`verification_check_service`).
6. On success → `completed`. On failure → `failed`, triggers `rollback_executor._do_rollback`, which walks completed steps in reverse and invokes each step's declared rollback action.

**Rollback model**
- **Implicit rollback** for change types in `safety_engine._IMPLICIT_ROLLBACK_TYPES` — well-known inverses (e.g., `ec2_start` ↔ `ec2_stop`, `s3_bucket_create` → delete).
- **Explicit rollback action** in the CR-type definition (`rollback_action` field) for irreversible/asymmetric ops.
- **Reconstitution rollback** for ops that cannot be cleanly undone (e.g., IAM key revoke) — save state beforehand, re-provision equivalent access on rollback. Pattern documented in user memory.
- **Project-level rollback** (`project_rollback_service.py`) — orchestrates reverse traversal of all CRs in a project; persists in `project_rollbacks` table; resumed on backend startup.

## Agent System

**Go binary in `agent/`.** Entry point `agent/main.go`. Compiled for linux/amd64, linux/arm64, windows/amd64, darwin/arm64. Distributed via S3 (`nexplane-agent-downloads`, us-east-1, public-read).

**Key packages**
- `config/` — flag + env config (`--control-plane`, `--secret`, `--mode`, `--hostname`, `--poll-interval`).
- `fingerprint/` — stable machine ID across reboots.
- `updater/` — `CheckAndUpdate` fetches `version` from S3; if behind, downloads binary + .sha256, verifies, `os.Rename` + `syscall.Exec` to replace itself.
- `registration/` — `POST /agent/register` with machine ID, hostname, OS, version → receives agent_id + asset_id.
- `client/` — HTTP client (bearer + HMAC).
- `poller/` — `RunService` (long-poll loop with backoff) and `RunEphemeral` (single-job mode).
- `executor/` — command dispatcher; each command registers a handler at init time.
- `commands/<package>/` — per-feature handlers. 27 packages including `changip`, `linuxpatch`, `winpatch`, `isolation`, `forensics`, `compliance`, `credrotation`, `iac`, `fleet`, `backup`, `reboot`, `dbadmin`, `ossecurity`, `linuxauth`, `winharden`, `crossplatform`, `linuxupgrade`, `containerizebuild`, `containerizeretire`, `appdiscovery`, `configsyslog`, `deepdiscover`, `ebpf`, `estimatesize`, `listpkgs`, `macos`, `uploadimage`, `virtualize`.

**Registration / poll / dispatch**
1. Agent loads config, calls updater, registers (creates an Asset and an Agent row server-side).
2. `changip.CheckPendingRollback` runs the dead-man's-switch recovery before entering the main loop.
3. Main loop long-polls `GET /agent/jobs/next` with HMAC-signed query.
4. On job receipt, `executor.Dispatch(command, params, withResult, rawParams)` runs the handler and POSTs the result with HMAC signature.

**Platform support matrix**
| Package | Linux | Windows | macOS |
|---|---|---|---|
| changip | yes | yes (netsh) | no |
| linuxpatch | yes | — | — |
| winpatch | — | yes (WUA COM) | — |
| macos (defaults, profiles, santa, lock_local_user, patch) | — | — | yes |
| isolation | yes | yes | partial |
| forensics, fleet, backup, reboot, crossplatform | yes | yes | partial |
| compliance, ossecurity, linuxauth, linuxupgrade | yes | — | — |
| winharden | — | yes | — |
| iac, credrotation, dbadmin | yes | partial | partial |
| ebpf (network + LSM) | yes | — | yes (pf + BSM shims) |

## Change Request Lifecycle

States (`ChangeRequestStatus` in `models/change_request.py`):

```
draft → planned → awaiting_approval → approved → executing → verifying → completed
                                              ↘                   ↘
                                                failed → rolled_back
```

| Transition | Endpoint | Who | Notes |
|---|---|---|---|
| create | `POST /change-requests` | security_operator+ | sets `draft` |
| plan | `POST /change-requests/{id}/plan` | security_operator+ | builds `ChangePlan`; safety_engine assigns risk |
| approve | `POST /change-requests/{id}/approve` | approver (+ admin for critical) | freeze check; bypass requires ir_responder + justification |
| execute | `POST /change-requests/{id}/execute` | security_operator+ | enqueues workflow |
| verify | (workflow) | system | runs verification_check_service |
| rollback | `POST /change-requests/{id}/rollback` | security_operator+ | reverse-walk steps via rollback_executor |
| batch create / bulk approve | `POST /change-requests/batch`, `/bulk-approve` | scoped by role | used by campaigns and runbooks |

Background processes that move CRs forward:
- `app.workers.scheduled_cr_worker.execute_scheduled_crs` — every minute.
- `app.workers.escalation_worker.check_emergency_escalations` — every 5 min, drives auto-escalations for breached SLA.
- `app.workers.soak_timer_worker.check_soak_timers` — every 30 min, used by security_policy autogen audit→enforce promotion.
- `app.services.runbook_executor.tick_all_executions` — every 30s, advances runbook executions.
- `app.workers.drift_check_worker.check_policy_drift` — daily.
- `app.workers.credential_expiry_worker.check_credential_expiry` — daily at 06:00.

On startup `_scrub_orphaned_crs` (in `main.py`) marks any `executing`/`verifying` CRs as `failed` — they are not resumable on the current asyncio runner. `project_rollback_service.resume_interrupted` re-attaches in-progress project rollbacks.

## AI Planning

**Files**
- `backend/app/services/ai_service.py` — `AIService` class; provider selection via `_resolve_provider_config` (Anthropic or OpenAI per org settings). Builds system prompt from the live CR manifest (`_build_change_types_text`) and asset context (`_build_asset_context_text`).
- `backend/app/services/planning_engine.py` — converts plan output into structured `ChangePlanStep` rows.
- `backend/app/services/manifest_builder.py` — infers `domain`, `action_class`, `touches`, `preconditions`, `effects`, `rollback_type` for each CR type by reading `change_type_definitions/*.json`. Called at startup (`main.py` lifespan) so the manifest is cached in process.
- `backend/app/routers/cr_manifest.py` — exposes the manifest with filters.
- `backend/app/mcp_tools/` — MCP tools (including `get_cr_manifest`) for agentic external clients.
- `backend/app/services/safety_engine.py` — post-processes AI output: blocks freeform shell commands, blocks remote commands without an approved template.

**Models**
- Default Anthropic model: `AI_MODEL` env, defaults to `claude-sonnet-4-6`.
- OpenAI: per-org configured model.

**Prompt pattern**
- System prompt: role + CR manifest (rendered from `manifest_builder` output) + asset inventory snapshot. Braces in the manifest are escaped (`42703df`) and the formatted text is cached.
- User prompt: operator goal description from the Projects/AI chat panel.
- Output schema: structured proposals referencing CR types by manifest name. Validated against the manifest before insert (`AI_MANIFEST_PLAN` smoke verifies this).

## Frontend Architecture

**Stack:** React 18 + TypeScript + Vite + Tailwind CSS + TanStack Query v5 + React Router v6 + React Flow 11 + Dagre.

**Layout**
- `src/main.tsx` boots the app; `App.tsx` wires the router.
- `src/routes/` — route table.
- `src/components/Layout.tsx`, `Sidebar.tsx`, `PageHeader.tsx` — chrome.
- `src/pages/` — top-level screens (Dashboard, Projects, ProjectDetail, ChangeRequestList, ChangeRequestDetail, CreateChangeRequest, ApprovalsQueue, Connectors, Assets, AssetDetail, Settings, Login, Runbooks, RunbookEditor, RunbookExecution, AccessReviews, AccessReviewDetail, Compliance, BackupRecovery, MaintenanceWindows, Notifications, ScheduledOperations, SmokeTests, VulnerabilityRemediation).
- `src/components/` — reusable widgets (`AIPanel`, `AddConnectorModal`, `IPMigrationWizard`, `ContainerizationWizard` + Steps 2–5, `IRPlaybookLauncher`, `IRStepStatusBadge`, `ProjectGraph/`, `RemediationPolicyEditor`, `SLAWidget`, `FreezeAlert`, `CredentialModal`, `MigrateDrawer`, `FindingActionPanel`, `FindingQueue`, `MitigationPanel`, `PatchCampaignList`, `SecurityPolicySoakPanel`, `AgentTokenManager`, `ApiTokenManager`, `ScheduleModal`, `CreateCampaignDrawer`, `change-requests/`, `smoke/`).
- `src/api/` — typed API client wrappers; TanStack Query hooks live in `src/hooks/`.
- `src/types/` — shared TS types mirroring backend schemas.

**State management** is TanStack Query for server state; component-local state for UI; no Redux/Zustand.

## Infrastructure & Deployment

**Docker Compose (`docker-compose.yml`)**
- `db` — `postgres:16-alpine`, port 5432.
- `backend` — multi-stage build from `backend/Dockerfile`: compiles Go agent binaries, then Python backend image. Bound `0.0.0.0:8000:8000` in the default compose (note: README claims 127.0.0.1 only, but the file checked binds 0.0.0.0). Runs `tailscaled --tun=userspace-networking` then `alembic upgrade head && python seed.py && uvicorn --reload`. Has `cap_add: NET_ADMIN`, `/dev/net/tun`, `net.ipv4.ip_forward=1` for Tailscale userspace.
- `frontend` — Vite dev server on port 3000.
- `mailhog` — local SMTP catcher on 1025/8025.
- Volumes: `postgres_data`, `tailscale_state`.

Production overlays: `docker-compose.prod.yml`, `docker-compose.override.yml`, plus `helm/` and `deploy/` directories (not the primary path today).

**EC2 platform (production-like dev)**
- Instance `i-050bab85006f0b73c`, Tailscale IP `100.101.186.39`.
- Access via `ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39`.
- Containers live in `/home/ec2-user/nexplane`; local Windows edits must be `scp`'d up.
- Provisioned by `provision_ec2_platform.py` at repo root.
- Never exposed to public internet; Tailscale is the only path in (per project rules in user memory).

**Networking**
- Tailscale provides agent reachability — managed hosts join via `tailscale_join` CR.
- Smoke tests use direct VPC IPs to DC/LDAP/WinRM (platform is in the same VPC as runners).

## Smoke Test Infrastructure

**Location:** `backend/tests/smoke/`. All files independently runnable inside the backend container.

**Shared helpers**
- `smoke_helpers.py` — `NexplaneClient`, per-cloud SDK factories, naming prefixes (`nexplane-smoke-*`), rollback-stack helpers.
- `conftest.py` — pytest fixtures.
- `_check_cr.py`, `_check_creds.py`, `_insert_phases.py` — utility scripts.

**Files and coverage**
| File | Scope |
|---|---|
| `test_aws_live.py` | AWS phases A–X + IP_*: EC2/IAM/S3/Route53/RDS/CW/ALB/Terraform/Ansible/agent/IP migration |
| `test_gcp_live.py` | GCP phases L–R: GCE/firewall/storage/SA/IaC |
| `test_azure_live.py` | Azure phases N–Z: VM/NSG/storage/identity/RBAC/VNet/DNS/SQL/Monitor |
| `test_oci_live.py` | OCI sub-projects 1–5 |
| `test_agent_live.py` | All Linux agent command packages × AWS; stubs for GCP/Azure/Windows |
| `test_multicloud_live.py` | Parallel AWS+GCP+Azure VM lifecycle |
| `test_parallel_live.py` | Per-phase parallel orchestration |
| `test_cloud_live.py` | Cross-cloud aggregator |
| `test_platform_live.py` | Platform internals — IR_ISOLATE_HOST, IR_PRESERVE_EVIDENCE, IR_LOCKDOWN_ACCOUNT, IR_PHISHING_RESPONSE, VULN_PIPELINE, RUNBOOK_ONBOARDING, RUNBOOK_ACCOUNT_COMPROMISE, RUNBOOK_PATCH_CAMPAIGN, ACCESS_REVIEW, HOST_SETUP, PROJECT_MICROSEG (and others — CR_MANIFEST, AI_MANIFEST_PLAN, PROJECT_ROLLBACK, EBPF_POLICY, SELINUX_AUTOGEN added recently) |
| `test_runbook_connectors_live.py` | Connector-specific runbook flows |
| `test_secret_store_live.py` | External secret backend integration |
| `test_feature_smoke_live.py` | Feature-area aggregations |
| `test_host_bootstrap.py` | Provisioning a Linux host with the agent |

**`run_on_ec2.py` pattern.** Rather than running smoke from the Windows laptop (which hits WatchFiles/HMR contention and adds latency), `run_on_ec2.py` provisions a `t3.small` runner named `nxp-ec2-test-runner` (deliberately not `nexplane-smoke-*` so backend cleanup does not destroy it), tars and SSM-copies the suite, installs deps, then forwards CLI args to the inner test. Output streamed back via SSM.

**AMI cache pattern.** Any infra taking >60s to provision (LDAP DC, Vault, Wiz simulator, Palo Alto, HSM, DB) is snapshotted as an AMI after first setup. Cache keyed in SSM under `/nexplane/smoke-amis/{service}/{hash}`; `get_or_create_smoke_ami()` helper resolves cache hits. DC AMI boot time documented at ~360s.

**Rollback stack.** Each phase keeps a LIFO `rollback_stack: list[tuple[cr_id, label]]`. On success, resources are torn down by triggering Nexplane's own rollback (exercising the rollback system itself). On failure the `finally` block iterates reversed stack calling `client.rollback_cr()`, then a boto3 safety net. This is the dogfooding pattern: smoke proves the rollback promise on real infra.

**Smoke-done watchdog.** Some sh-vs-bash PIPESTATUS edge cases blocked `smoke_done` marker creation, so a tmux watchdog monitors and creates it when the inner test exits (memory: `feedback_smoke_done_watchdog`).

## Key Design Decisions

**Why a CR abstraction over every action.** Audit, approval, rollback, and freeze all hang off one lifecycle. Anything that ships outside the CR pipeline does not get those guarantees — so even smoke tests must go through the CR lifecycle (dogfooding principle, user memory `feedback_smoke_test_rollback_pattern`).

**Why outbound-poll agents instead of inbound SSH.** Inbound SSH requires firewall holes per host and a credentials problem at scale. The agent reverses the direction: every host calls home, signed-by-shared-secret, with HMAC-verified payloads. The backend never opens a session.

**Why Tailscale instead of public ingress.** The backend listens only on the loopback in the documented prod posture; managed hosts reach the control plane via the tailnet (`tailscale_join` CR adds them). User memory rule: never expose to public internet, even via ngrok.

**Why a CR manifest builder.** Hardcoding the AI prompt's CR vocabulary led to drift between the prompt and the actual `change_type_definitions/`. `manifest_builder.py` infers the vocabulary at startup and serves it both to the AI service and to MCP clients. The `AI_MANIFEST_PLAN` smoke phase asserts proposals use manifest types.

**Why an explicit `StepOutputStore` instead of step DB rows.** Credentials and other secrets often need to flow from step N to step N+1 (rotate-then-update pattern). Persisting them — even encrypted — expands blast radius and audit complexity. `StepOutputStore` keeps them in process memory only; `__repr__` deliberately omits values.

**Why reconstitution rollback instead of "just don't do irreversible ops".** Real sysadmin work includes IAM key revoke, certificate rotation, etc. — operations whose undo is to re-provision equivalent access, not literal inversion. The platform models this explicitly so the rollback promise still holds for the user (memory `project_design_philosophy_rollback`).

**Why a Go agent and a Python backend.** Go gives static binaries with no runtime dependency for hostile/locked-down endpoints (no apt, no pip). Python is dense for the backend orchestration where the iteration speed and library breadth matter more than deployment surface.

**Why dynamic catalog JSON instead of hard-coded executor maps.** The catalog drives the connector creation UI, the credential schema, the action picker, and the manifest. New connectors land as catalog + executor pair without touching enums (other than the central `ChangeType` enum). Drift risk is the cost — see the recommendation to add a parity CI check.

**Why a startup `_scrub_orphaned_crs` instead of resuming.** The asyncio runner has no durable state. Pretending a crashed CR can resume would lie to the operator. Marking it `failed` makes the dashboard honest and forces a manual re-plan. Replacing the runner with Temporal is the documented next step.

**Why AMI caching for slow infra.** LDAP/Vault/HSM/DB setup is order-of-minutes; smoke iteration loops would compound that. Hashing the setup config and snapshotting an AMI per hash collapses repeated runs to seconds. SSM Parameter Store is the cache index.

**Why the platform must itself be tested live.** Mocks hide drift; live smoke is the only proof the rollback guarantee holds. This is the smoke-test-driven-development principle the project treats as foundational as TDD (user memory `feedback_smoke_test_driven_development`).
