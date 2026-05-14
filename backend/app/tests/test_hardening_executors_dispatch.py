import pytest
from unittest.mock import AsyncMock, patch


HARDENING_EXECUTORS = [
    ("configure_seccomp", "configure_seccomp"),
    ("configure_apparmor", "configure_apparmor"),
    ("configure_selinux", "configure_selinux"),
    ("apply_sysctl_hardening", "apply_sysctl_hardening"),
    ("configure_host_firewall", "configure_host_firewall"),
    ("blacklist_kernel_modules", "blacklist_kernel_modules"),
    ("harden_mount_options", "harden_mount_options"),
    ("deploy_auditd_rules", "deploy_auditd_rules"),
    ("setup_file_integrity_monitoring", "setup_file_integrity_monitoring"),
    ("deploy_ebpf_policy", "deploy_ebpf_policy"),
    ("configure_ebpf_security_policy", "configure_ebpf_security_policy"),
    ("harden_ssh", "harden_ssh"),
    ("configure_pam", "configure_pam"),
]


@pytest.mark.parametrize("module_name,expected_command", HARDENING_EXECUTORS)
@pytest.mark.asyncio
async def test_executor_dispatches_to_agent(module_name, expected_command):
    import importlib
    mod = importlib.import_module(
        f"app.connectors.executors.nexplane_agent.{module_name}"
    )
    mock_dispatch = AsyncMock(return_value={"status": "ok", "snapshot_id": "snap-123"})
    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await mod.execute({"service_name": "nginx"}, ["asset-uuid-1"], None)

    mock_dispatch.assert_called_once()
    call_kwargs = mock_dispatch.call_args
    assert (
        call_kwargs.kwargs.get("command") == expected_command
        or (call_kwargs.args and call_kwargs.args[0] == expected_command)
    )
    assert result.get("_asset_ids") == ["asset-uuid-1"]


READ_ONLY_EXECUTORS = [
    "audit_os_security_posture",
    "audit_ebpf_posture",
    "audit_scheduled_tasks",
]


@pytest.mark.parametrize("module_name", READ_ONLY_EXECUTORS)
@pytest.mark.asyncio
async def test_readonly_executor_dispatches_to_agent(module_name):
    import importlib
    mod = importlib.import_module(
        f"app.connectors.executors.nexplane_agent.{module_name}"
    )
    mock_dispatch = AsyncMock(return_value={"status": "ok"})
    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await mod.execute({}, ["asset-uuid-1"], None)

    mock_dispatch.assert_called_once()


PATCH_EXECUTORS = [
    ("apply_linux_patches", "apply_linux_patches"),
    ("audit_linux_patch_status", "audit_linux_patch_status"),
]


@pytest.mark.parametrize("module_name,expected_command", PATCH_EXECUTORS)
@pytest.mark.asyncio
async def test_patch_executor_dispatches_to_agent(module_name, expected_command):
    import importlib
    mod = importlib.import_module(
        f"app.connectors.executors.nexplane_agent.{module_name}"
    )
    mock_dispatch = AsyncMock(return_value={"status": "ok", "snapshot_id": "snap-patch"})
    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await mod.execute({"packages": ["openssl"]}, ["asset-uuid-1"], None)

    mock_dispatch.assert_called_once()
