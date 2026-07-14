# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for the scan_for_references orchestrator."""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_cr(search_terms=None, connector_type="aws", connector_id=None, migration_context=None):
    mock_cr = MagicMock()
    mock_cr.id = uuid.uuid4()
    mock_cr.organization_id = uuid.uuid4()
    mock_cr.parameters = {
        "search_terms": search_terms or ["old-db.internal"],
        "migration_context": migration_context or {
            "source_term": "old-db.internal",
            "target_term": "new-db.internal",
            "notes": "test migration",
        },
        "connectors": [
            {
                "connector_type": connector_type,
                "connector_id": str(connector_id or uuid.uuid4()),
            }
        ],
    }
    return mock_cr


def _make_db(connector_found=True):
    mock_db = AsyncMock()
    mock_connector = MagicMock() if connector_found else None
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_connector
    mock_db.execute.return_value = mock_result
    return mock_db


def _make_hit(term="old-db.internal", surface="aws_lambda_env"):
    return {
        "surface": surface,
        "location": "arn:aws:lambda:us-east-1:123456789:function:my-fn",
        "matched_term": term,
        "snippet": f"DB_HOST={term}",
        "consumer_identity": {
            "stable_id": "arn:aws:lambda:us-east-1:123456789:function:my-fn",
            "hostname": None,
            "surface_metadata": {},
        },
    }


@pytest.mark.asyncio
async def test_happy_path_two_hits_one_confident_one_exception():
    """2 hits → 1 confident update + 1 exception → exception saved to DB."""
    from app.connectors.executors.reference.scan_orchestrator import orchestrate_scan
    from app.services.identity_resolution import IdentityResolutionResult
    from app.services.reference_triage import TriageResult

    cr = _make_cr()
    db = _make_db()
    mock_secrets = MagicMock()
    mock_settings = MagicMock()
    mock_settings.anthropic_api_key_encrypted = "enc"

    hit1 = _make_hit("old-db.internal", "aws_lambda_env")
    hit2 = _make_hit("old-db.internal", "aws_ecs_task_def")
    aws_hits = [hit1, hit2]

    resolved_asset_id = uuid.uuid4()
    triage_result = TriageResult(
        confident_updates=[
            {
                "hit_index": 0,
                "asset_id": str(resolved_asset_id),
                "location": hit1["location"],
                "surface": hit1["surface"],
                "old_value": "old-db.internal",
                "new_value": "new-db.internal",
                "change_type_params": {},
            }
        ],
        exceptions=[
            {
                "hit_index": 1,
                "asset_id": None,
                "reason": "ambiguous substitution in task definition",
                "suggested_action": "manual review",
                "confidence": 0.4,
            }
        ],
    )

    with patch(
        "app.connectors.executors.reference.scan_orchestrator._run_aws_scans",
        new_callable=AsyncMock,
        return_value=aws_hits,
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator._run_k8s_scans",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator._run_agent_scans",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator.resolve_consumer_identity",
        new_callable=AsyncMock,
        return_value=IdentityResolutionResult(
            asset_id=resolved_asset_id, tier=1, confidence=0.99, new_asset_data=None
        ),
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator.triage_scan_hits",
        new_callable=AsyncMock,
        return_value=triage_result,
    ):
        result = await orchestrate_scan(cr, db, mock_secrets, mock_settings)

    assert result["hits_total"] == 2
    assert result["exceptions_created"] == 1
    assert len(result["confident_updates"]) == 1
    assert result["assets_registered"] == 0
    # Verify ScanException was added to DB
    db.add.assert_called()
    db.commit.assert_called()


@pytest.mark.asyncio
async def test_empty_hits_returns_zeros():
    """When no connector returns hits, all counts are zero."""
    from app.connectors.executors.reference.scan_orchestrator import orchestrate_scan
    from app.services.reference_triage import TriageResult

    cr = _make_cr()
    db = _make_db()
    mock_secrets = MagicMock()
    mock_settings = MagicMock()

    with patch(
        "app.connectors.executors.reference.scan_orchestrator._run_aws_scans",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator._run_k8s_scans",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator._run_agent_scans",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator.triage_scan_hits",
        new_callable=AsyncMock,
        return_value=TriageResult(confident_updates=[], exceptions=[]),
    ):
        result = await orchestrate_scan(cr, db, mock_secrets, mock_settings)

    assert result["hits_total"] == 0
    assert result["exceptions_created"] == 0
    assert result["confident_updates"] == []
    assert result["assets_registered"] == 0


@pytest.mark.asyncio
async def test_triage_parse_failure_all_hits_fall_to_exceptions():
    """When AI triage fails to parse, all hits should be in exceptions."""
    from app.connectors.executors.reference.scan_orchestrator import orchestrate_scan
    from app.services.identity_resolution import IdentityResolutionResult
    from app.services.reference_triage import TriageResult

    cr = _make_cr()
    db = _make_db()
    mock_secrets = MagicMock()
    mock_settings = MagicMock()

    hit1 = _make_hit("old-db.internal", "aws_lambda_env")
    hit2 = _make_hit("old-db.internal", "aws_ssm_parameter")
    hit3 = _make_hit("old-db.internal", "aws_rds_parameter_group")
    aws_hits = [hit1, hit2, hit3]

    # Simulate parse failure — all 3 hits fall to exceptions
    fallback_triage = TriageResult(
        confident_updates=[],
        exceptions=[
            {"hit_index": i, "asset_id": None, "reason": "AI triage failed to parse", "suggested_action": "manual review", "confidence": 0.0}
            for i in range(3)
        ],
    )

    with patch(
        "app.connectors.executors.reference.scan_orchestrator._run_aws_scans",
        new_callable=AsyncMock,
        return_value=aws_hits,
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator._run_k8s_scans",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator._run_agent_scans",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator.resolve_consumer_identity",
        new_callable=AsyncMock,
        return_value=IdentityResolutionResult(
            asset_id=None,
            tier=4,
            confidence=0.0,
            new_asset_data={
                "name": "unknown-fn",
                "asset_type": "application",
                "environment": "unknown",
                "asset_metadata": {},
            },
        ),
    ), patch(
        "app.connectors.executors.reference.scan_orchestrator.triage_scan_hits",
        new_callable=AsyncMock,
        return_value=fallback_triage,
    ):
        result = await orchestrate_scan(cr, db, mock_secrets, mock_settings)

    assert result["hits_total"] == 3
    assert result["exceptions_created"] == 3
    assert result["confident_updates"] == []
    # 3 assets auto-registered (tier 4 for each hit)
    assert result["assets_registered"] == 3
