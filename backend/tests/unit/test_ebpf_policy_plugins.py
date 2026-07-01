# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# backend/tests/unit/test_ebpf_policy_plugins.py
import pytest

def test_plugin_registry_ebpf_network():
    from app.services.security_policy.plugins import get_plugin
    plugin = get_plugin("ebpf_network")
    assert plugin.policy_type == "ebpf_network"

def test_plugin_registry_ebpf_lsm():
    from app.services.security_policy.plugins import get_plugin
    plugin = get_plugin("ebpf_lsm")
    assert plugin.policy_type == "ebpf_lsm"


def test_ebpf_network_synthesize_basic():
    from app.services.security_policy.plugins.ebpf_network import EBPF_NETWORK_PLUGIN
    raw = {
        "asset-1": [
            {"dst_ip": "8.8.8.8",  "dst_port": 53,  "protocol": "udp", "process": "systemd-resolved"},
            {"dst_ip": "10.0.1.5", "dst_port": 443, "protocol": "tcp", "process": "nginx"},
            {"dst_ip": "10.0.1.5", "dst_port": 443, "protocol": "tcp", "process": "nginx"},  # duplicate
        ],
        "asset-2": [
            {"dst_ip": "8.8.8.8", "dst_port": 53, "protocol": "udp", "process": "systemd-resolved"},
        ],
    }
    profile = EBPF_NETWORK_PLUGIN.synthesize(raw)
    assert profile["default_action"] == "audit"
    rules = profile["rules"]
    dns_rules = [r for r in rules if r["dst_port"] == 53]
    assert len(dns_rules) == 1
    https_rules = [r for r in rules if r["dst_port"] == 443]
    assert len(https_rules) == 1
    assert https_rules[0]["process"] == "nginx"


def test_ebpf_network_synthesize_empty():
    from app.services.security_policy.plugins.ebpf_network import EBPF_NETWORK_PLUGIN
    profile = EBPF_NETWORK_PLUGIN.synthesize({})
    assert profile["rules"] == []
    assert profile["default_action"] == "audit"


def test_ebpf_network_delta():
    from app.services.security_policy.plugins.ebpf_network import EBPF_NETWORK_PLUGIN
    prior = {
        "default_action": "audit",
        "rules": [
            {"dst_ip": "8.8.8.8", "dst_port": 53,  "protocol": "udp", "process": "systemd-resolved"},
            {"dst_ip": "10.0.1.5","dst_port": 443, "protocol": "tcp", "process": "nginx"},
        ],
    }
    current = {
        "default_action": "audit",
        "rules": [
            {"dst_ip": "8.8.8.8", "dst_port": 53,  "protocol": "udp", "process": "systemd-resolved"},
            {"dst_ip": "1.2.3.4", "dst_port": 80,  "protocol": "tcp", "process": "curl"},
        ],
    }
    delta = EBPF_NETWORK_PLUGIN.delta_extract(prior) - EBPF_NETWORK_PLUGIN.delta_extract(current)
    assert ("10.0.1.5", 443, "tcp") in delta  # removed
    added = EBPF_NETWORK_PLUGIN.delta_extract(current) - EBPF_NETWORK_PLUGIN.delta_extract(prior)
    assert ("1.2.3.4", 80, "tcp") in added  # added


def test_ebpf_network_default_action_always_audit():
    from app.services.security_policy.plugins.ebpf_network import EBPF_NETWORK_PLUGIN
    raw = {"asset-1": [{"dst_ip": "1.1.1.1", "dst_port": 443, "protocol": "tcp", "process": "curl"}]}
    profile = EBPF_NETWORK_PLUGIN.synthesize(raw)
    assert profile["default_action"] == "audit"


def test_ebpf_lsm_synthesize_basic():
    from app.services.security_policy.plugins.ebpf_lsm import EBPF_LSM_PLUGIN
    raw = {
        "asset-1": [
            {"syscall": "open",   "path": "/etc/nginx/nginx.conf", "process": "nginx", "uid": 0},
            {"syscall": "open",   "path": "/etc/nginx/nginx.conf", "process": "nginx", "uid": 0},  # dup
            {"syscall": "execve", "path": "/usr/sbin/nginx",       "process": "nginx", "uid": 0},
        ],
        "asset-2": [
            {"syscall": "open", "path": "/etc/resolv.conf", "process": "systemd-resolved", "uid": 101},
        ],
    }
    profile = EBPF_LSM_PLUGIN.synthesize(raw)
    assert profile["default_action"] == "audit"
    rules = profile["rules"]
    open_nginx = [r for r in rules if r["syscall"] == "open" and "nginx" in r["path_pattern"]]
    assert len(open_nginx) == 1
    assert open_nginx[0]["action"] == "allow"


def test_ebpf_lsm_synthesize_empty():
    from app.services.security_policy.plugins.ebpf_lsm import EBPF_LSM_PLUGIN
    profile = EBPF_LSM_PLUGIN.synthesize({})
    assert profile["rules"] == []
    assert profile["default_action"] == "audit"


def test_ebpf_lsm_delta():
    from app.services.security_policy.plugins.ebpf_lsm import EBPF_LSM_PLUGIN
    prior = {
        "default_action": "audit",
        "rules": [
            {"syscall": "open",   "path_pattern": "/etc/nginx/*", "process": "nginx", "action": "allow"},
            {"syscall": "execve", "path_pattern": "/usr/sbin/*",  "process": "nginx", "action": "allow"},
        ],
    }
    current = {
        "default_action": "audit",
        "rules": [
            {"syscall": "open",   "path_pattern": "/etc/nginx/*", "process": "nginx", "action": "allow"},
            {"syscall": "open",   "path_pattern": "/var/log/*",   "process": "nginx", "action": "allow"},
        ],
    }
    removed = EBPF_LSM_PLUGIN.delta_extract(prior) - EBPF_LSM_PLUGIN.delta_extract(current)
    assert ("execve", "/usr/sbin/*", "nginx") in removed
    added = EBPF_LSM_PLUGIN.delta_extract(current) - EBPF_LSM_PLUGIN.delta_extract(prior)
    assert ("open", "/var/log/*", "nginx") in added


def test_ebpf_lsm_default_action_always_audit():
    from app.services.security_policy.plugins.ebpf_lsm import EBPF_LSM_PLUGIN
    raw = {"asset-1": [{"syscall": "open", "path": "/tmp/x", "process": "bash", "uid": 1000}]}
    profile = EBPF_LSM_PLUGIN.synthesize(raw)
    assert profile["default_action"] == "audit"


def test_plugin_registry_both_registered():
    from app.services.security_policy.plugins import get_plugin
    assert get_plugin("ebpf_network").policy_type == "ebpf_network"
    assert get_plugin("ebpf_lsm").policy_type == "ebpf_lsm"
