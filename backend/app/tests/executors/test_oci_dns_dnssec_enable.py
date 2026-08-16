# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def connector():
    c = MagicMock()
    c.credentials = {
        "user": "ocid1.user.oc1..test",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
        "fingerprint": "aa:bb:cc:dd",
        "tenancy": "ocid1.tenancy.oc1..test",
        "region": "us-ashburn-1",
    }
    return c


@pytest.mark.asyncio
async def test_already_enabled_returns_no_op(connector):
    mock_zone = MagicMock()
    mock_zone.dnssec_state = "ENABLED"
    mock_zone.id = "ocid1.dns.zone.test"

    with patch("app.connectors.executors.oci.oci_dns_dnssec_enable._run") as mock_run:
        mock_run.return_value = mock_zone
        from app.connectors.executors.oci.oci_dns_dnssec_enable import execute
        result = await execute({"zone_id": "ocid1.dns.zone.test"}, [], connector)

    assert result["already_enabled"] is True
    assert result["status"] == "enabled"


@pytest.mark.asyncio
async def test_enables_dnssec(connector):
    call_count = [0]

    async def fake_run(fn):
        call_count[0] += 1
        if call_count[0] == 1:  # get_zone
            z = MagicMock()
            z.dnssec_state = "DISABLED"
            z.id = "ocid1.dns.zone.test"
            return z
        if call_count[0] == 2:  # update_zone
            return MagicMock()
        # poll
        z = MagicMock()
        z.dnssec_state = "ENABLED"
        return z

    with patch("app.connectors.executors.oci.oci_dns_dnssec_enable._run", side_effect=fake_run):
        from app.connectors.executors.oci.oci_dns_dnssec_enable import execute
        result = await execute({"zone_id": "ocid1.dns.zone.test"}, [], connector)

    assert result["status"] == "enabled"
    assert result["already_enabled"] is False
