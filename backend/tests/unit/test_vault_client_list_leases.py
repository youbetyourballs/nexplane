"""Unit tests for VaultClient.list_leases — recursive traversal and real TTL reading."""
import pytest
from unittest.mock import MagicMock, call
from app.connectors.executors.hashicorp_vault._client import VaultClient


def _make_client(list_responses: dict, read_responses: dict) -> VaultClient:
    """Build a VaultClient whose hvac sys methods are mocked."""
    hvac = MagicMock()

    def list_leases(prefix=""):
        if prefix in list_responses:
            return {"data": {"keys": list_responses[prefix]}}
        raise Exception(f"no mock for list prefix={prefix!r}")

    def read_lease(lease_id):
        if lease_id in read_responses:
            return {"data": read_responses[lease_id]}
        raise Exception(f"no mock for lease_id={lease_id!r}")

    hvac.sys.list_leases.side_effect = list_leases
    hvac.sys.read_lease.side_effect = read_lease
    return VaultClient(hvac)


def test_flat_leases_return_real_ttl():
    client = _make_client(
        list_responses={"": ["abc123", "def456"]},
        read_responses={
            "abc123": {"ttl": 1800, "renewable": True},
            "def456": {"ttl": 900, "renewable": False},
        },
    )
    leases = client.list_leases()
    assert leases == [
        {"lease_id": "abc123", "ttl": 1800, "renewable": True},
        {"lease_id": "def456", "ttl": 900, "renewable": False},
    ]


def test_recursive_directory_traversal():
    client = _make_client(
        list_responses={
            "": ["secret/", "aws/"],
            "secret/": ["creds/abc"],
            "aws/": ["keys/xyz"],
        },
        read_responses={
            "secret/creds/abc": {"ttl": 600, "renewable": True},
            "aws/keys/xyz": {"ttl": 7200, "renewable": True},
        },
    )
    leases = client.list_leases()
    assert len(leases) == 2
    ids = {l["lease_id"] for l in leases}
    assert ids == {"secret/creds/abc", "aws/keys/xyz"}
    by_id = {l["lease_id"]: l for l in leases}
    assert by_id["secret/creds/abc"]["ttl"] == 600
    assert by_id["aws/keys/xyz"]["ttl"] == 7200


def test_expired_lease_skipped_gracefully():
    """A lease that vanishes between list and read_lease should be skipped, not crash."""
    client = _make_client(
        list_responses={"": ["valid123", "gone456"]},
        read_responses={
            "valid123": {"ttl": 300, "renewable": True},
            # gone456 raises (simulates expired lease)
        },
    )
    leases = client.list_leases()
    assert len(leases) == 1
    assert leases[0]["lease_id"] == "valid123"


def test_ttl_zero_reported_not_filtered():
    """A lease with ttl=0 (expired but not yet cleaned up) should still appear."""
    client = _make_client(
        list_responses={"": ["expiredlease"]},
        read_responses={"expiredlease": {"ttl": 0, "renewable": False}},
    )
    leases = client.list_leases()
    assert leases == [{"lease_id": "expiredlease", "ttl": 0, "renewable": False}]


def test_empty_vault_returns_empty_list():
    client = _make_client(list_responses={"": []}, read_responses={})
    assert client.list_leases() == []


def test_list_failure_returns_empty():
    hvac = MagicMock()
    hvac.sys.list_leases.side_effect = Exception("permission denied")
    client = VaultClient(hvac)
    assert client.list_leases() == []
