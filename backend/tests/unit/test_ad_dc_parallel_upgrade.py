# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for ad_dc_parallel_upgrade executor.

All WinRM sessions and boto3 clients are mocked — no live infra required.
"""
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

import app.connectors.executors.active_directory.ad_dc_parallel_upgrade as executor_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector(creds=None):
    c = MagicMock()
    c.id = "conn-ad-001"
    c.credentials = creds or {
        "winrm_hostname": "10.0.1.10",
        "winrm_username": "CORP\\Administrator",
        "winrm_password": "Test1234!",
        "winrm_port": "5985",
        "winrm_use_ssl": "false",
        "aws_access_key_id": "AKIA000",
        "aws_secret_access_key": "secret",
        "region": "us-east-1",
    }
    return c


def _make_parameters(**kwargs):
    defaults = {
        "source_dc_asset_id": "asset-dc01",
        "target_windows_version": "2022",
        "domain_admin_username": "CORP\\Administrator",
        "domain_admin_password": "Test1234!",
        "new_dc_instance_type": "t3.medium",
        "skip_fsmo_transfer": False,
        "replication_timeout_minutes": 5,
        "dry_run": False,
    }
    defaults.update(kwargs)
    return defaults


def _winrm_ok(stdout="ok", stderr="", rc=0):
    r = MagicMock()
    r.std_out = stdout.encode() if isinstance(stdout, str) else stdout
    r.std_err = stderr.encode() if isinstance(stderr, str) else stderr
    r.status_code = rc
    return r


# ---------------------------------------------------------------------------
# Test 1: preflight blocks on WinRM failure
# ---------------------------------------------------------------------------

def test_preflight_blocks_on_winrm_failure():
    """When WinRM to source DC is unreachable, preflight must return preflight_blocked."""
    params = _make_parameters()
    connector = _make_connector()

    with patch("app.connectors.executors.active_directory._client.get_winrm_session") as mock_session:
        mock_session.side_effect = Exception("Connection refused")
        result = asyncio.get_event_loop().run_until_complete(
            executor_mod._preflight(params, connector, connector.credentials)
        )

    assert result["status"] == "preflight_blocked"
    blocking = [c["name"] for c in result["blocking_checks"]]
    assert "winrm_reachable" in blocking


# ---------------------------------------------------------------------------
# Test 2: preflight blocks on single-DC domain
# ---------------------------------------------------------------------------

def test_preflight_blocks_single_dc_domain():
    """A domain with only 1 DC must block — demotion would destroy the domain."""
    params = _make_parameters()
    connector = _make_connector()

    session_mock = MagicMock()
    # WinRM reachable, then various checks
    session_mock.run_ps.side_effect = [
        _winrm_ok("ready"),                                   # WinRM ready check
        _winrm_ok("corp.example.com"),                        # Get-ADDomain
        _winrm_ok("10.0.1.10"),                               # Get-ADDomainController list (source DC only)
        _winrm_ok("1"),                                       # DC count = 1
        _winrm_ok("PDCEmulator          DC01\nRIDMaster    DC01"),  # netdom query fsmo
        _winrm_ok("Windows2016Domain"),                       # Get-ADDomain DomainMode
        _winrm_ok("Passed test DNS"),                         # dcdiag /test:dns
        _winrm_ok("passed test Replications"),                # dcdiag /test:replications
    ]

    boto3_mock = MagicMock()
    boto3_mock.describe_instances.return_value = {
        "Reservations": [{"Instances": [{"InstanceId": "i-0abc", "SubnetId": "subnet-1", "SecurityGroups": [{"GroupId": "sg-1"}]}]}]
    }
    boto3_ssm = MagicMock()
    boto3_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-0win2022"}}

    with patch("app.connectors.executors.active_directory._client.get_winrm_session", return_value=session_mock), \
         patch("app.connectors.executors.active_directory.ad_dc_parallel_upgrade._ec2_client", return_value=boto3_mock), \
         patch("app.connectors.executors.active_directory.ad_dc_parallel_upgrade._ssm_client", return_value=boto3_ssm):
        result = asyncio.get_event_loop().run_until_complete(
            executor_mod._preflight(params, connector, connector.credentials)
        )

    assert result["status"] == "preflight_blocked"
    blocking = [c["name"] for c in result["blocking_checks"]]
    assert "dc_count_safe" in blocking


# ---------------------------------------------------------------------------
# Test 3: dry_run returns preflight report without EC2 calls
# ---------------------------------------------------------------------------

def test_preflight_dry_run_returns_report():
    """dry_run=True must return the preflight report and never call run_instances."""
    params = _make_parameters(dry_run=True)
    connector = _make_connector()

    session_mock = MagicMock()
    session_mock.run_ps.side_effect = [
        _winrm_ok("ready"),
        _winrm_ok("corp.example.com"),
        _winrm_ok("10.0.1.10,DC02.corp.example.com"),
        _winrm_ok("2"),
        _winrm_ok("PDCEmulator          DC01\nRIDMaster    DC01\nInfrastructureMaster    DC01"),
        _winrm_ok("Windows2016Domain"),
        _winrm_ok("Passed test DNS"),
        _winrm_ok("passed test Replications"),
    ]

    boto3_ec2_mock = MagicMock()
    boto3_ssm_mock = MagicMock()
    boto3_ssm_mock.get_parameter.return_value = {"Parameter": {"Value": "ami-0win2022"}}
    boto3_ec2_mock.describe_instances.return_value = {
        "Reservations": [{"Instances": [{"InstanceId": "i-0src", "SubnetId": "subnet-1", "SecurityGroups": [{"GroupId": "sg-1"}]}]}]
    }

    with patch("app.connectors.executors.active_directory._client.get_winrm_session", return_value=session_mock), \
         patch("app.connectors.executors.active_directory.ad_dc_parallel_upgrade._ec2_client", return_value=boto3_ec2_mock), \
         patch("app.connectors.executors.active_directory.ad_dc_parallel_upgrade._ssm_client", return_value=boto3_ssm_mock):
        result = asyncio.get_event_loop().run_until_complete(
            executor_mod.execute(params, [], connector)
        )

    assert result["status"] == "dry_run"
    boto3_ec2_mock.run_instances.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: rollback Case A — no FSMO transfer
# ---------------------------------------------------------------------------

def test_rollback_case_a_no_fsmo_transfer():
    """fsmo_transferred=False in execution_result -> demote + terminate new DC."""
    params = _make_parameters()
    connector = _make_connector()

    execution_result = {
        "new_instance_id": "i-0newdc456",
        "new_dc_private_ip": "10.0.1.50",
        "fsmo_transferred": False,
        "demotion_completed": False,
        "domain_admin_password": "Test1234!",
        "domain_admin_username": "CORP\\Administrator",
    }

    session_mock = MagicMock()
    session_mock.run_ps.return_value = _winrm_ok("Demotion complete")

    boto3_mock = MagicMock()

    with patch("app.connectors.executors.active_directory._client.get_winrm_session", return_value=session_mock), \
         patch("app.connectors.executors.active_directory.ad_dc_parallel_upgrade._ec2_client", return_value=boto3_mock):
        result = asyncio.get_event_loop().run_until_complete(
            executor_mod.rollback(params, execution_result, connector)
        )

    assert result["rolled_back"] is True
    assert result["strategy"] == "demote_new_dc"
    boto3_mock.terminate_instances.assert_called_once_with(InstanceIds=["i-0newdc456"])


# ---------------------------------------------------------------------------
# Test 5: rollback Case B — FSMO transferred, source not yet demoted
# ---------------------------------------------------------------------------

def test_rollback_case_b_fsmo_transferred():
    """fsmo_transferred=True, demotion_completed=False -> seize FSMOs back then demote new DC."""
    params = _make_parameters()
    connector = _make_connector()

    execution_result = {
        "new_instance_id": "i-0newdc456",
        "new_dc_hostname": "DC02.corp.example.com",
        "source_dc_hostname": "DC01.corp.example.com",
        "fsmo_transferred": True,
        "demotion_completed": False,
        "domain_admin_password": "Test1234!",
        "domain_admin_username": "CORP\\Administrator",
        "fsmo_state_before": {"PDCEmulator": "DC01", "RIDMaster": "DC01"},
    }

    session_mock = MagicMock()
    session_mock.run_ps.return_value = _winrm_ok("seize successful")

    boto3_mock = MagicMock()

    with patch("app.connectors.executors.active_directory._client.get_winrm_session", return_value=session_mock), \
         patch("app.connectors.executors.active_directory.ad_dc_parallel_upgrade._ec2_client", return_value=boto3_mock):
        result = asyncio.get_event_loop().run_until_complete(
            executor_mod.rollback(params, execution_result, connector)
        )

    assert result["rolled_back"] is True
    assert result["strategy"] == "seize_fsmo_then_demote_new_dc"
    boto3_mock.terminate_instances.assert_called_once_with(InstanceIds=["i-0newdc456"])


# ---------------------------------------------------------------------------
# Test 6: rollback Case C — source DC already demoted
# ---------------------------------------------------------------------------

def test_rollback_case_c_demotion_complete():
    """demotion_completed=True -> rolled_back=False with manual instructions."""
    params = _make_parameters()
    connector = _make_connector()

    execution_result = {
        "new_instance_id": "i-0newdc456",
        "source_ec2_instance_id": "i-0src",
        "fsmo_transferred": True,
        "demotion_completed": True,
    }

    boto3_mock = MagicMock()

    with patch("app.connectors.executors.active_directory.ad_dc_parallel_upgrade._ec2_client", return_value=boto3_mock):
        result = asyncio.get_event_loop().run_until_complete(
            executor_mod.rollback(params, execution_result, connector)
        )

    assert result["rolled_back"] is False
    assert "source DC already demoted" in result["reason"]
    assert "new_instance_id" in result
    assert "source_ec2_instance_id" in result


# ---------------------------------------------------------------------------
# Test 7: FSMO netdom output parsing
# ---------------------------------------------------------------------------

def test_fsmo_parse_netdom_output():
    """netdom query fsmo output is parsed into a role->DC mapping."""
    output = (
        "Schema master               DC01.corp.example.com\n"
        "Domain naming master        DC01.corp.example.com\n"
        "PDC                         DC01.corp.example.com\n"
        "RID pool manager            DC02.corp.example.com\n"
        "Infrastructure master       DC02.corp.example.com\n"
        "The command completed successfully."
    )
    result = executor_mod._parse_fsmo_netdom(output)
    assert result["SchemaMaster"] == "DC01.corp.example.com"
    assert result["DomainNamingMaster"] == "DC01.corp.example.com"
    assert result["PDCEmulator"] == "DC01.corp.example.com"
    assert result["RIDMaster"] == "DC02.corp.example.com"
    assert result["InfrastructureMaster"] == "DC02.corp.example.com"


# ---------------------------------------------------------------------------
# Test 8: repadmin CSV parse — no errors
# ---------------------------------------------------------------------------

def test_repadmin_csv_parse_no_errors():
    """Healthy repadmin /showrepl /csv output -> replication_ok: True."""
    csv_output = (
        "Showrepl CSV,Default-First-Site-Name\\DC01,Default-First-Site-Name\\DC02,,0,0,2026-07-23 10:00:00\n"
        "Showrepl CSV,Default-First-Site-Name\\DC01,Default-First-Site-Name\\DC02,,0,0,2026-07-23 10:00:00\n"
    )
    ok, errors = executor_mod._parse_repadmin_csv(csv_output)
    assert ok is True
    assert errors == []


# ---------------------------------------------------------------------------
# Test 9: repadmin CSV parse — errors present
# ---------------------------------------------------------------------------

def test_repadmin_csv_parse_with_errors():
    """Error rows in repadmin CSV -> replication_ok: False."""
    csv_output = (
        "Showrepl CSV,Default-First-Site-Name\\DC01,Default-First-Site-Name\\DC02,,8461,1,2026-07-23 10:00:00\n"
    )
    ok, errors = executor_mod._parse_repadmin_csv(csv_output)
    assert ok is False
    assert len(errors) > 0


# ---------------------------------------------------------------------------
# Test 10: WinRM drop during DCPromo treated as expected reboot
# ---------------------------------------------------------------------------

def test_winrm_drop_during_dcpromo_is_handled():
    """Connection reset raised during DCPromo is caught and treated as expected reboot."""
    assert executor_mod._is_expected_reboot_disconnect(
        Exception("winrm.exceptions.InvalidCredentialsError: 401")
    ) is False
    assert executor_mod._is_expected_reboot_disconnect(
        Exception("Connection reset by peer")
    ) is True
    assert executor_mod._is_expected_reboot_disconnect(
        ConnectionResetError("EOF")
    ) is True


# ---------------------------------------------------------------------------
# Test 11: WinRM drop during demotion treated as expected reboot
# ---------------------------------------------------------------------------

def test_winrm_drop_during_demotion_is_handled():
    """Same _is_expected_reboot_disconnect logic applies during demotion."""
    assert executor_mod._is_expected_reboot_disconnect(
        Exception("Transport endpoint is not connected")
    ) is True
    assert executor_mod._is_expected_reboot_disconnect(
        Exception("timed out")
    ) is True
    assert executor_mod._is_expected_reboot_disconnect(
        Exception("Access denied")
    ) is False
