# backend/app/services/security_policy/plugins/apparmor.py
from __future__ import annotations
import re
from collections import defaultdict

from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin

_OP_TO_MASK = {
    "file_read": "r",
    "file_write": "w",
    "file_exec": "ix",
    "file_append": "a",
    "file_link": "l",
}

_GLOB_RULES = [
    (re.compile(r"^(/var/log/[^/]+)/.*"), r"\1/**"),
    (re.compile(r"^(/etc/[^/]+)/.*"),     r"\1/**"),
    (re.compile(r"^(/var/lib/[^/]+)/.*"), r"\1/**"),
    (re.compile(r"^(/run/[^/]+)/.*"),     r"\1/*"),
    (re.compile(r"^/tmp/.*"),             "/tmp/**"),
]


def _glob_path(path: str) -> str:
    for pattern, replacement in _GLOB_RULES:
        if pattern.match(path):
            return pattern.sub(replacement, path)
    return path


def _synthesize(raw_observations: dict) -> dict:
    file_rules: dict[str, set[str]] = defaultdict(set)
    capabilities: set[str] = set()
    network_protos: set[str] = set()

    for events in raw_observations.values():
        if not events:
            continue
        for event in events:
            op = event.get("operation", "")
            resource = event.get("resource", "")
            if op in _OP_TO_MASK:
                file_rules[_glob_path(resource)].add(_OP_TO_MASK[op])
            elif op == "capability":
                capabilities.add(resource)
            elif op == "network":
                network_protos.add(resource)

    lines = [
        "#include <tunables/global>",
        "",
        "/usr/sbin/{service_name} {",
        "  #include <abstractions/base>",
        "",
    ]
    for cap in sorted(capabilities):
        lines.append(f"  capability {cap},")
    if capabilities:
        lines.append("")
    for proto in sorted(network_protos):
        lines.append(f"  network {proto},")
    if network_protos:
        lines.append("")
    for path in sorted(file_rules):
        mask = "".join(sorted(file_rules[path]))
        lines.append(f"  {path} {mask},")
    lines.append("}")

    return {
        "profile_name": "nexplane-{service_name}",  # executor substitutes service_name
        "mode": "complain",
        "profile_text": "\n".join(lines),
    }


def _delta_extract(profile: dict) -> set:
    text = profile.get("profile_text", "")
    rules: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip().rstrip(",")
        if (
            stripped
            and not stripped.startswith("#")
            and stripped not in ("{", "}")
            and not stripped.startswith("/usr/sbin/")
        ):
            rules.add(stripped)
    return rules


APPARMOR_PLUGIN = PolicyPlugin(
    policy_type="apparmor",
    learn_command="apparmor_learn",
    synthesize=_synthesize,
    cr_change_type=ChangeType.configure_apparmor,
    delta_extract=_delta_extract,
    cr_title_template="Apply AppArmor profile — {service_name}",
    cr_description_template=(
        "AppArmor profile synthesized from soak session. "
        "Rules: {rule_count}. Partial observation: {partial}."
    ),
)
