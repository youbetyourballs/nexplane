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
