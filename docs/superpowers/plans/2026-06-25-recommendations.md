# Recommendations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rule-based recommendation engine: 5 rules applied to org assets, surfaced on a functional /recommendations page.

**Architecture:** Pure read-only endpoint queries assets and asset_relationships to find rule violations. No new tables. Frontend renders results with priority filter tabs.

**Tech Stack:** FastAPI, SQLAlchemy async, React/TypeScript

---

## Tasks

- [ ] **Task 1 — Backend router**
  - Create `backend/app/routers/recommendations.py` with 5 rules
  - Register router in `backend/app/main.py`
  - Commit: `feat: add rule-based recommendations engine (GET /recommendations)`

- [ ] **Task 2 — Frontend page**
  - Add `Recommendation` interface + `recommendationsApi` to `frontend/src/api/endpoints.ts`
  - Replace stub `frontend/src/pages/RecommendationsPage.tsx` with functional implementation
  - Commit: `feat: implement Recommendations page with priority filter tabs`

## Verification

- Demo orgs have assets with missing owners, missing why_exists, and asset_relationships — GET /recommendations should return non-empty lists
- Priority filter tabs show correct counts
- Empty state renders when filter yields zero results
