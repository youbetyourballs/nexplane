# backend/tests/unit/test_ebpf_policy_plugins.py
import pytest

def test_plugin_registry_ebpf_network():
    from app.services.security_policy.plugins import get_plugin
    with pytest.raises(ValueError, match="ebpf_network"):
        get_plugin("ebpf_network")

def test_plugin_registry_ebpf_lsm():
    from app.services.security_policy.plugins import get_plugin
    with pytest.raises(ValueError, match="ebpf_lsm"):
        get_plugin("ebpf_lsm")
