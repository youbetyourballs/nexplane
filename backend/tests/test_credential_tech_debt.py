from unittest.mock import MagicMock, patch, AsyncMock


# ── A1: SSH authorized_keys executor ─────────────────────────────────────────

def test_authorized_keys_audit_parse_keys():
    raw = (
        "ssh-rsa AAAAB3NzaC1yc2EAAAA... deploy@prod\n"
        "ssh-ed25519 AAAAC3NzaC1lZDI1... ops@server\n"
        "# this is a comment\n"
        "\n"
    )
    from app.connectors.executors.ssh.authorized_keys_audit import _parse_authorized_keys
    keys = _parse_authorized_keys(raw)
    assert len(keys) == 2
    assert keys[0]["key_type"] == "ssh-rsa"
    assert keys[0]["key_fingerprint"] == "AAAAB3NzaC1yc2"  # first 14 chars of material
    assert keys[0]["comment"] == "deploy@prod"
    assert keys[0]["added_date"] is None
    assert keys[1]["key_type"] == "ssh-ed25519"
    assert keys[1]["comment"] == "ops@server"


def test_authorized_keys_audit_extracts_date_from_comment():
    raw = "ssh-rsa AAAAB3NzaC1yc2EAAAA... deploy@prod added:2023-04-15\n"
    from app.connectors.executors.ssh.authorized_keys_audit import _parse_authorized_keys
    keys = _parse_authorized_keys(raw)
    assert len(keys) == 1
    assert keys[0]["added_date"] == "2023-04-15"
    assert keys[0]["comment"] == "deploy@prod added:2023-04-15"


def test_authorized_keys_audit_skips_blank_and_comments():
    raw = "# comment\n\n   \n"
    from app.connectors.executors.ssh.authorized_keys_audit import _parse_authorized_keys
    keys = _parse_authorized_keys(raw)
    assert keys == []


# ── A1: worker wiring ─────────────────────────────────────────────────────────
import pytest

@pytest.mark.asyncio
async def test_run_ssh_authorized_keys_audit_no_connector():
    """Asset with no connector_id returns empty list."""
    from app.workers.credential_expiry_worker import _run_ssh_authorized_keys_audit
    from unittest.mock import AsyncMock

    asset = MagicMock()
    asset.connector_id = None

    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    result = await _run_ssh_authorized_keys_audit(asset, db)
    assert result == []


@pytest.mark.asyncio
async def test_run_ssh_authorized_keys_audit_wrong_type():
    """Asset whose connector is not type 'ssh' returns empty list."""
    from app.workers.credential_expiry_worker import _run_ssh_authorized_keys_audit
    from unittest.mock import AsyncMock
    import uuid

    asset = MagicMock()
    asset.connector_id = uuid.uuid4()

    connector = MagicMock()
    connector.connector_type = "aws"

    db = AsyncMock()
    db.get = AsyncMock(return_value=connector)

    result = await _run_ssh_authorized_keys_audit(asset, db)
    assert result == []


@pytest.mark.asyncio
async def test_run_ssh_authorized_keys_audit_calls_executor():
    """SSH connector causes executor to be called; keys are returned."""
    from app.workers.credential_expiry_worker import _run_ssh_authorized_keys_audit
    from unittest.mock import AsyncMock, patch
    import uuid

    asset = MagicMock()
    asset.connector_id = uuid.uuid4()

    connector = MagicMock()
    connector.connector_type = "ssh"

    fake_keys = [{"key_type": "ssh-rsa", "key_fingerprint": "AAAA", "comment": "ci", "added_date": None}]

    db = AsyncMock()
    db.get = AsyncMock(return_value=connector)

    with patch("app.connectors.executors.ssh.authorized_keys_audit.execute",
               new=AsyncMock(return_value={"keys": fake_keys, "host": "1.2.3.4"})):
        result = await _run_ssh_authorized_keys_audit(asset, db)

    assert result == fake_keys


@pytest.mark.asyncio
async def test_check_ssh_key_age_passes_db():
    """_check_ssh_key_age passes db into _run_ssh_authorized_keys_audit."""
    from app.workers.credential_expiry_worker import _check_ssh_key_age
    from unittest.mock import AsyncMock, patch

    db = AsyncMock()
    captured = {}

    async def fake_audit(asset, db_arg):
        captured["db"] = db_arg
        return []

    asset = MagicMock()
    asset.connector_id = None
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [asset]
    db.execute = AsyncMock(return_value=mock_result)

    with patch("app.workers.credential_expiry_worker._run_ssh_authorized_keys_audit", fake_audit):
        await _check_ssh_key_age(db)

    assert captured.get("db") is db


# ── A2: IAM key age check ─────────────────────────────────────────────────────

def test_list_old_keys_filters_inactive():
    """_list_old_keys skips keys with Status != Active."""
    from app.workers.credential_expiry_worker import _list_old_keys
    from datetime import datetime, timezone, timedelta
    from unittest.mock import MagicMock, patch

    fake_keys = [
        {"AccessKeyId": "AKIA1", "Status": "Inactive",
         "CreateDate": datetime.now(timezone.utc) - timedelta(days=100)},
        {"AccessKeyId": "AKIA2", "Status": "Active",
         "CreateDate": datetime.now(timezone.utc) - timedelta(days=5)},
    ]
    fake_users = [{"UserName": "alice"}]

    mock_iam = MagicMock()
    mock_iam.get_paginator.return_value.paginate.return_value = [{"Users": fake_users}]
    mock_iam.list_access_keys.return_value = {"AccessKeyMetadata": fake_keys}

    creds = {"aws_access_key_id": "K", "aws_secret_access_key": "S", "region": "us-east-1"}

    with patch("boto3.client", return_value=mock_iam):
        result = _list_old_keys(creds)

    assert result == []  # inactive filtered, active key < 90 days


def test_list_old_keys_age_threshold():
    """_list_old_keys returns keys >= 90 days old."""
    from app.workers.credential_expiry_worker import _list_old_keys
    from datetime import datetime, timezone, timedelta
    from unittest.mock import MagicMock, patch

    old_key = {
        "AccessKeyId": "AKIAOLD1234567",
        "Status": "Active",
        "CreateDate": datetime.now(timezone.utc) - timedelta(days=95),
    }
    new_key = {
        "AccessKeyId": "AKIANEW1234567",
        "Status": "Active",
        "CreateDate": datetime.now(timezone.utc) - timedelta(days=30),
    }
    mock_iam = MagicMock()
    mock_iam.get_paginator.return_value.paginate.return_value = [{"Users": [{"UserName": "bob"}]}]
    mock_iam.list_access_keys.return_value = {"AccessKeyMetadata": [old_key, new_key]}

    creds = {"aws_access_key_id": "K", "aws_secret_access_key": "S"}

    with patch("boto3.client", return_value=mock_iam):
        result = _list_old_keys(creds)

    assert len(result) == 1
    username, key_id, age_days = result[0]
    assert username == "bob"
    assert key_id == "AKIAOLD1234567"
    assert age_days >= 95


@pytest.mark.asyncio
async def test_check_iam_key_age_uses_connector_creds():
    """_check_iam_key_age queries AWS connectors and uses their credentials."""
    from app.workers.credential_expiry_worker import _check_iam_key_age
    from unittest.mock import AsyncMock, MagicMock, patch

    connector = MagicMock()
    connector.credentials = {
        "aws_access_key_id": "AKIATEST",
        "aws_secret_access_key": "SECRET",
        "region": "us-east-1",
    }

    db = AsyncMock()
    captured_creds = {}

    def fake_list_old_keys(creds):
        captured_creds.update(creds)
        return []  # no old keys

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type",
               new=AsyncMock(return_value=[connector])), \
         patch("app.workers.credential_expiry_worker._list_old_keys", fake_list_old_keys):
        await _check_iam_key_age(db)

    assert captured_creds.get("aws_access_key_id") == "AKIATEST"
    assert captured_creds.get("aws_secret_access_key") == "SECRET"


# ── A3: revoke_exposed_credential new types ───────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_gcp_service_account_key():
    """GCP path calls google IAM delete with correct key resource name."""
    from app.connectors.executors.aws.revoke_exposed_credential import execute

    connector = MagicMock()
    connector.creds = {
        "service_account_key_json": '{"type": "service_account", "project_id": "my-proj"}'
    }

    mock_service = MagicMock()
    mock_keys = MagicMock()
    mock_service.projects.return_value.serviceAccounts.return_value.keys.return_value = mock_keys
    mock_delete = MagicMock()
    mock_keys.delete.return_value = mock_delete

    with patch("googleapiclient.discovery.build", return_value=mock_service), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info",
               return_value=MagicMock()):
        result = await execute(
            {"credential_type": "gcp_service_account_key", "credential_id": "abc123key"},
            [], connector,
        )

    mock_keys.delete.assert_called_once_with(
        name="projects/-/serviceAccounts/-/keys/abc123key"
    )
    mock_delete.execute.assert_called_once()
    assert result["success"] is True
    assert result["rolled_back_available"] is False


@pytest.mark.asyncio
async def test_revoke_azure_client_secret():
    """Azure path calls Graph removePassword with correct app_id and key_id."""
    from app.connectors.executors.aws.revoke_exposed_credential import execute

    connector = MagicMock()
    connector.creds = {
        "tenant_id": "tenant-1",
        "client_id": "client-1",
        "client_secret": "secret-1",
    }

    credential_id = "app-object-id/key-guid-1234"

    mock_token_resp = MagicMock()
    mock_token_resp.json.return_value = {"access_token": "tok123"}
    mock_token_resp.raise_for_status = MagicMock()

    mock_remove_resp = MagicMock()
    mock_remove_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=[mock_token_resp, mock_remove_resp])

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await execute(
            {"credential_type": "azure_client_secret", "credential_id": credential_id},
            [], connector,
        )

    remove_call = mock_client.post.call_args_list[1]
    assert "removePassword" in remove_call.args[0]
    assert remove_call.kwargs["json"]["keyId"] == "key-guid-1234"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_revoke_ldap_password():
    """LDAP path calls modify with userAccountControl=514 (disabled)."""
    from app.connectors.executors.aws.revoke_exposed_credential import execute

    connector = MagicMock()
    connector.creds = {
        "server": "ldap://dc.corp.example",
        "bind_dn": "cn=svc,dc=corp,dc=example",
        "bind_password": "pass",
    }
    credential_id = "cn=jdoe,ou=users,dc=corp,dc=example"

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.modify = MagicMock(return_value=True)

    with patch("ldap3.Server", return_value=MagicMock()), \
         patch("ldap3.Connection", return_value=mock_conn):
        result = await execute(
            {"credential_type": "ldap_password", "credential_id": credential_id},
            [], connector,
        )

    mock_conn.modify.assert_called_once()
    call_args = mock_conn.modify.call_args
    assert call_args.args[0] == credential_id
    changes = call_args.args[1]
    assert "userAccountControl" in changes
    assert 514 in changes["userAccountControl"][0]
    assert result["success"] is True


@pytest.mark.asyncio
async def test_revoke_unsupported_type_still_raises():
    """ValueError still raised for unknown credential types."""
    from app.connectors.executors.aws.revoke_exposed_credential import execute

    connector = MagicMock()
    connector.creds = {"something": "here"}

    with pytest.raises(ValueError, match="Unsupported credential_type"):
        await execute(
            {"credential_type": "foobar_token", "credential_id": "xyz"},
            [], connector,
        )
