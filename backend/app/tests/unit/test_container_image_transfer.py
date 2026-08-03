# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def test_change_type_enum_value():
    from app.models.change_request import ChangeType
    assert ChangeType.container_image_transfer.value == "container_image_transfer"
