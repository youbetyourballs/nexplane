# Access Review Enrichment — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Replace the thin AccessReview stub with a full campaign-based access review system: live connector snapshots, evidence enrichment from existing asset data, flexible reviewer assignment, a governed lifecycle, and auditor-ready evidence export.

---

## Background

The current `AccessReview` model is a stub — a single record with unstructured JSON blobs for scope and decisions, no per-entry structure, no reviewer assignment, and no connection to connector data. The UI shows an empty entry list with no way to populate it. This spec replaces it entirely with a campaign-based system that pulls real access data from connected identity systems, enriches each entry with evidence from the existing asset inventory, routes entries to the right reviewers, and produces a complete audit trail suitable for SOC 1 / SOC 2 evidence packages.

---

## Design Decisions

- **Campaign-based, not continuous:** Each review is a point-in-time snapshot campaign. This maps to how auditors think (quarterly access review, Q2 2026) and avoids the complexity of maintaining a live cross-connector access inventory.
- **Two models replace one:** `ReviewCampaign` (top-level config and lifecycle) + `ReviewEntry` (one row per user × resource × permission triple). The current `AccessReview` table is replaced by `ReviewCampaign`; `ReviewEntry` is new.
- **Evidence from existing data:** Last login, asset sensitivity, and user status come from asset_metadata already populated by scheduled ingest — no new data pipelines needed.
- **Reviewer assignment at collection time:** Resolves once when the background collection job runs, not lazily. Unresolvable entries fall back to a configured fallback reviewer.
- **Revocations via existing change actions:** Approved revocations generate change requests using connector actions already implemented (e.g. `remove_org_member`, `remove_from_groups`, `revoke_okta_sessions`). No new execution logic needed.
- **Three reviewer models, one campaign:** `manager_centric`, `resource_owner`, `security_team` — each campaign picks one. All three resolve to a `reviewer_id` (Nexplane user), keeping the entry model uniform.

---

## Section 1: Data Models

### ReviewCampaign

Replaces the current `access_reviews` table (migration drops and recreates).

```python
class ReviewCampaign(Base):
    __tablename__ = "review_campaigns"

    id: uuid.UUID                        # PK
    organization_id: uuid.UUID           # FK → organizations
    created_by: uuid.UUID               # FK → users
    title: str                          # "Q2 2026 SOC 1 User Access Review"
    description: str | None
    campaign_type: str                  # "manager_centric" | "resource_owner" | "security_team"
    scope: dict                         # JSONB — see schema below
    reviewer_assignment_rule: dict      # JSONB — see schema below
    evidence_options: dict              # JSONB — see schema below
    status: str                         # "draft" | "collecting" | "in_review" | "awaiting_approval" | "completed" | "cancelled"
    due_date: datetime | None
    created_at: datetime
    completed_at: datetime | None
```

**scope JSONB shape:**
```json
{
  "connector_ids": ["uuid", ...] | null,
  "asset_tags": ["pci-in-scope", "sox-relevant"] | null,
  "user_groups": ["Engineering", "Finance"] | null,
  "include_inactive_users": false
}
```
All null = all connectors, all assets, all users in the org.

**reviewer_assignment_rule JSONB shape:**
```json
{
  "type": "manager_centric" | "resource_owner" | "security_team",
  "fallback_reviewer_id": "uuid"
}
```

**evidence_options JSONB shape:**
```json
{
  "include_last_login": true,
  "include_days_inactive": true,
  "include_asset_sensitivity": true
}
```

---

### ReviewEntry

One row per (user, resource, permission) triple. Populated by the collection background job.

```python
class ReviewEntry(Base):
    __tablename__ = "review_entries"

    id: uuid.UUID                        # PK
    campaign_id: uuid.UUID              # FK → review_campaigns
    # Who
    user_email: str
    user_display_name: str | None
    user_status: str                    # "active" | "suspended" | "disabled"
    # What they have access to
    resource_name: str                  # Asset name or system name
    resource_type: str                  # connector type (e.g. "okta", "github")
    connector_id: uuid.UUID | None      # FK → connectors
    permission_level: str               # "admin" | "member" | "read" | "owner" | etc.
    is_privileged: bool                 # auto-flagged for admin/owner/Domain Admins
    # Evidence (from asset_metadata at collection time)
    evidence: dict                      # JSONB — see schema below
    # Reviewer
    reviewer_id: uuid.UUID | None       # FK → users (resolved at collection)
    reviewer_unresolved: bool           # true if fallback was used
    # Decision
    decision: str | None               # "keep" | "revoke" | null
    decision_note: str | None
    decided_at: datetime | None
    decided_by: uuid.UUID | None        # FK → users
    # Change request generated on approval
    change_request_id: uuid.UUID | None # FK → change_requests
```

**evidence JSONB shape:**
```json
{
  "last_login_at": "2026-03-01T14:22:00Z" | null,
  "days_inactive": 62 | null,
  "asset_criticality": "critical" | "high" | "medium" | "low" | null,
  "asset_tags": ["pci-in-scope"],
  "flagged_inactive": true,
  "flagged_privileged": false
}
```

`flagged_inactive` = true when days_inactive > 90 (configurable). `flagged_privileged` = true when `is_privileged` is true.

---

## Section 2: Data Collectors

Each connector implements `async def get_access_entries(connector, db) -> list[AccessEntry]` where `AccessEntry` is a dataclass: `(user_email, user_display_name, resource_name, permission_level, is_privileged)`.

**Implemented collectors:**

| Connector | What it collects |
|-----------|-----------------|
| `active_directory` | Group memberships; `is_privileged=True` for Domain Admins, Administrators, Schema Admins |
| `okta` | App assignments (resource = app name) + group memberships; `is_privileged=True` for admin roles |
| `google_workspace` | Group memberships; shared drive access |
| `github` | Org roles (owner/member) + repo-level collaborator permissions; `is_privileged=True` for owner |
| `entra_id` | Role assignments + group memberships; `is_privileged=True` for Global Admin, Privileged Role Administrator |
| `aws` | IAM user → group memberships + inline policy attachments; `is_privileged=True` for AdministratorAccess |

Connectors without a collector implementation are skipped gracefully. The collection job logs which connectors were skipped and why.

**Evidence enrichment** (runs after collection, before entries are written):

1. **Last login / days inactive** — query the Asset table for an Identity asset matching `user_email`. Read `asset_metadata.last_login_at` (populated by Okta/AD/Google ingest). Compute `days_inactive = (now - last_login_at).days`. Set `flagged_inactive = days_inactive > 90`.

2. **Asset sensitivity** — query the Asset table for an asset matching `resource_name` and `connector_id`. Copy `criticality` and `tags` into entry evidence.

3. **User status** — read `asset_metadata.status` from the user's Identity asset. Map to `active | suspended | disabled`.

4. **Privileged flag** — set from the collector's `is_privileged` field.

---

## Section 3: Reviewer Assignment

Resolved at collection time by `resolve_reviewer(entry, rule, db) -> uuid | None`.

**`manager_centric`:**
1. Look up user's Identity asset by email.
2. Read `asset_metadata.manager_email`.
3. Find a Nexplane `User` record with that email in the same org.
4. If found → `reviewer_id = user.id`. If not → `reviewer_id = fallback_reviewer_id`, `reviewer_unresolved = True`.

**`resource_owner`:**
1. Look up the Asset record for the resource (by `resource_name` + `connector_id`).
2. Read `tags` for a tag matching pattern `owner:email@domain.com` or `asset_metadata.owner`.
3. Find matching Nexplane user. If not found → fallback.

**`security_team`:**
All entries → `reviewer_id = fallback_reviewer_id`. No lookup needed.

---

## Section 4: Campaign Lifecycle

```
draft → [Launch] → collecting → in_review → [All decided] → awaiting_approval → [Approve] → completed
                                                                               → [Cancel]  → cancelled
```

**draft** — campaign is configured but not launched. Entries do not exist yet.

**collecting** — `POST /review-campaigns/{id}/launch` triggers a background asyncio task. Task:
1. For each connector in scope: call its `get_access_entries()`, apply scope filters (tags, groups, inactive users).
2. Enrich each entry with evidence.
3. Resolve reviewer for each entry.
4. Bulk-insert `ReviewEntry` rows.
5. Set campaign `status = "in_review"`.

If collection fails, status returns to `draft` with an error message stored in campaign metadata.

**in_review** — reviewers log in, see their entries, submit `keep` / `revoke` decisions with optional notes.

**awaiting_approval** — automatically entered when all entries have a non-null decision. The campaign owner or any admin sees an Approve button showing the count of revocations.

**completed** — `POST /review-campaigns/{id}/approve`:
1. For each `revoke` entry: create a change request using the appropriate connector action (e.g. Okta entry → `revoke_okta_sessions` + `suspend_user`; GitHub entry → `remove_org_member`; AD entry → `disable_ad_account` + group removal).
2. Set `entry.change_request_id` for each revocation.
3. Set campaign `status = "completed"`, `completed_at = now()`.
4. Campaign and all entries are locked (no further edits).

**cancelled** — `POST /review-campaigns/{id}/cancel`. Allowed from any non-completed status. Entries are soft-deleted (retained for audit trail).

---

## Section 5: Lifecycle Triggers

Two optional automatic triggers, configurable in Settings per organization.

**Trigger 1: Offboarding review**
When an `offboard_user` change request is submitted, before execution Nexplane checks if the trigger is enabled. If yes, creates a `ReviewCampaign` with:
- `title = "Offboarding Review: {user_email}"`
- `campaign_type = "security_team"`
- `scope.user_emails = [user_email]`
- `reviewer_assignment_rule.fallback_reviewer_id` = org's configured security reviewer
- Immediately launches collection

The offboarding change request execution is held in `awaiting_approval` until the access review campaign reaches `completed` or `cancelled`.

**Trigger 2: Asset sensitivity escalation**
When an asset's `criticality` is raised to `critical` or a configured tag (e.g. `pci-in-scope`) is added, Nexplane checks if the trigger is enabled. If yes, creates a `ReviewCampaign` with:
- `title = "Sensitivity Review: {asset_name}"`
- `campaign_type = "resource_owner"`
- `scope.asset_tags = [new_tag]` (or asset_ids if tag-based scope isn't sufficient)
- Immediately launches collection

Both triggers are stored as org settings: `offboarding_review_enabled: bool`, `sensitivity_review_enabled: bool`, `default_security_reviewer_id: uuid`.

---

## Section 6: API Endpoints

New router: `backend/app/routers/review_campaigns.py` (prefix `/review-campaigns`).

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/review-campaigns` | Create campaign (draft) |
| `GET` | `/review-campaigns` | List campaigns for org (filter: status, assigned_to_me) |
| `GET` | `/review-campaigns/{id}` | Campaign detail + aggregate stats |
| `POST` | `/review-campaigns/{id}/launch` | Start collection, → collecting |
| `POST` | `/review-campaigns/{id}/cancel` | Cancel |
| `POST` | `/review-campaigns/{id}/approve` | Approve all decisions, generate CRs, → completed |
| `GET` | `/review-campaigns/{id}/entries` | List entries (filter: reviewer, decision, flagged) |
| `PUT` | `/review-campaigns/{id}/entries/{entry_id}` | Submit decision (keep/revoke + note) |
| `GET` | `/review-campaigns/{id}/evidence-export` | Download audit package (JSON) |

The old `/api/access-reviews` endpoints are replaced by `/review-campaigns`. Frontend routes update accordingly.

---

## Section 7: Frontend

**`/access-reviews`** — Campaign list, two tabs:
- **All Campaigns** — title, type badge, status badge, `32/47 entries decided` progress, due date (red if overdue), Launch / View button.
- **My Reviews** — filters to campaigns where the current user has assigned entries. Shows only their pending entries count.
- **New Campaign** button → opens creation wizard.

**Creation Wizard** (4 steps, modal or full page):
1. **Basics** — title, description, campaign type (radio), due date, fallback reviewer (user picker).
2. **Scope** — connector multi-select, asset tag filter (text chips), user group filter; toggle "Include inactive/suspended users".
3. **Evidence** — checkboxes: last login, days inactive, asset sensitivity.
4. **Review & Launch** — summary card, Launch button.

**`/access-reviews/:id`** — Campaign detail:
- **Header** — campaign title, status badge, due date, campaign type, created by.
- **Progress bar** — total / decided / pending / flagged high-risk (privileged or inactive).
- **Per-reviewer breakdown** (collapsible, manager/resource-owner campaigns) — each reviewer's name, assigned count, decided count, completion %.
- **Entry table** — columns: User | Resource | Permission | Last Login | Risk Flags | Decision. Filterable by reviewer, decision status (`all | keep | revoke | pending`), risk flag (`all | flagged`). Each row: inline Keep / Revoke toggle, note field (expandable), evidence tooltip on hover showing full evidence JSONB.
- **Approve button** — visible only when all entries decided. Shows "Approve (12 revocations will generate change requests)".
- **Download Evidence** — visible on completed campaigns. Downloads JSON with full audit trail.

**Settings additions** — new "Access Review Triggers" section:
- Toggle: Create review when a user is offboarded
- Toggle: Create review when asset sensitivity changes
- Picker: Default security reviewer (used as fallback and trigger owner)

---

## Files Changed

| File | Change |
|------|--------|
| `backend/alembic/versions/017_review_campaigns.py` | Drop `access_reviews` table; create `review_campaigns` + `review_entries` |
| `backend/app/models/review_campaign.py` | New — `ReviewCampaign` + `ReviewEntry` SQLAlchemy models |
| `backend/app/schemas/review_campaign.py` | New — Pydantic schemas |
| `backend/app/routers/review_campaigns.py` | New — all endpoints replacing `/api/access-reviews` |
| `backend/app/services/review_collector.py` | New — collection background task + per-connector `get_access_entries()` implementations |
| `backend/app/services/review_evidence.py` | New — evidence enrichment (last login, sensitivity, user status) |
| `backend/app/services/review_approver.py` | New — generates change requests for `revoke` entries |
| `backend/app/main.py` | Replace `access_reviews` router with `review_campaigns` router |
| `backend/app/models/__init__.py` | Register new models |
| `backend/app/tests/test_review_campaigns.py` | New — API + collector tests |
| `frontend/src/pages/AccessReviews.tsx` | Rewrite — campaign list + detail using new data model |
| `frontend/src/pages/AccessReviewDetail.tsx` | New — campaign detail page (or merged into AccessReviews.tsx) |
| `frontend/src/api/reviewCampaigns.ts` | New — API client replacing accessReviews.ts |
| `frontend/src/routes/index.tsx` | Update routes: `/access-reviews` + `/access-reviews/:id` |
| `frontend/src/pages/Settings.tsx` | Add Access Review Triggers section |
