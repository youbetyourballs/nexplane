# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from app.services.planning_engine import _validate_credential_rotation_fanout_fields

def test_missing_search_terms_raises():
    with pytest.raises(ValueError, match="search_terms"):
        _validate_credential_rotation_fanout_fields({"scan_scope": ["aws"], "new_value": "x"})

def test_empty_search_terms_raises():
    with pytest.raises(ValueError, match="search_terms"):
        _validate_credential_rotation_fanout_fields({"search_terms": [], "scan_scope": ["aws"], "new_value": "x"})

def test_missing_scan_scope_raises():
    with pytest.raises(ValueError, match="scan_scope"):
        _validate_credential_rotation_fanout_fields({"search_terms": ["old"], "new_value": "x"})

def test_invalid_scan_scope_raises():
    with pytest.raises(ValueError, match="Invalid scan_scope"):
        _validate_credential_rotation_fanout_fields({"search_terms": ["old"], "scan_scope": ["invalid"], "new_value": "x"})

def test_missing_new_value_without_rotate_raises():
    with pytest.raises(ValueError, match="new_value"):
        _validate_credential_rotation_fanout_fields({"search_terms": ["old"], "scan_scope": ["aws"]})

def test_new_value_not_required_when_rotate_present():
    # Should not raise
    _validate_credential_rotation_fanout_fields({
        "search_terms": ["old"], "scan_scope": ["aws"],
        "rotate": {"connector_type": "aws", "action_id": "rotate_secrets_manager_secret", "params": {}}
    })

def test_valid_desired_outcome_passes():
    _validate_credential_rotation_fanout_fields({
        "search_terms": ["old-pass"], "scan_scope": ["aws", "kubernetes"], "new_value": "new-pass"
    })
