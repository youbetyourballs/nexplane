# Identity Connectors Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the identity graph (cross-system user correlation), fan-out CR execution, pre-state snapshot/rollback, identity snapshot to S3, and identity reconstitution from snapshot.

**Architecture:** Identity graph tables (identity_profiles, identity_accounts) are populated by a sync service that either extracts cross-system references from Okta/Entra ID profile attributes (IdP-primary) or matches by email (fallback). Fan-out CRs spawn one child CR per connected identity account using the existing project/child CR model. Each child CR snapshots account state before executing and restores from it on rollback. Identity snapshot and reconstitute are separate CR types for disaster recovery.

**Tech Stack:** SQLAlchemy 2.0 (Mapped/mapped_column), Alembic, APScheduler, FastAPI, asyncio, boto3 (S3 for snapshots), existing connector executor pattern.

---

## File Map

| File | Action |
|------|--------|
| `backend/app/models/identity_profile.py` | New — IdentityProfile + IdentityAccount models |
| `backend/alembic/versions/XXXX_identity_graph.py` | New — migration for identity_profiles + identity_accounts + parent_change_request_id on change_requests |
| `backend/app/models/change_request.py` | Add identity_snapshot, identity_reconstitute to ChangeType; add parent_change_request_id FK |
| `backend/app/connectors/executors/identity/fan_out_registry.py` | New — FAN_OUT_ACTIONS dict |
| `backend/app/services/identity_sync_service.py` | New — sync engine (IdP-primary + email fallback, scheduled + event-triggered) |
| `backend/app/services/change_request_service.py` | Modify — detect fan-out ChangeTypes, spawn child CRs |
| `backend/app/connectors/executors/identity/get_account_state.py` | New — per-connector pre-state reads |
| `backend/app/connectors/executors/identity/restore_account_state.py` | New — per-connector pre-state restore |
| `backend/app/connectors/executors/identity/fan_out_executor.py` | New — fan-out execute/rollback (spawns child CRs) |
| `backend/app/connectors/executors/identity/identity_snapshot.py` | New — capture full directory state to S3 |
| `backend/app/connectors/executors/identity/identity_reconstitute.py` | New — dry-run + reconstitute from snapshot |
| `backend/app/connectors/change_type_definitions/identity_snapshot.json` | New — CTD |
| `backend/app/connectors/change_type_definitions/identity_reconstitute.json` | New — CTD |
| `backend/app/routers/identity.py` | New — GET /identity/profiles, GET /identity/profiles/{id}, POST /identity/sync |
| `backend/app/services/scheduler_service.py` | Add 4h identity sync job |
| `backend/tests/test_identity_graph.py` | New — unit tests |
| `backend/tests/smoke/test_aws_live.py` | Add IDENTITY_FANOUT, IDENTITY_SNAPSHOT, IDENTITY_SYNC phases |

---

## Task 1: Identity Graph Models + Migration

**Files:**
- Create: `backend/app/models/identity_profile.py`
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/XXXX_identity_graph.py`

- [ ] **Step 1: Write failing test for IdentityProfile model**

```python
# backend/tests/test_identity_graph.py
import pytest
import uuid
from app.models.identity_profile import IdentityProfile, IdentityAccount


def test_identity_profile_fields():
    profile = IdentityProfile(
        display_name="Alice Smith",
        primary_email="alice@example.com",
        correlation_method="email",
    )
    assert profile.primary_email == "alice@example.com"
    assert profile.correlation_method == "email"
    assert profile.id is None  # not persisted


def test_identity_account_fields():
    profile_id = uuid.uuid4()
    connector_id = uuid.uuid4()
    account = IdentityAccount(
        identity_profile_id=profile_id,
        connector_id=connector_id,
        connector_type="active_directory",
        external_id="CN=alice,DC=corp,DC=local",
        username="alice",
        email="alice@corp.local",
        raw_attributes={"enabled": True},
        is_stale=False,
    )
    assert account.connector_type == "active_directory"
    assert account.raw_attributes == {"enabled": True}
    assert account.is_stale is False
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && python -m pytest tests/test_identity_graph.py::test_identity_profile_fields -v
```

Expected: `ModuleNotFoundError: No module named 'app.models.identity_profile'`

- [ ] **Step 3: Create `backend/app/models/identity_profile.py`**

```python
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class IdentityProfile(Base):
    __tablename__ = "identity_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    display_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    primary_email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_idp_connector_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connectors.id"), nullable=True
    )
    correlation_method: Mapped[str] = mapped_column(String, nullable=False)  # "idp" | "email"
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    accounts: Mapped[list["IdentityAccount"]] = relationship(
        "IdentityAccount", back_populates="profile", cascade="all, delete-orphan"
    )


class IdentityAccount(Base):
    __tablename__ = "identity_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    identity_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("identity_profiles.id"), nullable=False
    )
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connectors.id"), nullable=False
    )
    connector_type: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=False, default="")
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_attributes: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    profile: Mapped["IdentityProfile"] = relationship("IdentityProfile", back_populates="accounts")
```

- [ ] **Step 4: Add ChangeType values and parent_change_request_id to `backend/app/models/change_request.py`**

Find the `ChangeType` enum and add:
```python
identity_snapshot = "identity_snapshot"
identity_reconstitute = "identity_reconstitute"
```

Find the `ChangeRequest` class and add the column (after the `id` field):
```python
parent_change_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
    UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True
)
```

Also add to imports at top if not present: `from typing import Optional`

- [ ] **Step 5: Create Alembic migration**

Run to find the current head:
```
cd backend && alembic heads
```

Create the migration file at `backend/alembic/versions/XXXX_identity_graph.py` — replace XXXX with next sequential number and current head revision:

```python
"""identity graph tables

Revision ID: <generate with: python -c "import uuid; print(uuid.uuid4().hex[:12])">
Revises: f510f4f16763
Create Date: 2026-05-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "<12-char hex>"
down_revision = "f510f4f16763"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "identity_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=False, server_default=""),
        sa.Column("primary_email", sa.String(), nullable=False),
        sa.Column("source_idp_connector_id", UUID(as_uuid=True), sa.ForeignKey("connectors.id"), nullable=True),
        sa.Column("correlation_method", sa.String(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_identity_profiles_primary_email", "identity_profiles", ["primary_email"])

    op.create_table(
        "identity_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_profile_id", UUID(as_uuid=True), sa.ForeignKey("identity_profiles.id"), nullable=False),
        sa.Column("connector_id", UUID(as_uuid=True), sa.ForeignKey("connectors.id"), nullable=False),
        sa.Column("connector_type", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False, server_default=""),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("raw_attributes", JSONB(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default="false"),
    )

    op.add_column(
        "change_requests",
        sa.Column("parent_change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=True),
    )

    # Add new ChangeType enum values
    op.execute("ALTER TYPE changetype ADD VALUE IF NOT EXISTS 'identity_snapshot'")
    op.execute("ALTER TYPE changetype ADD VALUE IF NOT EXISTS 'identity_reconstitute'")


def downgrade():
    op.drop_column("change_requests", "parent_change_request_id")
    op.drop_table("identity_accounts")
    op.drop_index("ix_identity_profiles_primary_email", "identity_profiles")
    op.drop_table("identity_profiles")
```

- [ ] **Step 6: Run tests to verify they pass**

```
cd backend && python -m pytest tests/test_identity_graph.py::test_identity_profile_fields tests/test_identity_graph.py::test_identity_account_fields -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/identity_profile.py backend/app/models/change_request.py "backend/alembic/versions/*identity_graph*" backend/tests/test_identity_graph.py
git commit -m "feat: identity graph models, migration, ChangeType enum additions"
```

---

## Task 2: Fan-out Registry

**Files:**
- Create: `backend/app/connectors/executors/identity/__init__.py`
- Create: `backend/app/connectors/executors/identity/fan_out_registry.py`

- [ ] **Step 1: Write failing test**

```python
# Add to backend/tests/test_identity_graph.py

from app.connectors.executors.identity.fan_out_registry import (
    FAN_OUT_ACTIONS,
    get_fan_out_action,
    is_fan_out_change_type,
)


def test_fan_out_registry_emergency_lockout():
    assert FAN_OUT_ACTIONS["emergency_user_lockout"]["active_directory"] == "disable_account"
    assert FAN_OUT_ACTIONS["emergency_user_lockout"]["okta"] == "suspend_user"
    assert FAN_OUT_ACTIONS["emergency_user_lockout"]["github"] == "suspend_org_member"


def test_get_fan_out_action_returns_none_for_unknown():
    assert get_fan_out_action("emergency_user_lockout", "unknown_connector") is None


def test_is_fan_out_change_type():
    assert is_fan_out_change_type("emergency_user_lockout") is True
    assert is_fan_out_change_type("identity_snapshot") is False
    assert is_fan_out_change_type("deploy_agent") is False
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && python -m pytest tests/test_identity_graph.py::test_fan_out_registry_emergency_lockout -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `backend/app/connectors/executors/identity/__init__.py`**

Empty file.

- [ ] **Step 4: Create `backend/app/connectors/executors/identity/fan_out_registry.py`**

```python
from __future__ import annotations

FAN_OUT_ACTIONS: dict[str, dict[str, str]] = {
    "emergency_user_lockout": {
        "active_directory": "disable_account",
        "okta":             "suspend_user",
        "entra_id":         "disable_user",
        "github":           "suspend_org_member",
        "gitlab":           "gitlab_suspend_user",
        "ldap":             "ldap_disable_user",
        "freeipa":          "freeipa_disable_user",
        "keycloak":         "keycloak_disable_user",
        "gitea":            "gitea_suspend_user",
        "teleport":         "teleport_lock_user",
        "kubernetes":       "k8s_revoke_rolebinding",
    },
    "user_suspension": {
        "active_directory": "disable_account",
        "okta":             "suspend_user",
        "entra_id":         "disable_user",
        "github":           "suspend_org_member",
        "gitlab":           "gitlab_suspend_user",
        "ldap":             "ldap_disable_user",
        "freeipa":          "freeipa_disable_user",
        "keycloak":         "keycloak_disable_user",
        "gitea":            "gitea_suspend_user",
    },
    "enforce_mfa": {
        "active_directory": "enforce_mfa",
        "okta":             "enforce_mfa",
        "entra_id":         "reset_mfa",
    },
    "user_scope_reduction": {
        "active_directory": "remove_from_group",
        "okta":             "deprovision_from_app",
        "entra_id":         "remove_from_role",
        "github":           "remove_org_member",
        "kubernetes":       "k8s_revoke_rolebinding",
    },
}

FAN_OUT_CHANGE_TYPES: frozenset[str] = frozenset(FAN_OUT_ACTIONS.keys())


def get_fan_out_action(change_type: str, connector_type: str) -> str | None:
    return FAN_OUT_ACTIONS.get(change_type, {}).get(connector_type)


def is_fan_out_change_type(change_type: str) -> bool:
    return change_type in FAN_OUT_CHANGE_TYPES
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd backend && python -m pytest tests/test_identity_graph.py -k "fan_out" -v
```

Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/identity/ backend/tests/test_identity_graph.py
git commit -m "feat: fan-out registry (ChangeType x connector_type -> action)"
```

---

## Task 3: Identity Sync Service

**Files:**
- Create: `backend/app/services/identity_sync_service.py`

The sync service runs after each connector sync to correlate accounts into identity profiles. IdP connectors (okta, entra_id) store cross-system account references in user profile attributes; extract those directly. All others match by email.

- [ ] **Step 1: Write failing tests**

```python
# Add to backend/tests/test_identity_graph.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.identity_sync_service import (
    _extract_idp_cross_refs,
    _correlate_by_email,
    _upsert_account,
)


def test_extract_idp_cross_refs_okta():
    raw = {
        "profile": {
            "login": "alice@corp.com",
            "email": "alice@corp.com",
            "samAccountName": "alice",   # AD
            "githubUsername": "alice-gh",
        }
    }
    refs = _extract_idp_cross_refs("okta", raw)
    assert refs.get("primary_email") == "alice@corp.com"


def test_extract_idp_cross_refs_entra_id():
    raw = {
        "userPrincipalName": "alice@corp.onmicrosoft.com",
        "mail": "alice@corp.com",
        "onPremisesSamAccountName": "alice",
    }
    refs = _extract_idp_cross_refs("entra_id", raw)
    assert refs.get("primary_email") == "alice@corp.com"


def test_correlate_by_email_returns_email_key():
    raw = {"mail": "bob@example.com", "uid": "bob"}
    result = _correlate_by_email("freeipa", raw)
    assert result == "bob@example.com"


def test_correlate_by_email_fallback_fields():
    raw = {"email": "carol@example.com"}
    assert _correlate_by_email("ldap", raw) == "carol@example.com"

    raw2 = {"userPrincipalName": "dan@example.com"}
    assert _correlate_by_email("active_directory", raw2) == "dan@example.com"
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && python -m pytest tests/test_identity_graph.py -k "extract_idp or correlate_by_email" -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `backend/app/services/identity_sync_service.py`**

```python
"""
Identity sync service — correlates connector accounts into identity profiles.

IdP-primary: Okta and Entra ID are authoritative. Extract cross-system refs
from their profile attributes. Email fallback for all other connectors.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.identity_profile import IdentityProfile, IdentityAccount
from app.models.connector import Connector

logger = logging.getLogger(__name__)

IDP_CONNECTOR_TYPES = {"okta", "entra_id"}
STALE_THRESHOLD_HOURS = 24

# Fields to check for email in priority order, per connector type
_EMAIL_FIELDS: dict[str, list[str]] = {
    "okta":             ["profile.email", "profile.login"],
    "entra_id":         ["mail", "userPrincipalName"],
    "active_directory": ["mail", "userPrincipalName"],
    "ldap":             ["mail", "email"],
    "freeipa":          ["mail", "email"],
    "github":           ["email"],
    "gitlab":           ["email"],
    "keycloak":         ["email"],
    "gitea":            ["email"],
    "teleport":         ["traits.email", "email"],
    "kubernetes":       ["email"],
}
_DEFAULT_EMAIL_FIELDS = ["email", "mail", "userPrincipalName"]


def _get_nested(obj: dict, path: str) -> Optional[str]:
    parts = path.split(".")
    cur = obj
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur if isinstance(cur, str) else None


def _correlate_by_email(connector_type: str, raw: dict) -> Optional[str]:
    fields = _EMAIL_FIELDS.get(connector_type, _DEFAULT_EMAIL_FIELDS)
    for field in fields:
        val = _get_nested(raw, field)
        if val:
            return val.lower().strip()
    return None


def _extract_idp_cross_refs(connector_type: str, raw: dict) -> dict:
    """Extract primary_email and any cross-system identifiers from an IdP user record."""
    result: dict = {}
    if connector_type == "okta":
        profile = raw.get("profile", raw)
        for field in ("email", "login"):
            val = profile.get(field)
            if val:
                result["primary_email"] = val.lower().strip()
                break
    elif connector_type == "entra_id":
        for field in ("mail", "userPrincipalName"):
            val = raw.get(field)
            if val:
                result["primary_email"] = val.lower().strip()
                break
    return result


def _upsert_account(
    accounts_by_connector_external: dict[tuple, IdentityAccount],
    profile: IdentityProfile,
    connector_id,
    connector_type: str,
    external_id: str,
    username: str,
    email: Optional[str],
    raw: dict,
    now: datetime,
) -> IdentityAccount:
    key = (str(connector_id), external_id)
    account = accounts_by_connector_external.get(key)
    if account is None:
        account = IdentityAccount(
            identity_profile_id=profile.id,
            connector_id=connector_id,
            connector_type=connector_type,
            external_id=external_id,
        )
        accounts_by_connector_external[key] = account
    account.username = username
    account.email = email
    account.raw_attributes = raw
    account.last_synced_at = now
    account.is_stale = False
    return account


async def sync_connector_accounts(
    db: AsyncSession,
    connector_id,
    connector_type: str,
    accounts: list[dict],
) -> dict:
    """
    Correlate a list of discovered accounts into identity_profiles/identity_accounts.

    `accounts` is the raw list returned by discover_users/discover_identities.
    Each item must have at minimum: external_id (str), username (str), raw_attributes (dict).
    """
    now = datetime.now(timezone.utc)
    is_idp = connector_type in IDP_CONNECTOR_TYPES

    # Load existing profiles by email for fast lookup
    profiles_by_email: dict[str, IdentityProfile] = {}
    result = await db.execute(select(IdentityProfile))
    for p in result.scalars():
        profiles_by_email[p.primary_email] = p

    # Load existing accounts for this connector
    acct_result = await db.execute(
        select(IdentityAccount).where(IdentityAccount.connector_id == connector_id)
    )
    accounts_by_ext: dict[str, IdentityAccount] = {
        a.external_id: a for a in acct_result.scalars()
    }

    created_profiles = 0
    upserted_accounts = 0

    for raw in accounts:
        external_id = raw.get("external_id") or raw.get("id") or ""
        username = raw.get("username") or raw.get("login") or raw.get("samAccountName") or ""
        raw_attrs = raw.get("raw_attributes") or raw

        if is_idp:
            refs = _extract_idp_cross_refs(connector_type, raw_attrs)
            email = refs.get("primary_email")
        else:
            email = _correlate_by_email(connector_type, raw_attrs)

        if not email:
            logger.debug("sync: no email for %s account %s, skipping", connector_type, external_id)
            continue

        # Find or create profile
        profile = profiles_by_email.get(email)
        if profile is None:
            profile = IdentityProfile(
                primary_email=email,
                display_name=raw_attrs.get("displayName") or raw_attrs.get("name") or username,
                correlation_method="idp" if is_idp else "email",
                last_synced_at=now,
            )
            if is_idp:
                profile.source_idp_connector_id = connector_id
            db.add(profile)
            await db.flush()
            profiles_by_email[email] = profile
            created_profiles += 1
        else:
            profile.last_synced_at = now
            if is_idp and profile.source_idp_connector_id is None:
                profile.source_idp_connector_id = connector_id
                profile.correlation_method = "idp"

        # Upsert account
        existing = accounts_by_ext.get(external_id)
        if existing is None:
            existing = IdentityAccount(
                identity_profile_id=profile.id,
                connector_id=connector_id,
                connector_type=connector_type,
                external_id=external_id,
            )
            db.add(existing)
        existing.username = username
        existing.email = email
        existing.raw_attributes = raw_attrs
        existing.last_synced_at = now
        existing.is_stale = False
        upserted_accounts += 1

    # Mark accounts not seen in this sync as stale
    stale_cutoff = now - timedelta(hours=STALE_THRESHOLD_HOURS)
    await db.execute(
        update(IdentityAccount)
        .where(IdentityAccount.connector_id == connector_id)
        .where(IdentityAccount.last_synced_at < stale_cutoff)
        .values(is_stale=True)
    )

    return {
        "created_profiles": created_profiles,
        "upserted_accounts": upserted_accounts,
    }


async def sync_all(db: AsyncSession) -> dict:
    """Sync all identity connectors. Called by APScheduler every 4h."""
    connector_result = await db.execute(
        select(Connector).where(Connector.enabled == True)
    )
    connectors = list(connector_result.scalars())
    total = {"created_profiles": 0, "upserted_accounts": 0, "connectors_synced": 0}

    for connector in connectors:
        connector_type = getattr(connector, "connector_type", None)
        if not connector_type:
            continue
        try:
            from app.services.connector_service import execute_action
            result = await execute_action(connector, "discover_users", {})
            accounts = result.get("users") or result.get("accounts") or []
            stats = await sync_connector_accounts(db, connector.id, connector_type, accounts)
            total["created_profiles"] += stats["created_profiles"]
            total["upserted_accounts"] += stats["upserted_accounts"]
            total["connectors_synced"] += 1
        except Exception as exc:
            logger.warning("identity sync failed for connector %s: %s", connector.id, exc)

    return total
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && python -m pytest tests/test_identity_graph.py -k "extract_idp or correlate_by_email" -v
```

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/identity_sync_service.py backend/tests/test_identity_graph.py
git commit -m "feat: identity sync service (IdP-primary + email fallback correlation)"
```

---

## Task 4: Pre-State Snapshot and Restore

**Files:**
- Create: `backend/app/connectors/executors/identity/get_account_state.py`
- Create: `backend/app/connectors/executors/identity/restore_account_state.py`

- [ ] **Step 1: Write failing tests**

```python
# Add to backend/tests/test_identity_graph.py
from app.connectors.executors.identity.get_account_state import build_pre_state_from_raw
from app.connectors.executors.identity.restore_account_state import (
    compute_restore_ops,
    RESTORABLE_CONNECTOR_TYPES,
)


def test_build_pre_state_active_directory():
    raw = {
        "enabled": True,
        "locked": False,
        "group_memberships": ["Domain Users", "VPN"],
        "mfa_enforced": False,
    }
    state = build_pre_state_from_raw("active_directory", raw)
    assert state["enabled"] is True
    assert state["group_memberships"] == ["Domain Users", "VPN"]


def test_build_pre_state_okta():
    raw = {
        "status": "ACTIVE",
        "mfa_enrolled_factors": ["totp"],
        "app_assignments": ["slack", "github"],
    }
    state = build_pre_state_from_raw("okta", raw)
    assert state["status"] == "ACTIVE"
    assert "mfa_enrolled_factors" in state


def test_build_pre_state_unknown_connector():
    raw = {"foo": "bar"}
    state = build_pre_state_from_raw("unknown_system", raw)
    assert state == {}


def test_compute_restore_ops_disable_to_enabled():
    pre_state = {"enabled": True, "locked": False, "group_memberships": []}
    post_state = {"enabled": False, "locked": False, "group_memberships": []}
    ops = compute_restore_ops("active_directory", pre_state, post_state)
    assert any(op["action"] == "enable_account" for op in ops)


def test_restorable_connector_types_includes_common():
    assert "active_directory" in RESTORABLE_CONNECTOR_TYPES
    assert "okta" in RESTORABLE_CONNECTOR_TYPES
    assert "freeipa" in RESTORABLE_CONNECTOR_TYPES
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && python -m pytest tests/test_identity_graph.py -k "pre_state or restore_ops or restorable" -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `backend/app/connectors/executors/identity/get_account_state.py`**

```python
"""
Read pre-state from raw_attributes stored in IdentityAccount.
Called before any fan-out child CR executes its action.
"""
from __future__ import annotations

# Fields captured per connector type. Keys must be stable — used by restore.
_STATE_FIELDS: dict[str, list[str]] = {
    "active_directory": ["enabled", "locked", "group_memberships", "mfa_enforced"],
    "okta":             ["status", "mfa_enrolled_factors", "app_assignments"],
    "entra_id":         ["accountEnabled", "signInSessionsValidFromDateTime", "assigned_roles"],
    "github":           ["org_membership_state", "role", "team_memberships"],
    "gitlab":           ["state", "group_memberships"],
    "ldap":             ["enabled", "locked"],
    "freeipa":          ["enabled", "locked"],
    "keycloak":         ["enabled", "required_actions"],
    "gitea":            ["login", "active", "prohibited_login"],
    "teleport":         ["locked", "lock_expires"],
    "kubernetes":       ["rolebindings"],
}


def build_pre_state_from_raw(connector_type: str, raw: dict) -> dict:
    """Extract the fields we care about from raw_attributes for pre-state snapshot."""
    fields = _STATE_FIELDS.get(connector_type, [])
    return {k: raw[k] for k in fields if k in raw}


async def get_account_state(connector, external_id: str, connector_type: str) -> dict:
    """
    Live read of current account state from the connector.
    Falls back to empty dict if the connector doesn't support it — child CR
    will then have no pre_state and rollback will be a no-op.
    """
    try:
        from app.services.connector_service import execute_action
        result = await execute_action(connector, "get_user", {"user_id": external_id})
        raw = result.get("user") or result.get("account") or result or {}
        return build_pre_state_from_raw(connector_type, raw)
    except Exception:
        return {}
```

- [ ] **Step 4: Create `backend/app/connectors/executors/identity/restore_account_state.py`**

```python
"""
Restore account state from pre_state snapshot.
Called by child CR rollback.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

RESTORABLE_CONNECTOR_TYPES = frozenset({
    "active_directory", "okta", "entra_id", "github", "gitlab",
    "ldap", "freeipa", "keycloak", "gitea", "teleport", "kubernetes",
})


def compute_restore_ops(connector_type: str, pre_state: dict, post_state: dict) -> list[dict]:
    """
    Diff pre vs post state and return a list of restore operations to apply.
    Each op: {"action": str, "params": dict}
    """
    ops: list[dict] = []

    if connector_type in ("active_directory", "ldap", "freeipa", "keycloak", "gitea"):
        pre_enabled = pre_state.get("enabled", True)
        post_enabled = post_state.get("enabled", True)
        if pre_enabled and not post_enabled:
            ops.append({"action": "enable_account", "params": {}})
        elif not pre_enabled and post_enabled:
            ops.append({"action": "disable_account", "params": {}})

    elif connector_type == "okta":
        pre_status = pre_state.get("status", "ACTIVE")
        post_status = post_state.get("status", "ACTIVE")
        if pre_status != post_status:
            if pre_status == "ACTIVE":
                ops.append({"action": "unsuspend_user", "params": {}})
            elif pre_status == "SUSPENDED":
                ops.append({"action": "suspend_user", "params": {}})

    elif connector_type == "entra_id":
        pre_enabled = pre_state.get("accountEnabled", True)
        post_enabled = post_state.get("accountEnabled", True)
        if pre_enabled and not post_enabled:
            ops.append({"action": "enable_user", "params": {}})
        elif not pre_enabled and post_enabled:
            ops.append({"action": "disable_user", "params": {}})

    elif connector_type == "github":
        pre_state_val = pre_state.get("org_membership_state", "active")
        post_state_val = post_state.get("org_membership_state", "active")
        if pre_state_val == "active" and post_state_val != "active":
            ops.append({"action": "unsuspend_org_member", "params": {}})

    elif connector_type == "gitlab":
        pre_state_val = pre_state.get("state", "active")
        post_state_val = post_state.get("state", "active")
        if pre_state_val == "active" and post_state_val == "blocked":
            ops.append({"action": "gitlab_unblock_user", "params": {}})

    elif connector_type == "teleport":
        pre_locked = pre_state.get("locked", False)
        post_locked = post_state.get("locked", False)
        if not pre_locked and post_locked:
            ops.append({"action": "teleport_unlock_user", "params": {}})

    elif connector_type == "kubernetes":
        pre_rbs = pre_state.get("rolebindings", [])
        post_rbs = post_state.get("rolebindings", [])
        removed = [rb for rb in pre_rbs if rb not in post_rbs]
        for rb in removed:
            ops.append({"action": "k8s_restore_rolebinding", "params": {"rolebinding": rb}})

    return ops


async def restore_account_state(
    connector,
    external_id: str,
    connector_type: str,
    pre_state: dict,
) -> dict:
    """
    Restore an account to its pre-state snapshot.
    Returns dict with status and any warnings.
    """
    if connector_type not in RESTORABLE_CONNECTOR_TYPES:
        return {"status": "skipped", "reason": f"no restore logic for {connector_type}"}

    if not pre_state:
        return {"status": "skipped", "reason": "no pre_state captured"}

    try:
        from app.services.connector_service import execute_action

        # Get current state for diff
        current_raw = {}
        try:
            result = await execute_action(connector, "get_user", {"user_id": external_id})
            current_raw = result.get("user") or result.get("account") or {}
        except Exception:
            pass

        from app.connectors.executors.identity.get_account_state import build_pre_state_from_raw
        current_state = build_pre_state_from_raw(connector_type, current_raw)
        ops = compute_restore_ops(connector_type, pre_state, current_state)

        warnings = []
        for op in ops:
            try:
                await execute_action(connector, op["action"], {"user_id": external_id, **op["params"]})
            except Exception as exc:
                warnings.append(f"{op['action']}: {exc}")

        return {
            "status": "completed" if not warnings else "partial",
            "ops_applied": len(ops) - len(warnings),
            "warnings": warnings,
        }
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd backend && python -m pytest tests/test_identity_graph.py -k "pre_state or restore_ops or restorable" -v
```

Expected: 5 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/identity/get_account_state.py backend/app/connectors/executors/identity/restore_account_state.py backend/tests/test_identity_graph.py
git commit -m "feat: pre-state snapshot/restore for identity fan-out child CRs"
```

---

## Task 5: Fan-out Executor

**Files:**
- Create: `backend/app/connectors/executors/identity/fan_out_executor.py`

The fan-out executor is called when a fan-out ChangeType CR executes. It looks up the target user's IdentityProfile, then spawns one child CR per IdentityAccount. Child CRs inherit the parent's approval.

- [ ] **Step 1: Write failing test**

```python
# Add to backend/tests/test_identity_graph.py
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.mark.asyncio
async def test_fan_out_executor_spawns_child_crs(monkeypatch):
    from app.connectors.executors.identity import fan_out_executor

    mock_profile = MagicMock()
    mock_profile.id = uuid.uuid4()
    mock_profile.primary_email = "alice@corp.com"

    mock_account_ad = MagicMock()
    mock_account_ad.connector_id = uuid.uuid4()
    mock_account_ad.connector_type = "active_directory"
    mock_account_ad.external_id = "alice-ad"
    mock_account_ad.is_stale = False

    mock_account_okta = MagicMock()
    mock_account_okta.connector_id = uuid.uuid4()
    mock_account_okta.connector_type = "okta"
    mock_account_okta.external_id = "00u123"
    mock_account_okta.is_stale = False

    mock_profile.accounts = [mock_account_ad, mock_account_okta]

    spawned = []

    async def mock_spawn(parent_cr_id, connector_id, connector_type, external_id, action, parameters):
        spawned.append({"connector_type": connector_type, "action": action})
        return MagicMock(id=uuid.uuid4())

    monkeypatch.setattr(fan_out_executor, "_spawn_child_cr", mock_spawn)
    monkeypatch.setattr(fan_out_executor, "_lookup_profile", AsyncMock(return_value=mock_profile))

    connector = MagicMock()
    connector.id = uuid.uuid4()

    result = await fan_out_executor.execute(
        parameters={"identity_profile_id": str(mock_profile.id)},
        asset_ids=[],
        connector=connector,
        change_request_id=uuid.uuid4(),
        change_type="emergency_user_lockout",
    )

    assert result["status"] == "completed"
    assert len(spawned) == 2
    assert any(s["connector_type"] == "active_directory" for s in spawned)
    assert any(s["connector_type"] == "okta" for s in spawned)
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && python -m pytest tests/test_identity_graph.py::test_fan_out_executor_spawns_child_crs -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `backend/app/connectors/executors/identity/fan_out_executor.py`**

```python
"""
Fan-out executor — spawns child CRs for each IdentityAccount on a target profile.

Called for fan-out ChangeTypes: emergency_user_lockout, user_suspension,
user_scope_reduction, enforce_mfa.

Signature matches executor contract but adds change_request_id and change_type
which are injected by the modified execute_change_workflow.
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


async def _lookup_profile(profile_id: uuid.UUID, db=None):
    from app.database import db_factory
    from app.models.identity_profile import IdentityProfile
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    ctx = db_factory() if db is None else None
    _db = await ctx.__aenter__() if ctx else db
    try:
        result = await _db.execute(
            select(IdentityProfile)
            .where(IdentityProfile.id == profile_id)
            .options(selectinload(IdentityProfile.accounts))
        )
        return result.scalar_one_or_none()
    finally:
        if ctx:
            await ctx.__aexit__(None, None, None)


async def _spawn_child_cr(
    parent_cr_id: uuid.UUID,
    connector_id: uuid.UUID,
    connector_type: str,
    external_id: str,
    action: str,
    parameters: dict,
):
    """Create and auto-approve a child ChangeRequest."""
    from app.database import db_factory
    from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
    from datetime import datetime, timezone

    async with db_factory() as db:
        child = ChangeRequest(
            id=uuid.uuid4(),
            change_type=action,
            status=ChangeRequestStatus.approved,
            connector_id=connector_id,
            parameters={**parameters, "external_id": external_id},
            parent_change_request_id=parent_cr_id,
            stateful_approved_at=datetime.now(timezone.utc),
        )
        db.add(child)
        await db.commit()
        await db.refresh(child)
        return child


async def execute(
    parameters: dict,
    asset_ids: list,
    connector,
    change_request_id: Optional[uuid.UUID] = None,
    change_type: str = "",
    db=None,
) -> dict:
    from app.connectors.executors.identity.fan_out_registry import get_fan_out_action

    profile_id_str = parameters.get("identity_profile_id") or parameters.get("profile_id")
    if not profile_id_str:
        return {"status": "failed", "reason": "identity_profile_id is required"}

    try:
        profile_id = uuid.UUID(str(profile_id_str))
    except ValueError:
        return {"status": "failed", "reason": f"invalid identity_profile_id: {profile_id_str}"}

    profile = await _lookup_profile(profile_id, db=db)
    if profile is None:
        return {"status": "failed", "reason": f"IdentityProfile {profile_id} not found"}

    accounts = [a for a in profile.accounts if not a.is_stale]
    if not accounts:
        return {"status": "failed", "reason": "no non-stale accounts found for this profile"}

    tasks = []
    skipped = []
    for account in accounts:
        action = get_fan_out_action(change_type, account.connector_type)
        if action is None:
            skipped.append({"connector_type": account.connector_type, "reason": "no action mapped"})
            continue
        tasks.append((account, action))

    child_results = []
    for account, action in tasks:
        try:
            child = await _spawn_child_cr(
                parent_cr_id=change_request_id,
                connector_id=account.connector_id,
                connector_type=account.connector_type,
                external_id=account.external_id,
                action=action,
                parameters=parameters,
            )
            child_results.append({
                "child_cr_id": str(child.id),
                "connector_type": account.connector_type,
                "action": action,
                "status": "spawned",
            })
        except Exception as exc:
            child_results.append({
                "connector_type": account.connector_type,
                "action": action,
                "status": "error",
                "reason": str(exc),
            })

    succeeded = [r for r in child_results if r["status"] == "spawned"]
    failed = [r for r in child_results if r["status"] == "error"]

    if not succeeded:
        status = "failed"
    elif failed:
        status = "partial"
    else:
        status = "completed"

    return {
        "status": status,
        "profile_id": str(profile_id),
        "profile_email": profile.primary_email,
        "child_crs": child_results,
        "skipped": skipped,
        "warnings": [f"{r['connector_type']}: {r['reason']}" for r in failed],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """
    Parent rollback: trigger rollback on all child CRs that succeeded.
    Each child rolls back independently via its own pre_state.
    """
    child_crs = execution_result.get("child_crs", [])
    spawned_ids = [r["child_cr_id"] for r in child_crs if r.get("status") == "spawned"]

    if not spawned_ids:
        return {"rolled_back": True, "reason": "no child CRs to roll back"}

    rollback_results = []
    from app.database import db_factory
    from app.models.change_request import ChangeRequest
    from sqlalchemy import select

    async with db_factory() as db:
        for child_id in spawned_ids:
            try:
                result = await db.execute(
                    select(ChangeRequest).where(ChangeRequest.id == uuid.UUID(child_id))
                )
                child_cr = result.scalar_one_or_none()
                if child_cr is None:
                    rollback_results.append({"child_cr_id": child_id, "status": "not_found"})
                    continue
                from app.services.change_request_service import rollback_change_request
                rb = await rollback_change_request(child_cr, db)
                rollback_results.append({"child_cr_id": child_id, "status": rb.get("status", "unknown")})
            except Exception as exc:
                rollback_results.append({"child_cr_id": child_id, "status": "error", "reason": str(exc)})

    all_ok = all(r["status"] in ("completed", "skipped") for r in rollback_results)
    any_ok = any(r["status"] in ("completed", "skipped") for r in rollback_results)

    return {
        "rolled_back": True,
        "status": "completed" if all_ok else ("partial" if any_ok else "failed"),
        "child_rollbacks": rollback_results,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && python -m pytest tests/test_identity_graph.py::test_fan_out_executor_spawns_child_crs -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/identity/fan_out_executor.py backend/tests/test_identity_graph.py
git commit -m "feat: fan-out executor (spawns child CRs with auto-approval)"
```

---

## Task 6: Wire Fan-out into Change Request Service

**Files:**
- Modify: `backend/app/services/change_request_service.py`

When a fan-out ChangeType CR is approved and executed, the execution workflow must route to the fan-out executor rather than a regular single-connector executor. The change_request_service already calls executor actions — we add a branch for fan-out types.

- [ ] **Step 1: Locate the execute path in `backend/app/services/change_request_service.py`**

Read the file and find the function that calls `connector_service.execute_action` or the executor. Look for where `change_type` is used to dispatch execution.

- [ ] **Step 2: Add fan-out dispatch**

In the execute function (or wherever ChangeType dispatches to executor), add before the normal executor call:

```python
from app.connectors.executors.identity.fan_out_registry import is_fan_out_change_type

if is_fan_out_change_type(change_request.change_type.value):
    from app.connectors.executors.identity import fan_out_executor
    result = await fan_out_executor.execute(
        parameters=change_request.parameters or {},
        asset_ids=[str(a) for a in (change_request.asset_ids or [])],
        connector=connector,
        change_request_id=change_request.id,
        change_type=change_request.change_type.value,
    )
    return result
```

And in the rollback function, add a parallel branch:

```python
if is_fan_out_change_type(change_request.change_type.value):
    from app.connectors.executors.identity import fan_out_executor
    return await fan_out_executor.rollback(
        parameters=change_request.parameters or {},
        execution_result=change_request.execution_result or {},
        connector=connector,
    )
```

- [ ] **Step 3: Add identity_snapshot and identity_reconstitute to action catalog**

Check `backend/app/connectors/action_catalog.json` and verify `identity_snapshot` and `identity_reconstitute` will be added as part of Task 7/8. No changes needed here — the CTD registration handles it.

- [ ] **Step 4: Run existing CR service tests to verify no regression**

```
cd backend && python -m pytest tests/ -k "change_request" -v --tb=short
```

Expected: all previously-passing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/change_request_service.py
git commit -m "feat: wire fan-out ChangeTypes into CR execution dispatch"
```

---

## Task 7: Identity Snapshot Executor + CTD

**Files:**
- Create: `backend/app/connectors/executors/identity/identity_snapshot.py`
- Create: `backend/app/connectors/change_type_definitions/identity_snapshot.json`

- [ ] **Step 1: Write failing test**

```python
# Add to backend/tests/test_identity_graph.py
import json
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_identity_snapshot_produces_manifest(monkeypatch):
    from app.connectors.executors.identity import identity_snapshot

    mock_accounts = [
        {"external_id": "alice", "username": "alice", "raw_attributes": {"enabled": True}},
        {"external_id": "bob", "username": "bob", "raw_attributes": {"enabled": True}},
    ]

    async def mock_discover(connector, action, params):
        return {"users": mock_accounts}

    uploaded = {}

    def mock_upload(bucket, key, body):
        uploaded[key] = body

    monkeypatch.setattr(identity_snapshot, "_discover_users", mock_discover)
    monkeypatch.setattr(identity_snapshot, "_s3_put", mock_upload)

    connector = MagicMock()
    connector.id = uuid.uuid4()
    connector.connector_type = "active_directory"
    connector.credentials = {"bucket": "test-bucket", "prefix": "test/"}

    result = await identity_snapshot.execute(
        parameters={"s3_prefix": "test/snapshots/"},
        asset_ids=[],
        connector=connector,
    )

    assert result["status"] == "completed"
    assert "snapshot_id" in result
    manifest_keys = [k for k in uploaded if "manifest.json" in k]
    assert len(manifest_keys) == 1
    manifest = json.loads(uploaded[manifest_keys[0]])
    assert manifest["format"] == "identity_snapshot_v1"
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && python -m pytest tests/test_identity_graph.py::test_identity_snapshot_produces_manifest -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `backend/app/connectors/executors/identity/identity_snapshot.py`**

```python
"""
identity_snapshot — capture full directory state across all identity connectors to S3.

S3 layout:
  {prefix}/identity-snapshot-{timestamp}/
    manifest.json
    {connector_id}/
      users.json
      groups.json
"""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


async def _discover_users(connector, action: str, params: dict) -> dict:
    from app.services.connector_service import execute_action
    return await execute_action(connector, action, params)


def _s3_put(bucket: str, key: str, body: str) -> None:
    import boto3
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=body.encode())


def _s3_client(creds: dict):
    import boto3
    kwargs = {}
    if creds.get("aws_access_key_id"):
        kwargs["aws_access_key_id"] = creds["aws_access_key_id"]
        kwargs["aws_secret_access_key"] = creds["aws_secret_access_key"]
    if creds.get("region"):
        kwargs["region_name"] = creds["region"]
    return boto3.client("s3", **kwargs)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    bucket = parameters.get("s3_bucket") or creds.get("s3_bucket") or creds.get("bucket", "")
    prefix = parameters.get("s3_prefix") or creds.get("s3_prefix") or creds.get("prefix", "")

    if not bucket:
        return {"status": "failed", "reason": "s3_bucket is required (parameter or connector credential)"}

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"identity-snapshot-{ts}"
    base_key = f"{prefix.rstrip('/')}/{snapshot_id}"

    # Discover all users from this connector
    try:
        result = await _discover_users(connector, "discover_users", {})
        users = result.get("users") or result.get("accounts") or []
    except Exception as exc:
        return {"status": "failed", "reason": f"discover_users failed: {exc}"}

    groups = []
    try:
        grp_result = await _discover_users(connector, "discover_groups", {})
        groups = grp_result.get("groups") or []
    except Exception:
        pass

    connector_id = str(connector.id)
    connector_type = getattr(connector, "connector_type", "unknown")

    artifact_counts = {"users": len(users), "groups": len(groups)}

    # Upload artifacts
    connector_prefix = f"{base_key}/{connector_id}"
    try:
        import boto3
        s3 = _s3_client(creds)

        def _put(key: str, obj) -> None:
            s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(obj, default=str).encode())

        _put(f"{connector_prefix}/users.json", users)
        _put(f"{connector_prefix}/groups.json", groups)

        manifest = {
            "format": "identity_snapshot_v1",
            "snapshot_id": snapshot_id,
            "snapshot_timestamp": ts,
            "systems": [connector_type],
            "connector_ids": [connector_id],
            "artifact_counts": artifact_counts,
        }
        _put(f"{base_key}/manifest.json", manifest)

    except Exception as exc:
        return {"status": "failed", "reason": f"S3 upload failed: {exc}"}

    return {
        "status": "completed",
        "snapshot_id": snapshot_id,
        "s3_prefix": base_key,
        "artifact_counts": artifact_counts,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_prefix = execution_result.get("s3_prefix", "")
    return {
        "rolled_back": False,
        "reason": (
            f"Snapshots are read-only artifacts and are not deleted on rollback. "
            f"If needed, manually delete S3 objects at: {snapshot_prefix}"
        ),
    }
```

- [ ] **Step 4: Create `backend/app/connectors/change_type_definitions/identity_snapshot.json`**

Look at an existing CTD (e.g., `emergency_user_lockout.json`) to match the format exactly, then create:

```json
{
  "change_type": "identity_snapshot",
  "display_name": "Identity Snapshot",
  "description": "Capture full directory state across all connected identity systems to S3 for disaster recovery and reconstitution.",
  "risk_level": "low",
  "requires_approval": false,
  "rollback_supported": false,
  "parameters": [
    {
      "name": "s3_bucket",
      "type": "string",
      "required": false,
      "description": "S3 bucket name. Falls back to connector credential s3_bucket."
    },
    {
      "name": "s3_prefix",
      "type": "string",
      "required": false,
      "description": "S3 key prefix for snapshot artifacts. Default: empty string."
    }
  ],
  "applicable_connector_types": [
    "active_directory", "okta", "entra_id", "github", "gitlab",
    "ldap", "freeipa", "keycloak", "gitea", "teleport"
  ]
}
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd backend && python -m pytest tests/test_identity_graph.py::test_identity_snapshot_produces_manifest -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/identity/identity_snapshot.py backend/app/connectors/change_type_definitions/identity_snapshot.json backend/tests/test_identity_graph.py
git commit -m "feat: identity_snapshot executor (full directory state to S3)"
```

---

## Task 8: Identity Reconstitute Executor + CTD

**Files:**
- Create: `backend/app/connectors/executors/identity/identity_reconstitute.py`
- Create: `backend/app/connectors/change_type_definitions/identity_reconstitute.json`

- [ ] **Step 1: Write failing test**

```python
# Add to backend/tests/test_identity_graph.py

@pytest.mark.asyncio
async def test_identity_reconstitute_dry_run_detects_missing(monkeypatch):
    from app.connectors.executors.identity import identity_reconstitute

    snapshot_users = [
        {"external_id": "alice", "username": "alice", "raw_attributes": {"enabled": True}},
        {"external_id": "bob", "username": "bob", "raw_attributes": {"enabled": True}},
    ]

    async def mock_load_snapshot(s3_bucket, s3_prefix, creds):
        return {
            "manifest": {"format": "identity_snapshot_v1", "connector_ids": ["conn1"]},
            "users_by_connector": {"conn1": snapshot_users},
        }

    async def mock_current_users(connector, action, params):
        # Only alice exists currently; bob is missing
        return {"users": [snapshot_users[0]]}

    monkeypatch.setattr(identity_reconstitute, "_load_snapshot", mock_load_snapshot)
    monkeypatch.setattr(identity_reconstitute, "_discover_users", mock_current_users)

    connector = MagicMock()
    connector.id = MagicMock()
    connector.id.__str__ = lambda self: "conn1"
    connector.credentials = {}

    result = await identity_reconstitute.execute(
        parameters={
            "s3_bucket": "test-bucket",
            "s3_prefix": "test/snapshots/identity-snapshot-20260523T120000Z",
            "dry_run": True,
        },
        asset_ids=[],
        connector=connector,
    )

    assert result["status"] == "dry_run_complete"
    missing = [a for a in result["analysis"] if a["classification"] == "missing"]
    assert any(a["external_id"] == "bob" for a in missing)


@pytest.mark.asyncio
async def test_identity_reconstitute_rejects_invalid_manifest(monkeypatch):
    from app.connectors.executors.identity import identity_reconstitute

    async def mock_load_bad(s3_bucket, s3_prefix, creds):
        return {
            "manifest": {"format": "wrong_format"},
            "users_by_connector": {},
        }

    monkeypatch.setattr(identity_reconstitute, "_load_snapshot", mock_load_bad)

    connector = MagicMock()
    connector.credentials = {}

    result = await identity_reconstitute.execute(
        parameters={"s3_bucket": "b", "s3_prefix": "p", "dry_run": True},
        asset_ids=[],
        connector=connector,
    )
    assert result["status"] == "failed"
    assert "format" in result["reason"]
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && python -m pytest tests/test_identity_graph.py -k "reconstitute" -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `backend/app/connectors/executors/identity/identity_reconstitute.py`**

```python
"""
identity_reconstitute — compare current state to a snapshot, dry-run detects
divergence, then operator-approved reconstitution recreates missing/corrupted accounts.

Execution sequence:
1. Download and validate manifest
2. Dry-run analysis (always first) — missing | corrupted | orphaned | clean
3. If dry_run=True: return analysis, pause (operator approval via CR parameters)
4. Reconstitute: delete corrupted, recreate missing
5. Verify and report
"""
from __future__ import annotations
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

STRUCTURAL_FIELDS = {"enabled", "locked", "group_memberships", "role_assignments",
                     "mfa_state", "status", "accountEnabled", "rolebindings"}
MAX_BATCH_DEFAULT = 100


async def _load_snapshot(s3_bucket: str, s3_prefix: str, creds: dict) -> dict:
    import boto3
    kwargs = {}
    if creds.get("aws_access_key_id"):
        kwargs["aws_access_key_id"] = creds["aws_access_key_id"]
        kwargs["aws_secret_access_key"] = creds["aws_secret_access_key"]
    if creds.get("region"):
        kwargs["region_name"] = creds["region"]
    s3 = boto3.client("s3", **kwargs)

    manifest_key = f"{s3_prefix.rstrip('/')}/manifest.json"
    manifest_obj = s3.get_object(Bucket=s3_bucket, Key=manifest_key)
    manifest = json.loads(manifest_obj["Body"].read())

    users_by_connector = {}
    for connector_id in manifest.get("connector_ids", []):
        users_key = f"{s3_prefix.rstrip('/')}/{connector_id}/users.json"
        try:
            obj = s3.get_object(Bucket=s3_bucket, Key=users_key)
            users_by_connector[connector_id] = json.loads(obj["Body"].read())
        except Exception:
            users_by_connector[connector_id] = []

    return {"manifest": manifest, "users_by_connector": users_by_connector}


async def _discover_users(connector, action: str, params: dict) -> dict:
    from app.services.connector_service import execute_action
    return await execute_action(connector, action, params)


def _classify(snapshot_user: dict, current_by_id: dict) -> str:
    ext_id = snapshot_user.get("external_id", "")
    current = current_by_id.get(ext_id)
    if current is None:
        return "missing"
    # Check structural field divergence
    snap_raw = snapshot_user.get("raw_attributes") or {}
    curr_raw = current.get("raw_attributes") or {}
    for field in STRUCTURAL_FIELDS:
        if field in snap_raw and snap_raw.get(field) != curr_raw.get(field):
            return "corrupted"
    return "clean"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    s3_bucket = parameters.get("s3_bucket") or creds.get("s3_bucket", "")
    s3_prefix = parameters.get("s3_prefix") or creds.get("s3_prefix", "")
    dry_run = parameters.get("dry_run", True)
    max_batch = int(parameters.get("max_batch", MAX_BATCH_DEFAULT))

    if not s3_bucket or not s3_prefix:
        return {"status": "failed", "reason": "s3_bucket and s3_prefix are required"}

    # 1. Load and validate manifest
    try:
        snapshot = await _load_snapshot(s3_bucket, s3_prefix, creds)
    except Exception as exc:
        return {"status": "failed", "reason": f"Failed to load snapshot: {exc}"}

    manifest = snapshot["manifest"]
    if manifest.get("format") != "identity_snapshot_v1":
        return {
            "status": "failed",
            "reason": f"Invalid snapshot format: {manifest.get('format')!r}. Expected 'identity_snapshot_v1'.",
        }

    connector_id = str(connector.id)
    snapshot_users = snapshot["users_by_connector"].get(connector_id, [])

    # 2. Discover current users
    try:
        current_result = await _discover_users(connector, "discover_users", {})
        current_users = current_result.get("users") or current_result.get("accounts") or []
    except Exception as exc:
        return {"status": "failed", "reason": f"discover_users failed: {exc}"}

    current_by_id = {u.get("external_id", u.get("id", "")): u for u in current_users}
    snapshot_ids = {u.get("external_id", "") for u in snapshot_users}

    # Classify each account
    analysis = []
    for user in snapshot_users:
        ext_id = user.get("external_id", "")
        classification = _classify(user, current_by_id)
        analysis.append({
            "external_id": ext_id,
            "username": user.get("username", ""),
            "classification": classification,
        })

    # Orphaned: in current but not in snapshot
    for ext_id, user in current_by_id.items():
        if ext_id not in snapshot_ids:
            analysis.append({
                "external_id": ext_id,
                "username": user.get("username", ""),
                "classification": "orphaned",
                "note": "exists now, not in snapshot — flagged for operator review, will NOT be auto-deleted",
            })

    summary = {c: sum(1 for a in analysis if a["classification"] == c)
               for c in ("missing", "corrupted", "orphaned", "clean")}

    # 3. Dry-run gate
    if dry_run:
        return {
            "status": "dry_run_complete",
            "analysis": analysis,
            "summary": summary,
            "next_step": "Re-submit CR with dry_run=false to execute reconstitution",
        }

    # 4. Reconstitute — missing and corrupted only; never touch orphaned
    to_reconstitute = [a for a in analysis if a["classification"] in ("missing", "corrupted")]
    if len(to_reconstitute) > max_batch:
        return {
            "status": "failed",
            "reason": (
                f"Reconstitution batch size {len(to_reconstitute)} exceeds max_batch={max_batch}. "
                "Re-submit with explicit max_batch override to proceed."
            ),
        }

    reconstituted = []
    failed = []
    snapshot_by_id = {u.get("external_id", ""): u for u in snapshot_users}

    from app.services.connector_service import execute_action

    for item in to_reconstitute:
        ext_id = item["external_id"]
        snap_user = snapshot_by_id.get(ext_id, {})
        try:
            if item["classification"] == "corrupted":
                await execute_action(connector, "delete_user", {"user_id": ext_id})
            await execute_action(connector, "create_user", {
                "user_id": ext_id,
                "attributes": snap_user.get("raw_attributes") or {},
            })
            reconstituted.append({"external_id": ext_id, "status": "reconstituted"})
        except Exception as exc:
            failed.append({"external_id": ext_id, "status": "error", "reason": str(exc)})

    status = "completed" if not failed else ("partial" if reconstituted else "failed")
    return {
        "status": status,
        "summary": summary,
        "reconstituted": len(reconstituted),
        "failed": len(failed),
        "results": reconstituted + failed,
        "orphaned_flagged": summary.get("orphaned", 0),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": (
            "identity_reconstitute rollback is not automatic. "
            "Run a fresh identity_snapshot of the current state and compare manually, "
            "or re-run identity_reconstitute targeting the pre-reconstitution snapshot."
        ),
    }
```

- [ ] **Step 4: Create `backend/app/connectors/change_type_definitions/identity_reconstitute.json`**

```json
{
  "change_type": "identity_reconstitute",
  "display_name": "Identity Reconstitution",
  "description": "Compare current identity state against a snapshot and restore missing or corrupted accounts. Dry-run mode always runs first; operator approval required before destructive actions.",
  "risk_level": "critical",
  "requires_approval": true,
  "rollback_supported": false,
  "parameters": [
    {
      "name": "s3_bucket",
      "type": "string",
      "required": true,
      "description": "S3 bucket containing the identity snapshot."
    },
    {
      "name": "s3_prefix",
      "type": "string",
      "required": true,
      "description": "S3 prefix for the snapshot (e.g. snapshots/identity-snapshot-20260523T120000Z)."
    },
    {
      "name": "dry_run",
      "type": "boolean",
      "required": false,
      "description": "If true (default), returns analysis without making changes. Set false to execute reconstitution after reviewing dry-run output."
    },
    {
      "name": "max_batch",
      "type": "integer",
      "required": false,
      "description": "Maximum number of accounts to reconstitute in one CR. Default 100. Larger batches require explicit override."
    }
  ],
  "applicable_connector_types": [
    "active_directory", "okta", "entra_id", "github", "gitlab",
    "ldap", "freeipa", "keycloak", "gitea"
  ]
}
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd backend && python -m pytest tests/test_identity_graph.py -k "reconstitute" -v
```

Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/identity/identity_reconstitute.py backend/app/connectors/change_type_definitions/identity_reconstitute.json backend/tests/test_identity_graph.py
git commit -m "feat: identity_reconstitute executor (dry-run + operator-gated reconstitution)"
```

---

## Task 9: Identity Router + Scheduler Job

**Files:**
- Create: `backend/app/routers/identity.py`
- Modify: `backend/app/services/scheduler_service.py`
- Modify: `backend/app/main.py` (register router)

- [ ] **Step 1: Write failing test for router**

```python
# Add to backend/tests/test_identity_graph.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_identity_profiles_endpoint_returns_list(test_client):
    response = await test_client.get("/api/identity/profiles")
    assert response.status_code in (200, 401)  # 401 if auth required in test env
```

- [ ] **Step 2: Create `backend/app/routers/identity.py`**

Look at an existing router (e.g., `backend/app/routers/connectors.py`) to match the auth pattern (`Depends(current_user)`, `Depends(get_db)`), then create:

```python
from __future__ import annotations
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.auth import current_user
from app.models.identity_profile import IdentityProfile, IdentityAccount

router = APIRouter(prefix="/identity", tags=["identity"])


@router.get("/profiles")
async def list_profiles(
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    result = await db.execute(
        select(IdentityProfile).options(selectinload(IdentityProfile.accounts))
    )
    profiles = result.scalars().all()
    return [
        {
            "id": str(p.id),
            "display_name": p.display_name,
            "primary_email": p.primary_email,
            "correlation_method": p.correlation_method,
            "last_synced_at": p.last_synced_at.isoformat() if p.last_synced_at else None,
            "accounts": [
                {
                    "id": str(a.id),
                    "connector_id": str(a.connector_id),
                    "connector_type": a.connector_type,
                    "external_id": a.external_id,
                    "username": a.username,
                    "email": a.email,
                    "is_stale": a.is_stale,
                    "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
                }
                for a in p.accounts
            ],
        }
        for p in profiles
    ]


@router.get("/profiles/{profile_id}")
async def get_profile(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    result = await db.execute(
        select(IdentityProfile)
        .where(IdentityProfile.id == profile_id)
        .options(selectinload(IdentityProfile.accounts))
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="IdentityProfile not found")
    return {
        "id": str(profile.id),
        "display_name": profile.display_name,
        "primary_email": profile.primary_email,
        "correlation_method": profile.correlation_method,
        "last_synced_at": profile.last_synced_at.isoformat() if profile.last_synced_at else None,
        "accounts": [
            {
                "id": str(a.id),
                "connector_id": str(a.connector_id),
                "connector_type": a.connector_type,
                "external_id": a.external_id,
                "username": a.username,
                "email": a.email,
                "is_stale": a.is_stale,
            }
            for a in profile.accounts
        ],
    }


@router.post("/sync")
async def trigger_sync(
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    from app.services.identity_sync_service import sync_all
    stats = await sync_all(db)
    await db.commit()
    return {"status": "completed", **stats}
```

- [ ] **Step 3: Register router in `backend/app/main.py`**

Find where other routers are included (e.g., `app.include_router(connectors.router)`) and add:

```python
from app.routers import identity
app.include_router(identity.router, prefix="/api")
```

- [ ] **Step 4: Add scheduler job to `backend/app/services/scheduler_service.py`**

Find the section where APScheduler jobs are added and add:

```python
async def _run_identity_sync():
    async with _db_factory() as db:
        from app.services.identity_sync_service import sync_all
        try:
            stats = await sync_all(db)
            await db.commit()
            logger.info("identity sync complete: %s", stats)
        except Exception as exc:
            logger.error("identity sync failed: %s", exc)

scheduler.add_job(
    _run_identity_sync,
    trigger="interval",
    hours=4,
    id="identity_sync",
    replace_existing=True,
    coalesce=True,
)
```

- [ ] **Step 5: Verify app starts without errors**

```
cd backend && python -c "from app.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/identity.py backend/app/main.py backend/app/services/scheduler_service.py backend/tests/test_identity_graph.py
git commit -m "feat: identity router (/identity/profiles, /identity/sync) + APScheduler 4h sync"
```

---

## Task 10: Smoke Phase — IDENTITY_SYNC

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

The IDENTITY_SYNC phase uses FreeIPA only (fastest). It validates that after registering a FreeIPA connector and triggering a sync, a new user appears in the identity graph.

- [ ] **Step 1: Find the phase dispatch table in `test_aws_live.py`**

Locate the `PHASES` dict or the section where phases are dispatched (around the `run_phase_*` function pattern). Note the exact location.

- [ ] **Step 2: Add IDENTITY_SYNC phase function**

Add to `test_aws_live.py` after the last identity-related phase:

```python
async def run_phase_identity_sync(session, backend_url, aws_session, args):
    """IDENTITY_SYNC — FreeIPA connector sync populates identity graph."""
    log("=== IDENTITY_SYNC ===")

    # Launch FreeIPA from cached AMI
    freeipa_instance_id, freeipa_private_ip = await get_or_create_smoke_ami(
        aws_session, "freeipa", args
    )
    log(f"FreeIPA: {freeipa_instance_id} at {freeipa_private_ip}")

    # Register FreeIPA connector
    connector_payload = {
        "name": "smoke-freeipa-identity",
        "connector_type": "freeipa",
        "credentials": {
            "host": freeipa_private_ip,
            "port": 389,
            "bind_dn": "cn=Directory Manager",
            "bind_password": FREEIPA_BIND_PASSWORD,
            "base_dn": "dc=smoke,dc=nexplane,dc=local",
        },
        "enabled": True,
    }
    connector = await cr_post(session, f"{backend_url}/api/connectors", connector_payload)
    connector_id = connector["id"]
    log(f"Connector registered: {connector_id}")

    # Trigger initial sync
    sync_result = await cr_post(session, f"{backend_url}/api/identity/sync", {})
    log(f"Initial sync: {sync_result}")
    assert sync_result["status"] == "completed"

    # Create a new user directly in FreeIPA via connector action
    test_username = "sync-smoke-user"
    test_email = f"{test_username}@smoke.nexplane.local"
    await cr_post(session, f"{backend_url}/api/connectors/{connector_id}/execute", {
        "action": "create_user",
        "parameters": {
            "username": test_username,
            "email": test_email,
            "password": "SmokeTest123!",
        },
    })
    log(f"Created test user: {test_username}")

    # Trigger manual sync
    sync2 = await cr_post(session, f"{backend_url}/api/identity/sync", {})
    assert sync2["status"] == "completed"
    log(f"Post-create sync: {sync2}")

    # Verify user appears in identity graph
    profiles = await cr_get(session, f"{backend_url}/api/identity/profiles")
    matching = [p for p in profiles if any(
        a.get("username") == test_username for a in p.get("accounts", [])
    )]
    assert matching, f"Expected {test_username} in identity graph after sync"
    profile = matching[0]
    assert not any(
        a.get("is_stale") for a in profile["accounts"] if a.get("username") == test_username
    ), "Account should not be stale after sync"
    log(f"User found in identity graph: profile_id={profile['id']}")

    # Cleanup
    await cr_post(session, f"{backend_url}/api/connectors/{connector_id}/execute", {
        "action": "delete_user",
        "parameters": {"username": test_username},
    })
    await cr_delete(session, f"{backend_url}/api/connectors/{connector_id}")
    await terminate_instance(aws_session, freeipa_instance_id)
    log("IDENTITY_SYNC: PASS")
```

- [ ] **Step 3: Register phase in the dispatch table**

Add `"IDENTITY_SYNC": run_phase_identity_sync` to the PHASES dict.

- [ ] **Step 4: Verify syntax on EC2 runner**

```
docker exec nexplane-backend-1 python3 -c "
import ast, sys
with open('/app/tests/smoke/test_aws_live.py', 'rb') as f:
    src = f.read().lstrip(b'\\xef\\xbb\\xbf')
ast.parse(src)
print('syntax OK')
"
```

Expected: `syntax OK`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "smoke: add IDENTITY_SYNC phase (FreeIPA connector sync → identity graph)"
```

---

## Task 11: Smoke Phase — IDENTITY_FANOUT

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

The IDENTITY_FANOUT phase uses AD DC + FreeIPA. Creates a user in both systems with matching email, runs sync to build identity graph, then executes `emergency_user_lockout` fan-out CR and verifies both systems are affected. Rolls back and verifies restoration.

- [ ] **Step 1: Add IDENTITY_FANOUT phase function**

```python
async def run_phase_identity_fanout(session, backend_url, aws_session, args):
    """IDENTITY_FANOUT — emergency_user_lockout fans out across AD + FreeIPA."""
    log("=== IDENTITY_FANOUT ===")

    # Launch AD DC from cached AMI
    dc_instance_id, dc_private_ip = await get_or_create_smoke_ami(
        aws_session, "dc-smoke", args
    )
    log(f"AD DC: {dc_instance_id} at {dc_private_ip}")

    # Launch FreeIPA from cached AMI
    freeipa_instance_id, freeipa_private_ip = await get_or_create_smoke_ami(
        aws_session, "freeipa", args
    )
    log(f"FreeIPA: {freeipa_instance_id} at {freeipa_private_ip}")

    TEST_EMAIL = "fanout-smoke@smoke.nexplane.local"
    TEST_USERNAME = "fanout-smoke"

    # Register AD connector
    ad_connector = await cr_post(session, f"{backend_url}/api/connectors", {
        "name": "smoke-ad-fanout",
        "connector_type": "active_directory",
        "credentials": {
            "host": dc_private_ip,
            "port": 389,
            "bind_dn": "CN=Administrator,CN=Users,DC=smoke,DC=nexplane,DC=local",
            "bind_password": AD_ADMIN_PASSWORD,
            "base_dn": "DC=smoke,DC=nexplane,DC=local",
        },
        "enabled": True,
    })
    ad_connector_id = ad_connector["id"]

    # Register FreeIPA connector
    freeipa_connector = await cr_post(session, f"{backend_url}/api/connectors", {
        "name": "smoke-freeipa-fanout",
        "connector_type": "freeipa",
        "credentials": {
            "host": freeipa_private_ip,
            "port": 389,
            "bind_dn": "cn=Directory Manager",
            "bind_password": FREEIPA_BIND_PASSWORD,
            "base_dn": "dc=smoke,dc=nexplane,dc=local",
        },
        "enabled": True,
    })
    freeipa_connector_id = freeipa_connector["id"]

    # Create matching user in both systems
    for connector_id, action_params in [
        (ad_connector_id, {"username": TEST_USERNAME, "email": TEST_EMAIL, "password": "FanoutSmoke1!"}),
        (freeipa_connector_id, {"username": TEST_USERNAME, "email": TEST_EMAIL, "password": "FanoutSmoke1!"}),
    ]:
        await cr_post(session, f"{backend_url}/api/connectors/{connector_id}/execute", {
            "action": "create_user",
            "parameters": action_params,
        })

    log("Test users created in AD and FreeIPA")

    # Sync identity graph
    await cr_post(session, f"{backend_url}/api/identity/sync", {})

    # Verify both accounts appear under one profile
    profiles = await cr_get(session, f"{backend_url}/api/identity/profiles")
    matching = [p for p in profiles if p["primary_email"] == TEST_EMAIL]
    assert matching, f"No profile found for {TEST_EMAIL} after sync"
    profile = matching[0]
    account_types = {a["connector_type"] for a in profile["accounts"]}
    assert "active_directory" in account_types, "AD account not in profile"
    assert "freeipa" in account_types, "FreeIPA account not in profile"
    profile_id = profile["id"]
    log(f"Identity profile: {profile_id} with {account_types}")

    # Create and execute emergency_user_lockout fan-out CR
    cr = await create_and_approve_cr(session, backend_url, {
        "change_type": "emergency_user_lockout",
        "parameters": {"identity_profile_id": profile_id},
        "connector_id": ad_connector_id,  # parent connector (any identity connector)
    })
    cr_id = cr["id"]
    execution = await wait_for_cr_completion(session, backend_url, cr_id)
    assert execution["status"] in ("completed", "partial"), f"Fan-out CR failed: {execution}"
    log(f"Fan-out CR: {execution['status']}, child CRs: {len(execution.get('child_crs', []))}")

    # Verify AD account is disabled
    ad_state = await cr_post(session, f"{backend_url}/api/connectors/{ad_connector_id}/execute", {
        "action": "get_user",
        "parameters": {"username": TEST_USERNAME},
    })
    assert not ad_state.get("user", {}).get("enabled", True), "AD account should be disabled"

    # Verify FreeIPA account is locked
    freeipa_state = await cr_post(session, f"{backend_url}/api/connectors/{freeipa_connector_id}/execute", {
        "action": "get_user",
        "parameters": {"username": TEST_USERNAME},
    })
    assert freeipa_state.get("user", {}).get("locked", False) or \
           not freeipa_state.get("user", {}).get("enabled", True), "FreeIPA account should be locked"

    log("Both systems locked — rolling back")

    # Rollback parent CR
    rollback_result = await cr_post(session, f"{backend_url}/api/change-requests/{cr_id}/rollback", {})
    assert rollback_result.get("status") in ("completed", "partial")

    # Verify AD account re-enabled
    ad_state2 = await cr_post(session, f"{backend_url}/api/connectors/{ad_connector_id}/execute", {
        "action": "get_user",
        "parameters": {"username": TEST_USERNAME},
    })
    assert ad_state2.get("user", {}).get("enabled", False), "AD account should be re-enabled after rollback"
    log("Rollback verified")

    # Cleanup
    for connector_id, username in [(ad_connector_id, TEST_USERNAME), (freeipa_connector_id, TEST_USERNAME)]:
        try:
            await cr_post(session, f"{backend_url}/api/connectors/{connector_id}/execute", {
                "action": "delete_user",
                "parameters": {"username": username},
            })
        except Exception:
            pass
    await cr_delete(session, f"{backend_url}/api/connectors/{ad_connector_id}")
    await cr_delete(session, f"{backend_url}/api/connectors/{freeipa_connector_id}")
    await terminate_instance(aws_session, dc_instance_id)
    await terminate_instance(aws_session, freeipa_instance_id)
    log("IDENTITY_FANOUT: PASS")
```

- [ ] **Step 2: Register in dispatch table**

Add `"IDENTITY_FANOUT": run_phase_identity_fanout` to PHASES dict.

- [ ] **Step 3: Verify syntax**

```
docker exec nexplane-backend-1 python3 -c "
import ast
with open('/app/tests/smoke/test_aws_live.py', 'rb') as f:
    src = f.read().lstrip(b'\\xef\\xbb\\xbf')
ast.parse(src)
print('syntax OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "smoke: add IDENTITY_FANOUT phase (emergency_user_lockout across AD + FreeIPA + rollback)"
```

---

## Task 12: Smoke Phase — IDENTITY_SNAPSHOT

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

The IDENTITY_SNAPSHOT phase validates `identity_snapshot` to S3, then simulates corruption, runs `identity_reconstitute` dry-run to detect it, runs reconstitute for real, and verifies restoration. Runs rollback to verify.

- [ ] **Step 1: Add IDENTITY_SNAPSHOT phase function**

```python
async def run_phase_identity_snapshot(session, backend_url, aws_session, args):
    """IDENTITY_SNAPSHOT — snapshot + reconstitute round-trip with corruption detection."""
    log("=== IDENTITY_SNAPSHOT ===")

    # Launch FreeIPA (faster than AD for this phase)
    freeipa_instance_id, freeipa_private_ip = await get_or_create_smoke_ami(
        aws_session, "freeipa", args
    )
    log(f"FreeIPA: {freeipa_instance_id} at {freeipa_private_ip}")

    S3_BUCKET = args.s3_bucket  # smoke test infra bucket
    S3_PREFIX = "smoke/identity-snapshots/"
    TEST_USERNAME = "snapshot-smoke-user"
    TEST_EMAIL = f"{TEST_USERNAME}@smoke.nexplane.local"

    # Register FreeIPA connector
    connector = await cr_post(session, f"{backend_url}/api/connectors", {
        "name": "smoke-freeipa-snapshot",
        "connector_type": "freeipa",
        "credentials": {
            "host": freeipa_private_ip,
            "port": 389,
            "bind_dn": "cn=Directory Manager",
            "bind_password": FREEIPA_BIND_PASSWORD,
            "base_dn": "dc=smoke,dc=nexplane,dc=local",
            "s3_bucket": S3_BUCKET,
            "s3_prefix": S3_PREFIX,
        },
        "enabled": True,
    })
    connector_id = connector["id"]

    # Create test user
    await cr_post(session, f"{backend_url}/api/connectors/{connector_id}/execute", {
        "action": "create_user",
        "parameters": {"username": TEST_USERNAME, "email": TEST_EMAIL, "password": "SnapSmoke1!"},
    })
    log(f"Created test user: {TEST_USERNAME}")

    # Run identity_snapshot CR
    snap_cr = await create_and_approve_cr(session, backend_url, {
        "change_type": "identity_snapshot",
        "parameters": {"s3_bucket": S3_BUCKET, "s3_prefix": S3_PREFIX},
        "connector_id": connector_id,
    })
    snap_exec = await wait_for_cr_completion(session, backend_url, snap_cr["id"])
    assert snap_exec["status"] == "completed", f"Snapshot failed: {snap_exec}"
    snapshot_prefix = snap_exec["s3_prefix"]
    log(f"Snapshot at: {snapshot_prefix}")

    # Verify manifest exists in S3
    import boto3
    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket=S3_BUCKET, Key=f"{snapshot_prefix}/manifest.json")
    import json
    manifest = json.loads(obj["Body"].read())
    assert manifest["format"] == "identity_snapshot_v1"
    log("Manifest verified")

    # Simulate corruption: disable the user directly via LDAP
    await cr_post(session, f"{backend_url}/api/connectors/{connector_id}/execute", {
        "action": "freeipa_disable_user",
        "parameters": {"username": TEST_USERNAME},
    })
    log("Simulated corruption: user disabled")

    # Dry-run reconstitute — should detect corrupted
    recon_cr = await create_and_approve_cr(session, backend_url, {
        "change_type": "identity_reconstitute",
        "parameters": {
            "s3_bucket": S3_BUCKET,
            "s3_prefix": snapshot_prefix,
            "dry_run": True,
        },
        "connector_id": connector_id,
    })
    dry_exec = await wait_for_cr_completion(session, backend_url, recon_cr["id"])
    assert dry_exec["status"] == "dry_run_complete"
    corrupted = [a for a in dry_exec["analysis"] if a["classification"] == "corrupted"]
    assert any(a["external_id"] == TEST_USERNAME or a.get("username") == TEST_USERNAME
               for a in corrupted), f"Expected {TEST_USERNAME} detected as corrupted"
    log(f"Dry-run detected: {dry_exec['summary']}")

    # Real reconstitute
    recon_real_cr = await create_and_approve_cr(session, backend_url, {
        "change_type": "identity_reconstitute",
        "parameters": {
            "s3_bucket": S3_BUCKET,
            "s3_prefix": snapshot_prefix,
            "dry_run": False,
        },
        "connector_id": connector_id,
    })
    real_exec = await wait_for_cr_completion(session, backend_url, recon_real_cr["id"])
    assert real_exec["status"] in ("completed", "partial"), f"Reconstitute failed: {real_exec}"
    log(f"Reconstitute: {real_exec['status']}, reconstituted={real_exec.get('reconstituted')}")

    # Verify user is now enabled
    user_state = await cr_post(session, f"{backend_url}/api/connectors/{connector_id}/execute", {
        "action": "get_user",
        "parameters": {"username": TEST_USERNAME},
    })
    assert user_state.get("user", {}).get("enabled", False), "User should be enabled after reconstitute"
    log("Reconstitution verified")

    # Cleanup
    try:
        await cr_post(session, f"{backend_url}/api/connectors/{connector_id}/execute", {
            "action": "delete_user",
            "parameters": {"username": TEST_USERNAME},
        })
    except Exception:
        pass
    # Clean up S3 artifacts
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=snapshot_prefix):
            for obj in page.get("Contents", []):
                s3.delete_object(Bucket=S3_BUCKET, Key=obj["Key"])
    except Exception as exc:
        log(f"S3 cleanup warning: {exc}")
    await cr_delete(session, f"{backend_url}/api/connectors/{connector_id}")
    await terminate_instance(aws_session, freeipa_instance_id)
    log("IDENTITY_SNAPSHOT: PASS")
```

- [ ] **Step 2: Register in dispatch table**

Add `"IDENTITY_SNAPSHOT": run_phase_identity_snapshot` to PHASES dict.

- [ ] **Step 3: Verify syntax**

```
docker exec nexplane-backend-1 python3 -c "
import ast
with open('/app/tests/smoke/test_aws_live.py', 'rb') as f:
    src = f.read().lstrip(b'\\xef\\xbb\\xbf')
ast.parse(src)
print('syntax OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "smoke: add IDENTITY_SNAPSHOT phase (snapshot to S3, corruption detection, reconstitute)"
```

---

## Self-Review

**Spec coverage check:**

| Spec Section | Plan Task |
|---|---|
| identity_profiles + identity_accounts tables | Task 1 |
| Alembic migration | Task 1 |
| parent_change_request_id on ChangeRequest | Task 1 |
| ChangeType enum additions | Task 1 |
| FAN_OUT_ACTIONS registry | Task 2 |
| Sync engine (IdP-primary + email fallback) | Task 3 |
| Scheduled sync (APScheduler 4h) | Task 9 |
| Pre-state snapshot per connector | Task 4 |
| Per-connector rollback restore | Task 4 |
| Fan-out spawns child CRs, auto-approval | Task 5 |
| Fan-out wired into CR execution | Task 6 |
| identity_snapshot executor | Task 7 |
| identity_snapshot CTD | Task 7 |
| identity_reconstitute executor | Task 8 |
| identity_reconstitute CTD | Task 8 |
| GET /identity/profiles | Task 9 |
| GET /identity/profiles/{id} | Task 9 |
| POST /identity/sync | Task 9 |
| IDENTITY_SYNC smoke phase | Task 10 |
| IDENTITY_FANOUT smoke phase | Task 11 |
| IDENTITY_SNAPSHOT smoke phase | Task 12 |

**Event-triggered sync (after fan-out CR completes):** Partially covered by fan-out executor design — a follow-up enhancement can add a hook in the CR completion path to call `sync_connector_accounts` for affected accounts. Not a blocker for Phase 5 ship. Noted for backlog.

**Stale account exclusion from fan-out:** Implemented in Task 5 fan_out_executor.py — `accounts = [a for a in profile.accounts if not a.is_stale]`.

**Orphan safety in reconstitute:** Implemented in Task 8 — orphaned accounts are classified but never deleted.

**Operator approval gate in reconstitute:** Implemented via `dry_run=True` default — operator must re-submit with `dry_run=False` to execute.

**Type consistency verified:** `build_pre_state_from_raw` used in both get_account_state.py and restore_account_state.py via import. `FAN_OUT_ACTIONS`/`FAN_OUT_CHANGE_TYPES`/`get_fan_out_action`/`is_fan_out_change_type` consistent across Task 2 and Task 6.
