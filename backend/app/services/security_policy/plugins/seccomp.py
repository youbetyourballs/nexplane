# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# backend/app/services/security_policy/plugins/seccomp.py
from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin


def _synthesize(raw_observations: dict) -> dict:
    all_syscalls: set[str] = set()
    for syscalls in raw_observations.values():
        if syscalls:
            all_syscalls.update(syscalls)
    return {
        "defaultAction": "SCMP_ACT_ERRNO",
        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
        "syscalls": [{"names": sorted(all_syscalls), "action": "SCMP_ACT_ALLOW"}],
    }


def _delta_extract(profile: dict) -> set:
    syscalls = profile.get("syscalls", [])
    if not syscalls:
        return set()
    return set(syscalls[0].get("names", []))


SECCOMP_PLUGIN = PolicyPlugin(
    policy_type="seccomp",
    learn_command="seccomp_learn",
    synthesize=_synthesize,
    cr_change_type=ChangeType.configure_seccomp,
    delta_extract=_delta_extract,
    cr_title_template="Apply seccomp profile — {service_name}",
    cr_description_template=(
        "Seccomp profile synthesized from soak session. "
        "Syscalls allowed: {rule_count}. Partial observation: {partial}."
    ),
)
