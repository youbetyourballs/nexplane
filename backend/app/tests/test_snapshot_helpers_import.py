# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def test_snapshot_helpers_importable():
    from app.connectors.executors.nexplane_agent._snapshot_helpers import (
        _take_snapshot,
        _restore_snapshot,
        _get_aws_creds,
        _make_ec2_client,
    )
    assert callable(_take_snapshot)
    assert callable(_restore_snapshot)
    assert callable(_get_aws_creds)
    assert callable(_make_ec2_client)


def test_os_upgrade_still_imports():
    import app.connectors.executors.nexplane_agent.os_upgrade as m
    assert hasattr(m, "_take_snapshot")
    assert hasattr(m, "_restore_snapshot")
