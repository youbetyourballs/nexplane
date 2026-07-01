# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.models.connector import ConnectorType


def test_santa_sync_server_in_enum():
    assert ConnectorType.santa_sync_server.value == "santa_sync_server"
