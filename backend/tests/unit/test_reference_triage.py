# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

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


@pytest.mark.asyncio
async def test_triage_empty_hits_returns_empty_result():
    mock_settings = MagicMock()
    mock_secrets = MagicMock()

    result = await triage_scan_hits(
        hits=[],
        migration_context=MIGRATION_CONTEXT,
        settings=mock_settings,
        secrets_svc=mock_secrets,
    )

    assert isinstance(result, TriageResult)
    assert result.confident_updates == []
    assert result.exceptions == []


@pytest.mark.asyncio
async def test_triage_json_parse_failure_falls_all_to_exceptions():
    mock_settings = MagicMock()
    mock_settings.anthropic_api_key_encrypted = "encrypted_key"
    mock_secrets = MagicMock()
    mock_secrets.decrypt.return_value = "sk-test"

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="not valid json at all")]

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
    assert result.confident_updates == []
    assert len(result.exceptions) == len(SAMPLE_HITS)
    assert result.exceptions[0]["reason"] == "AI triage failed to parse"
