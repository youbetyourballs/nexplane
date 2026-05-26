import pytest
from unittest.mock import AsyncMock, MagicMock


def test_new_cr_types_exist():
    from app.models.change_request import ChangeType
    assert ChangeType.apply_protocol_control.value == "apply_protocol_control"
    assert ChangeType.disable_kernel_feature.value == "disable_kernel_feature"
    assert ChangeType.apply_registry_fix.value == "apply_registry_fix"
    assert ChangeType.remove_vulnerable_package.value == "remove_vulnerable_package"
    assert ChangeType.revoke_exposed_credential.value == "revoke_exposed_credential"


@pytest.mark.asyncio
async def test_apply_protocol_control_execute_tls10():
    from app.connectors.executors.ssh.apply_protocol_control import execute
    connector = MagicMock()
    connector.run_command = AsyncMock(side_effect=[
        "MinProtocol = TLSv1\n",
        "",
    ])
    result = await execute(
        {"protocol": "tls10", "target_os": "linux"},
        ["asset-1"],
        connector,
    )
    assert result["success"] is True
    assert "pre_state" in result
    assert result["pre_state"]["original_line"] == "MinProtocol = TLSv1"


@pytest.mark.asyncio
async def test_apply_protocol_control_rollback():
    from app.connectors.executors.ssh.apply_protocol_control import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"protocol": "tls10", "target_os": "linux"},
        {"pre_state": {"original_line": "MinProtocol = TLSv1", "config_file": "/etc/ssl/openssl.cnf"}},
        connector,
    )
    assert result["rolled_back"] is True


@pytest.mark.asyncio
async def test_apply_protocol_control_rollback_no_pre_state():
    from app.connectors.executors.ssh.apply_protocol_control import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"protocol": "tls10", "target_os": "linux"},
        {"pre_state": {"original_line": None, "config_file": "/etc/ssl/openssl.cnf"}},
        connector,
    )
    assert result["rolled_back"] is True
