# backend/app/services/security_policy/plugins/ebpf_network.py
from __future__ import annotations
from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin


def _synthesize(raw_observations: dict) -> dict:
    seen: set[tuple] = set()
    rules: list[dict] = []
    for flows in raw_observations.values():
        if not flows:
            continue
        for flow in flows:
            key = (flow.get("dst_ip", ""), flow.get("dst_port", 0), flow.get("protocol", ""), flow.get("process", ""))
            if key in seen:
                continue
            seen.add(key)
            rules.append({
                "dst_ip": flow.get("dst_ip", ""),
                "dst_port": flow.get("dst_port", 0),
                "protocol": flow.get("protocol", "tcp"),
                "process": flow.get("process", "*"),
            })
    return {"rules": sorted(rules, key=lambda r: (r["dst_port"], r["dst_ip"])), "default_action": "audit"}


def _delta_extract(profile: dict) -> set:
    return {
        (r["dst_ip"], r["dst_port"], r["protocol"])
        for r in profile.get("rules", [])
    }


EBPF_NETWORK_PLUGIN = PolicyPlugin(
    policy_type="ebpf_network",
    learn_command="ebpf_network_soak",
    synthesize=_synthesize,
    cr_change_type=ChangeType.configure_ebpf_network,
    delta_extract=_delta_extract,
    cr_title_template="Apply eBPF network policy — {service_name}",
    cr_description_template=(
        "eBPF network egress policy synthesized from soak session. "
        "Rules: {rule_count}. Partial observation: {partial}."
    ),
)
