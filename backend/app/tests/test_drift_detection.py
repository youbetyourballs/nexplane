# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Tests for drift detection logic.
"""
import pytest
from unittest.mock import MagicMock


def test_detect_drift_returns_failing_non_skipped_controls():
    from app.compliance.drift import detect_drift

    baseline = MagicMock()
    baseline.config = {
        "overrides": {
            "5.2.9": "skip",   # PasswordAuthentication — skip for this baseline
        }
    }
    audit_result = {
        "controls": [
            {"id": "5.2.4", "status": "fail"},   # should be drifted
            {"id": "5.2.9", "status": "fail"},   # skipped in baseline — not drifted
            {"id": "3.1.1", "status": "pass"},   # passing — not drifted
            {"id": "1.6.1.1", "status": "fail"}, # should be drifted
        ]
    }
    drifted = detect_drift(audit_result, baseline)
    assert set(drifted) == {"5.2.4", "1.6.1.1"}
    assert "5.2.9" not in drifted


def test_detect_drift_no_overrides():
    from app.compliance.drift import detect_drift

    baseline = MagicMock()
    baseline.config = {}
    audit_result = {
        "controls": [
            {"id": "3.1.1", "status": "fail"},
            {"id": "3.1.2", "status": "pass"},
        ]
    }
    drifted = detect_drift(audit_result, baseline)
    assert drifted == ["3.1.1"]


def test_detect_drift_all_pass():
    from app.compliance.drift import detect_drift

    baseline = MagicMock()
    baseline.config = {}
    audit_result = {
        "controls": [
            {"id": "3.1.1", "status": "pass"},
            {"id": "3.1.2", "status": "pass"},
        ]
    }
    drifted = detect_drift(audit_result, baseline)
    assert drifted == []


def test_write_cis_score_to_metadata_keeps_history():
    from app.compliance.drift import _build_updated_metadata

    existing = {
        "cis_compliance": {
            "latest": {"score": 0.6, "level": 1, "collected_at": "2026-04-01T00:00:00Z"},
            "history": [
                {"score": 0.5, "level": 1, "collected_at": "2026-03-01T00:00:00Z"},
            ],
        }
    }
    audit_result = {"score": 0.74, "level": 2, "collected_at": "2026-05-03T00:00:00Z", "controls": []}
    updated = _build_updated_metadata(existing, audit_result)
    assert updated["cis_compliance"]["latest"]["score"] == 0.74
    assert len(updated["cis_compliance"]["history"]) == 2
    assert updated["cis_compliance"]["history"][-1]["score"] == 0.6


def test_write_cis_score_caps_history_at_30():
    from app.compliance.drift import _build_updated_metadata

    history = [{"score": 0.5, "level": 1, "collected_at": "t"} for _ in range(30)]
    existing = {
        "cis_compliance": {
            "latest": {"score": 0.6, "level": 1, "collected_at": "t"},
            "history": history,
        }
    }
    audit_result = {"score": 0.9, "level": 1, "collected_at": "t", "controls": []}
    updated = _build_updated_metadata(existing, audit_result)
    assert len(updated["cis_compliance"]["history"]) == 30


# ---------------------------------------------------------------------------
# Phase 4: PolicyBaseline / DriftAlert model tests
# ---------------------------------------------------------------------------

def test_drift_alert_model_exists():
    from app.models.policy_baseline import DriftAlert
    assert hasattr(DriftAlert, 'new_behaviors')
    assert hasattr(DriftAlert, 'status')
    assert hasattr(DriftAlert, 'detected_at')


def test_drift_check_worker_importable():
    from app.workers.drift_check_worker import check_policy_drift
    assert callable(check_policy_drift)
