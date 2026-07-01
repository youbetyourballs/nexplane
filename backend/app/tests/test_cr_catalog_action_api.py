# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Run with --noconftest."""
import pytest
import uuid
from pydantic import ValidationError

from app.models.change_request import ChangeType
from app.schemas.change_request import ChangeRequestCreate


def test_catalog_action_cr_omitting_target_asset_ids_is_valid():
    """Omitting target_asset_ids entirely must succeed and default to []."""
    cr = ChangeRequestCreate(
        title="Provision acme",
        change_type=ChangeType.catalog_action,
        desired_outcome={
            "connector_type": "commercial",
            "action_id": "provision_instance",
            "params": {"client_id": "acme"},
        },
    )
    assert cr.target_asset_ids == []


def test_catalog_action_cr_empty_list_is_valid():
    """Explicitly passing [] must also succeed."""
    cr = ChangeRequestCreate(
        title="Provision acme",
        change_type=ChangeType.catalog_action,
        target_asset_ids=[],
        desired_outcome={
            "connector_type": "commercial",
            "action_id": "provision_instance",
            "params": {"client_id": "acme"},
        },
    )
    assert cr.target_asset_ids == []


def test_other_change_type_still_accepts_assets():
    """Non-catalog_action change types still work with asset ids."""
    asset_id = uuid.uuid4()
    cr = ChangeRequestCreate(
        title="Some other CR",
        change_type=ChangeType.dns_update,
        target_asset_ids=[asset_id],
        desired_outcome={"action": "grant"},
    )
    assert cr.target_asset_ids == [asset_id]
