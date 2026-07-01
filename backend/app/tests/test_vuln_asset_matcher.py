# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest

from app.models.asset import Asset, AssetType, Environment, Criticality
from app.services.vuln_asset_matcher import match_asset, blast_radius_query


@pytest.mark.asyncio
async def test_match_asset_by_ip(db_session, test_org, test_asset_with_ip):
    """Asset with matching IP should be returned."""
    matched_id = await match_asset(
        db_session,
        test_org.id,
        ip="10.0.1.50",
        hostname=None,
    )
    assert matched_id == test_asset_with_ip.id


@pytest.mark.asyncio
async def test_match_asset_by_hostname(db_session, test_org, test_asset_with_hostname):
    matched_id = await match_asset(
        db_session,
        test_org.id,
        ip=None,
        hostname="web-prod-01",
    )
    assert matched_id == test_asset_with_hostname.id


@pytest.mark.asyncio
async def test_match_asset_ip_takes_precedence(db_session, test_org, test_asset_with_ip):
    """IP match wins when both IP and hostname provided."""
    matched_id = await match_asset(
        db_session,
        test_org.id,
        ip="10.0.1.50",
        hostname="no-such-host",
    )
    assert matched_id == test_asset_with_ip.id


@pytest.mark.asyncio
async def test_match_asset_no_match_returns_none(db_session, test_org):
    matched_id = await match_asset(
        db_session,
        test_org.id,
        ip="192.168.99.99",
        hostname="unknown-host",
    )
    assert matched_id is None


@pytest.mark.asyncio
async def test_blast_radius_query_finds_affected_assets(db_session, test_org, test_asset_with_software_metadata):
    """Assets with matching package/version in asset_metadata should appear."""
    from app.models.vulnerability import VulnerabilityFinding
    # Insert a finding for this CVE so blast_radius_query can look it up
    finding = VulnerabilityFinding(
        organization_id=test_org.id,
        scanner="qualys",
        scanner_finding_id=f"QID-blast-{uuid.uuid4().hex[:8]}",
        source="webhook",
        finding_type="cve",
        severity="critical",
        cve_id="CVE-2024-1234",
        title="OpenSSL vuln",
        affected_package="openssl",
        affected_version="3.0.2",
        fixed_version="3.0.7",
    )
    db_session.add(finding)
    await db_session.flush()

    affected, fixed_version = await blast_radius_query(
        db_session,
        test_org.id,
        cve_id="CVE-2024-1234",
    )
    assert len(affected) >= 1
    assert any(a.asset_id == test_asset_with_software_metadata.id for a in affected)


@pytest.mark.asyncio
async def test_blast_radius_returns_empty_for_unknown_cve(db_session, test_org):
    affected, fixed_version = await blast_radius_query(
        db_session,
        test_org.id,
        cve_id="CVE-0000-0000",
    )
    assert affected == []
    assert fixed_version is None
