"""Tests for AWS backup connector actions — all boto3 calls mocked."""
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import pytest


# ---------------------------------------------------------------------------
# create_ebs_snapshot
# ---------------------------------------------------------------------------

def test_create_ebs_snapshot_mock():
    """No credentials path returns mock result."""
    from app.connectors.executors.aws.create_ebs_snapshot import execute
    connector = MagicMock()
    connector.credentials = {}
    params = {
        "volume_id": "vol-0123456789abcdef0",
        "backup_name": "nightly-vol",
        "retention_days": 7,
    }
    result = asyncio.get_event_loop().run_until_complete(execute(params, [], connector))
    assert result["mock"] is True
    assert result["action"] == "create_ebs_snapshot"


def test_create_ebs_snapshot_real():
    """With credentials, ec2.create_snapshot is called."""
    from app.connectors.executors.aws.create_ebs_snapshot import execute
    mock_ec2 = MagicMock()
    mock_ec2.create_snapshot.return_value = {"SnapshotId": "snap-abc", "State": "pending"}
    connector = MagicMock()
    connector.credentials = {"region": "us-east-1"}

    with patch("app.connectors.executors.aws.create_ebs_snapshot._get_ec2_client", return_value=mock_ec2):
        result = asyncio.get_event_loop().run_until_complete(
            execute({"volume_id": "vol-abc", "backup_name": "test", "retention_days": 7}, [], connector)
        )
    assert result["snapshot_id"] == "snap-abc"
    assert result["state"] == "pending"
    mock_ec2.create_snapshot.assert_called_once()


# ---------------------------------------------------------------------------
# create_rds_snapshot
# ---------------------------------------------------------------------------

def test_create_rds_snapshot_mock():
    from app.connectors.executors.aws.create_rds_snapshot import execute
    connector = MagicMock()
    connector.credentials = {}
    result = asyncio.get_event_loop().run_until_complete(
        execute({"db_instance_id": "mydb", "backup_name": "mydb-snap", "retention_days": 30}, [], connector)
    )
    assert result["mock"] is True


def test_create_rds_snapshot_real():
    from app.connectors.executors.aws.create_rds_snapshot import execute
    mock_rds = MagicMock()
    mock_rds.create_db_snapshot.return_value = {
        "DBSnapshot": {"DBSnapshotIdentifier": "nexplane-mydb-1234", "Status": "creating"}
    }
    connector = MagicMock()
    connector.credentials = {"region": "us-east-1"}

    with patch("app.connectors.executors.aws.create_rds_snapshot._get_rds_client", return_value=mock_rds):
        result = asyncio.get_event_loop().run_until_complete(
            execute({"db_instance_id": "mydb", "backup_name": "mydb-snap", "retention_days": 30}, [], connector)
        )
    assert "nexplane-mydb" in result["snapshot_id"]
    assert result["status"] == "creating"


# ---------------------------------------------------------------------------
# verify_rds_backup
# ---------------------------------------------------------------------------

def test_verify_rds_backup_mock():
    from app.connectors.executors.aws.verify_rds_backup import execute
    connector = MagicMock()
    connector.credentials = {}
    result = asyncio.get_event_loop().run_until_complete(
        execute({"backup_id": "snap-xyz", "verification_query": "SELECT 1"}, [], connector)
    )
    assert result["mock"] is True


# ---------------------------------------------------------------------------
# dr_dns_failover_route53
# ---------------------------------------------------------------------------

def test_dr_dns_failover_route53_mock():
    from app.connectors.executors.aws.dr_dns_failover_route53 import execute
    connector = MagicMock()
    connector.credentials = {}
    result = asyncio.get_event_loop().run_until_complete(
        execute({
            "dns_record_id": "app.example.com",
            "dr_endpoint": "dr.example.com",
            "hosted_zone_id": "Z123ABC",
        }, [], connector)
    )
    assert result["mock"] is True


def test_dr_dns_failover_route53_real():
    from app.connectors.executors.aws.dr_dns_failover_route53 import execute
    mock_r53 = MagicMock()
    mock_r53.change_resource_record_sets.return_value = {"ChangeInfo": {"Status": "PENDING"}}
    connector = MagicMock()
    connector.credentials = {"region": "us-east-1"}

    with patch("app.connectors.executors.aws.dr_dns_failover_route53._get_r53_client", return_value=mock_r53):
        result = asyncio.get_event_loop().run_until_complete(
            execute({
                "dns_record_id": "app.example.com",
                "dr_endpoint": "dr.example.com",
                "hosted_zone_id": "Z123ABC",
            }, [], connector)
        )
    assert result["dns_updated"] is True
    assert result["new_endpoint"] == "dr.example.com"
    mock_r53.change_resource_record_sets.assert_called_once()
