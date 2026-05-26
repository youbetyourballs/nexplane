import pytest
from unittest.mock import AsyncMock, MagicMock


def test_new_cr_types_exist():
    from app.models.change_request import ChangeType
    assert ChangeType.apply_protocol_control.value == "apply_protocol_control"
    assert ChangeType.disable_kernel_feature.value == "disable_kernel_feature"
    assert ChangeType.apply_registry_fix.value == "apply_registry_fix"
    assert ChangeType.remove_vulnerable_package.value == "remove_vulnerable_package"
    assert ChangeType.revoke_exposed_credential.value == "revoke_exposed_credential"
