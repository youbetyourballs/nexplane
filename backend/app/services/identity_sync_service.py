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
    organization_id=None,
) -> dict:
    """
    Correlate a list of discovered accounts into identity_profiles/identity_accounts.
    """
    now = datetime.now(timezone.utc)
    is_idp = connector_type in IDP_CONNECTOR_TYPES

    # Load existing profiles by email for fast lookup (scoped to org if provided)
    profiles_by_email: dict[str, IdentityProfile] = {}
    q = select(IdentityProfile)
    if organization_id is not None:
        q = q.where(IdentityProfile.organization_id == organization_id)
    result = await db.execute(q)
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
                organization_id=organization_id,
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
                organization_id=organization_id,
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
        org_id = getattr(connector, "organization_id", None)
        try:
            from app.services.connector_service import execute_action
            result = await execute_action(connector, "discover_users", {})
            accounts = result.get("users") or result.get("accounts") or []
            stats = await sync_connector_accounts(db, connector.id, connector_type, accounts, organization_id=org_id)
            total["created_profiles"] += stats["created_profiles"]
            total["upserted_accounts"] += stats["upserted_accounts"]
            total["connectors_synced"] += 1
        except Exception as exc:
            logger.warning("identity sync failed for connector %s: %s", connector.id, exc)

    return total
