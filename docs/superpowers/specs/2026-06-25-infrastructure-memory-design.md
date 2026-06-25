# Infrastructure Memory Design Spec

**Date:** 2026-06-25
**Sub-project:** 4 of 6 (Repositioning series)
**Status:** Ready to implement

---

## What it does

Infrastructure Memory is a searchable, browsable view of institutional knowledge across all assets in the organization. It surfaces two fields already stored in `asset_metadata` — `owner` (team/person responsible) and `why_exists` (one-sentence rationale) — as first-class queryable data. The `/infrastructure-memory` page replaces its current stub with a real search UI, and the asset detail page promotes `owner` and `why_exists` out of the raw metadata JSON into the header section.

---

## API Endpoint

### `GET /infrastructure-memory`

**Auth:** Bearer token (same `current_user` dependency as all other routers)

**Query parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `q` | `string` (optional) | Case-insensitive substring search across `name`, `owner`, and `why_exists` |
| `owner` | `string` (optional) | Exact match on `owner` field (case-insensitive) |
| `page` | `int` (optional, default 1) | 1-based page number |
| `page_size` | `int` (optional, default 50, max 200) | Items per page |

**Response shape:**

```json
{
  "total": 142,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "id": "uuid",
      "name": "prod-db-primary",
      "asset_type": "database",
      "environment": "prod",
      "criticality": "critical",
      "owner": "platform-eng",
      "why_exists": "Primary PostgreSQL instance for the billing service."
    }
  ]
}
```

**Implementation notes:**
- `owner` and `why_exists` are extracted from `asset_metadata` JSONB column: `asset_metadata->>'owner'` and `asset_metadata->>'why_exists'`
- Text search uses PostgreSQL `ILIKE` on name, and `ILIKE` on the extracted JSONB string values
- Results ordered by `criticality` (critical → high → medium → low) then `name`
- Assets where both `owner` and `why_exists` are null/empty are included (shown with "—" in the UI)
- Scoped to `organization_id` from auth token

---

## UI Changes

### InfrastructureMemoryPage (`frontend/src/pages/InfrastructureMemoryPage.tsx`)

Replace stub entirely. New layout:

- **Header:** Brain icon (keep), title "Infrastructure Memory", tagline "Why does this exist?" — remove amber "Preview" badge
- **Search bar:** Single text input, `?q=` param, debounced 300ms, updates URL query string
- **Results table** with columns:
  - Asset name (link to `/assets/{id}`)
  - Owner (pill/badge styled like existing criticality badges)
  - Why it exists (text, truncated at ~120 chars with title tooltip for full text)
  - Type (asset_type value)
  - Environment (dev/staging/prod badge)
- **Loading state:** Skeleton rows while fetching
- **Empty state:** "No assets match your search." with a clear-search link when `q` is set; "No assets found." when no assets exist at all
- **Pagination:** Simple prev/next with page counter ("Page 1 of 3")

No owner filter dropdown in v1 — text search covers it adequately.

### AssetDetail (`frontend/src/pages/AssetDetail.tsx`)

In the asset header/overview section, add two explicit labeled fields before (or alongside) the existing criticality/environment badges:

- **Owner:** Label "Owner" with the value from `asset.asset_metadata?.owner`. If absent, show "—".
- **Why it exists:** Label "Why it exists" with the value from `asset.asset_metadata?.why_exists`. If absent, show "—".

These should render as part of the header info row, not buried in the raw metadata JSON expander.

---

## Out of scope

- AI-generated provenance (inferring `why_exists` from change history)
- Automatic knowledge extraction from change requests or tickets
- Version history of `why_exists` or `owner` fields
- Inline editing of `owner`/`why_exists` from the Infrastructure Memory page
- `owner` filter dropdown (deferred; text search covers the use case)

---

## Acceptance criteria

- [ ] `GET /infrastructure-memory` returns paginated asset list with `owner` and `why_exists` extracted from `asset_metadata`
- [ ] `?q=` search filters on name, owner, and why_exists (case-insensitive)
- [ ] Response is scoped to the authenticated org
- [ ] InfrastructureMemoryPage renders real data (not stub copy)
- [ ] Amber "Preview" badge is removed
- [ ] Search bar debounces and updates results without full page reload
- [ ] Asset name links to `/assets/{id}`
- [ ] Empty state shows when no results match
- [ ] AssetDetail shows `owner` and `why_exists` as explicit labeled fields in the header section
- [ ] AssetDetail gracefully renders "—" when either field is absent
- [ ] Backend has a passing pytest test for the new endpoint (search, pagination, org scoping)
