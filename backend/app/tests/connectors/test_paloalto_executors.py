# SMOKE: requires live PaloAlto firewall instance
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import MagicMock, patch


def _make_connector(creds=None):
    c = MagicMock()
    c.credentials = creds or {"hostname": "10.0.0.1", "username": "admin", "password": "s3cr3t"}
    return c


class TestAnalyzeFlows:
    @pytest.mark.asyncio
    async def test_returns_real_flow_count_not_hardcoded_42(self):
        mock_fw = MagicMock()
        mock_fw.op.return_value = MagicMock(text='<response><result><log><logs count="7"></logs></log></result></response>')
        with patch("app.connectors.executors.paloalto.analyze_flows._get_firewall", return_value=mock_fw):
            from app.connectors.executors.paloalto.analyze_flows import execute
            result = await execute({}, ["asset-1"], _make_connector())
        assert result["flows_analyzed"] == 7
        assert result.get("flows_analyzed") != 42

    @pytest.mark.asyncio
    async def test_no_connector_raises_or_returns_error(self):
        from app.connectors.executors.paloalto.analyze_flows import execute
        c = MagicMock()
        c.credentials = {}
        result = await execute({}, ["asset-1"], c)
        assert result.get("status") == "error"


class TestValidateStaged:
    @pytest.mark.asyncio
    async def test_validate_staged_no_credentials(self):
        from app.connectors.executors.paloalto.validate_staged import execute
        c = MagicMock()
        c.credentials = {}
        result = await execute({}, ["a"], c)
        assert result.get("status") == "error"

    @pytest.mark.asyncio
    async def test_returns_false_when_blocking_rule_found(self):
        mock_fw = MagicMock()
        mock_fw.op.return_value = MagicMock(
            text='<response><result><config><security><rules><entry name="block-all">'
                 '<action>deny</action><from><member>trust</member></from>'
                 '<to><member>untrust</member></to></entry></rules></security></config></result></response>'
        )
        with patch("app.connectors.executors.paloalto.validate_staged._get_firewall", return_value=mock_fw):
            from app.connectors.executors.paloalto.validate_staged import execute
            result = await execute(
                {"source_zone": "trust", "destination_zone": "untrust"},
                ["asset-1"], _make_connector()
            )
        assert result["no_critical_flows_blocked"] is False

    @pytest.mark.asyncio
    async def test_returns_true_when_no_blocking_rule(self):
        mock_fw = MagicMock()
        mock_fw.op.return_value = MagicMock(text='<response><result><config><security><rules/></security></config></result></response>')
        with patch("app.connectors.executors.paloalto.validate_staged._get_firewall", return_value=mock_fw):
            from app.connectors.executors.paloalto.validate_staged import execute
            result = await execute({"source_zone": "trust", "destination_zone": "untrust"}, ["asset-1"], _make_connector())
        assert result["no_critical_flows_blocked"] is True


class TestRemoveStagedPolicy:
    @pytest.mark.asyncio
    async def test_remove_staged_policy_no_credentials(self):
        from app.connectors.executors.paloalto.remove_staged_policy import execute
        c = MagicMock()
        c.credentials = {}
        result = await execute({}, ["a"], c)
        assert result.get("status") == "error"

    @pytest.mark.asyncio
    async def test_calls_delete_on_firewall_not_fake(self):
        mock_fw = MagicMock()
        mock_fw.op.return_value = MagicMock(text='<response status="success"/>')
        with patch("app.connectors.executors.paloalto.remove_staged_policy._get_firewall", return_value=mock_fw):
            from app.connectors.executors.paloalto.remove_staged_policy import execute
            result = await execute({"policy_name": "test-policy"}, ["asset-1"], _make_connector())
        mock_fw.op.assert_called()
        assert result.get("removed") is True

    @pytest.mark.asyncio
    async def test_returns_error_when_policy_not_found(self):
        mock_fw = MagicMock()
        mock_fw.op.side_effect = Exception("Object not found")
        with patch("app.connectors.executors.paloalto.remove_staged_policy._get_firewall", return_value=mock_fw):
            from app.connectors.executors.paloalto.remove_staged_policy import execute
            result = await execute({"policy_name": "nonexistent"}, ["asset-1"], _make_connector())
        assert result.get("status") == "error"
