# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeConnector:
    def __init__(self, creds=None):
        self.credentials = creds or {"server": "dc.example.com", "username": "admin", "password": "pass"}


def _make_mock_conn(modify_result=None):
    conn = MagicMock()
    conn.result = modify_result or {"result": 0, "description": "success"}
    return conn


class TestDisableAccountRollback(unittest.TestCase):
    """rollback() on disable_account should set UAC to 512 (re-enable)."""

    def test_rollback_calls_modify_with_uac_512(self):
        mock_conn = _make_mock_conn()
        with patch(
            "backend.app.connectors.executors.active_directory.disable_account.get_connection",
            return_value=mock_conn,
        ), patch(
            "backend.app.connectors.executors.active_directory.disable_account.prepare_ad_target",
            new=AsyncMock(return_value={"server": "dc.example.com"}),
        ):
            from backend.app.connectors.executors.active_directory import disable_account
            result = run(
                disable_account.rollback(
                    {"username": "bob"},
                    {"username": "bob", "user_dn": "CN=bob,DC=example,DC=com"},
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        from ldap3 import MODIFY_REPLACE
        mock_conn.modify.assert_called_once_with(
            "CN=bob,DC=example,DC=com",
            {"userAccountControl": [(MODIFY_REPLACE, [512])]},
        )

    def test_rollback_returns_false_on_ldap_error(self):
        mock_conn = _make_mock_conn()
        mock_conn.modify.side_effect = Exception("LDAP connection refused")
        with patch(
            "backend.app.connectors.executors.active_directory.disable_account.get_connection",
            return_value=mock_conn,
        ), patch(
            "backend.app.connectors.executors.active_directory.disable_account.prepare_ad_target",
            new=AsyncMock(return_value={"server": "dc.example.com"}),
        ):
            from backend.app.connectors.executors.active_directory import disable_account
            result = run(
                disable_account.rollback(
                    {"username": "bob"},
                    {"username": "bob", "user_dn": "CN=bob,DC=example,DC=com"},
                    FakeConnector(),
                )
            )
        self.assertFalse(result["rolled_back"])
        self.assertIn("error", result)

    def test_rollback_mock_when_no_creds(self):
        from backend.app.connectors.executors.active_directory import disable_account
        connector = FakeConnector(creds={})
        result = run(
            disable_account.rollback(
                {"username": "bob"},
                {"username": "bob", "user_dn": "CN=bob,DC=example,DC=com"},
                connector,
            )
        )
        self.assertTrue(result["rolled_back"])
        self.assertTrue(result.get("mock"))


class TestEnableAccountRollback(unittest.TestCase):
    """rollback() on enable_account should set UAC to 514 (re-disable)."""

    def test_rollback_calls_modify_with_uac_514(self):
        mock_conn = _make_mock_conn()
        with patch(
            "backend.app.connectors.executors.active_directory.enable_account.get_connection",
            return_value=mock_conn,
        ), patch(
            "backend.app.connectors.executors.active_directory.enable_account.prepare_ad_target",
            new=AsyncMock(return_value={"server": "dc.example.com"}),
        ):
            from backend.app.connectors.executors.active_directory import enable_account
            result = run(
                enable_account.rollback(
                    {"username": "alice"},
                    {"username": "alice", "user_dn": "CN=alice,DC=example,DC=com"},
                    FakeConnector(),
                )
            )
        self.assertTrue(result["rolled_back"])
        from ldap3 import MODIFY_REPLACE
        mock_conn.modify.assert_called_once_with(
            "CN=alice,DC=example,DC=com",
            {"userAccountControl": [(MODIFY_REPLACE, [514])]},
        )

    def test_rollback_returns_false_on_ldap_error(self):
        mock_conn = _make_mock_conn()
        mock_conn.modify.side_effect = Exception("Timeout")
        with patch(
            "backend.app.connectors.executors.active_directory.enable_account.get_connection",
            return_value=mock_conn,
        ), patch(
            "backend.app.connectors.executors.active_directory.enable_account.prepare_ad_target",
            new=AsyncMock(return_value={"server": "dc.example.com"}),
        ):
            from backend.app.connectors.executors.active_directory import enable_account
            result = run(
                enable_account.rollback(
                    {"username": "alice"},
                    {"username": "alice", "user_dn": "CN=alice,DC=example,DC=com"},
                    FakeConnector(),
                )
            )
        self.assertFalse(result["rolled_back"])
        self.assertIn("error", result)

    def test_rollback_mock_when_no_creds(self):
        from backend.app.connectors.executors.active_directory import enable_account
        connector = FakeConnector(creds={})
        result = run(
            enable_account.rollback(
                {"username": "alice"},
                {"username": "alice", "user_dn": "CN=alice,DC=example,DC=com"},
                connector,
            )
        )
        self.assertTrue(result["rolled_back"])
        self.assertTrue(result.get("mock"))


if __name__ == "__main__":
    unittest.main()
