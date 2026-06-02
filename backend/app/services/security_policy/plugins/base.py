# backend/app/services/security_policy/plugins/base.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

from app.models.change_request import ChangeType


def _default_observations_extractor(result: dict) -> list:
    return (
        result.get("syscalls_seen")
        or result.get("apparmor_events")
        or result.get("avc_lines")
        or result.get("flows")
        or result.get("events")
        or []
    )


@dataclass
class PolicyPlugin:
    policy_type: str
    learn_command: str
    synthesize: Callable[[dict], dict]
    cr_change_type: ChangeType
    delta_extract: Callable[[dict], set]
    cr_title_template: str        # receives {service_name}
    cr_description_template: str  # receives {rule_count}, {partial}
    observations_extractor: Callable[[dict], list] = _default_observations_extractor
