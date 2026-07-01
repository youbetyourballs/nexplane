# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""BIND/RFC-2136 DNS client helpers using dnspython."""
from __future__ import annotations
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.tsig
import dns.tsigkeyring
import dns.update


def get_keyring(creds: dict):
    """Build TSIG keyring from credentials. Returns (keyring, key_name) or (None, None)."""
    key_name = creds.get("tsig_key_name", "").strip()
    key_secret = creds.get("tsig_key_secret", "").strip()
    if not key_name or not key_secret:
        return None, None
    algorithm_map = {
        "hmac-sha256": dns.tsig.HMAC_SHA256,
        "hmac-sha512": dns.tsig.HMAC_SHA512,
        "hmac-md5": dns.tsig.HMAC_MD5,
    }
    algorithm = algorithm_map.get(
        creds.get("tsig_algorithm", "hmac-sha256"), dns.tsig.HMAC_SHA256
    )
    keyring = dns.tsigkeyring.from_text({key_name: key_secret})
    return keyring, key_name


def make_update(zone: str, creds: dict) -> dns.update.UpdateMessage:
    """Return a prepared UpdateMessage for the given zone, with TSIG if configured."""
    keyring, key_name = get_keyring(creds)
    update = dns.update.UpdateMessage(zone, keyring=keyring, keyname=key_name)
    return update


def send_update(update: dns.update.UpdateMessage, server: str, port: int) -> dns.message.Message:
    """Send a DNS UPDATE via TCP and raise on non-NOERROR rcode."""
    response = dns.query.tcp(update, server, port=port, timeout=10)
    rcode = response.rcode()
    if rcode != dns.rcode.NOERROR:
        raise RuntimeError(f"DNS update failed: {dns.rcode.to_text(rcode)}")
    return response


def _resolve_zone(parameters: dict, creds: dict) -> str:
    """Return zone from parameters (override) or connector credentials."""
    return (parameters.get("zone") or creds.get("zone") or "").strip()
