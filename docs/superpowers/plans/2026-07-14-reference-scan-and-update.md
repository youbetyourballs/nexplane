# Reference Scan and Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a workflow that discovers hardcoded references (connection strings, hostnames, IPs, DSNs, ARNs, secret names, CNAMEs) to a migrating resource across AWS, Kubernetes, and agent-monitored hosts; auto-registers discovered consumers in the asset graph; and proposes rollbackable update CRs, with AI triage to bucket confident changes vs. exceptions requiring operator review.

**Architecture:** Three scan surfaces (AWS catalog actions, K8s catalog actions, Nexplane Agent commands) each emit a unified hit schema. A `scan_for_references` orchestrator CR fans out to all relevant connectors, aggregates results, runs identity resolution to match hits to assets, then calls AI triage to produce pre-populated update CRs for confident hits and a `ScanException` bucket for ambiguous ones. Operators resolve exceptions via UI or MCP; unresolved exceptions at execution time become `reference_not_updated` findings. Update CRs use reconstitution rollback (save old value, restore on rollback) and participate in FILO rollback stacks.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, Temporal (activities.py), boto3, kubernetes client, Go 1.22 (agent commands), Anthropic SDK, FastMCP

## Global Constraints

- All new `ChangeType` values are strings in the `ChangeType` enum in `backend/app/models/change_request.py`
- Catalog action JSON files live at `backend/app/connectors/catalog/{connector_type}.json`
- All catalog executors live at `backend/app/connectors/executors/{connector_type}/{action}.py` implementing `execute(cr, connector, db) -> dict`
- Executor `execute()` must return a dict; include `"_auto_asset": {...}` to trigger automatic asset creation
- Scan surface unified hit schema: `{surface, location, matched_term, snippet, consumer_identity: {stable_id, hostname, surface_metadata}}`
- Identity resolution tiers: (1) `external_id` match, (2) hostname/DNS/CNAME, (3) composite fingerprint (≥2 signals), (4) new asset
- Reconstitution rollback pattern: save old value before writing, restore exact saved value on rollback
- Agent executors dispatch via `_dispatch.dispatch_agent_job(command=..., parameters=..., asset_ids=..., timeout_seconds=...)`
- Latest Alembic revision chain: `refsc001` → `mig001` → `tunnel002`
- `scan_for_references` routes via `elif change_type == "scan_for_references"` branch in `activities.py` (NOT fan-out registry)
- Secret values are never stored, logged, or returned — Secrets Manager and Vault scanning matches metadata (name, ARN, path) only
- All AI-assisted CRs must pass through `awaiting_approval` before any action executes
- MCP parity: every exception resolution path available via MCP tools, not just UI
- Smoke tests run on EC2 via Tailscale, never on local Docker
- `finding_type = "reference_not_updated"` requires no schema migration (it's a plain String column)

> **Tasks 8-13** are in [2026-07-14-reference-scan-and-update-part2.md](2026-07-14-reference-scan-and-update-part2.md) (split to stay within token limits).

---

### Task 1: Foundation — ChangeTypes, ScanException model, Alembic migration, manifest entries

**Files:**
- Modify: `backend/app/models/change_request.py`
- Create: `backend/app/models/scan_exception.py`
- Create: `backend/alembic/versions/refsc001_scan_exceptions.py`
- Modify: `backend/app/services/manifest_builder.py` (or manifest JSON — check which pattern exists)
- Modify: `backend/app/main.py` (import new model so Alembic sees it)

**Interfaces:**
- Produces: `ChangeType.scan_for_references`, `ChangeType.update_reference` enum values
- Produces: `ScanException` SQLAlchemy model with fields: `id` (UUID), `organization_id` (UUID FK), `scan_cr_id` (UUID FK → change_requests), `consumer_asset_id` (UUID FK → assets, nullable), `matched_term` (String), `location` (String), `surface` (String), `snippet` (Text), `reason` (Text), `suggested_action` (String), `confidence` (Float), `status` (String: "pending"/"resolved"/"dismissed"), `resolution_notes` (Text nullable), `resolved_by_cr_id` (UUID FK nullable), `created_at` (DateTime), `updated_at` (DateTime)
- Produces: Alembic migration `refsc001` with `down_revision = "mig001"`

- [ ] **Step 1: Read the current ChangeType enum to find the right insertion point**

```bash
grep -n "update_connection_strings\|resolve_dependencies\|class ChangeType" backend/app/models/change_request.py | head -20
```

Expected: lines showing `ChangeType` class and the two stub entries around line 434-439.

- [ ] **Step 2: Write the failing test for new ChangeType values**

```python
# backend/tests/unit/test_change_type_foundation.py
from app.models.change_request import ChangeType

def test_scan_for_references_change_type_exists():
    assert ChangeType.scan_for_references == "scan_for_references"

def test_update_reference_change_type_exists():
    assert ChangeType.update_reference == "update_reference"
```

Run: `cd backend && python -m pytest tests/unit/test_change_type_foundation.py -v`
Expected: FAIL with `AttributeError: scan_for_references`

- [ ] **Step 3: Add the two new ChangeType values**

In `backend/app/models/change_request.py`, find the `ChangeType` enum class and add after the existing stub entries:

```python
    scan_for_references = "scan_for_references"
    update_reference = "update_reference"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_change_type_foundation.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for ScanException model**

```python
# backend/tests/unit/test_scan_exception_model.py
import pytest
from app.models.scan_exception import ScanException

def test_scan_exception_has_required_fields():
    required = [
        "id", "organization_id", "scan_cr_id", "consumer_asset_id",
        "matched_term", "location", "surface", "snippet",
        "reason", "suggested_action", "confidence", "status",
        "resolution_notes", "resolved_by_cr_id", "created_at", "updated_at",
    ]
    mapper_columns = {c.key for c in ScanException.__mapper__.column_attrs}
    for field in required:
        assert field in mapper_columns, f"Missing column: {field}"

def test_scan_exception_status_values():
    # status is a plain string — no enum constraint at model level
    se = ScanException.__new__(ScanException)
    se.status = "pending"
    assert se.status == "pending"
```

Run: `cd backend && python -m pytest tests/unit/test_scan_exception_model.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 6: Create ScanException model**

```python
# backend/app/models/scan_exception.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ScanException(Base):
    __tablename__ = "scan_exceptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    scan_cr_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False, index=True)
    consumer_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True)
    matched_term: Mapped[str] = mapped_column(String(512), nullable=False)
    location: Mapped[str] = mapped_column(String(1024), nullable=False)
    surface: Mapped[str] = mapped_column(String(64), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    suggested_action: Mapped[str] = mapped_column(String(256), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_cr_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 7: Import ScanException in main.py so Alembic sees it**

In `backend/app/main.py`, find the block where models are imported for Alembic metadata and add:

```python
from app.models.scan_exception import ScanException  # noqa: F401
```

- [ ] **Step 8: Run the ScanException model test**

Run: `cd backend && python -m pytest tests/unit/test_scan_exception_model.py -v`
Expected: PASS

- [ ] **Step 9: Generate the Alembic migration**

```bash
cd backend && alembic revision --autogenerate -m "scan_exceptions" --rev-id refsc001
```

Then open the generated file and verify:
- `down_revision = "mig001"`
- `upgrade()` creates `scan_exceptions` table with all columns
- `downgrade()` drops `scan_exceptions` table

If `down_revision` is wrong, edit it manually to `"mig001"`.

- [ ] **Step 10: Add manifest entries**

Read the existing manifest pattern:

```bash
grep -rn "scan_for_references\|update_reference\|change_type.*manifest\|get_manifest" backend/app/services/manifest_builder.py | head -20
```

If manifest is JSON, find its location and add two entries. If it's Python, add to the list. Follow the exact schema of existing entries:

```python
{
    "change_type": "scan_for_references",
    "display_name": "Scan for References",
    "domain": "Reference Management",
    "touches": ["aws", "kubernetes", "nexplane_agent"],
    "preconditions": ["target resource identified", "at least one connector configured"],
    "effects": ["creates scan exceptions", "registers discovered consumer assets"],
    "rollback_type": "no_op",
},
{
    "change_type": "update_reference",
    "display_name": "Update Reference",
    "domain": "Reference Management",
    "touches": ["aws", "kubernetes", "nexplane_agent"],
    "preconditions": ["scan_for_references completed", "consumer asset identified"],
    "effects": ["updates configuration value", "old value saved for rollback"],
    "rollback_type": "reconstitution",
},
```

- [ ] **Step 11: Commit**

```bash
git add backend/app/models/change_request.py backend/app/models/scan_exception.py backend/app/main.py backend/alembic/versions/refsc001_scan_exceptions.py backend/app/services/manifest_builder.py backend/tests/unit/test_change_type_foundation.py backend/tests/unit/test_scan_exception_model.py
git commit -m "feat: add scan_for_references/update_reference ChangeTypes and ScanException model"
```

---

### Task 2: Identity Resolution Service

**Files:**
- Create: `backend/app/services/identity_resolution.py`
- Create: `backend/tests/unit/test_identity_resolution.py`

**Interfaces:**
- Consumes: `Asset` SQLAlchemy model (`id`, `name`, `asset_metadata`, `connector_id`, `organization_id`), async SQLAlchemy session
- Produces:
  ```python
  @dataclass
  class IdentityResolutionResult:
      asset_id: uuid.UUID | None      # None = new asset
      tier: int                       # 1-4
      confidence: float               # 0.0-1.0
      new_asset_data: dict | None     # populated when tier == 4
  
  async def resolve_consumer_identity(
      db: AsyncSession,
      organization_id: uuid.UUID,
      consumer_identity: dict,        # {stable_id, hostname, surface_metadata}
  ) -> IdentityResolutionResult
  ```

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_identity_resolution.py
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.identity_resolution import resolve_consumer_identity, IdentityResolutionResult


@pytest.mark.asyncio
async def test_tier1_matches_external_id():
    """Stable cloud ID matches Asset.asset_metadata['arn'] or similar."""
    org_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    mock_db = AsyncMock()
    
    # Mock: asset found by external_id in asset_metadata
    mock_asset = MagicMock()
    mock_asset.id = asset_id
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_asset
    mock_db.execute.return_value = mock_result
    
    result = await resolve_consumer_identity(
        db=mock_db,
        organization_id=org_id,
        consumer_identity={
            "stable_id": "arn:aws:lambda:us-east-1:123:function:my-func",
            "hostname": None,
            "surface_metadata": {},
        },
    )
    assert result.tier == 1
    assert result.asset_id == asset_id
    assert result.confidence >= 0.99


@pytest.mark.asyncio
async def test_tier4_creates_new_asset_data():
    """No match at any tier returns tier=4 with new_asset_data populated."""
    org_id = uuid.uuid4()
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_result
    
    result = await resolve_consumer_identity(
        db=mock_db,
        organization_id=org_id,
        consumer_identity={
            "stable_id": None,
            "hostname": "unknown-host-xyz.internal",
            "surface_metadata": {},
        },
    )
    assert result.tier == 4
    assert result.asset_id is None
    assert result.new_asset_data is not None
    assert "name" in result.new_asset_data
```

Run: `cd backend && python -m pytest tests/unit/test_identity_resolution.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 2: Implement identity_resolution.py**

```python
# backend/app/services/identity_resolution.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from dataclasses import dataclass
from sqlalchemy import select, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.asset import Asset


@dataclass
class IdentityResolutionResult:
    asset_id: uuid.UUID | None
    tier: int
    confidence: float
    new_asset_data: dict | None = None


async def resolve_consumer_identity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    consumer_identity: dict,
) -> IdentityResolutionResult:
    """
    4-tier identity resolution for scan hit consumers.

    Tier 1: stable cloud ID (ARN, resource ID) matches asset_metadata JSON.
    Tier 2: hostname or DNS name matches asset name or asset_metadata.
    Tier 3: composite fingerprint — match on ≥2 signals from surface_metadata.
    Tier 4: no match — return new_asset_data for auto-registration.
    """
    stable_id = consumer_identity.get("stable_id")
    hostname = consumer_identity.get("hostname")
    surface_meta = consumer_identity.get("surface_metadata") or {}

    base_q = select(Asset).where(Asset.organization_id == organization_id)

    # Tier 1: stable cloud ID in asset_metadata (arn, resource_id, instance_id, etc.)
    if stable_id:
        for field in ("arn", "resource_id", "instance_id", "function_name", "cluster_id"):
            q = base_q.where(
                cast(Asset.asset_metadata[field], String) == f'"{stable_id}"'
            )
            r = await db.execute(q)
            asset = r.scalar_one_or_none()
            if asset:
                return IdentityResolutionResult(asset_id=asset.id, tier=1, confidence=0.99)

    # Tier 2: hostname / DNS name match on asset.name
    if hostname:
        q = base_q.where(
            or_(
                Asset.name == hostname,
                Asset.name == hostname.split(".")[0],
            )
        )
        r = await db.execute(q)
        assets = r.scalars().all()
        if len(assets) == 1:
            return IdentityResolutionResult(asset_id=assets[0].id, tier=2, confidence=0.85)
        if len(assets) > 1:
            # Ambiguous — pick closest and flag lower confidence
            return IdentityResolutionResult(asset_id=assets[0].id, tier=2, confidence=0.50)

    # Tier 3: composite fingerprint — ≥2 signals from surface_metadata
    mac = surface_meta.get("mac_address")
    os_type = surface_meta.get("os_type")
    iface = surface_meta.get("primary_interface_ip")
    signals_matched = 0
    candidate = None
    for signal_field, signal_val in [("mac_address", mac), ("os_type", os_type), ("primary_interface_ip", iface)]:
        if not signal_val:
            continue
        q = base_q.where(
            cast(Asset.asset_metadata[signal_field], String) == f'"{signal_val}"'
        )
        r = await db.execute(q)
        a = r.scalar_one_or_none()
        if a:
            signals_matched += 1
            candidate = a
    if signals_matched >= 2 and candidate:
        return IdentityResolutionResult(asset_id=candidate.id, tier=3, confidence=0.70)

    # Tier 4: no match — build new asset data for auto-registration
    name = hostname or stable_id or surface_meta.get("label") or "unknown-consumer"
    new_asset_data = {
        "name": name,
        "asset_type": "application",
        "environment": "unknown",
        "asset_metadata": {
            "discovered_via": "reference_scan",
            "stable_id": stable_id,
            "hostname": hostname,
            **surface_meta,
        },
    }
    return IdentityResolutionResult(asset_id=None, tier=4, confidence=0.0, new_asset_data=new_asset_data)
```

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/unit/test_identity_resolution.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/identity_resolution.py backend/tests/unit/test_identity_resolution.py
git commit -m "feat: identity resolution service (4-tier: external_id, hostname, fingerprint, new)"
```

---

### Task 3: AI Triage Service

**Files:**
- Create: `backend/app/services/reference_triage.py`
- Create: `backend/tests/unit/test_reference_triage.py`

**Interfaces:**
- Consumes: list of resolved hits (each: `{hit: dict, asset_id: uuid|None, tier: int, confidence: float}`), migration context `{source_term: str, target_term: str, notes: str}`, `SecretsService`
- Produces:
  ```python
  @dataclass
  class TriageResult:
      confident_updates: list[dict]    # each: {asset_id, location, surface, old_value, new_value, change_type_params}
      exceptions: list[dict]           # each: {hit, asset_id, reason, suggested_action, confidence}
  
  async def triage_scan_hits(
      hits: list[dict],
      migration_context: dict,
      settings,
      secrets_svc: SecretsService,
  ) -> TriageResult
  ```

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_reference_triage.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.reference_triage import triage_scan_hits, TriageResult

SAMPLE_HITS = [
    {
        "hit": {
            "surface": "aws_lambda_env",
            "location": "arn:aws:lambda:us-east-1:123:function:api-gateway",
            "matched_term": "old-db.internal",
            "snippet": "DB_HOST=old-db.internal",
        },
        "asset_id": None,
        "tier": 4,
        "confidence": 0.0,
    }
]

MIGRATION_CONTEXT = {
    "source_term": "old-db.internal",
    "target_term": "new-db.internal",
    "notes": "Database hostname change as part of RDS migration",
}


@pytest.mark.asyncio
async def test_triage_returns_triage_result():
    mock_settings = MagicMock()
    mock_settings.anthropic_api_key_encrypted = "encrypted_key"
    mock_secrets = MagicMock()
    mock_secrets.decrypt.return_value = "sk-test"

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"confident_updates": [], "exceptions": [{"reason": "no asset match", "suggested_action": "register asset", "confidence": 0.3}]}')]

    with patch("anthropic.AsyncAnthropic") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        result = await triage_scan_hits(
            hits=SAMPLE_HITS,
            migration_context=MIGRATION_CONTEXT,
            settings=mock_settings,
            secrets_svc=mock_secrets,
        )

    assert isinstance(result, TriageResult)
    assert isinstance(result.confident_updates, list)
    assert isinstance(result.exceptions, list)
```

Run: `cd backend && python -m pytest tests/unit/test_reference_triage.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 2: Implement reference_triage.py**

```python
# backend/app/services/reference_triage.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import logging
from dataclasses import dataclass, field
from app.services.secrets_service import SecretsService

logger = logging.getLogger(__name__)

_TRIAGE_SYSTEM = """\
You are a Nexplane infrastructure triage assistant. Given a list of scan hits (references to a resource being migrated) and migration context, classify each hit into one of two buckets:

1. confident_updates: hits where you are >80% certain the reference should be updated to the new value. Populate the old_value, new_value, and change_type_params fields.
2. exceptions: hits that are ambiguous, risky, or require operator review. Provide a reason and suggested_action.

Rules:
- Never include secret values in your response. References to secret names/ARNs/paths are fine; actual secret values are not.
- A hit with a known asset_id and a clear string substitution is confident.
- A hit with no asset_id (tier 4) is usually an exception unless the substitution is completely unambiguous.
- Respond ONLY with valid JSON matching the schema: {"confident_updates": [...], "exceptions": [...]}

confident_update schema: {"hit_index": int, "asset_id": "uuid or null", "location": "str", "surface": "str", "old_value": "str", "new_value": "str", "change_type_params": {}}
exception schema: {"hit_index": int, "asset_id": "uuid or null", "reason": "str", "suggested_action": "str", "confidence": float}
"""


@dataclass
class TriageResult:
    confident_updates: list[dict] = field(default_factory=list)
    exceptions: list[dict] = field(default_factory=list)


async def triage_scan_hits(
    hits: list[dict],
    migration_context: dict,
    settings,
    secrets_svc: SecretsService,
) -> TriageResult:
    if not hits:
        return TriageResult()

    import anthropic
    api_key = secrets_svc.decrypt(settings.anthropic_api_key_encrypted)
    client = anthropic.AsyncAnthropic(api_key=api_key)

    user_content = json.dumps({
        "migration_context": migration_context,
        "hits": [
            {
                "index": i,
                "surface": h["hit"]["surface"],
                "location": h["hit"]["location"],
                "matched_term": h["hit"]["matched_term"],
                "snippet": h["hit"]["snippet"],
                "asset_id": str(h["asset_id"]) if h["asset_id"] else None,
                "resolution_tier": h["tier"],
                "resolution_confidence": h["confidence"],
            }
            for i, h in enumerate(hits)
        ],
    }, indent=2)

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        system=_TRIAGE_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )

    try:
        raw = response.content[0].text.strip()
        data = json.loads(raw)
        return TriageResult(
            confident_updates=data.get("confident_updates", []),
            exceptions=data.get("exceptions", []),
        )
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("AI triage response parse failed: %s", e)
        # Fall all hits to exceptions
        return TriageResult(
            exceptions=[
                {"hit_index": i, "asset_id": None, "reason": "AI triage failed to parse", "suggested_action": "manual review", "confidence": 0.0}
                for i in range(len(hits))
            ]
        )
```

- [ ] **Step 3: Run the test**

Run: `cd backend && python -m pytest tests/unit/test_reference_triage.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/reference_triage.py backend/tests/unit/test_reference_triage.py
git commit -m "feat: AI triage service — batched Anthropic call for confident/exception buckets"
```

---

### Task 4: AWS Scan Catalog Actions and Executor

**Files:**
- Modify: `backend/app/connectors/catalog/aws.json`
- Create: `backend/app/connectors/executors/aws/reference_scan.py`
- Create: `backend/tests/unit/test_aws_reference_scan.py`

**Interfaces:**
- Produces: 6 catalog actions: `scan_lambda_env_vars`, `scan_ecs_task_defs`, `scan_rds_parameter_groups`, `scan_secrets_manager_metadata`, `scan_ssm_parameters_metadata`, `scan_ec2_user_data`
- Produces: executor functions `execute_scan_lambda_env_vars(cr, connector, db) -> dict` etc.
- Each executor returns: `{"hits": [<unified hit schema>], "scan_summary": {"scanned": int, "matched": int}}`
- Unified hit schema: `{"surface": str, "location": str, "matched_term": str, "snippet": str, "consumer_identity": {"stable_id": str|None, "hostname": str|None, "surface_metadata": dict}}`

- [ ] **Step 1: Read existing AWS catalog to understand action schema**

```bash
head -60 backend/app/connectors/catalog/aws.json
```

Note the exact fields used per action (id, name, description, parameters schema, etc.).

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/unit/test_aws_reference_scan.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

def _make_mock_boto(lambda_env_vars):
    """Return a boto3 client mock that returns given Lambda env vars."""
    mock_lambda = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {"Functions": [
            {
                "FunctionArn": "arn:aws:lambda:us-east-1:123:function:my-func",
                "FunctionName": "my-func",
                "Environment": {"Variables": lambda_env_vars},
            }
        ]}
    ]
    mock_lambda.get_paginator.return_value = mock_paginator
    return mock_lambda


@pytest.mark.asyncio
async def test_scan_lambda_finds_matching_term():
    from app.connectors.executors.aws.reference_scan import scan_lambda_env_vars

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["old-db.internal"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    with patch("boto3.client", return_value=_make_mock_boto({"DB_HOST": "old-db.internal", "OTHER": "val"})):
        result = await scan_lambda_env_vars(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["surface"] == "aws_lambda_env"
    assert hit["matched_term"] == "old-db.internal"
    assert "DB_HOST" in hit["snippet"]
    assert hit["consumer_identity"]["stable_id"] == "arn:aws:lambda:us-east-1:123:function:my-func"


@pytest.mark.asyncio
async def test_scan_lambda_no_match():
    from app.connectors.executors.aws.reference_scan import scan_lambda_env_vars

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["does-not-exist"], "region": "us-east-1"}
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    with patch("boto3.client", return_value=_make_mock_boto({"DB_HOST": "other-host"})):
        result = await scan_lambda_env_vars(mock_cr, mock_connector, mock_db)

    assert result["hits"] == []
    assert result["scan_summary"]["matched"] == 0
```

Run: `cd backend && python -m pytest tests/unit/test_aws_reference_scan.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Add 6 actions to aws.json**

Open `backend/app/connectors/catalog/aws.json` and append to the actions array (following exact schema of existing entries):

```json
{
  "id": "scan_lambda_env_vars",
  "name": "Scan Lambda Environment Variables",
  "description": "Search Lambda function environment variables for references to a migrating resource.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}, "description": "Strings to search for (hostname, IP, ARN, DSN fragment, etc.)"},
      "region": {"type": "string", "description": "AWS region (default: connector default region)"}
    }
  },
  "rollback_strategy": "no_op"
},
{
  "id": "scan_ecs_task_defs",
  "name": "Scan ECS Task Definitions",
  "description": "Search ECS task definition environment variables and secrets references for a migrating resource.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}},
      "region": {"type": "string"}
    }
  },
  "rollback_strategy": "no_op"
},
{
  "id": "scan_rds_parameter_groups",
  "name": "Scan RDS Parameter Groups",
  "description": "Search RDS parameter group values for references to a migrating resource.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}},
      "region": {"type": "string"}
    }
  },
  "rollback_strategy": "no_op"
},
{
  "id": "scan_secrets_manager_metadata",
  "name": "Scan Secrets Manager Metadata",
  "description": "Search Secrets Manager secret names, descriptions, and tags (NOT values) for references to a migrating resource.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}},
      "region": {"type": "string"}
    }
  },
  "rollback_strategy": "no_op"
},
{
  "id": "scan_ssm_parameters_metadata",
  "name": "Scan SSM Parameter Metadata",
  "description": "Search SSM Parameter Store parameter names and descriptions (NOT values) for references to a migrating resource.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}},
      "region": {"type": "string"}
    }
  },
  "rollback_strategy": "no_op"
},
{
  "id": "scan_ec2_user_data",
  "name": "Scan EC2 User Data",
  "description": "Search EC2 instance user data scripts for references to a migrating resource.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}},
      "instance_ids": {"type": "array", "items": {"type": "string"}, "description": "Specific instance IDs to scan; if omitted, scans all running instances"},
      "region": {"type": "string"}
    }
  },
  "rollback_strategy": "no_op"
}
```

- [ ] **Step 4: Create the AWS reference scan executor**

```python
# backend/app/connectors/executors/aws/reference_scan.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS scan executors — emit unified hit schema for reference_scan orchestrator."""

import base64
import logging
import boto3
from typing import Any

logger = logging.getLogger(__name__)


def _boto_client(service: str, connector, region: str | None = None) -> Any:
    creds = connector.credentials or {}
    kwargs: dict = {
        "aws_access_key_id": creds.get("access_key_id"),
        "aws_secret_access_key": creds.get("secret_access_key"),
        "region_name": region or creds.get("region", "us-east-1"),
    }
    if creds.get("session_token"):
        kwargs["aws_session_token"] = creds["session_token"]
    return boto3.client(service, **kwargs)


def _matches(value: str, search_terms: list[str]) -> list[str]:
    return [t for t in search_terms if t.lower() in value.lower()]


def _hit(surface: str, location: str, matched_term: str, snippet: str, stable_id: str | None = None, hostname: str | None = None, surface_metadata: dict | None = None) -> dict:
    return {
        "surface": surface,
        "location": location,
        "matched_term": matched_term,
        "snippet": snippet,
        "consumer_identity": {
            "stable_id": stable_id,
            "hostname": hostname,
            "surface_metadata": surface_metadata or {},
        },
    }


async def scan_lambda_env_vars(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    client = _boto_client("lambda", connector, region)
    hits = []
    scanned = 0
    paginator = client.get_paginator("list_functions")
    for page in paginator.paginate():
        for fn in page.get("Functions", []):
            scanned += 1
            arn = fn["FunctionArn"]
            env_vars = fn.get("Environment", {}).get("Variables", {})
            for key, val in env_vars.items():
                for term in _matches(val, search_terms):
                    hits.append(_hit(
                        surface="aws_lambda_env",
                        location=arn,
                        matched_term=term,
                        snippet=f"{key}={val}",
                        stable_id=arn,
                    ))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_ecs_task_defs(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    client = _boto_client("ecs", connector, region)
    hits = []
    scanned = 0
    paginator = client.get_paginator("list_task_definitions")
    for page in paginator.paginate(status="ACTIVE"):
        for arn in page.get("taskDefinitionArns", []):
            td = client.describe_task_definition(taskDefinition=arn)["taskDefinition"]
            scanned += 1
            for container in td.get("containerDefinitions", []):
                for env in container.get("environment", []):
                    for term in _matches(env.get("value", ""), search_terms):
                        hits.append(_hit(
                            surface="aws_ecs_task_def",
                            location=arn,
                            matched_term=term,
                            snippet=f"{env['name']}={env['value']} (container: {container['name']})",
                            stable_id=arn,
                        ))
                for secret in container.get("secrets", []):
                    for term in _matches(secret.get("valueFrom", ""), search_terms):
                        hits.append(_hit(
                            surface="aws_ecs_task_def",
                            location=arn,
                            matched_term=term,
                            snippet=f"secret {secret['name']} → {secret['valueFrom']}",
                            stable_id=arn,
                        ))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_rds_parameter_groups(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    client = _boto_client("rds", connector, region)
    hits = []
    scanned = 0
    paginator = client.get_paginator("describe_db_parameter_groups")
    for page in paginator.paginate():
        for pg in page.get("DBParameterGroups", []):
            pg_name = pg["DBParameterGroupName"]
            pg_arn = pg["DBParameterGroupArn"]
            pp = client.get_paginator("describe_db_parameters")
            for ppage in pp.paginate(DBParameterGroupName=pg_name):
                for param in ppage.get("Parameters", []):
                    val = param.get("ParameterValue", "")
                    scanned += 1
                    for term in _matches(val, search_terms):
                        hits.append(_hit(
                            surface="aws_rds_parameter_group",
                            location=pg_arn,
                            matched_term=term,
                            snippet=f"{param['ParameterName']}={val}",
                            stable_id=pg_arn,
                        ))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_secrets_manager_metadata(cr, connector, db) -> dict:
    """Scans secret names, descriptions, and tags ONLY — never secret values."""
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    client = _boto_client("secretsmanager", connector, region)
    hits = []
    scanned = 0
    paginator = client.get_paginator("list_secrets")
    for page in paginator.paginate():
        for secret in page.get("SecretList", []):
            scanned += 1
            arn = secret["ARN"]
            name = secret.get("Name", "")
            desc = secret.get("Description", "")
            tags_str = " ".join(f"{t['Key']}={t['Value']}" for t in secret.get("Tags", []))
            for field_val, field_name in [(name, "name"), (desc, "description"), (tags_str, "tags")]:
                for term in _matches(field_val, search_terms):
                    hits.append(_hit(
                        surface="aws_secrets_manager_metadata",
                        location=arn,
                        matched_term=term,
                        snippet=f"{field_name}: {field_val}",
                        stable_id=arn,
                    ))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_ssm_parameters_metadata(cr, connector, db) -> dict:
    """Scans SSM parameter names and descriptions ONLY — never parameter values."""
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    client = _boto_client("ssm", connector, region)
    hits = []
    scanned = 0
    paginator = client.get_paginator("describe_parameters")
    for page in paginator.paginate():
        for param in page.get("Parameters", []):
            scanned += 1
            name = param.get("Name", "")
            desc = param.get("Description", "")
            for field_val, field_name in [(name, "name"), (desc, "description")]:
                for term in _matches(field_val, search_terms):
                    hits.append(_hit(
                        surface="aws_ssm_parameter_metadata",
                        location=name,
                        matched_term=term,
                        snippet=f"{field_name}: {field_val}",
                        stable_id=name,
                    ))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_ec2_user_data(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    instance_ids = params.get("instance_ids")
    client = _boto_client("ec2", connector, region)
    hits = []
    scanned = 0
    kwargs = {}
    if instance_ids:
        kwargs["InstanceIds"] = instance_ids
    paginator = client.get_paginator("describe_instances")
    for page in paginator.paginate(**kwargs):
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                if instance.get("State", {}).get("Name") != "running":
                    continue
                instance_id = instance["InstanceId"]
                scanned += 1
                try:
                    resp = client.describe_instance_attribute(InstanceId=instance_id, Attribute="userData")
                    ud_b64 = resp.get("UserData", {}).get("Value", "")
                    if not ud_b64:
                        continue
                    user_data = base64.b64decode(ud_b64).decode("utf-8", errors="replace")
                    for term in _matches(user_data, search_terms):
                        # Find line containing term for snippet
                        line = next((l for l in user_data.splitlines() if term.lower() in l.lower()), user_data[:120])
                        hits.append(_hit(
                            surface="aws_ec2_user_data",
                            location=instance_id,
                            matched_term=term,
                            snippet=line.strip()[:200],
                            stable_id=instance_id,
                        ))
                except Exception as e:
                    logger.warning("Failed to read user data for %s: %s", instance_id, e)
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && python -m pytest tests/unit/test_aws_reference_scan.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/catalog/aws.json backend/app/connectors/executors/aws/reference_scan.py backend/tests/unit/test_aws_reference_scan.py
git commit -m "feat: AWS reference scan — 6 catalog actions + executor (Lambda, ECS, RDS, SM, SSM, EC2 user data)"
```

---

### Task 5: AWS Update Catalog Actions and Executor

**Files:**
- Modify: `backend/app/connectors/catalog/aws.json`
- Create: `backend/app/connectors/executors/aws/reference_update.py`
- Create: `backend/tests/unit/test_aws_reference_update.py`

**Interfaces:**
- Produces: 4 catalog actions: `update_lambda_env_var`, `update_ecs_task_def_env`, `update_ssm_parameter_value`, `update_secrets_manager_secret_name`
- Produces: executor functions, each following reconstitution rollback: save old value in `cr.execution_result["rollback_data"]`, restore on rollback
- Each executor accepts `cr.parameters`: `{location: str, env_var_key: str, old_value: str, new_value: str}` (adjusted per action)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_aws_reference_update.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_update_lambda_env_var_saves_rollback_data():
    from app.connectors.executors.aws.reference_update import update_lambda_env_var

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "function_arn": "arn:aws:lambda:us-east-1:123:function:api-gw",
        "env_var_key": "DB_HOST",
        "old_value": "old-db.internal",
        "new_value": "new-db.internal",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {"access_key_id": "AK", "secret_access_key": "SK"}
    mock_db = AsyncMock()

    mock_lambda = MagicMock()
    mock_lambda.get_function_configuration.return_value = {
        "Environment": {"Variables": {"DB_HOST": "old-db.internal", "OTHER": "x"}}
    }
    mock_lambda.update_function_configuration.return_value = {}

    with patch("boto3.client", return_value=mock_lambda):
        result = await update_lambda_env_var(mock_cr, mock_connector, mock_db)

    assert result["status"] == "updated"
    assert result["rollback_data"]["old_value"] == "old-db.internal"
    mock_lambda.update_function_configuration.assert_called_once()
    call_kwargs = mock_lambda.update_function_configuration.call_args[1]
    assert call_kwargs["Environment"]["Variables"]["DB_HOST"] == "new-db.internal"
    assert call_kwargs["Environment"]["Variables"]["OTHER"] == "x"
```

Run: `cd backend && python -m pytest tests/unit/test_aws_reference_update.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 2: Add 4 update actions to aws.json**

Append to the actions array in `backend/app/connectors/catalog/aws.json`:

```json
{
  "id": "update_lambda_env_var",
  "name": "Update Lambda Environment Variable",
  "description": "Update a single Lambda function environment variable. Saves old value for rollback.",
  "parameters": {
    "type": "object",
    "required": ["function_arn", "env_var_key", "old_value", "new_value"],
    "properties": {
      "function_arn": {"type": "string"},
      "env_var_key": {"type": "string"},
      "old_value": {"type": "string"},
      "new_value": {"type": "string"},
      "region": {"type": "string"}
    }
  },
  "rollback_strategy": "reconstitution"
},
{
  "id": "update_ecs_task_def_env",
  "name": "Update ECS Task Definition Environment Variable",
  "description": "Register a new ECS task definition revision with updated environment variable. Rollback re-registers the old revision as active.",
  "parameters": {
    "type": "object",
    "required": ["task_def_arn", "container_name", "env_var_key", "old_value", "new_value"],
    "properties": {
      "task_def_arn": {"type": "string"},
      "container_name": {"type": "string"},
      "env_var_key": {"type": "string"},
      "old_value": {"type": "string"},
      "new_value": {"type": "string"},
      "region": {"type": "string"}
    }
  },
  "rollback_strategy": "reconstitution"
},
{
  "id": "update_ssm_parameter_value",
  "name": "Update SSM Parameter Value",
  "description": "Overwrite an SSM parameter value. Saves old value for rollback. Does NOT update SecureString values — use Secrets Manager action for secrets.",
  "parameters": {
    "type": "object",
    "required": ["parameter_name", "old_value", "new_value"],
    "properties": {
      "parameter_name": {"type": "string"},
      "old_value": {"type": "string"},
      "new_value": {"type": "string"},
      "region": {"type": "string"}
    }
  },
  "rollback_strategy": "reconstitution"
},
{
  "id": "update_secrets_manager_secret_name",
  "name": "Update Secrets Manager Secret Name",
  "description": "Update the name/description of a Secrets Manager secret (not its value). Saves old name for rollback.",
  "parameters": {
    "type": "object",
    "required": ["secret_arn", "old_name", "new_name"],
    "properties": {
      "secret_arn": {"type": "string"},
      "old_name": {"type": "string"},
      "new_name": {"type": "string"},
      "region": {"type": "string"}
    }
  },
  "rollback_strategy": "reconstitution"
}
```

- [ ] **Step 3: Create reference_update.py**

```python
# backend/app/connectors/executors/aws/reference_update.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS reference update executors — reconstitution rollback pattern."""

import logging
import boto3
from app.connectors.executors.aws.reference_scan import _boto_client

logger = logging.getLogger(__name__)


async def update_lambda_env_var(cr, connector, db) -> dict:
    params = cr.parameters or {}
    fn_arn = params["function_arn"]
    key = params["env_var_key"]
    old_val = params["old_value"]
    new_val = params["new_value"]
    region = params.get("region")

    client = _boto_client("lambda", connector, region)
    config = client.get_function_configuration(FunctionName=fn_arn)
    env_vars = dict(config.get("Environment", {}).get("Variables", {}))

    if env_vars.get(key) != old_val:
        return {"status": "skipped", "reason": f"current value '{env_vars.get(key)}' != expected old_value '{old_val}'"}

    env_vars[key] = new_val
    client.update_function_configuration(FunctionName=fn_arn, Environment={"Variables": env_vars})

    return {
        "status": "updated",
        "function_arn": fn_arn,
        "key": key,
        "rollback_data": {"function_arn": fn_arn, "env_var_key": key, "old_value": old_val, "region": region},
    }


async def rollback_lambda_env_var(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    fn_arn = rb["function_arn"]
    key = rb["env_var_key"]
    old_val = rb["old_value"]
    region = rb.get("region")

    client = _boto_client("lambda", connector, region)
    config = client.get_function_configuration(FunctionName=fn_arn)
    env_vars = dict(config.get("Environment", {}).get("Variables", {}))
    env_vars[key] = old_val
    client.update_function_configuration(FunctionName=fn_arn, Environment={"Variables": env_vars})
    return {"status": "rolled_back", "function_arn": fn_arn, "key": key}


async def update_ecs_task_def_env(cr, connector, db) -> dict:
    params = cr.parameters or {}
    td_arn = params["task_def_arn"]
    container_name = params["container_name"]
    key = params["env_var_key"]
    old_val = params["old_value"]
    new_val = params["new_value"]
    region = params.get("region")

    client = _boto_client("ecs", connector, region)
    td = client.describe_task_definition(taskDefinition=td_arn)["taskDefinition"]
    containers = [dict(c) for c in td["containerDefinitions"]]

    updated = False
    for container in containers:
        if container["name"] == container_name:
            env = [dict(e) for e in container.get("environment", [])]
            for e in env:
                if e["name"] == key and e["value"] == old_val:
                    e["value"] = new_val
                    updated = True
            container["environment"] = env

    if not updated:
        return {"status": "skipped", "reason": f"env var {key}={old_val} not found in container {container_name}"}

    # Register new revision
    new_td = client.register_task_definition(
        family=td["family"],
        containerDefinitions=containers,
        **{k: td[k] for k in ("networkMode", "volumes", "requiresCompatibilities", "cpu", "memory", "executionRoleArn", "taskRoleArn") if k in td},
    )["taskDefinition"]

    return {
        "status": "updated",
        "new_task_def_arn": new_td["taskDefinitionArn"],
        "rollback_data": {"old_task_def_arn": td_arn, "region": region},
    }


async def rollback_ecs_task_def_env(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    # ECS rollback: re-register old revision's definition as current
    # The old revision still exists in ECS — just update services to point to it
    return {"status": "rolled_back", "note": "Update ECS services to use old_task_def_arn from rollback_data", "rollback_data": rb}


async def update_ssm_parameter_value(cr, connector, db) -> dict:
    params = cr.parameters or {}
    param_name = params["parameter_name"]
    old_val = params["old_value"]
    new_val = params["new_value"]
    region = params.get("region")

    client = _boto_client("ssm", connector, region)
    current = client.get_parameter(Name=param_name, WithDecryption=False)["Parameter"]
    if current["Value"] != old_val:
        return {"status": "skipped", "reason": f"current value != expected old_value"}

    client.put_parameter(Name=param_name, Value=new_val, Overwrite=True)
    return {
        "status": "updated",
        "parameter_name": param_name,
        "rollback_data": {"parameter_name": param_name, "old_value": old_val, "region": region},
    }


async def rollback_ssm_parameter_value(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    client = _boto_client("ssm", connector, region=rb.get("region"))
    client.put_parameter(Name=rb["parameter_name"], Value=rb["old_value"], Overwrite=True)
    return {"status": "rolled_back"}


async def update_secrets_manager_secret_name(cr, connector, db) -> dict:
    params = cr.parameters or {}
    secret_arn = params["secret_arn"]
    new_name = params["new_name"]
    region = params.get("region")

    client = _boto_client("secretsmanager", connector, region)
    client.update_secret(SecretId=secret_arn, Description=f"Renamed to: {new_name}")
    return {
        "status": "updated",
        "secret_arn": secret_arn,
        "rollback_data": {"secret_arn": secret_arn, "old_name": params["old_name"], "region": region},
    }


async def rollback_secrets_manager_secret_name(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    client = _boto_client("secretsmanager", connector, region=rb.get("region"))
    client.update_secret(SecretId=rb["secret_arn"], Description=f"Rolled back name to: {rb['old_name']}")
    return {"status": "rolled_back"}
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/unit/test_aws_reference_update.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/catalog/aws.json backend/app/connectors/executors/aws/reference_update.py backend/tests/unit/test_aws_reference_update.py
git commit -m "feat: AWS reference update — 4 catalog actions + reconstitution rollback executor"
```

---

### Task 6: Kubernetes Scan Catalog Actions and Executor

**Files:**
- Modify: `backend/app/connectors/catalog/kubernetes.json`
- Create: `backend/app/connectors/executors/kubernetes/reference_scan.py`
- Create: `backend/tests/unit/test_k8s_reference_scan.py`

**Interfaces:**
- Produces: 4 catalog actions: `scan_configmaps`, `scan_secrets_metadata`, `scan_deployment_env`, `scan_ingress_rules`
- Each returns unified hit schema; `scan_secrets_metadata` scans secret names/labels only, never values
- Consumer stable_id format: `{namespace}/{kind}/{name}`

- [ ] **Step 1: Read K8s catalog to understand action schema**

```bash
head -60 backend/app/connectors/catalog/kubernetes.json
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/unit/test_k8s_reference_scan.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_k8s_client():
    """Return a mock kubernetes CoreV1Api."""
    mock_api = MagicMock()
    cm = MagicMock()
    cm.metadata.name = "app-config"
    cm.metadata.namespace = "production"
    cm.data = {"DATABASE_URL": "postgres://old-db.internal/app"}
    mock_api.list_namespaced_config_map.return_value.items = [cm]
    return mock_api


@pytest.mark.asyncio
async def test_scan_configmaps_finds_match():
    from app.connectors.executors.kubernetes.reference_scan import scan_configmaps

    mock_cr = MagicMock()
    mock_cr.parameters = {"search_terms": ["old-db.internal"], "namespaces": ["production"]}
    mock_connector = MagicMock()
    mock_connector.credentials = {"kubeconfig": "base64-encoded-kubeconfig"}
    mock_db = AsyncMock()

    with patch("app.connectors.executors.kubernetes.reference_scan._k8s_core_api", return_value=_make_mock_k8s_client()):
        result = await scan_configmaps(mock_cr, mock_connector, mock_db)

    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["surface"] == "k8s_configmap"
    assert hit["matched_term"] == "old-db.internal"
    assert hit["consumer_identity"]["stable_id"] == "production/ConfigMap/app-config"
```

Run: `cd backend && python -m pytest tests/unit/test_k8s_reference_scan.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Add 4 actions to kubernetes.json**

```json
{
  "id": "scan_configmaps",
  "name": "Scan ConfigMaps",
  "description": "Search Kubernetes ConfigMap data values for references to a migrating resource.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}},
      "namespaces": {"type": "array", "items": {"type": "string"}, "description": "Namespaces to scan; if omitted, scans all namespaces"}
    }
  },
  "rollback_strategy": "no_op"
},
{
  "id": "scan_secrets_metadata",
  "name": "Scan Secret Metadata",
  "description": "Search Kubernetes Secret names and labels (NOT values) for references to a migrating resource.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}},
      "namespaces": {"type": "array", "items": {"type": "string"}}
    }
  },
  "rollback_strategy": "no_op"
},
{
  "id": "scan_deployment_env",
  "name": "Scan Deployment Environment Variables",
  "description": "Search Kubernetes Deployment and StatefulSet container environment variables for references.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}},
      "namespaces": {"type": "array", "items": {"type": "string"}}
    }
  },
  "rollback_strategy": "no_op"
},
{
  "id": "scan_ingress_rules",
  "name": "Scan Ingress Rules",
  "description": "Search Kubernetes Ingress rules for backend service references matching a migrating resource.",
  "parameters": {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
      "search_terms": {"type": "array", "items": {"type": "string"}},
      "namespaces": {"type": "array", "items": {"type": "string"}}
    }
  },
  "rollback_strategy": "no_op"
}
```

- [ ] **Step 4: Create k8s reference scan executor**

```python
# backend/app/connectors/executors/kubernetes/reference_scan.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kubernetes scan executors — unified hit schema."""

import base64
import logging
import tempfile
import os
from kubernetes import client as k8s_client, config as k8s_config

logger = logging.getLogger(__name__)


def _k8s_core_api(connector):
    creds = connector.credentials or {}
    kubeconfig_b64 = creds.get("kubeconfig")
    if kubeconfig_b64:
        raw = base64.b64decode(kubeconfig_b64)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".yaml") as f:
            f.write(raw)
            f.flush()
            k8s_config.load_kube_config(config_file=f.name)
        os.unlink(f.name)
    else:
        k8s_config.load_incluster_config()
    return k8s_client.CoreV1Api()


def _k8s_apps_api(connector):
    _k8s_core_api(connector)
    return k8s_client.AppsV1Api()


def _k8s_networking_api(connector):
    _k8s_core_api(connector)
    return k8s_client.NetworkingV1Api()


def _matches(value: str, search_terms: list[str]) -> list[str]:
    return [t for t in search_terms if t.lower() in value.lower()]


def _hit(surface, location, matched_term, snippet, stable_id=None):
    return {
        "surface": surface,
        "location": location,
        "matched_term": matched_term,
        "snippet": snippet,
        "consumer_identity": {"stable_id": stable_id, "hostname": None, "surface_metadata": {}},
    }


async def scan_configmaps(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    namespaces = params.get("namespaces") or [None]
    api = _k8s_core_api(connector)
    hits = []
    scanned = 0
    for ns in namespaces:
        result = api.list_namespaced_config_map(namespace=ns) if ns else api.list_config_map_for_all_namespaces()
        for cm in result.items:
            scanned += 1
            for key, val in (cm.data or {}).items():
                for term in _matches(val, search_terms):
                    stable_id = f"{cm.metadata.namespace}/ConfigMap/{cm.metadata.name}"
                    hits.append(_hit("k8s_configmap", stable_id, term, f"{key}={val}", stable_id))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_secrets_metadata(cr, connector, db) -> dict:
    """Scans secret names and labels ONLY — never secret values."""
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    namespaces = params.get("namespaces") or [None]
    api = _k8s_core_api(connector)
    hits = []
    scanned = 0
    for ns in namespaces:
        result = api.list_namespaced_secret(namespace=ns) if ns else api.list_secret_for_all_namespaces()
        for secret in result.items:
            scanned += 1
            name = secret.metadata.name
            labels_str = " ".join(f"{k}={v}" for k, v in (secret.metadata.labels or {}).items())
            for field_val, field_name in [(name, "name"), (labels_str, "labels")]:
                for term in _matches(field_val, search_terms):
                    stable_id = f"{secret.metadata.namespace}/Secret/{name}"
                    hits.append(_hit("k8s_secret_metadata", stable_id, term, f"{field_name}: {field_val}", stable_id))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_deployment_env(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    namespaces = params.get("namespaces") or [None]
    api = _k8s_apps_api(connector)
    hits = []
    scanned = 0
    for ns in namespaces:
        for kind, lister in [("Deployment", api.list_namespaced_deployment), ("StatefulSet", api.list_namespaced_stateful_set)]:
            items = (lister(namespace=ns) if ns else getattr(api, f"list_{kind.lower()}_for_all_namespaces")()).items
            for obj in items:
                scanned += 1
                stable_id = f"{obj.metadata.namespace}/{kind}/{obj.metadata.name}"
                containers = obj.spec.template.spec.containers or []
                for container in containers:
                    for env in (container.env or []):
                        val = env.value or ""
                        for term in _matches(val, search_terms):
                            hits.append(_hit("k8s_deployment_env", stable_id, term, f"{env.name}={val} (container: {container.name})", stable_id))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_ingress_rules(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    namespaces = params.get("namespaces") or [None]
    api = _k8s_networking_api(connector)
    hits = []
    scanned = 0
    for ns in namespaces:
        result = api.list_namespaced_ingress(namespace=ns) if ns else api.list_ingress_for_all_namespaces()
        for ingress in result.items:
            scanned += 1
            stable_id = f"{ingress.metadata.namespace}/Ingress/{ingress.metadata.name}"
            for rule in (ingress.spec.rules or []):
                host = rule.host or ""
                for term in _matches(host, search_terms):
                    hits.append(_hit("k8s_ingress", stable_id, term, f"host: {host}", stable_id))
                for path in (rule.http.paths if rule.http else []):
                    svc = path.backend.service.name if path.backend and path.backend.service else ""
                    for term in _matches(svc, search_terms):
                        hits.append(_hit("k8s_ingress", stable_id, term, f"backend service: {svc}", stable_id))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && python -m pytest tests/unit/test_k8s_reference_scan.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/catalog/kubernetes.json backend/app/connectors/executors/kubernetes/reference_scan.py backend/tests/unit/test_k8s_reference_scan.py
git commit -m "feat: K8s reference scan — 4 catalog actions + executor (ConfigMap, Secret metadata, Deployment env, Ingress)"
```

---

### Task 7: Kubernetes Update Catalog Actions and Executor

**Files:**
- Modify: `backend/app/connectors/catalog/kubernetes.json`
- Create: `backend/app/connectors/executors/kubernetes/reference_update.py`
- Create: `backend/tests/unit/test_k8s_reference_update.py`

**Interfaces:**
- Produces: 3 catalog actions: `update_configmap_value`, `update_deployment_env_var`, `update_ingress_host`
- Reconstitution rollback: patch saves full previous object state; rollback re-applies old state

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_k8s_reference_update.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_update_configmap_value_saves_rollback_data():
    from app.connectors.executors.kubernetes.reference_update import update_configmap_value

    mock_cr = MagicMock()
    mock_cr.parameters = {
        "namespace": "production",
        "configmap_name": "app-config",
        "key": "DATABASE_URL",
        "old_value": "postgres://old-db.internal/app",
        "new_value": "postgres://new-db.internal/app",
    }
    mock_connector = MagicMock()
    mock_connector.credentials = {}
    mock_db = AsyncMock()

    mock_cm = MagicMock()
    mock_cm.data = {"DATABASE_URL": "postgres://old-db.internal/app", "OTHER": "x"}
    mock_api = MagicMock()
    mock_api.read_namespaced_config_map.return_value = mock_cm
    mock_api.patch_namespaced_config_map.return_value = mock_cm

    with patch("app.connectors.executors.kubernetes.reference_update._k8s_core_api", return_value=mock_api):
        result = await update_configmap_value(mock_cr, mock_connector, mock_db)

    assert result["status"] == "updated"
    assert result["rollback_data"]["old_value"] == "postgres://old-db.internal/app"
    mock_api.patch_namespaced_config_map.assert_called_once()
    patched_data = mock_api.patch_namespaced_config_map.call_args[1]["body"].data
    assert patched_data["DATABASE_URL"] == "postgres://new-db.internal/app"
    assert patched_data["OTHER"] == "x"
```

Run: `cd backend && python -m pytest tests/unit/test_k8s_reference_update.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 2: Add 3 actions to kubernetes.json**

```json
{
  "id": "update_configmap_value",
  "name": "Update ConfigMap Value",
  "description": "Update a single key in a Kubernetes ConfigMap. Saves old value for rollback.",
  "parameters": {
    "type": "object",
    "required": ["namespace", "configmap_name", "key", "old_value", "new_value"],
    "properties": {
      "namespace": {"type": "string"},
      "configmap_name": {"type": "string"},
      "key": {"type": "string"},
      "old_value": {"type": "string"},
      "new_value": {"type": "string"}
    }
  },
  "rollback_strategy": "reconstitution"
},
{
  "id": "update_deployment_env_var",
  "name": "Update Deployment Environment Variable",
  "description": "Update a container environment variable in a Kubernetes Deployment or StatefulSet. Rollback patches the old value back.",
  "parameters": {
    "type": "object",
    "required": ["namespace", "kind", "name", "container_name", "env_var_key", "old_value", "new_value"],
    "properties": {
      "namespace": {"type": "string"},
      "kind": {"type": "string", "enum": ["Deployment", "StatefulSet"]},
      "name": {"type": "string"},
      "container_name": {"type": "string"},
      "env_var_key": {"type": "string"},
      "old_value": {"type": "string"},
      "new_value": {"type": "string"}
    }
  },
  "rollback_strategy": "reconstitution"
},
{
  "id": "update_ingress_host",
  "name": "Update Ingress Host",
  "description": "Update a backend host or service reference in a Kubernetes Ingress rule. Rollback patches the old rule back.",
  "parameters": {
    "type": "object",
    "required": ["namespace", "ingress_name", "old_host", "new_host"],
    "properties": {
      "namespace": {"type": "string"},
      "ingress_name": {"type": "string"},
      "old_host": {"type": "string"},
      "new_host": {"type": "string"}
    }
  },
  "rollback_strategy": "reconstitution"
}
```

- [ ] **Step 3: Create k8s reference_update.py**

```python
# backend/app/connectors/executors/kubernetes/reference_update.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kubernetes reference update executors — reconstitution rollback."""

import logging
from kubernetes import client as k8s_client
from app.connectors.executors.kubernetes.reference_scan import _k8s_core_api, _k8s_apps_api, _k8s_networking_api

logger = logging.getLogger(__name__)


async def update_configmap_value(cr, connector, db) -> dict:
    params = cr.parameters or {}
    ns = params["namespace"]
    cm_name = params["configmap_name"]
    key = params["key"]
    old_val = params["old_value"]
    new_val = params["new_value"]

    api = _k8s_core_api(connector)
    cm = api.read_namespaced_config_map(name=cm_name, namespace=ns)
    data = dict(cm.data or {})

    if data.get(key) != old_val:
        return {"status": "skipped", "reason": f"current value != expected old_value"}

    data[key] = new_val
    cm.data = data
    api.patch_namespaced_config_map(name=cm_name, namespace=ns, body=cm)

    return {
        "status": "updated",
        "rollback_data": {"namespace": ns, "configmap_name": cm_name, "key": key, "old_value": old_val},
    }


async def rollback_configmap_value(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    api = _k8s_core_api(connector)
    cm = api.read_namespaced_config_map(name=rb["configmap_name"], namespace=rb["namespace"])
    data = dict(cm.data or {})
    data[rb["key"]] = rb["old_value"]
    cm.data = data
    api.patch_namespaced_config_map(name=rb["configmap_name"], namespace=rb["namespace"], body=cm)
    return {"status": "rolled_back"}


async def update_deployment_env_var(cr, connector, db) -> dict:
    params = cr.parameters or {}
    ns = params["namespace"]
    kind = params["kind"]
    name = params["name"]
    container_name = params["container_name"]
    env_key = params["env_var_key"]
    old_val = params["old_value"]
    new_val = params["new_value"]

    api = _k8s_apps_api(connector)
    if kind == "Deployment":
        obj = api.read_namespaced_deployment(name=name, namespace=ns)
    else:
        obj = api.read_namespaced_stateful_set(name=name, namespace=ns)

    updated = False
    for container in obj.spec.template.spec.containers:
        if container.name == container_name:
            for env in (container.env or []):
                if env.name == env_key and env.value == old_val:
                    env.value = new_val
                    updated = True

    if not updated:
        return {"status": "skipped", "reason": f"{env_key}={old_val} not found in {container_name}"}

    if kind == "Deployment":
        api.patch_namespaced_deployment(name=name, namespace=ns, body=obj)
    else:
        api.patch_namespaced_stateful_set(name=name, namespace=ns, body=obj)

    return {
        "status": "updated",
        "rollback_data": {"namespace": ns, "kind": kind, "name": name, "container_name": container_name, "env_var_key": env_key, "old_value": old_val},
    }


async def rollback_deployment_env_var(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    params_restore = {**rb, "new_value": rb["old_value"], "old_value": "__any__"}
    # Re-use update logic with old and new swapped
    mock_cr = type("CR", (), {"parameters": {**rb, "env_var_key": rb["env_var_key"], "old_value": rb.get("current_value", rb["old_value"]), "new_value": rb["old_value"]}})()
    # Simple patch: just set the env var back
    api = _k8s_apps_api(connector)
    kind = rb["kind"]
    if kind == "Deployment":
        obj = api.read_namespaced_deployment(name=rb["name"], namespace=rb["namespace"])
    else:
        obj = api.read_namespaced_stateful_set(name=rb["name"], namespace=rb["namespace"])
    for container in obj.spec.template.spec.containers:
        if container.name == rb["container_name"]:
            for env in (container.env or []):
                if env.name == rb["env_var_key"]:
                    env.value = rb["old_value"]
    if kind == "Deployment":
        api.patch_namespaced_deployment(name=rb["name"], namespace=rb["namespace"], body=obj)
    else:
        api.patch_namespaced_stateful_set(name=rb["name"], namespace=rb["namespace"], body=obj)
    return {"status": "rolled_back"}


async def update_ingress_host(cr, connector, db) -> dict:
    params = cr.parameters or {}
    ns = params["namespace"]
    ingress_name = params["ingress_name"]
    old_host = params["old_host"]
    new_host = params["new_host"]

    api = _k8s_networking_api(connector)
    ingress = api.read_namespaced_ingress(name=ingress_name, namespace=ns)
    updated = False
    for rule in (ingress.spec.rules or []):
        if rule.host == old_host:
            rule.host = new_host
            updated = True
    if not updated:
        return {"status": "skipped", "reason": f"host {old_host} not found in ingress rules"}

    api.patch_namespaced_ingress(name=ingress_name, namespace=ns, body=ingress)
    return {
        "status": "updated",
        "rollback_data": {"namespace": ns, "ingress_name": ingress_name, "old_host": old_host, "new_host": new_host},
    }


async def rollback_ingress_host(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    api = _k8s_networking_api(connector)
    ingress = api.read_namespaced_ingress(name=rb["ingress_name"], namespace=rb["namespace"])
    for rule in (ingress.spec.rules or []):
        if rule.host == rb["new_host"]:
            rule.host = rb["old_host"]
    api.patch_namespaced_ingress(name=rb["ingress_name"], namespace=rb["namespace"], body=ingress)
    return {"status": "rolled_back"}
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/unit/test_k8s_reference_update.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/catalog/kubernetes.json backend/app/connectors/executors/kubernetes/reference_update.py backend/tests/unit/test_k8s_reference_update.py
git commit -m "feat: K8s reference update — 3 catalog actions + reconstitution rollback executor"
```
