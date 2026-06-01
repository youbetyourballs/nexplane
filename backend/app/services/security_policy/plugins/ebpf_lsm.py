# backend/app/services/security_policy/plugins/ebpf_lsm.py
from __future__ import annotations
import re
from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin

_DIR_GLOB_RULES = [
    re.compile(r"^(/etc/[^/]+)/[^/]+$"),
    re.compile(r"^(/var/log/[^/]+)/[^/]+$"),
    re.compile(r"^(/var/lib/[^/]+)/[^/]+$"),
    re.compile(r"^(/usr/[^/]+/[^/]+)/[^/]+$"),
    re.compile(r"^(/run/[^/]+)/[^/]+$"),
]

_TMP_RE = re.compile(r"^/tmp/[^/]+$")


def _glob_path(path: str) -> str:
    if _TMP_RE.match(path):
        return "/tmp/*"
    for pattern in _DIR_GLOB_RULES:
        m = pattern.match(path)
        if m:
            return m.group(1) + "/*"
    return path


def _synthesize(raw_observations: dict) -> dict:
    seen: set[tuple] = set()
    rules: list[dict] = []
    for events in raw_observations.values():
        if not events:
            continue
        for event in events:
            syscall = event.get("syscall", "")
            path_pattern = _glob_path(event.get("path", ""))
            process = event.get("process", "*")
            key = (syscall, path_pattern, process)
            if key in seen:
                continue
            seen.add(key)
            rules.append({"syscall": syscall, "path_pattern": path_pattern, "process": process, "action": "allow"})
    return {
        "rules": sorted(rules, key=lambda r: (r["syscall"], r["path_pattern"])),
        "default_action": "audit",
    }


def _delta_extract(profile: dict) -> set:
    return {
        (r["syscall"], r["path_pattern"], r["process"])
        for r in profile.get("rules", [])
    }


EBPF_LSM_PLUGIN = PolicyPlugin(
    policy_type="ebpf_lsm",
    learn_command="ebpf_lsm_soak",
    synthesize=_synthesize,
    cr_change_type=ChangeType.configure_ebpf_lsm,
    delta_extract=_delta_extract,
    cr_title_template="Apply eBPF LSM policy — {service_name}",
    cr_description_template=(
        "eBPF LSM kernel policy synthesized from soak session. "
        "Rules: {rule_count}. Partial observation: {partial}."
    ),
)
