import pytest
import uuid
from unittest.mock import MagicMock, patch, AsyncMock

from app.services.project_rollback_service import (
    _get_permanent_types,
    _find_backup_cr,
    _build_preflight_warnings,
    RECONSTITUTION_PAIRS,
)
from app.models.change_request import ChangeRequestStatus


def _make_cr(change_type: str, status=ChangeRequestStatus.completed, assets=None):
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.change_type = change_type
    cr.status = status
    cr.target_asset_ids = assets or ["asset-1"]
    return cr


def test_reconstitution_pairs_covers_known_permanent_types():
    assert "rotate_iam_key" in RECONSTITUTION_PAIRS
    assert "ec2_terminate" in RECONSTITUTION_PAIRS
    assert "rds_instance_delete" in RECONSTITUTION_PAIRS


def test_find_backup_cr_found():
    permanent_cr = _make_cr("rotate_iam_key", assets=["asset-1"])
    backup_cr = _make_cr("create_backup", assets=["asset-1"])
    completed_crs = [backup_cr, permanent_cr]

    result = _find_backup_cr(permanent_cr, completed_crs)
    assert result is not None
    assert result.id == backup_cr.id


def test_find_backup_cr_wrong_asset():
    permanent_cr = _make_cr("rotate_iam_key", assets=["asset-1"])
    backup_cr = _make_cr("create_backup", assets=["asset-2"])  # different asset
    completed_crs = [backup_cr, permanent_cr]

    result = _find_backup_cr(permanent_cr, completed_crs)
    assert result is None


def test_find_backup_cr_not_in_pairs():
    permanent_cr = _make_cr("some_unknown_type", assets=["asset-1"])
    backup_cr = _make_cr("create_backup", assets=["asset-1"])
    completed_crs = [backup_cr, permanent_cr]

    result = _find_backup_cr(permanent_cr, completed_crs)
    assert result is None


def test_build_preflight_warnings_permanent_no_backup():
    permanent_types = {"rotate_iam_key"}
    cr = _make_cr("rotate_iam_key", assets=["asset-1"])
    cr.title = "Rotate IAM Key"
    member = MagicMock()
    member.change_request = cr

    warnings = _build_preflight_warnings([member], [], permanent_types)
    assert len(warnings) == 1
    assert "rotate_iam_key" in warnings[0]


def test_build_preflight_warnings_permanent_with_backup():
    permanent_types = {"rotate_iam_key"}
    cr = _make_cr("rotate_iam_key", assets=["asset-1"])
    backup_cr = _make_cr("create_backup", assets=["asset-1"])
    member = MagicMock()
    member.change_request = cr

    warnings = _build_preflight_warnings([member], [backup_cr, cr], permanent_types)
    assert len(warnings) == 0


def test_build_preflight_warnings_standard_no_warning():
    permanent_types = {"rotate_iam_key"}
    cr = _make_cr("configure_selinux", assets=["asset-1"])
    member = MagicMock()
    member.change_request = cr

    warnings = _build_preflight_warnings([member], [cr], permanent_types)
    assert len(warnings) == 0
