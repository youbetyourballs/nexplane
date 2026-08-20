# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from app.models.drift import ResourceState, DriftPolicy, DriftEvent


def test_models_importable():
    assert ResourceState.__tablename__ == "resource_states"
    assert DriftPolicy.__tablename__ == "drift_policies"
    assert DriftEvent.__tablename__ == "drift_events"
