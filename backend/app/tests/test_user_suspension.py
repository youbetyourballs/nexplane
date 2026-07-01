# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def test_user_suspension_executor_exists():
    from app.connectors.executors.nexplane_agent.user_suspension import execute, rollback
    assert callable(execute)
    assert callable(rollback)
