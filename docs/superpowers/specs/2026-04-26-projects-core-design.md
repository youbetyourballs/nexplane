# Projects Core — Design Spec (Spec 2a)

**Date:** 2026-04-26  
**Status:** Approved  
**Scope:** Project entity, manual CR assembly (pick existing + create inline), dependency ordering, project list and detail pages, execution view. AI conversational planning is Spec 2b (separate).

---

## Problem Statement

Complex infrastructure initiatives (microsegmentation, key rotation across services, large-scale IP changes) involve many interdependent change requests that currently have no grouping, sequencing, or shared execution context. Operators must hunt through the change requests list to find what to execute next, and there is no way to express "CR B cannot start until CR A completes."

Projects provide a named, goal-oriented container for a set of change requests with explicit dependency ordering and a centralized execution view.

---

## Decisions

- **Project status** is manually set (draft → in_progress → completed/cancelled) — a tracking label, not a computed state.
- **Eligible CR logic** is computed: a CR is eligible to execute when all CRs it `depends_on` have status `completed`. Not stored — derived on read.
- **Dependencies** stored as a JSON array of `ProjectChangeRequest.id`s on each `ProjectChangeRequest` row. Circular dependency prevention is enforced in the API and UI.
- **Execution granularity**: each CR is executed individually from the project view. No bulk execution — operator control at each step is a core safety requirement.
- **Inline CR creation**: uses the existing `POST /change-requests` endpoint. The new CR is immediately added to the project. Plan generation and approval happen on the CR detail page separately.
- **Visualization**: list view with dependency indicators for now. Visual DAG is deferred.
- **AI planning**: deferred to Spec 2b.

---

## Section 1: Backend

### 1.1 New DB Models

**`Project`**

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID PK | |
| `organization_id` | UUID FK | |
| `created_by` | UUID FK → User | |
| `name` | String 500 | |
| `description` | Text | Optional longer description |
| `goal` | Text | Human-readable objective (also AI prompt seed for Spec 2b) |
| `status` | Enum | `draft \| in_progress \| completed \| cancelled` |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

**`ProjectChangeRequest`** (join table)

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID PK | Referenced in `depends_on` arrays |
| `project_id` | UUID FK | |
| `change_request_id` | UUID FK | |
| `sequence_order` | Integer | Display and default execution order |
| `depends_on` | JSON | List of `ProjectChangeRequest.id`s that must be `completed` before this CR is eligible |

### 1.2 Pydantic Schemas

```python
class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    goal: str = ""

class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    goal: str | None = None
    status: ProjectStatus | None = None

class ProjectRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    organization_id: uuid.UUID
    created_by: uuid.UUID
    name: str
    description: str
    goal: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime

class ProjectMemberCreate(BaseModel):
    change_request_id: uuid.UUID
    sequence_order: int = 0
    depends_on: list[uuid.UUID] = []  # ProjectChangeRequest IDs

class ProjectMemberUpdate(BaseModel):
    sequence_order: int | None = None
    depends_on: list[uuid.UUID] | None = None

class ProjectMemberRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    project_id: uuid.UUID
    change_request_id: uuid.UUID
    sequence_order: int
    depends_on: list[uuid.UUID]
    # Embedded CR summary for display
    change_request: ChangeRequestSummary
    # Computed eligibility
    eligible: bool
```

### 1.3 API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET /projects` | list | All org projects with member count + completed count |
| `POST /projects` | create | Creates project in `draft` status |
| `GET /projects/{id}` | detail | Project + all members with embedded CR summaries + computed `eligible` |
| `PATCH /projects/{id}` | update | Name, description, goal, status |
| `POST /projects/{id}/members` | add member | Add existing CR to project; validates CR belongs to org |
| `DELETE /projects/{id}/members/{pcr_id}` | remove member | Remove CR from project |
| `PATCH /projects/{id}/members/{pcr_id}` | update member | Update `sequence_order` or `depends_on` |

### 1.4 Circular Dependency Prevention

On `POST /projects/{id}/members` and `PATCH /projects/{id}/members/{pcr_id}`, the API walks the proposed `depends_on` graph and returns `400` if any cycle is detected. Check: for the new `depends_on` list, traverse the graph depth-first; if the current PCR ID is encountered during traversal, it is a cycle.

### 1.5 Eligible CR Computation

In `GET /projects/{id}`, for each `ProjectChangeRequest`:

```python
def is_eligible(pcr: ProjectChangeRequest, all_members: list[ProjectChangeRequest]) -> bool:
    cr_by_pcr_id = {m.id: m for m in all_members}
    for dep_pcr_id in pcr.depends_on:
        dep = cr_by_pcr_id.get(dep_pcr_id)
        if not dep or dep.change_request.status != ChangeRequestStatus.completed:
            return False
    return True
```

CRs with no `depends_on` entries are always eligible.

### 1.6 Member Mutation Rules

`POST /projects/{id}/members` and `DELETE /projects/{id}/members/{pcr_id}` return `409` if project status is not `draft`. `PATCH /projects/{id}/members/{pcr_id}` (updating `depends_on` or `sequence_order`) is allowed for `draft` and `in_progress` projects.

### 1.7 Audit Events

- `project.created` — on POST /projects
- `project.updated` — on PATCH /projects/{id}
- `project.member_added` — on POST /projects/{id}/members
- `project.member_removed` — on DELETE /projects/{id}/members/{pcr_id}

---

## Section 2: Projects List Page

**Route:** `/projects` → `Projects.tsx`  
**Sidebar:** Add `Projects` link between Dashboard and Change Requests in `Sidebar.tsx`.

**Page layout:** `PageHeader` with "Projects" title and **New Project** button. Card grid below.

**Project card:**
- Project name (bold) and goal (truncated, 1 line)
- Status badge
- Progress bar + `{N completed} / {N total} change requests`
- Created date · creator name
- Clicking card navigates to `/projects/:id`

**New Project** button navigates to `/projects/new`. The `ProjectDetail` component handles both create and edit — in create mode it renders with empty fields and auto-focuses the name input.

---

## Section 3: Project Detail / Edit Page

**Routes:** `/projects/:id` and `/projects/new` → `ProjectDetail.tsx`

Draft projects are fully editable. In-progress projects allow updating status and member dependencies but not adding/removing members. Completed and cancelled projects are read-only.

### 3.1 Header

```
[← Projects]  {Project name input}                [Status dropdown]  [Save]
               {Goal input — one-line description}
```

Name and goal are inline inputs. Status dropdown shows `draft | in_progress | completed | cancelled`. **Save** calls `PATCH /projects/{id}` with name, goal, and status only. Member mutations (add, remove, reorder, set dependencies) are applied immediately via their own API calls — they do not wait for Save.

### 3.2 Change Request Assembly (Draft Mode)

A list of project members, each rendered as a row:

```
[↕] ① Update firewall policy – payments zone   [security_group_update] [draft]
     Depends on: —                                              [Set deps ▾] [×]

[↕] ② Update DNS record for api.payments.internal  [dns_update] [draft]
     Depends on: ① Update firewall policy                       [Set deps ▾] [×]
```

**Fields per row:**
- Drag handle `↕` — reorders by updating `sequence_order` on drop
- Sequence number (1-indexed, display only)
- CR title + change type badge + current CR status badge
- `Depends on:` — shows names of dependency CRs or "—" if none
- `[Set deps ▾]` — popover listing all other project CRs as checkboxes; selecting creates dependency; circular dependency attempts show an inline error and are not saved
- `[×]` — removes CR from project (does not delete the CR itself)

**Bottom of list:**
- `[+ Add existing CR]` — search popover: type-ahead filtering of org's change requests by title, excludes CRs already in the project; selecting adds via `POST /projects/{id}/members`
- `[+ Create new CR]` — expands an inline form (see 3.3)

### 3.3 Inline CR Creation Form

Expands below the member list in draft mode. Uses existing `POST /change-requests` then immediately `POST /projects/{id}/members`:

```
Title:          [_________________________________]
Change Type:    [dns_update ▾]
Target Assets:  [Multi-select from asset list    ]
Desired Outcome: [JSON textarea — pre-filled with template for selected type]

[Create & Add to Project]  [Cancel]
```

The desired outcome textarea is pre-filled with the same JSON template used in the standalone `CreateChangeRequest` page. On success, the inline form collapses and the new CR appears at the bottom of the member list. The operator can navigate to the CR detail page separately to generate a plan and submit for approval.

### 3.4 Execution View (In-Progress Mode)

When status is `in_progress`, the drag handles and add/remove controls are hidden. The list becomes a status-focused execution panel:

**Per-row in execution view:**
- Sequence number + CR title + change type badge
- CR status badge (live — refetches every 5s while any CR is executing or verifying)
- Dependency line: `✓ Ready` (green) or `⏳ Waiting on: [CR name(s)]` (amber)
- Action button (rightmost):
  - `Generate Plan` — CR is `draft`, no plan yet; opens CR detail page
  - `Submit for Approval` — CR is `planned`; performs submit inline
  - `View Approvals` — CR is `awaiting_approval`; navigates to CR detail
  - **`Execute →`** — CR is `approved` and `eligible: true`; triggers execution inline via `POST /change-requests/{id}/execute`; highlighted in brand color
  - `Executing…` — spinner, disabled; CR is `executing` or `verifying`
  - `Completed ✓` — green, disabled
  - `—` — greyed out; CR is blocked by unmet dependencies

**Project-level summary bar** above the list:
```
● 2 completed  ⟳ 1 executing  ⏳ 3 blocked  ○ 1 pending
```

---

## Section 4: Summary of New Files

| File | Purpose |
|------|---------|
| `backend/app/models/project.py` | Project + ProjectChangeRequest ORM models |
| `backend/app/schemas/project.py` | Pydantic schemas (ProjectCreate, ProjectRead, ProjectMemberRead, etc.) |
| `backend/app/routers/projects.py` | All project endpoints |
| `backend/alembic/versions/003_add_projects.py` | Migration: projects + project_change_requests tables |
| `frontend/src/types/api.ts` | Add Project, ProjectMember, ProjectStatus types |
| `frontend/src/api/endpoints.ts` | Add projectsApi (list, get, create, update, addMember, removeMember, updateMember) |
| `frontend/src/pages/Projects.tsx` | Project list page |
| `frontend/src/pages/ProjectDetail.tsx` | Project detail + edit + execution view |
| `frontend/src/routes/index.tsx` | Add /projects and /projects/:id routes; add /projects/new |
| `frontend/src/components/Sidebar.tsx` | Add Projects nav link |
