# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Review campaign collection service.

Queries Asset table (populated by connector ingest) to extract access entries.
Each Identity asset's asset_metadata contains groups, roles, app_assignments
depending on the connector type.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import Asset, AssetType
from app.models.user import User
from app.models.review_campaign import ReviewCampaign, ReviewEntry

PRIVILEGED_TERMS = {
    "admin", "owner", "administrator", "domain admins", "schema admins",
    "global administrator", "privileged role administrator",
    "administratoraccess",
}

INACTIVE_THRESHOLD_DAYS = 90


def extract_entries_from_asset(asset) -> list[dict]:
    """Extract (user_email, resource_name, permission_level, is_privileged) tuples from an Identity asset."""
    meta = asset.asset_metadata or {}
    connector_type = asset.connector.connector_type.value if asset.connector else "unknown"
    user_email = meta.get("email") or asset.name
    user_display_name = meta.get("display_name") or meta.get("name") or asset.name
    user_status = _normalize_status(meta.get("status", "active"), connector_type)
    connector_id = asset.connector_id

    entries = []

    if connector_type == "okta":
        for group in meta.get("groups", []):
            entries.append(_make_entry(user_email, user_display_name, user_status, connector_id,
                                       connector_type, group, "member", _is_privileged(group)))
        for app in meta.get("app_assignments", []):
            app_name = app.get("app_name", "Unknown App")
            role = app.get("role", "user")
            entries.append(_make_entry(user_email, user_display_name, user_status, connector_id,
                                       connector_type, app_name, role,
                                       _is_privileged(role) or meta.get("is_admin", False)))

    elif connector_type == "active_directory":
        for group in meta.get("groups", []):
            entries.append(_make_entry(user_email, user_display_name, user_status, connector_id,
                                       connector_type, group, "member", _is_privileged(group)))

    elif connector_type == "google_workspace":
        for group in meta.get("groups", []):
            role = group.get("role", "member") if isinstance(group, dict) else "member"
            name = group.get("name", group) if isinstance(group, dict) else group
            entries.append(_make_entry(user_email, user_display_name, user_status, connector_id,
                                       connector_type, name, role, _is_privileged(role)))

    elif connector_type == "github":
        org_role = meta.get("role", "member")
        org_name = meta.get("org", "GitHub Organization")
        entries.append(_make_entry(user_email, user_display_name, user_status, connector_id,
                                   connector_type, org_name, org_role, _is_privileged(org_role)))
        for repo in meta.get("repo_permissions", []):
            entries.append(_make_entry(user_email, user_display_name, user_status, connector_id,
                                       connector_type, repo.get("repo", "repo"),
                                       repo.get("permission", "read"),
                                       _is_privileged(repo.get("permission", ""))))

    elif connector_type in ("entra_id", "azure"):
        for role in meta.get("assigned_roles", []):
            entries.append(_make_entry(user_email, user_display_name, user_status, connector_id,
                                       connector_type, role, "role assignment", _is_privileged(role)))
        for group in meta.get("groups", []):
            entries.append(_make_entry(user_email, user_display_name, user_status, connector_id,
                                       connector_type, group, "member", False))

    elif connector_type == "aws":
        for group in meta.get("iam_groups", []):
            entries.append(_make_entry(user_email, user_display_name, user_status, connector_id,
                                       connector_type, group, "iam_group_member", _is_privileged(group)))

    return entries


def _make_entry(user_email, user_display_name, user_status, connector_id,
                connector_type, resource_name, permission_level, is_privileged) -> dict:
    return {
        "user_email": user_email,
        "user_display_name": user_display_name,
        "user_status": user_status,
        "connector_id": connector_id,
        "resource_type": connector_type,
        "resource_name": resource_name,
        "permission_level": permission_level,
        "is_privileged": is_privileged,
    }


def _is_privileged(value: str) -> bool:
    return any(term in str(value).lower() for term in PRIVILEGED_TERMS)


def _normalize_status(raw: str, connector_type: str) -> str:
    raw = str(raw).lower()
    if raw in ("active", "enabled", "provisioned"):
        return "active"
    if raw in ("suspended", "deprovisioned", "locked_out"):
        return "suspended"
    if raw in ("inactive", "disabled", "deactivated"):
        return "disabled"
    return "active"


def enrich_entry(entry_data: dict, identity_asset, resource_asset, options: dict) -> dict:
    """Add evidence fields to an entry dict from asset_metadata."""
    evidence = {}
    meta = (identity_asset.asset_metadata or {}) if identity_asset else {}

    if options.get("include_last_login", True) or options.get("include_days_inactive", True):
        last_login_str = meta.get("last_login_at") or meta.get("last_sign_in")
        if last_login_str:
            try:
                last_login = datetime.fromisoformat(last_login_str.replace("Z", "+00:00"))
                days_inactive = (datetime.now(timezone.utc) - last_login).days
                evidence["last_login_at"] = last_login_str
                evidence["days_inactive"] = days_inactive
                evidence["flagged_inactive"] = days_inactive > INACTIVE_THRESHOLD_DAYS
            except (ValueError, TypeError):
                evidence["last_login_at"] = None
                evidence["days_inactive"] = None
                evidence["flagged_inactive"] = False
        else:
            evidence["last_login_at"] = None
            evidence["days_inactive"] = None
            evidence["flagged_inactive"] = False

    if options.get("include_asset_sensitivity", True) and resource_asset:
        evidence["asset_criticality"] = resource_asset.criticality.value if resource_asset.criticality else None
        evidence["asset_tags"] = resource_asset.tags or []
    else:
        evidence["asset_criticality"] = None
        evidence["asset_tags"] = []

    evidence["flagged_privileged"] = entry_data.get("is_privileged", False)
    return evidence


def resolve_reviewer(entry_data: dict, rule: dict,
                     manager_email: Optional[str],
                     owner_email: Optional[str]) -> tuple[Optional[str], bool]:
    """Returns (reviewer_ref, was_unresolved). reviewer_ref is email or UUID string."""
    rule_type = rule.get("type", "security_team")
    fallback = rule.get("fallback_reviewer_id")

    if rule_type == "security_team":
        return (fallback, False)
    elif rule_type == "manager_centric":
        return (manager_email, False) if manager_email else (fallback, True)
    elif rule_type == "resource_owner":
        return (owner_email, False) if owner_email else (fallback, True)
    return (fallback, True)


async def run_collection(campaign_id: uuid.UUID, db_factory) -> None:
    """Background task: collect entries, enrich, resolve reviewers, write ReviewEntry rows."""
    async with db_factory() as db:
        campaign = await db.get(ReviewCampaign, campaign_id)
        if not campaign:
            return

        try:
            scope = campaign.scope or {}
            options = campaign.evidence_options or {}
            rule = campaign.reviewer_assignment_rule or {}

            stmt = (
                select(Asset)
                .options(selectinload(Asset.connector))
                .where(
                    Asset.organization_id == campaign.organization_id,
                    Asset.asset_type == AssetType.identity,
                )
            )
            if scope.get("connector_ids"):
                ids = [uuid.UUID(c) for c in scope["connector_ids"]]
                stmt = stmt.where(Asset.connector_id.in_(ids))

            result = await db.execute(stmt)
            identity_assets = result.scalars().all()

            allowed_groups = set(scope.get("user_groups") or [])
            required_tags = set(scope.get("asset_tags") or [])

            for asset in identity_assets:
                if not asset.connector:
                    continue
                meta = asset.asset_metadata or {}
                status = _normalize_status(meta.get("status", "active"), "")
                if not scope.get("include_inactive_users", False) and status != "active":
                    continue

                asset_entries = extract_entries_from_asset(asset)
                if allowed_groups:
                    asset_entries = [e for e in asset_entries if e["resource_name"] in allowed_groups]

                for entry_data in asset_entries:
                    resource_asset = None
                    if required_tags or options.get("include_asset_sensitivity"):
                        res_q = await db.execute(
                            select(Asset).where(
                                Asset.organization_id == campaign.organization_id,
                                Asset.name == entry_data["resource_name"],
                            ).limit(1)
                        )
                        resource_asset = res_q.scalar_one_or_none()
                        if required_tags:
                            if resource_asset and not required_tags.issubset(set(resource_asset.tags or [])):
                                continue
                            elif not resource_asset:
                                continue

                    evidence = enrich_entry(entry_data, asset, resource_asset, options)

                    manager_email = meta.get("manager_email")
                    owner_email = None
                    if resource_asset:
                        for tag in (resource_asset.tags or []):
                            if tag.startswith("owner:"):
                                owner_email = tag.split(":", 1)[1]
                                break
                        if not owner_email:
                            owner_email = (resource_asset.asset_metadata or {}).get("owner")

                    reviewer_ref, unresolved = resolve_reviewer(entry_data, rule, manager_email, owner_email)

                    reviewer_uuid = None
                    if reviewer_ref:
                        try:
                            reviewer_uuid = uuid.UUID(reviewer_ref)
                        except ValueError:
                            user_q = await db.execute(
                                select(User).where(
                                    User.organization_id == campaign.organization_id,
                                    User.email == reviewer_ref,
                                )
                            )
                            u = user_q.scalar_one_or_none()
                            reviewer_uuid = u.id if u else None

                    entry = ReviewEntry(id=uuid.uuid4(), campaign_id=campaign_id)
                    entry.user_email = entry_data["user_email"]
                    entry.user_display_name = entry_data.get("user_display_name")
                    entry.user_status = entry_data.get("user_status", "active")
                    entry.resource_name = entry_data["resource_name"]
                    entry.resource_type = entry_data["resource_type"]
                    entry.connector_id = entry_data.get("connector_id")
                    entry.permission_level = entry_data["permission_level"]
                    entry.is_privileged = entry_data.get("is_privileged", False)
                    entry.evidence = evidence
                    entry.reviewer_id = reviewer_uuid
                    entry.reviewer_unresolved = unresolved
                    db.add(entry)

            campaign.status = "in_review"
            campaign.error_message = None
            await db.commit()

        except Exception as exc:
            campaign.status = "draft"
            campaign.error_message = str(exc)[:500]
            await db.commit()
            raise
