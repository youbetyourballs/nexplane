# Nexplane

**The safe execution layer for security-driven infrastructure change.**

Nexplane enables security teams to safely execute infrastructure and security engineering changes — DNS updates, cloud snapshots, security group modifications, key rotations, telemetry agent deployments, approved remote commands, and microsegmentation policies — with governed safety review, approval workflow, controlled execution, verification, automatic rollback, and immutable audit trail.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  React Frontend (Vite + TypeScript + Tailwind)              │
│  ┌──────────┐ ┌───────────────┐ ┌──────────────────────┐   │
│  │Dashboard │ │Change Requests│ │ Approvals Queue       │   │
│  └──────────┘ └───────────────┘ └──────────────────────┘   │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP/REST
┌────────────────────────▼────────────────────────────────────┐
│  FastAPI Backend                                             │
│  ┌──────────────┐ ┌─────────────────┐ ┌─────────────────┐  │
│  │Safety Engine │ │Planning Engine  │ │ Audit Service    │  │
│  └──────────────┘ └─────────────────┘ └─────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ ExecuteChangeWorkflow (Temporal-compatible pattern)  │   │
│  │  preflight → execute → verify → complete/rollback    │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Mock Connectors                                      │   │
│  │  aws_mock  cloudflare_mock  paloalto_mock  ssh_mock  │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────┘
                         │ SQLAlchemy async
┌────────────────────────▼────────────────────────────────────┐
│  PostgreSQL 16                                               │
└─────────────────────────────────────────────────────────────┘
```

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, TanStack Query |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 async |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| Workflow | Temporal-pattern abstraction (asyncio MVP, Temporal-ready) |
| Auth | JWT + bcrypt (SAML/OIDC-ready interface) |
| Deployment | Docker Compose |

## Quick Start

```bash
# Clone the repo
git clone <repo-url>
cd nexplane

# Start everything
docker compose up --build

# Frontend: http://localhost:3000
# Backend API: http://localhost:8000
# API Docs: http://localhost:8000/docs
```

### Demo Credentials

| Email | Password | Role |
|-------|----------|------|
| admin@acme.example | admin123 | Admin |
| operator@acme.example | operator123 | Security Operator |
| approver@acme.example | approver123 | Approver |
| auditor@acme.example | auditor123 | Auditor |

## Local Development

### Backend

```bash
cd backend

# Create virtualenv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start PostgreSQL (or use Docker)
docker run -d -e POSTGRES_USER=nexplane -e POSTGRES_PASSWORD=nexplane_dev \
  -e POSTGRES_DB=nexplane -p 5432:5432 postgres:16-alpine

# Run migrations
alembic upgrade head

# Seed data
python seed.py

# Start API server
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Running Tests

```bash
cd backend
pytest
```

## Project Structure

```
nexplane/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app + CORS
│   │   ├── config.py            # Settings via pydantic-settings
│   │   ├── database.py          # Async SQLAlchemy engine
│   │   ├── models/              # SQLAlchemy ORM models
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── routers/             # FastAPI route handlers
│   │   │   ├── auth.py
│   │   │   ├── assets.py
│   │   │   ├── connectors.py
│   │   │   ├── change_requests.py
│   │   │   └── audit.py
│   │   ├── services/
│   │   │   ├── safety_engine.py   # Risk scoring + approval rules
│   │   │   ├── planning_engine.py # Change plan generation
│   │   │   ├── audit_service.py   # Immutable audit event recording
│   │   │   └── connector_service.py # Mock connector execution
│   │   ├── workflows/
│   │   │   ├── runner.py          # Temporal-compatible workflow runner
│   │   │   ├── activities.py      # External I/O activities
│   │   │   └── execute_change_workflow.py  # Orchestration logic
│   │   └── tests/
│   ├── alembic/                 # Database migrations
│   ├── seed.py                  # Demo data seeder
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── api/                 # API client + typed endpoints
│       ├── components/          # Reusable UI components
│       ├── hooks/               # useAuth context
│       ├── pages/               # Page components
│       ├── routes/              # React Router routes
│       └── types/               # Centralized API types
└── docker-compose.yml
```

## Demo Script — Investor Walkthrough

This script demonstrates the full Nexplane governance loop.

### 1. Login as Security Operator

```
Email: operator@acme.example
Password: operator123
```

### 2. Create a DNS Change Request

Navigate to **Change Requests → New Request**

- **Title**: Update DNS A record for api.acme.example
- **Change Type**: DNS Update
- **Target Asset**: Prod DNS Zone (acme.example)
- **Desired Outcome**: (pre-filled template — change `new_value` to `"203.0.113.99"`)

Click **Create Change Request**.

### 3. Generate the Change Plan

On the Change Request detail page, click **Generate Change Plan**.

Observe:
- **Blast Radius** — impact scope, affected environments, recovery time estimate
- **Execution Steps** — 4 deterministic steps with connector actions
- **Safety Checks** — preflight validations before execution
- **Rollback Plan** — automatic restore of previous DNS value

### 4. Submit for Approval

Click **Submit for Approval**. Status transitions to `Approval Required`.

### 5. Approve (as Approver)

Log out, log in as `approver@acme.example / approver123`.

Navigate to **Approvals Queue**. The DNS change appears with its risk level.

Add a comment and click **Approve**. For high-risk changes, both an approver AND admin must approve.

### 6. Execute

Log back in as admin. Navigate to the change request. Click **Execute Change**.

Watch the status cycle:
```
approved → executing → verifying → completed
```

The workflow runs:
1. Loads change request and plan
2. Runs preflight checks (connector reachable, DNS record exists, new value reachable)
3. Executes DNS update via Cloudflare mock connector
4. Runs verification (DNS resolves to new value, application health check)
5. Marks completed + writes audit events

### 7. Show Audit Trail

Scroll down to **Audit Trail** on the detail page. Observe the immutable sequence:

```
change_request.created
change_plan.generated
change_request.submitted_for_approval
change_request.approved
workflow.started
preflight.passed
execution.started
execution.completed
verification.started
verification.passed
workflow.completed
```

### 8. Demonstrate Rollback Path

For a completed change: click **Manual Rollback** (requires admin or approver role).

The rollback activity executes — for DNS, this restores the previous record value. Status transitions to `rolled_back`.

### 9. Demonstrate Remote Command Safety

Create a new change request with type **Remote Command**. Try setting `template_id` to an unapproved value or adding `freeform_command`. The safety engine will block plan generation with:

> "Remote command requests must use an approved command template."

Only these templates are permitted: `restart_service`, `check_disk_usage`, `rotate_log`, `flush_dns_cache`, `collect_support_bundle`.

## Safety Design

| Scenario | Behavior |
|----------|----------|
| Prod + critical asset | `high` or `critical` risk score |
| Missing rollback strategy (prod/critical) | Blocked — cannot generate plan |
| Remote command without approved template | Blocked at safety review |
| Freeform shell command | Blocked unconditionally |
| Critical risk change | Requires 2 approvals (approver + admin); does not auto-execute |
| Microsegmentation policy | Staged in simulation mode only — no live enforcement |

## Workflow Architecture

The `ExecuteChangeWorkflow` mirrors Temporal's design principles:

- **Deterministic orchestration** — the workflow function contains no side effects
- **Activity isolation** — all DB writes, connector calls, and external I/O are in Activities
- **Serializable inputs** — `WorkflowInput` is a dataclass; safe to serialize/replay
- **Swap-ready** — replace `runner.py` with a `temporalio.client.Client` implementation to run against a real Temporal cluster

## API Documentation

Interactive OpenAPI docs available at `http://localhost:8000/docs` when the backend is running.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://nexplane:nexplane_dev@db:5432/nexplane` | PostgreSQL connection string |
| `SECRET_KEY` | (dev key) | JWT signing secret — **change in production** |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Allowed CORS origins |
| `ENVIRONMENT` | `development` | Environment name |
