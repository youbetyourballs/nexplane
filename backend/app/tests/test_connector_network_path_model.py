# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.models.connector import Connector

def test_connector_has_network_path_columns():
    cols = Connector.__table__.columns
    assert "network_path" in cols
    assert cols["network_path"].default.arg == "direct"
    assert "network_tls_skip_verify" in cols
    assert cols["network_tls_skip_verify"].default.arg is False
