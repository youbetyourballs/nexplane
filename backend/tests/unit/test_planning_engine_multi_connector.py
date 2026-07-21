# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Verify per-step connector resolution from asset.connectors."""
import pytest
from unittest.mock import MagicMock
from app.services.planning_engine import _resolve_step


def _make_connector(ctype, cid="aaaaaaaa-0000-0000-0000-000000000001"):
    c = MagicMock()
    c.id = __import__("uuid").UUID(cid)
    c.connector_type = MagicMock()
    c.connector_type.value = ctype
    return c


def _make_asset(connectors):
    a = MagicMock()
    a.connector_id = connectors[0].id if connectors else None
    a.connector = connectors[0] if connectors else None
    a.connectors = connectors
    a.asset_type = MagicMock()
    a.asset_type.value = "server"
    return a


def test_picks_aws_connector_for_aws_step(monkeypatch):
    aws_conn = _make_connector("aws", "aaaaaaaa-0000-0000-0000-000000000001")
    agent_conn = _make_connector("nexplane_agent", "bbbbbbbb-0000-0000-0000-000000000002")
    asset = _make_asset([agent_conn, aws_conn])

    catalog = MagicMock()
    aws_option = MagicMock()
    aws_option.connector_type = "aws"
    aws_option.execution_tier = 1
    aws_option.action_id = "server_snapshot"
    aws_option.name = "EBS Snapshot"
    aws_option.description = "Take EBS snapshot"
    aws_option.parameters = []
    aws_option.rollback_action = None
    aws_option.rollback_warning = None
    aws_option.estimated_duration = 60
    aws_option.blast_radius_hint = "low"
    catalog.get_options_for_action.return_value = [aws_option]

    step_def = {"generic_action": "server_snapshot"}
    result = _resolve_step(step_def, 1, {}, [asset], catalog)

    assert result["connector_type"] == "aws"
    assert str(result["connector_id"]) == "aaaaaaaa-0000-0000-0000-000000000001"
