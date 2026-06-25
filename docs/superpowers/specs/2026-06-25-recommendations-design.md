# Recommendations Engine — Design Spec

**Date:** 2026-06-25  
**Status:** Implemented

## Problem

The `/recommendations` page is a stub. Users have no automated signal about infrastructure posture gaps — missing owners, undocumented assets, orphaned critical assets, or high-blast-radius nodes.

## Solution

A pure read-only rule engine: `GET /recommendations` queries existing `assets` and `asset_relationships` tables, applies 5 rules, deduplicates by asset (higher priority wins), sorts critical → high → medium → low, and returns ≤50 results.

## Rules

| Rule | Trigger | Priority |
|------|---------|----------|
| `critical_missing_owner` | Critical asset, no `asset_metadata.owner` | critical |
| `critical_no_relationships` | Critical asset, zero edges in or out | high |
| `high_downstream_count` | Asset with ≥5 downstream dependents | high |
| `missing_owner` | Any asset with no owner (non-critical) | medium |
| `missing_why_exists` | Any asset with no `asset_metadata.why_exists` | low |

Deduplication: one recommendation per asset — highest priority rule wins. `critical_missing_owner` supersedes `missing_owner` for the same asset.

## API Response

```
GET /recommendations
→ 200 [{ id, asset_id, asset_name, asset_type, criticality, rule, title, description, priority, action_link }]
```

Sorted by priority then asset name. Max 50 items.

## Frontend

- Priority filter tabs: All / Critical / High / Medium / Low
- Color-coded priority badge per card
- Asset name links to `/assets/{id}`
- Empty state: "No recommendations — your infrastructure looks healthy!"
- No new tables, no new DB migrations.
