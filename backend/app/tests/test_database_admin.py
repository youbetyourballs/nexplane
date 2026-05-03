"""Tests for database administration connector actions and change type definitions."""
import json
import os
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


# ---- RDS promotion action tests ----

@pytest.mark.asyncio
async def test_promote_rds_replica_mock_when_no_creds():
    """Without credentials, execute() returns a mock result."""
    from app.connectors.executors.aws.promote_rds_replica import execute

    connector = MagicMock()
    connector.credentials = {}
    params = {"replica_identifier": "my-replica"}

    result = await execute(params, [], connector)

    assert result["action"] == "promote_db_replica"
    assert result["mock"] is True
    assert "new_endpoint" in result


@pytest.mark.asyncio
async def test_promote_rds_replica_real_execute_calls_boto3():
    """With credentials, execute() calls rds.promote_read_replica and waiter."""
    from app.connectors.executors.aws.promote_rds_replica import execute

    mock_rds = MagicMock()
    mock_rds.promote_read_replica.return_value = {}
    mock_waiter = MagicMock()
    mock_rds.get_waiter.return_value = mock_waiter
    mock_rds.describe_db_instances.return_value = {
        "DBInstances": [{"Endpoint": {"Address": "new-primary.us-east-1.rds.amazonaws.com"}}]
    }

    connector = MagicMock()
    connector.credentials = {"region": "us-east-1", "access_key_id": "AKIA...", "secret_access_key": "..."}
    connector.session = MagicMock()
    connector.session.client.return_value = mock_rds

    params = {
        "replica_identifier": "my-replica",
        "update_dns_record": False,
    }

    with patch("app.connectors.executors.aws.promote_rds_replica._verify_writable", return_value=None):
        result = await execute(params, [], connector)

    assert result["new_endpoint"] == "new-primary.us-east-1.rds.amazonaws.com"
    assert result["dns_updated"] is False
    mock_rds.promote_read_replica.assert_called_once_with(DBInstanceIdentifier="my-replica")
    mock_waiter.wait.assert_called_once_with(DBInstanceIdentifier="my-replica")


@pytest.mark.asyncio
async def test_promote_rds_replica_updates_route53_when_requested():
    """When update_dns_record=True, Route53 UPSERT is called."""
    from app.connectors.executors.aws.promote_rds_replica import execute

    mock_rds = MagicMock()
    mock_rds.promote_read_replica.return_value = {}
    mock_waiter = MagicMock()
    mock_rds.get_waiter.return_value = mock_waiter
    mock_rds.describe_db_instances.return_value = {
        "DBInstances": [{"Endpoint": {"Address": "new-primary.rds.amazonaws.com"}}]
    }
    mock_r53 = MagicMock()

    connector = MagicMock()
    connector.credentials = {"region": "us-east-1", "access_key_id": "AKIA...", "secret_access_key": "..."}
    connector.session = MagicMock()
    connector.session.client.side_effect = lambda svc: mock_rds if svc == "rds" else mock_r53

    params = {
        "replica_identifier": "my-replica",
        "update_dns_record": True,
        "dns_hosted_zone_id": "Z1234ABCD",
        "dns_record_name": "primary.example.com",
    }

    with patch("app.connectors.executors.aws.promote_rds_replica._verify_writable", return_value=None):
        result = await execute(params, [], connector)

    assert result["dns_updated"] is True
    mock_r53.change_resource_record_sets.assert_called_once()
    call_kwargs = mock_r53.change_resource_record_sets.call_args[1]
    assert call_kwargs["HostedZoneId"] == "Z1234ABCD"
    changes = call_kwargs["ChangeBatch"]["Changes"]
    assert changes[0]["ResourceRecordSet"]["Name"] == "primary.example.com"
    assert changes[0]["ResourceRecordSet"]["ResourceRecords"][0]["Value"] == "new-primary.rds.amazonaws.com"


@pytest.mark.asyncio
async def test_promote_rds_replica_rollback_is_unsupported():
    """Rollback for RDS promotion is always unsupported."""
    from app.connectors.executors.aws.promote_rds_replica import rollback

    result = await rollback({}, {}, MagicMock())
    assert result["rolled_back"] is False
    assert "cannot" in result["reason"].lower() or "unsupported" in result["reason"].lower()


# ---- Change type definition tests ----

CHANGE_TYPE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "connectors", "change_type_definitions"
)

DB_ADMIN_CHANGE_TYPES = [
    "provision_db_user",
    "deprovision_db_user",
    "db_permission_change",
    "configure_db_audit",
    "promote_db_replica",
    "db_connection_config",
]


def _load_change_type(name: str) -> dict:
    path = os.path.join(CHANGE_TYPE_DIR, f"{name}.json")
    with open(path) as f:
        return json.load(f)


@pytest.mark.parametrize("change_type", DB_ADMIN_CHANGE_TYPES)
def test_change_type_definition_exists_and_is_valid_json(change_type):
    """Each DB admin change type definition file must exist and parse as valid JSON."""
    data = _load_change_type(change_type)
    assert isinstance(data, dict), f"{change_type}.json must be a JSON object"


@pytest.mark.parametrize("change_type", DB_ADMIN_CHANGE_TYPES)
def test_change_type_definition_has_required_fields(change_type):
    """Each definition must have change_type and display_name fields."""
    data = _load_change_type(change_type)
    assert "change_type" in data, f"{change_type}.json missing 'change_type'"
    assert "display_name" in data, f"{change_type}.json missing 'display_name'"
    assert data["change_type"] == change_type, (
        f"change_type field '{data['change_type']}' does not match filename '{change_type}'"
    )


def test_promote_db_replica_has_no_rollback_step():
    """promote_db_replica must declare rollback_supported=false (irreversible action)."""
    data = _load_change_type("promote_db_replica")
    assert data.get("rollback_supported") is False, (
        "promote_db_replica must set rollback_supported=false"
    )


def test_provision_db_user_has_preflight_checks():
    """provision_db_user should declare preflight checks."""
    data = _load_change_type("provision_db_user")
    assert "preflight_checks" in data
    assert len(data["preflight_checks"]) > 0
