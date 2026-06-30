# Nexplane OSS + Commercial Framework Design
# 2026-06-30

---

## Summary

This spec covers two interconnected decisions:

1. **Open-source release** — Nexplane Community Edition (CE) published under a permissive-for-self-hosters license, with no meaningful feature removal.
2. **Commercial operations framework** — an internal control plane Nexplane uses to operate paid hosted customer instances, layered on top of CE without modifying it.

The central design principle: **the commercial layer is an operations layer, not a feature paywall.** CE remains a complete, production-credible product. Commercial value comes exclusively from managed hosting, operational assurance, support, compliance evidence, and customer lifecycle management.

---

## Design Constraints

- CE must remain fully functional: change requests, rollback, snapshots, scheduled backups, connectors, agents, MCP, and core orchestration.
- No self-serve instance provisioning. All provisioning is API-driven (request → Nexplane provisions on your behalf).
- No external billing integration (Stripe, etc.) in this iteration — only data model and placeholder scaffolding.
- The commercial framework is an internal Nexplane operations tool, not a customer-facing UI.
- All new code lives in a clearly separated namespace (`commercial/` or equivalent) to preserve CE boundary clarity.
- Secret handling: no plaintext secrets in logs, registry files, or CR outputs.

---

## Open Questions (requires confirmation before implementation)

| Item | Options | Recommendation |
|------|---------|----------------|
| **License** | MIT/Apache 2, AGPL-3.0, BUSL | **AGPL-3.0** — permissive for self-hosters, prevents SaaS forks without contribution |
| **Repo split** | Monorepo with CE/commercial separation vs. separate OSS repo | Monorepo with `commercial/` directory excluded from OSS build |
| **CE binary distribution** | Docker Hub only, GitHub releases, both | Docker Hub (`nexplane/nexplane`) + GitHub releases |

---

## Product Boundary

### Community Edition (CE)

Everything currently in the platform is CE. Nothing is removed or gated.

| Capability | CE | Hosted Commercial |
|---|---|---|
| Change requests | ✅ | ✅ |
| Rollback center | ✅ | ✅ |
| Asset snapshots | ✅ | ✅ |
| Scheduled backups | ✅ | ✅ + managed assurance |
| Connectors (all 70+) | ✅ | ✅ |
| Agents (Linux/macOS/Windows) | ✅ | ✅ |
| MCP server | ✅ | ✅ |
| Infrastructure memory | ✅ | ✅ |
| Impact simulation | ✅ | ✅ |
| Recommendations engine | ✅ | ✅ |
| Self-hosted deployment | ✅ | — |
| Managed hosting | — | ✅ |
| Managed upgrades | — | ✅ |
| Backup verification + restore drills | — | ✅ |
| Fleet health monitoring | — | ✅ |
| Support access (time-bound, audited) | — | ✅ |
| Compliance evidence exports | — | ✅ (Business+) |
| Advanced identity / SSO | — | ✅ (Enterprise) |
| Customer-managed keys | — | ✅ (Enterprise) |
| SLA tier | — | ✅ (Business+) |
| Dedicated support | — | ✅ (Enterprise) |

---

## Commercial Plans

Four tiers. Trial is temporary. CE is the self-hosted alternative, not a paid tier.

| Plan | Intended buyer | Key entitlements |
|---|---|---|
| **Trial** | Evaluators | 14 days, 5 users, 2 agents, 5 connectors, no backup assurance |
| **Team** | Small infra teams | 25 users, 10 agents, 20 connectors, managed backups, community support |
| **Business** | Mid-market | 100 users, 50 agents, unlimited connectors, backup verification, SLA, compliance exports |
| **Enterprise** | Large orgs | Unlimited users/agents/connectors, advanced identity, customer-managed keys, data residency, dedicated support, restore drills, custom SLA |

Entitlement overrides are supported per-customer for negotiated terms.

---

## Architecture

### Directory Layout

```
nexplane/
├── backend/
│   └── app/
│       ├── connectors/           # CE: executors, catalog, change type defs
│       ├── models/               # CE: core data models
│       └── ...                   # CE: all existing code
│
└── commercial/                   # NEW — Nexplane internal ops layer
    ├── registry/                 # Customer registry (YAML + schema + helpers)
    ├── plans/                    # Plan definitions + entitlement engine
    ├── catalog/                  # Commercial CR catalog (commercial.json)
    ├── executors/                # Commercial CR executors
    │   ├── lifecycle/            # create, provision, suspend, reactivate, etc.
    │   ├── support/              # support access, break-glass, diagnostics
    │   ├── health/               # fleet health checks
    │   ├── backup/               # managed backup assurance
    │   ├── upgrade/              # hardened upgrade lifecycle
    │   ├── billing/              # trial, conversion, suspension
    │   └── tokens/               # setup token lifecycle
    ├── secrets/                  # Secret abstraction layer
    ├── api/                      # Internal ops API (readiness layer for admin console)
    └── tests/                    # Commercial framework tests
```

### Key Design Principles

**Registry as source of truth.** Every commercial operation reads and writes to `client-registry/<slug>.yaml`. The registry is the audit trail, health dashboard, and lifecycle state machine in one place.

**CRs for everything.** All commercial operations are expressed as Nexplane change requests — using the same executor and catalog patterns as CE. This means commercial operations are themselves auditable, rollbackable where possible, and observable through the existing CR infrastructure.

**Secret abstraction.** No plaintext secrets in registry files, logs, or CR outputs. A `SecretsBackend` interface wraps secret storage. The local implementation uses references/handles. AWS SSM/Secrets Manager is the production backend.

**API-readiness over UI.** The commercial layer exposes structured data suitable for a future internal admin console. No admin UI is built in this iteration.

---

## Module Specifications

### 1. Customer Registry

**Location:** `commercial/registry/`

**Storage:** `client-registry/<client_slug>.yaml` per customer.

**Schema (per customer record):**

```yaml
slug: acme-corp
display_name: Acme Corporation
status: active              # trial | active | suspended | decommissioned | terminated
delivery_model: managed_ec2 # managed_ec2 | managed_ha | ecs_fargate | eks_helm | customer_vpc | self_hosted_compose
plan: business
region: us-east-1
compliance_tier: soc2       # none | soc2 | fedramp

contact:
  primary_email: ops@acme.com
  billing_email: billing@acme.com

instance:
  id: i-0abc123def456      # EC2 or logical instance ID
  url: https://acme.nexplane.io
  version: 0.1.2
  provisioned_at: "2026-06-30T12:00:00Z"
  deployment_mode: managed_ec2
  setup_token_status: consumed  # pending | issued | consumed | expired | revoked

trial:
  start: null
  end: null

billing:
  customer_id: null         # future Stripe customer ID
  subscription_id: null

entitlements:
  max_users: 100
  max_agents: 50
  max_connectors: -1        # -1 = unlimited
  managed_backups: true
  backup_verification: true
  restore_drills: false
  support_tier: standard
  advanced_identity: false
  compliance_exports: true
  private_deployment: false
  data_residency: false
  customer_managed_keys: false
  dedicated_support: false
  sla_tier: standard

health:
  status: healthy           # healthy | degraded | down | unknown
  last_check: "2026-06-30T12:00:00Z"
  version_verified: "0.1.2"
  disk_status: ok
  service_status: ok
  agent_status: ok
  connector_status: ok
  backup_freshness: ok
  open_incident_count: 0

backup:
  policy: daily             # none | daily | hourly
  retention_days: 30
  rpo_hours: 24
  rto_hours: 4
  last_backup: null
  last_successful_backup: null
  last_failed_backup: null
  last_restore_drill: null
  backup_health: unknown

support:
  access_status: none       # none | requested | active | revoked
  active_ticket: null
  access_expires: null
  access_granted_by: null

events:
  - timestamp: "2026-06-30T12:00:00Z"
    event: customer_created
    actor: nexplane-ops
    details: "Initial customer record created"
```

**Validation rules:**
- Slug: lowercase alphanumeric + hyphens, no spaces, globally unique among active customers
- Plan: must be one of `trial | team | business | enterprise`
- Region: must be a supported AWS region
- Delivery model: must be a supported mode
- Contact emails: required, valid format
- Status transitions: only allowed paths (e.g., `trial → active`, `active → suspended`, `suspended → active`, `active → decommissioned`, `decommissioned → terminated`)

**Helper functions:**
- `create_customer(slug, display_name, contact, plan, delivery_model, region)` — validates, creates YAML, appends `customer_created` event
- `load_customer(slug)` → dict
- `save_customer(slug, data)` — atomic write with schema validation
- `update_customer_status(slug, new_status, actor, reason)` — validates transition, appends event
- `append_customer_event(slug, event, actor, details)`
- `update_instance_metadata(slug, **kwargs)`
- `update_backup_metadata(slug, **kwargs)`
- `update_health_metadata(slug, **kwargs)`
- `update_support_metadata(slug, **kwargs)`
- `list_customers(status=None, plan=None, region=None)` → list

---

### 2. Plan and Entitlement System

**Location:** `commercial/plans/`

Entitlement definitions are code (not YAML) — a single source of truth that validates both the registry and CR execution.

```python
PLANS = {
    "trial": Entitlements(
        max_users=5, max_agents=2, max_connectors=5,
        managed_backups=False, backup_verification=False, restore_drills=False,
        support_tier="community", advanced_identity=False, compliance_exports=False,
        private_deployment=False, data_residency=False, customer_managed_keys=False,
        dedicated_support=False, sla_tier="none",
    ),
    "team": Entitlements(...),
    "business": Entitlements(...),
    "enterprise": Entitlements(...),
}
```

**Entitlement helpers:**
- `get_plan_entitlements(plan)` → `Entitlements`
- `validate_entitlements(entitlements)` — raises on invalid values
- `check_entitlement(slug, feature)` → bool — reads customer record + overrides
- `apply_entitlement_override(slug, feature, value, reason, actor)` — for negotiated enterprise terms
- `upgrade_plan(slug, new_plan, actor)` — validates upgrade path, updates registry
- `downgrade_plan(slug, new_plan, actor)` — validates downgrade path, updates registry

Entitlement overrides are stored as a `entitlement_overrides` block in the customer YAML and take precedence over plan defaults.

---

### 3. Commercial CR Catalog

**Location:** `commercial/catalog/commercial.json`

All commercial operations are expressed as CRs, following the same catalog schema as CE. Each entry includes:

```json
{
  "change_type": "provision_customer_instance",
  "display_name": "Provision Customer Instance",
  "description": "Provision a new hosted Nexplane instance for a registered customer.",
  "execution_tier": "commercial",
  "parameters": [...],
  "required_fields": ["customer_slug"],
  "steps": [
    {"generic_action": "provision_customer_instance", "purpose": "execute", "required": true}
  ],
  "rollback_action": "terminate_customer_instance",
  "safety_notes": "Validates no active instance already exists for customer. Prevents duplicate provisioning.",
  "expected_outputs": ["instance_id", "instance_url", "setup_token_ref"]
}
```

**Full catalog entries (34 total):**

| Category | CRs |
|---|---|
| **Lifecycle** | `create_customer`, `provision_customer_instance`, `suspend_customer`, `reactivate_customer`, `update_customer_plan`, `export_customer_data`, `decommission_customer`, `terminate_customer_instance` |
| **Support** | `open_support_case`, `request_support_access`, `grant_support_access`, `revoke_support_access`, `reset_instance_auth_breakglass`, `collect_support_bundle`, `run_instance_diagnostics`, `restart_customer_service`, `repair_failed_backup`, `repair_failed_upgrade`, `rotate_customer_credentials`, `reissue_setup_token`, `check_customer_health`, `collect_customer_logs` |
| **Health** | `check_instance_health`, `check_all_customer_health`, `verify_instance_version`, `check_disk_usage`, `check_container_status`, `check_database_health`, `check_backup_freshness`, `check_agent_status`, `check_connector_status` |
| **Backup** | `enable_managed_backups`, `disable_managed_backups`, `update_backup_policy`, `run_managed_backup`, `verify_latest_backup`, `run_restore_drill`, `restore_customer_instance`, `export_customer_backup`, `check_backup_policy_compliance` |
| **Billing/Trial** | `start_trial`, `extend_trial`, `convert_trial_to_paid`, `suspend_for_billing`, `reactivate_billing_account` |
| **Tokens** | `generate_setup_token`, `revoke_setup_token`, `cleanup_expired_setup_tokens`, `resend_setup_link` |
| **Upgrades** | `upgrade_customer_instance`, `rollback_customer_upgrade` |

---

### 4. Customer Lifecycle Executors

**Location:** `commercial/executors/lifecycle/`

#### `provision_customer_instance`

Steps:
1. Load and validate customer registry entry (status must be `active` or `trial`)
2. Check no active instance already exists for this customer
3. Validate plan entitlements and delivery model
4. Select provisioning path based on `delivery_model`:
   - `managed_ec2` → current EC2 + Docker Compose path (only mode implemented now)
   - All others → stub with clear extension point
5. Generate `SECRET_KEY` and store via secrets backend (returns reference, not value)
6. Generate setup token (expiry: 48h), store reference in registry
7. Launch instance (EC2 user data / equivalent)
8. Update registry: instance ID, URL, version, provisioned timestamp, setup token status
9. Append `instance_provisioned` event
10. Output: `instance_id`, `instance_url`, `setup_token_ref` (reference only, not plaintext)

**Rollback:** `terminate_customer_instance` (tears down EC2, marks registry `terminated`, appends event)

**Delivery model extension points (stub, not implemented):**
- `managed_ha` — multi-instance with load balancer
- `ecs_fargate` — ECS task definition deploy
- `eks_helm` — Helm chart install
- `customer_vpc` — provision in customer-provided VPC
- `self_hosted_compose` — generate and deliver docker-compose artifact

#### `suspend_customer`

1. Validate status transition (`active` → `suspended`)
2. Stop instance services (docker compose stop or equivalent)
3. Update registry status, append `customer_suspended` event
4. Optionally notify via SMTP placeholder

**Rollback:** `reactivate_customer`

#### `update_customer_plan`

1. Validate new plan is a valid upgrade or downgrade
2. Apply new entitlements (respecting overrides)
3. Update registry, append `plan_updated` event
4. No instance restart required (entitlements are checked at CR execution time, not cached on instance)

---

### 5. Support Access Layer

**Location:** `commercial/executors/support/`

Support access is time-bound, ticket-linked, and fully audited. No persistent backdoors.

#### `grant_support_access`

Parameters: `customer_slug`, `ticket_id`, `duration_hours` (max 24), `actor`

1. Verify open support case exists for ticket
2. Generate time-limited credential (SSH cert or temporary token via secrets backend)
3. Install access mechanism on customer instance
4. Update registry `support` block: status, ticket, expiry, granted_by
5. Append `support_access_granted` event with full audit detail
6. Schedule or flag automatic revocation at expiry

**Rollback / revocation:** `revoke_support_access` — removes credential, updates registry, appends event

#### `reset_instance_auth_breakglass`

Marked as **exceptional**. Requirements:
- Requires explicit `reason` field
- Actor must be explicitly named
- Generates new admin credentials via secrets backend (reference returned, not plaintext)
- Appends `breakglass_auth_reset` event with reason, actor, timestamp
- Does NOT log or output plaintext credentials

---

### 6. Fleet Health

**Location:** `commercial/executors/health/`

Each health check executor:
1. Connects to customer instance (via Nexplane agent or SSH)
2. Collects status for its domain
3. Updates `health` block in customer registry
4. Returns structured result suitable for admin console rendering

Health data shape per customer:

```python
@dataclass
class InstanceHealth:
    status: Literal["healthy", "degraded", "down", "unknown"]
    last_check: datetime
    version_verified: str
    disk_status: Literal["ok", "warning", "critical", "unknown"]
    service_status: Literal["ok", "degraded", "down", "unknown"]
    agent_status: Literal["ok", "degraded", "down", "unknown"]
    connector_status: Literal["ok", "degraded", "unknown"]
    backup_freshness: Literal["ok", "stale", "missing", "unknown"]
    open_incident_count: int
```

`check_all_customer_health` fans out to all active customers in parallel with a configurable concurrency limit.

---

### 7. Managed Backup Assurance

**Location:** `commercial/executors/backup/`

CE provides backup and snapshot mechanics. The commercial layer adds:

- **Policy management:** backup schedule, retention, RPO/RTO targets stored in registry
- **Verification:** post-backup integrity check (restore to throwaway, validate contents)
- **Restore drills:** periodic full restore test against a clean environment (Enterprise plan only)
- **Compliance tracking:** `check_backup_policy_compliance` validates actual backup frequency against policy

CE backup executors are called directly — no duplication. The commercial layer wraps them with policy enforcement and registry updates.

---

### 8. Upgrade Lifecycle

**Location:** `commercial/executors/upgrade/`

Replaces the current shallow version-bump behavior.

#### `upgrade_customer_instance`

Steps:
1. **Preflight health check** — instance must be `healthy` before upgrade begins
2. **Entitlement/status check** — customer must be active, on eligible plan
3. **Maintenance window placeholder** — emit warning if outside configured window
4. **Capture current version** — read from instance, store as rollback reference
5. **Backup checkpoint** — trigger a backup, verify it completes before proceeding
6. **Migration compatibility check** — validate new version is compatible with current schema (stub)
7. **Update image tags** in docker-compose configuration
8. **Restart services** — `docker compose pull && docker compose up -d`
9. **Post-upgrade health check** — wait for healthy status (max 5 min with polling)
10. **Version verification** — confirm running version matches target
11. **Registry update** — version, upgrade timestamp, event appended
12. **Automatic rollback on failure** — if steps 9–10 fail, execute rollback automatically

#### `rollback_customer_upgrade`

1. Revert image tags to captured previous version
2. Restart services
3. Health verification
4. Registry update: version reverted, event appended
5. If rollback also fails: mark instance status `degraded`, escalate to open incident

---

### 9. Secret Handling

**Location:** `commercial/secrets/`

```python
class SecretsBackend(Protocol):
    def store(self, key: str, value: str, metadata: dict) -> SecretRef: ...
    def retrieve(self, ref: SecretRef) -> str: ...
    def rotate(self, ref: SecretRef, new_value: str) -> SecretRef: ...
    def revoke(self, ref: SecretRef) -> None: ...

@dataclass
class SecretRef:
    backend: str     # "local" | "ssm" | "secretsmanager"
    path: str
    version: str

def redact(value: str, show_chars: int = 0) -> str:
    """Return redacted representation for logging."""
    return f"[REDACTED:{len(value)}chars]" if show_chars == 0 else value[:show_chars] + "[...]"
```

**Implementations:**
- `LocalSecretsBackend` — stores in memory or temp file (dev/test only)
- `SSMSecretsBackend` — AWS SSM Parameter Store (production)
- `SecretsManagerBackend` — AWS Secrets Manager (future)

**Rules:**
- All CR outputs containing credentials must use `SecretRef`, never plaintext
- All log statements must pass credentials through `redact()`
- Registry YAML never contains plaintext secrets

---

### 10. Setup Token Lifecycle

**Location:** `commercial/executors/tokens/`

Setup tokens are one-time-use, time-limited, and customer-associated.

Schema (stored in `setup_tokens` table, reference in registry):

```python
@dataclass
class SetupToken:
    id: str           # UUID
    customer_slug: str
    token_ref: SecretRef   # actual token stored via secrets backend
    status: Literal["pending", "issued", "consumed", "expired", "revoked"]
    expires_at: datetime
    issued_at: datetime
    consumed_at: Optional[datetime]
    revoked_at: Optional[datetime]
    revoked_by: Optional[str]
```

Token value is never logged. Registry stores token status and `SecretRef` only.

---

### 11. Billing and Trial Readiness

**Location:** `commercial/executors/billing/`

No Stripe integration in this iteration. The data model and state machine are complete; the payment mechanics are stubbed.

**State machine:**
```
[none] → start_trial → [trial]
[trial] → convert_trial_to_paid → [active]
[trial] → extend_trial → [trial]
[active] → suspend_for_billing → [suspended_billing]
[suspended_billing] → reactivate_billing_account → [active]
```

Registry fields added: `billing.customer_id`, `billing.subscription_id`, `trial.start`, `trial.end`, `trial.extended_count`.

---

### 12. Internal Ops API

**Location:** `commercial/api/`

Thin read layer over the registry and health data. No write operations through the API — all mutations go through CRs.

Endpoints (FastAPI, internal only):

| Endpoint | Returns |
|---|---|
| `GET /ops/customers` | Customer list with status, plan, health summary |
| `GET /ops/customers/{slug}` | Full customer record |
| `GET /ops/customers/{slug}/health` | Health block |
| `GET /ops/customers/{slug}/events` | Lifecycle event history |
| `GET /ops/customers/{slug}/backups` | Backup status block |
| `GET /ops/fleet/health` | All customers health summary |
| `GET /ops/fleet/upgrades` | Version distribution across fleet |

Response shapes are designed for a future admin console — consistent pagination, status fields, and ISO timestamps throughout.

---

## Testing Requirements

| Module | Test coverage required |
|---|---|
| Registry | Schema validation, duplicate detection, invalid transitions, all helper functions |
| Plans | Entitlement lookup, override application, upgrade/downgrade validation |
| Lifecycle CRs | create, provision (mock EC2), suspend/reactivate, plan update, decommission |
| Support | Access grant/revoke, break-glass audit trail, expiry behavior |
| Health | Each check executor with mocked infrastructure responses, fleet fan-out |
| Backup | Policy compliance check, verify/restore drill with mocked CE backup |
| Upgrade | Successful path, failed preflight, failed post-check, auto-rollback, rollback failure |
| Secrets | Redaction helper, SecretRef round-trip, no plaintext in outputs |
| Tokens | Lifecycle state machine, expiry, revocation |
| Billing | Trial start/extend/convert, suspend/reactivate state transitions |
| Catalog | Schema validity of all 34+ entries, no missing required fields |

---

## Remaining Gaps / Deferred Work

| Item | Deferred reason |
|---|---|
| Self-serve instance provisioning | Explicitly out of scope — API-driven only |
| Stripe / payment processing | Data model ready; integration deferred |
| Admin console UI | API readiness complete; UI is a separate project |
| Managed HA, ECS, EKS delivery modes | Extension points defined; implementations deferred |
| Customer VPC deployment | Deferred — requires customer network topology input |
| Email delivery (setup link, suspension notices) | SMTP placeholder only; delivery deferred |
| Restore drill automation | Infrastructure needed for throwaway restore env |
| Multi-region fleet | Single-region for now; data model is region-aware |
| OIDC / SSO for enterprise identity | Entitlement defined; feature deferred |
| Customer-managed keys | Entitlement defined; KMS integration deferred |
| Compliance evidence export | Entitlement defined; format and delivery deferred |
| MCP tools for commercial ops | Add after core executors are stable |

---

## Files to Create / Modify

| Path | Action |
|---|---|
| `commercial/__init__.py` | Create |
| `commercial/registry/__init__.py` | Create |
| `commercial/registry/schema.py` | Create — Pydantic schema + validation |
| `commercial/registry/helpers.py` | Create — all registry helper functions |
| `commercial/registry/client-registry/` | Create — YAML storage directory |
| `commercial/plans/__init__.py` | Create |
| `commercial/plans/definitions.py` | Create — plan + entitlement definitions |
| `commercial/plans/helpers.py` | Create — check_entitlement, upgrade/downgrade helpers |
| `commercial/catalog/commercial.json` | Create — full 34-entry commercial catalog |
| `commercial/executors/lifecycle/` | Create — 8 lifecycle executors |
| `commercial/executors/support/` | Create — 12 support executors |
| `commercial/executors/health/` | Create — 9 health executors |
| `commercial/executors/backup/` | Create — 9 backup executors |
| `commercial/executors/billing/` | Create — 5 billing executors |
| `commercial/executors/tokens/` | Create — 4 token executors |
| `commercial/executors/upgrade/` | Create — 2 upgrade executors |
| `commercial/secrets/__init__.py` | Create — SecretsBackend protocol + implementations |
| `commercial/api/routes.py` | Create — internal ops read API |
| `commercial/tests/` | Create — full test suite |
| `docs/commercial/` | Create — architecture docs |
| `LICENSE` | Create — **pending license decision (AGPL-3.0 recommended)** |

`provision_ec2_platform.py` and existing CE code: **no changes**.

---

## Out of Scope

- Changes to Community Edition code
- Removing or gating any existing CE feature
- Customer-facing UI or portal
- External billing integration
- Multi-tenant database architecture (each hosted customer gets an isolated instance)
- Sub-pages on nexplane.ai (GTM/website design is a separate session)
