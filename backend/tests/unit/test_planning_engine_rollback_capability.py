# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import types
import pytest


def _make_module(capability=None, reason=None, has_execute=True, has_rollback=True):
    """Build a minimal fake executor module."""
    mod = types.ModuleType("fake_executor")
    if has_execute:
        mod.execute = lambda: None
    if has_rollback:
        mod.rollback = lambda: None
    if capability is not None:
        mod.ROLLBACK_CAPABILITY = capability
    if reason is not None:
        mod.ROLLBACK_REASON = reason
    return mod


def _run_validation(module):
    """Call the planning engine capability validation logic directly."""
    from app.services.planning_engine import _validate_rollback_capability
    return _validate_rollback_capability(module, connector_type="aws", action_id="test_action")


def test_full_capability_returns_no_warning():
    mod = _make_module(capability="full")
    warning = _run_validation(mod)
    assert warning is None


def test_irreversible_capability_returns_warning_with_reason():
    mod = _make_module(capability="irreversible", reason="SSH command cannot be undone")
    warning = _run_validation(mod)
    assert warning is not None
    assert "SSH command cannot be undone" in warning


def test_missing_capability_raises_value_error():
    mod = _make_module()  # no ROLLBACK_CAPABILITY
    with pytest.raises(ValueError, match="ROLLBACK_CAPABILITY"):
        _run_validation(mod)


def test_invalid_capability_value_raises_value_error():
    mod = _make_module(capability="maybe")
    with pytest.raises(ValueError, match="ROLLBACK_CAPABILITY"):
        _run_validation(mod)


def test_irreversible_without_reason_raises_value_error():
    mod = _make_module(capability="irreversible")  # missing ROLLBACK_REASON
    with pytest.raises(ValueError, match="ROLLBACK_REASON"):
        _run_validation(mod)
