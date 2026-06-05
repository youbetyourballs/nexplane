# Technical Debt

_Honest engineering assessment. Written 2026-06-05 against the `master` branch._

---

## Critical

### 1. Asyncio CR executor — no durability, no resume

**Description:** `change_execution.py` runs CR workflows as bare asyncio tasks. `_scrub_orphaned_crs` in `main.py` marks any `executing`/`verifying` CR as `failed` on every startup — they are not resumable. A backend restart mid-execution silently aborts the operation and leaves infrastructure in whatever partial state it reached.

**Impact:** Any backend restart (deploy, OOM, host reboot) during a multi-step CR (firewall change, key rotation, patch) results in partial execution with no automatic remediation. Operators must manually inspect state and re-plan. For project-level rollbacks the situation is worse: `project_rollback_service.resume_interrupted` re-attaches in-progress rollbacks, but the individual step outputs are gone so the rollback may not have enough context to complete correctly. This directly undermines the rollback guarantee the product is built on.

**Suggested fix:** Replace the asyncio runner with Temporal (already identified as the target in the architecture). Until then, persist `StepOutputStore` snapshots to the `execution_runs` row at each step boundary so a resumed workflow has the context it needs.

---

### 2. `ChangeType` enum — single-file central registry

**Description:** Every CR type must be registered in the `ChangeType` Python enum in `models/change_request.py`. This file is touched by every new connector and every new CR type, making it a perpetual merge-conflict zone and a cognitive bottleneck during review.

**Impact:** Adding a new connector requires a migration (new enum values → Alembic), a catalog JSON, a CR-type definition JSON, an executor module, and an enum edit — five separate locations that must all stay in sync. Reviewers must read the entire enum to understand blast radius. The file is already large and growing at ~200+ values.

**Suggested fix:** Move to a string-keyed registry loaded from the JSON catalog at startup, with a DB column of type `VARCHAR` instead of a PG enum. New change types land as JSON-only additions; no migration or enum edit required. This also enables user-defined change types.

---

### 3. No durable job queue — backend is a single process

**Description:** All background workers (scheduled CRs, soak timers, escalation, drift checks, runbook ticking, credential expiry) run as asyncio tasks inside a single uvicorn process. There is no external queue, no distributed lock, and no worker isolation.

**Impact:** A process crash loses all in-flight timer state. Running multiple backend replicas would cause duplicate execution of every background worker. The platform cannot be horizontally scaled today without adding a distributed lock primitive on every worker. The `--reload` flag in dev makes the worker interval unreliable.

**Suggested fix:** Introduce a lightweight task queue (Celery + Redis, or Temporal as the already-planned migration). At minimum, move the soak timer worker — which drives the audit→enforce promotion of security policies — outside the web process, since a missed fire has a direct security consequence (policy never promoted to enforce).

---

## High

### 4. Connector credentials stored with Fernet, rotated manually

**Description:** Connector credentials are encrypted with a Fernet key (`FERNET_KEY` env var). If the key is compromised, all credentials are compromised. There is no key-rotation workflow, no key versioning, and no envelope encryption. The key is passed as a plain env var in Docker Compose.

**Impact:** Single-key symmetric encryption with no rotation is a compliance risk (SOC 2 CC6.1, CC6.3). A leaked key or compromised host exposes every connector credential for every tenant.

**Suggested fix:** Envelope-encrypt with a KMS-managed DEK per credential or per org. Key rotation then becomes a re-wrap of envelope keys, not a full re-encryption of all credential rows.

---

### 5. No multi-tenancy isolation at the DB layer

**Description:** All tables include an `organization_id` FK and row-level filtering is applied in Python code in the service layer. There is no Postgres RLS policy enforcing the tenant boundary. A bug in any service function that forgets the `organization_id` filter would expose cross-tenant data.

**Impact:** The blast radius of a service-layer bug is all tenants, not one. There is currently no automated test asserting that every query filters by `organization_id`. This is standard SaaS exposure until RLS or schema-per-tenant is in place.

**Suggested fix:** Add Postgres RLS policies for every table, activated for a non-superuser app role. The service layer filtering becomes defense-in-depth rather than the only gate. Alternatively, schema-per-tenant provides a harder boundary.

---

### 6. `StepOutputStore` holds secrets in process memory only — no crash recovery

**Description:** Credentials and intermediate step outputs flow between steps via `StepOutputStore`, which lives in memory only. `__repr__` deliberately omits values. On any mid-execution restart, these values are gone.

**Impact:** This is the direct corollary of debt item #1. Multi-step operations like "rotate IAM key → update parameter store → update application config" lose context mid-way. The only path forward after a crash is to manually inspect each affected system and re-run.

**Suggested fix:** Write a signed, encrypted snapshot of `StepOutputStore` to the `execution_runs` row at each step boundary. Encrypt with the step's scoped key, delete after workflow completion.

---

### 7. Frontend API client — no schema validation or code generation

**Description:** `src/api/` contains hand-written TypeScript wrapper functions and `src/types/` contains hand-written types that mirror backend Pydantic models. There is no code generation from the OpenAPI spec. Pydantic model changes do not automatically surface as TypeScript type errors.

**Impact:** Backend/frontend type drift is a constant source of runtime bugs. Every backend change to a response shape requires a manual TypeScript update. The current test suite does not catch these mismatches. Several `any` casts in the frontend indicate places where the sync has already drifted.

**Suggested fix:** Expose the FastAPI-generated OpenAPI JSON at `/openapi.json` and add a codegen step (openapi-typescript or similar) to the frontend build. Type drift becomes a CI failure rather than a runtime surprise.

---

### 8. Rollback strategy is underdeclared for most CR types

**Description:** The `ChangeType` rollback model has three tiers: implicit (well-known inverse), explicit (declared `rollback_action` in the JSON), and reconstitution. Many of the 396 CR-type definition files have no `rollback_action` declared and are not in the `_IMPLICIT_ROLLBACK_TYPES` set, meaning their rollback behavior is untested and undocumented.

**Impact:** The safety engine currently blocks plan/approve for prod/critical assets if rollback is missing. But the check is gated on the `rollback_type` field being populated correctly. If the JSON is wrong or absent, the gate may not fire. Live smoke tests exist for the rollback path on a subset of CR types, but the majority have never been exercised in rollback.

**Suggested fix:** Add a CI check that every CR-type definition either declares `rollback_action` or is listed in `_IMPLICIT_ROLLBACK_TYPES`. Expand smoke test coverage to include rollback assertion for every CR type used in production.

---

### 9. No CI parity check between catalog JSON and executor modules

**Description:** The connector catalog (`catalog/*.json`) and executor modules (`executors/<connector>/`) are independent file trees. A new catalog entry with no executor, or an executor with no catalog, silently fails at runtime. There is no compile-time or CI assertion of parity.

**Impact:** New connectors have been shipped with missing fields (the `POST /connectors` credentials field being silently dropped is one symptom). Catalog drift is invisible until a user tries the action.

**Suggested fix:** Add a pytest unit test (no live infra needed) that loads every catalog JSON, discovers every executor module, and asserts 1:1 correspondence between catalog-declared actions and executor functions.

---

### 10. Backend `0.0.0.0` bind in Docker Compose

**Description:** `docker-compose.yml` binds the backend to `0.0.0.0:8000` in the published port mapping. The README claims `127.0.0.1` only, but the file is the ground truth.

**Impact:** On a developer machine or the EC2 instance, the backend API is reachable from any network interface, not just localhost/Tailscale. On EC2 this means the API is reachable from the VPC without authentication at the transport layer — JWT auth is the only gate, which is correct but still reduces defense-in-depth.

**Suggested fix:** Change to `127.0.0.1:8000:8000` in the dev compose, and confirm the prod overlay does not bind publicly. Update the README to match the actual file.

---

## Medium

### 11. No structured observability — logging is `print` / `logger.info` with no trace IDs

**Description:** The backend uses Python's `logging` module with `INFO`-level output. There are no trace IDs, no correlation IDs across CR lifecycle events, and no structured JSON log format for aggregation.

**Impact:** Debugging a multi-step CR execution requires manually correlating log lines by timestamp. In a multi-tenant environment or under load, individual CR traces are indistinguishable in the log stream.

**Suggested fix:** Inject a trace ID (the `change_request.id` or a UUID) into every log line via a `logging.Filter` or a context var. Emit structured JSON logs compatible with CloudWatch Insights / Datadog.

---

### 12. Alembic migrations have no automated test in CI

**Description:** There are 38+ Alembic migration files. There is no CI step that runs `alembic upgrade head` against a fresh Postgres instance and then `alembic downgrade -1` for each migration to verify reversibility.

**Impact:** A migration that fails on upgrade or blocks downgrade will cause a production deployment failure with no advance warning. Irreversible migrations (type changes, constraint additions, data transforms) are currently invisible until deploy time.

**Suggested fix:** Add a GitHub Actions job that spins up a Postgres container, runs `alembic upgrade head`, seeds, and optionally runs `alembic downgrade base` + `alembic upgrade head` a second time. This is a 10-minute addition and catches silent migration failures.

---

### 13. `soak_timer_worker` fires every 30 minutes — coarse-grained audit→enforce window

**Description:** The security policy audit→enforce promotion is governed by a soak timer. The worker that checks these timers runs every 30 minutes (`check_soak_timers`). If a soak completes at minute 1, enforcement may be delayed up to 29 minutes.

**Impact:** For time-sensitive security policy deployments (e.g., responding to a detected lateral movement), a 30-minute enforcement delay is operationally significant. The worker is also inside the single-process web app (see debt item #3).

**Suggested fix:** Reduce the polling interval to 1–2 minutes, or replace the polling model with a task queue trigger on soak completion.

---

### 14. AI planning has no retry or fallback on provider failure

**Description:** `ai_service.py` makes a single call to the configured provider. If the provider is unavailable, rate-limited, or returns a malformed response, the planning endpoint returns a 500. There is no retry with backoff, no fallback to a second provider, and no graceful degradation (e.g., returning an empty plan with a warning).

**Impact:** Provider downtime causes every AI-assisted planning attempt to fail. For operators who rely on AI planning as their primary workflow, this is a hard blocker.

**Suggested fix:** Add exponential backoff with jitter (3 retries), a configurable timeout, and a clear error message distinguishing provider errors from planning errors.

---

### 15. Frontend component size — several components exceed 500 lines

**Description:** Several frontend components (`ChangeRequestDetail`, `ProjectDetail`, `VulnerabilityRemediation`, `ContainerizationWizard`) exceed 500 lines of TSX. They mix data fetching, business logic, and rendering.

**Impact:** These files are the first thing a new frontend engineer opens. They are hard to navigate, hard to test, and slow to load in the editor. TanStack Query mutations are often inlined rather than extracted to hooks.

**Suggested fix:** Extract data-fetching to hooks in `src/hooks/`, reduce component files to rendering + event handlers. This is low risk and high readability payoff. No need to do it all at once — apply on next touch.

---

### 16. No end-to-end test for the frontend

**Description:** The frontend has no automated test suite. All verification is done manually or via backend smoke tests. The UI smoke phases test the backend API, not the React rendering or client-side logic.

**Impact:** Frontend regressions (broken form, missing error state, routing issue) are caught only in manual review. There is no regression protection for the approval workflow, the CR detail view, or the AI planning panel.

**Suggested fix:** Add Playwright end-to-end tests for the three highest-traffic flows: login → create CR → approve → execute, and the AI planning panel. These tests can run against the existing Docker Compose stack.

---

## Low

### 17. Go agent has no structured output format for command results

**Description:** Agent command handlers return a `map[string]interface{}` result. The schema of each command's output is undocumented and implicitly inferred by the backend. There is no schema registry for agent results.

**Impact:** Backend parsers for agent results contain defensive `interface{}` type assertions that silently ignore unexpected shapes. This makes adding new fields to an agent result fragile — the backend may not notice the new data.

**Suggested fix:** Define result schemas as Go structs per command and JSON-marshal them. Add a Go unit test per command that asserts the result shape. This is a cross-cutting change but low urgency.

---

### 18. Docker Compose `--reload` in production-like dev environment

**Description:** The backend container in the dev compose runs uvicorn with `--reload`. The EC2 platform instance runs the same dev compose. WatchFiles monitors all Python files in the container; a `git pull` on the EC2 host triggers a reload mid-test or mid-execution.

**Impact:** Any deployment to EC2 while a smoke test is running will reload the backend and abort in-flight CRs. This has caused smoke test failures that look like test bugs.

**Suggested fix:** For the EC2 platform instance, use a `docker-compose.override.yml` that removes `--reload` (or uses a `CMD` override). Keep `--reload` only for local laptop development.

---

### 19. Seed data and demo data mixed in `seed.py`

**Description:** `seed.py` creates both the required initial data (admin user, default org) and a large set of synthetic demo assets, CRs, and findings. Running `seed.py` twice is not idempotent without manual intervention.

**Impact:** Fresh deployments with an existing database will collide on unique constraints. Demo data inflates the database and makes real operator data harder to find. Removing the demo data requires knowing which rows were seeded.

**Suggested fix:** Split `seed.py` into `seed_required.py` (idempotent: only creates data that must exist) and `seed_demo.py` (destroys and recreates demo data). The required seed runs on every startup; the demo seed is opt-in.

---

### 20. No rate limiting or request throttling on the API

**Description:** The FastAPI app has no rate limiting middleware. The AI planning endpoint, the smoke test launch endpoint, and the bulk-approve endpoint all accept unbounded request rates.

**Impact:** A misconfigured runbook or a polling client could saturate the backend or exhaust the AI provider quota. In a multi-tenant environment, one tenant can degrade service for others.

**Suggested fix:** Add `slowapi` rate limiting middleware with per-user limits on the AI planning, smoke test launch, and bulk-approve endpoints. This is a one-day addition.
