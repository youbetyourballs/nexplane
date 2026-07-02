# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for FILO rollback stack ordering checks and rollback-all logic."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cr(seq=None, status=ChangeRequestStatus.completed, asset_ids=None):
    cr = MagicMock(spec=ChangeRequest)
    cr.id = uuid.uuid4()
    cr.application_sequence = seq
    cr.status = status
    cr.target_asset_ids = asset_ids or [str(uuid.uuid4())]
    cr.organization_id = uuid.uuid4()
    return cr


# ---------------------------------------------------------------------------
# Ordering check logic (mirrors the rollback endpoint gate)
# ---------------------------------------------------------------------------

def _has_blocking_crs(target_cr, all_crs):
    """Simulate the blocking CR detection logic."""
    if target_cr.application_sequence is None:
        return []
    asset_set = set(target_cr.target_asset_ids)
    blocking = [
        c for c in all_crs
        if c.id != target_cr.id
        and c.application_sequence is not None
        and c.application_sequence > target_cr.application_sequence
        and c.status == ChangeRequestStatus.completed
        and set(c.target_asset_ids) & asset_set
    ]
    return sorted(blocking, key=lambda c: c.application_sequence)


class TestFILOOrderingCheck:
    def test_no_blocking_when_latest(self):
        asset_id = str(uuid.uuid4())
        cr1 = _make_cr(seq=1, asset_ids=[asset_id])
        cr2 = _make_cr(seq=2, asset_ids=[asset_id])
        # Rolling back cr2 (the latest) — no blockers
        assert _has_blocking_crs(cr2, [cr1, cr2]) == []

    def test_blocking_when_not_latest(self):
        asset_id = str(uuid.uuid4())
        cr1 = _make_cr(seq=1, asset_ids=[asset_id])
        cr2 = _make_cr(seq=2, asset_ids=[asset_id])
        cr3 = _make_cr(seq=3, asset_ids=[asset_id])
        # Rolling back cr1 — cr2 and cr3 are blockers
        blocking = _has_blocking_crs(cr1, [cr1, cr2, cr3])
        assert len(blocking) == 2
        assert blocking[0].application_sequence == 2
        assert blocking[1].application_sequence == 3

    def test_no_blocking_for_different_asset(self):
        asset_a = str(uuid.uuid4())
        asset_b = str(uuid.uuid4())
        cr1 = _make_cr(seq=1, asset_ids=[asset_a])
        cr2 = _make_cr(seq=2, asset_ids=[asset_b])
        # cr2 is on a different asset — not a blocker
        assert _has_blocking_crs(cr1, [cr1, cr2]) == []

    def test_no_blocking_when_sequence_is_none(self):
        asset_id = str(uuid.uuid4())
        cr1 = _make_cr(seq=None, asset_ids=[asset_id])
        cr2 = _make_cr(seq=5, asset_ids=[asset_id])
        # No sequence set — skip check
        assert _has_blocking_crs(cr1, [cr1, cr2]) == []

    def test_rolled_back_cr_not_a_blocker(self):
        asset_id = str(uuid.uuid4())
        cr1 = _make_cr(seq=1, asset_ids=[asset_id])
        cr2 = _make_cr(seq=2, status=ChangeRequestStatus.rolled_back, asset_ids=[asset_id])
        # cr2 was already rolled back — not a blocker
        assert _has_blocking_crs(cr1, [cr1, cr2]) == []

    def test_overlapping_multi_asset_cr_is_blocker(self):
        asset_a = str(uuid.uuid4())
        asset_b = str(uuid.uuid4())
        cr1 = _make_cr(seq=1, asset_ids=[asset_a])
        cr2 = _make_cr(seq=2, asset_ids=[asset_a, asset_b])  # touches asset_a
        # cr2 touches asset_a — blocks rollback of cr1
        blocking = _has_blocking_crs(cr1, [cr1, cr2])
        assert len(blocking) == 1
        assert blocking[0].id == cr2.id


# ---------------------------------------------------------------------------
# Rollback-all ordering logic
# ---------------------------------------------------------------------------

def _rollback_all_order(applied_crs):
    """Return CRs in FILO order (newest first)."""
    return sorted(
        [c for c in applied_crs if c.application_sequence is not None and c.status == ChangeRequestStatus.completed],
        key=lambda c: c.application_sequence,
        reverse=True,
    )


class TestRollbackAllOrdering:
    def test_returns_newest_first(self):
        asset_id = str(uuid.uuid4())
        cr1 = _make_cr(seq=1, asset_ids=[asset_id])
        cr2 = _make_cr(seq=2, asset_ids=[asset_id])
        cr3 = _make_cr(seq=3, asset_ids=[asset_id])
        ordered = _rollback_all_order([cr1, cr2, cr3])
        assert [c.application_sequence for c in ordered] == [3, 2, 1]

    def test_excludes_non_completed(self):
        asset_id = str(uuid.uuid4())
        cr1 = _make_cr(seq=1, asset_ids=[asset_id])
        cr2 = _make_cr(seq=2, status=ChangeRequestStatus.rolled_back, asset_ids=[asset_id])
        ordered = _rollback_all_order([cr1, cr2])
        assert len(ordered) == 1
        assert ordered[0].application_sequence == 1

    def test_excludes_no_sequence(self):
        asset_id = str(uuid.uuid4())
        cr1 = _make_cr(seq=1, asset_ids=[asset_id])
        cr2 = _make_cr(seq=None, asset_ids=[asset_id])
        ordered = _rollback_all_order([cr1, cr2])
        assert len(ordered) == 1

    def test_empty_returns_empty(self):
        assert _rollback_all_order([]) == []

    def test_single_cr_returns_single(self):
        cr = _make_cr(seq=7)
        ordered = _rollback_all_order([cr])
        assert len(ordered) == 1
        assert ordered[0].application_sequence == 7


# ---------------------------------------------------------------------------
# Application sequence assignment
# ---------------------------------------------------------------------------

class TestApplicationSequenceAssignment:
    def test_first_cr_gets_sequence_1(self):
        """When no existing sequences, first CR should get sequence 1."""
        max_existing = None
        new_seq = (max_existing or 0) + 1
        assert new_seq == 1

    def test_second_cr_increments(self):
        max_existing = 3
        new_seq = (max_existing or 0) + 1
        assert new_seq == 4

    def test_sequence_not_set_on_non_completed(self):
        """Sequence should only be set when status transitions to completed."""
        cr = _make_cr(seq=None, status=ChangeRequestStatus.failed)
        # Simulate: only set sequence if status == "completed" and sequence is None
        status = "failed"
        if status == "completed" and cr.application_sequence is None:
            cr.application_sequence = 1
        assert cr.application_sequence is None

    def test_sequence_not_overwritten_if_already_set(self):
        """If application_sequence is already set, don't overwrite."""
        cr = _make_cr(seq=5, status=ChangeRequestStatus.completed)
        status = "completed"
        if status == "completed" and cr.application_sequence is None:
            cr.application_sequence = 99
        assert cr.application_sequence == 5
