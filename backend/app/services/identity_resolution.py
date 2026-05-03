"""
Identity resolution service.

Given a target email, queries the assets table across all connectors in the
tenant to find matching user accounts. Each connector type stores the email
in a different JSONB metadata key; this module encodes that mapping.
"""
import uuid
from sqlalchemy import select, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.connector import Connector


# Maps connector_type -> list of JSONB path expressions for the email field.
_EMAIL_PATHS: dict[str, list[str]] = {
    "active_directory": [
        "metadata->>'mail'",
        "metadata->>'userPrincipalName'",
    ],
    "okta": [
        "metadata->>'login'",
        "metadata->>'email'",
    ],
    "entra_id": [
        "metadata->>'userPrincipalName'",
        "metadata->>'mail'",
    ],
    "google_workspace": [
        "metadata->>'primaryEmail'",
    ],
    "github": [
        "metadata->>'email'",
    ],
    "slack": [
        "metadata->'profile'->>'email'",
    ],
    "crowdstrike": [
        "metadata->>'last_logged_in_user'",
    ],
}


def _email_filter_for_connector(connector_type: str, email: str):
    """Return a SQLAlchemy text clause that matches the email for the given connector type,
    or None if the connector type is unknown."""
    paths = _EMAIL_PATHS.get(connector_type)
    if not paths:
        return None
    clauses = [text(f"({path} = :email)") for path in paths]
    return or_(*clauses)


async def resolve_user_across_connectors(
    db: AsyncSession,
    target_email: str,
    organization_id: uuid.UUID,
) -> list[dict]:
    """
    Returns a list of dicts with keys:
      connector_id, connector_type, asset_id, display_name, account_status
    for every connector in the organization that has an asset matching target_email.
    """
    # Fetch all active connectors for the org
    conn_result = await db.execute(
        select(Connector).where(Connector.organization_id == organization_id)
    )
    connectors = conn_result.scalars().all()

    results = []
    for connector in connectors:
        filter_clause = _email_filter_for_connector(connector.connector_type, target_email)
        if filter_clause is None:
            continue
        asset_result = await db.execute(
            select(Asset).where(
                Asset.connector_id == connector.id,
                Asset.organization_id == organization_id,
                filter_clause.bindparams(email=target_email),
            )
        )
        assets = asset_result.scalars().all()
        for asset in assets:
            results.append({
                "connector_id": connector.id,
                "connector_type": connector.connector_type,
                "asset_id": asset.id,
                "display_name": asset.name,
                "account_status": asset.asset_metadata.get("status", "unknown"),
            })

    return results
