import pytest
import re
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
