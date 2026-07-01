# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# backend/app/services/security_policy/synthesizer.py
"""Security policy synthesizer — delegates to policy plugins."""


def synthesize_seccomp(raw_observations: dict[str, list[str]]) -> dict:
    """Kept for backward compatibility. Use plugin.synthesize() for new code."""
    from app.services.security_policy.plugins.seccomp import SECCOMP_PLUGIN
    return SECCOMP_PLUGIN.synthesize(raw_observations)


def compute_delta(prior: dict, current: dict, policy_type: str = "seccomp") -> dict:
    """Return {added, removed} rule sets between two profiles."""
    from app.services.security_policy.plugins import get_plugin
    plugin = get_plugin(policy_type)
    prior_set = plugin.delta_extract(prior)
    current_set = plugin.delta_extract(current)
    return {
        "added": sorted(current_set - prior_set),
        "removed": sorted(prior_set - current_set),
    }
