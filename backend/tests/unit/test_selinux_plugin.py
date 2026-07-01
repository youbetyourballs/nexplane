# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for SELinux synthesizer plugin."""
import pytest
from app.services.security_policy.plugins.selinux import _synthesize, _delta_extract, _parse_avc_line


_SAMPLE_AVC = (
    "type=AVC msg=audit(1234.567:890): avc:  denied  { read } for "
    "pid=1234 comm=\"nginx\" name=\"nginx.conf\" dev=\"xvda1\" ino=12345 "
    "scontext=system_u:system_r:httpd_t:s0 "
    "tcontext=system_u:object_r:etc_t:s0 tclass=file permissive=1"
)

_SAMPLE_AVC_NETWORK = (
    "type=AVC msg=audit(1234.568:891): avc:  denied  { name_bind } for "
    "pid=1234 comm=\"nginx\" "
    "scontext=system_u:system_r:httpd_t:s0 "
    "tcontext=system_u:object_r:http_port_t:s0 tclass=tcp_socket permissive=1"
)


def test_parse_avc_line_file_read():
    result = _parse_avc_line(_SAMPLE_AVC)
    assert result is not None
    stype, ttype, tclass, perms = result
    assert stype == "httpd_t"
    assert ttype == "etc_t"
    assert tclass == "file"
    assert "read" in perms


def test_parse_avc_line_non_avc_returns_none():
    assert _parse_avc_line("type=SYSCALL msg=audit(...)") is None


def test_synthesize_empty_observations():
    profile = _synthesize({})
    assert "module_source" in profile
    assert "module_name" in profile
    assert "{service_name}" in profile["module_name"]


def test_synthesize_single_avc_line():
    obs = {"asset-1": [_SAMPLE_AVC]}
    profile = _synthesize(obs)
    text = profile["module_source"]
    assert "allow httpd_t etc_t:file" in text
    assert "read" in text


def test_synthesize_merges_perms_same_type_pair():
    avc2 = _SAMPLE_AVC.replace("{ read }", "{ write }")
    obs = {"asset-1": [_SAMPLE_AVC, avc2]}
    profile = _synthesize(obs)
    text = profile["module_source"]
    assert "allow httpd_t etc_t:file" in text
    assert "read" in text
    assert "write" in text


def test_delta_extract_roundtrip():
    obs = {"a": [_SAMPLE_AVC]}
    profile = _synthesize(obs)
    rules = _delta_extract(profile)
    assert any("allow httpd_t etc_t:file" in r for r in rules)


def test_delta_empty_profile():
    assert _delta_extract({}) == set()
