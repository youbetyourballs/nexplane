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


@pytest.mark.asyncio
async def test_disable_kernel_feature_execute():
    from app.connectors.executors.ssh.disable_kernel_feature import execute
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await execute({"feature": "usb_storage"}, ["asset-1"], connector)
    assert result["success"] is True
    assert result["feature"] == "usb_storage"
    calls = [str(c) for c in connector.run_command.call_args_list]
    assert any("blacklist usb_storage" in c for c in calls)
    assert any("rmmod" in c for c in calls)


@pytest.mark.asyncio
async def test_disable_kernel_feature_rollback():
    from app.connectors.executors.ssh.disable_kernel_feature import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback({"feature": "usb_storage"}, {}, connector)
    assert result["rolled_back"] is True
    calls = [str(c) for c in connector.run_command.call_args_list]
    assert any("sed" in c for c in calls)
    assert any("modprobe" in c for c in calls)


@pytest.mark.asyncio
async def test_apply_registry_fix_execute_new_key():
    from app.connectors.executors.ssh.apply_registry_fix import execute
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await execute(
        {
            "key_path": r"HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services",
            "value_name": "fDenyTSConnections",
            "value_data": "1",
            "value_type": "DWORD",
        },
        ["asset-win-1"],
        connector,
    )
    assert result["success"] is True
    assert result["pre_state"]["existed"] is False


@pytest.mark.asyncio
async def test_apply_registry_fix_rollback_new_key():
    from app.connectors.executors.ssh.apply_registry_fix import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"key_path": r"HKLM:\TEST", "value_name": "MyVal", "value_data": "1", "value_type": "DWORD"},
        {"pre_state": {"existed": False, "original_value": None}},
        connector,
    )
    assert result["rolled_back"] is True
    calls = [str(c) for c in connector.run_command.call_args_list]
    assert any("Remove-ItemProperty" in c for c in calls)


@pytest.mark.asyncio
async def test_apply_registry_fix_rollback_existing_key():
    from app.connectors.executors.ssh.apply_registry_fix import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"key_path": r"HKLM:\TEST", "value_name": "MyVal", "value_data": "1", "value_type": "DWORD"},
        {"pre_state": {"existed": True, "original_value": "0"}},
        connector,
    )
    assert result["rolled_back"] is True
    calls = [str(c) for c in connector.run_command.call_args_list]
    assert any("New-ItemProperty" in c for c in calls)


@pytest.mark.asyncio
async def test_remove_vulnerable_package_execute():
    from app.connectors.executors.ssh.remove_vulnerable_package import execute
    connector = MagicMock()
    connector.run_command = AsyncMock(side_effect=[
        "telnet-0.17-65.el8.x86_64\n",
        "",
    ])
    result = await execute({"package_name": "telnet", "target_os": "linux"}, ["a1"], connector)
    assert result["success"] is True
    assert "telnet" in result["pre_state"]["installed_version"]


@pytest.mark.asyncio
async def test_remove_vulnerable_package_rollback_with_version():
    from app.connectors.executors.ssh.remove_vulnerable_package import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"package_name": "telnet", "target_os": "linux"},
        {"pre_state": {"installed_version": "telnet-0.17-65.el8.x86_64"}},
        connector,
    )
    assert result["rolled_back"] is True
    calls = [str(c) for c in connector.run_command.call_args_list]
    assert any("install" in c for c in calls)


@pytest.mark.asyncio
async def test_remove_vulnerable_package_rollback_no_version():
    from app.connectors.executors.ssh.remove_vulnerable_package import rollback
    connector = MagicMock()
    connector.run_command = AsyncMock(return_value="")
    result = await rollback(
        {"package_name": "telnet", "target_os": "linux"},
        {"pre_state": {"installed_version": None}},
        connector,
    )
    assert result["rolled_back"] is False
    assert "version" in result["reason"]


@pytest.mark.asyncio
async def test_revoke_exposed_credential_mock_mode():
    from app.connectors.executors.aws.revoke_exposed_credential import execute
    connector = MagicMock()
    connector.creds = None
    result = await execute(
        {"credential_type": "aws_iam_key", "credential_id": "AKIATEST"},
        ["asset-1"],
        connector,
    )
    assert result["success"] is True
    assert result.get("mock") is True


@pytest.mark.asyncio
async def test_revoke_exposed_credential_rollback_always_false():
    from app.connectors.executors.aws.revoke_exposed_credential import rollback
    connector = MagicMock()
    result = await rollback({}, {}, connector)
    assert result["rolled_back"] is False
    assert "permanent" in result["reason"]


def test_mitigation_cr_map_contains_new_types():
    from app.routers.vulnerability import MITIGATION_CR_MAP
    assert "protocol_control" in MITIGATION_CR_MAP
    assert "kernel_feature" in MITIGATION_CR_MAP
    assert "registry_fix" in MITIGATION_CR_MAP
    assert "package_remove" in MITIGATION_CR_MAP
    assert "credential_revoke" in MITIGATION_CR_MAP
    assert MITIGATION_CR_MAP["protocol_control"] == "apply_protocol_control"
    assert MITIGATION_CR_MAP["credential_revoke"] == "revoke_exposed_credential"
