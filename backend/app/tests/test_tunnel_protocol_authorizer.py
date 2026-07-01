# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for the reverse-tunnel frame protocol and destination authorizer.

These modules are pure (stdlib only) and do not import the app; the tests avoid
the app-importing conftest by importing only app.tunnel.*.
"""
from app.tunnel import protocol as p
from app.tunnel import authorizer as az


# --- protocol --------------------------------------------------------------

def test_frame_roundtrip():
    raw = p.encode(7, p.DATA, b"hello")
    f = p.decode(raw)
    assert f.stream_id == 7 and f.type == p.DATA and f.payload == b"hello"


def test_open_frame_and_parse():
    raw = p.open_frame(42, "db.internal", 5432)
    f = p.decode(raw)
    assert f.type == p.OPEN
    host, port = p.parse_open(f.payload)
    assert host == "db.internal" and port == 5432


def test_open_frame_ipv6_host():
    host, port = p.parse_open(b"2001:db8::1:443")
    assert host == "2001:db8::1" and port == 443


def test_decode_rejects_short_and_unknown():
    import pytest
    with pytest.raises(p.ProtocolError):
        p.decode(b"\x00\x00")
    with pytest.raises(p.ProtocolError):
        p.decode(p.encode(1, p.DATA)[:4] + b"\x99")  # unknown type byte
    with pytest.raises(p.ProtocolError):
        p.encode(1, 99)


def test_parse_open_rejects_bad_port():
    import pytest
    with pytest.raises(p.ProtocolError):
        p.parse_open(b"host:0")
    with pytest.raises(p.ProtocolError):
        p.parse_open(b"host:99999")


# --- authorizer ------------------------------------------------------------

def test_cidr_port_allow_and_deny():
    al = az.parse_allowlist(["10.0.0.0/8:5432", "192.168.1.10:443"])
    assert az.is_allowed(al, "10.1.2.3", 5432) is True
    assert az.is_allowed(al, "10.1.2.3", 5433) is False    # wrong port
    assert az.is_allowed(al, "172.16.0.1", 5432) is False  # outside CIDR
    assert az.is_allowed(al, "192.168.1.10", 443) is True
    assert az.is_allowed(al, "192.168.1.11", 443) is False


def test_port_range_and_wildcard():
    al = az.parse_allowlist(["10.0.0.0/24:1-1024", "10.1.0.0/16:*"])
    assert az.is_allowed(al, "10.0.0.5", 80) is True
    assert az.is_allowed(al, "10.0.0.5", 2000) is False
    assert az.is_allowed(al, "10.1.9.9", 65000) is True


def test_hostname_rule_exact_only():
    al = az.parse_allowlist(["db.internal:5432"])
    assert az.is_allowed(al, "db.internal", 5432) is True
    assert az.is_allowed(al, "DB.INTERNAL", 5432) is True   # case-insensitive
    assert az.is_allowed(al, "evil.internal", 5432) is False
    # a hostname rule must NOT match an arbitrary IP
    assert az.is_allowed(al, "10.0.0.1", 5432) is False


def test_deny_by_default_empty():
    assert az.is_allowed([], "10.0.0.1", 5432) is False


def test_bad_entries_rejected():
    import pytest
    with pytest.raises(az.AllowlistError):
        az.parse_rule("no-port")
    with pytest.raises(az.AllowlistError):
        az.parse_rule("10.0.0.0/8:70000")
