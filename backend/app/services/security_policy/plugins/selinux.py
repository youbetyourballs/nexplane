from __future__ import annotations
import re
from collections import defaultdict

from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin

# Matches: avc:  denied  { perms } ... scontext=...:stype:... tcontext=...:ttype:... tclass=cls
_AVC_RE = re.compile(
    r'avc:\s+denied\s+\{([^}]+)\}.*?'
    r'scontext=\S+:\S+:(\w+):\S*\s+'
    r'tcontext=\S+:\S+:(\w+):\S*\s+'
    r'tclass=(\w+)'
)


def _parse_avc_line(line: str) -> tuple[str, str, str, frozenset[str]] | None:
    """Parse one AVC denial line. Returns (stype, ttype, tclass, perms) or None."""
    m = _AVC_RE.search(line)
    if not m:
        return None
    perms = frozenset(m.group(1).split())
    stype, ttype, tclass = m.group(2), m.group(3), m.group(4)
    return stype, ttype, tclass, perms


def _synthesize(raw_observations: dict) -> dict:
    """Union AVC lines across assets and generate a .te module source."""
    rules: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    for avc_lines in raw_observations.values():
        if not avc_lines:
            continue
        for line in avc_lines:
            parsed = _parse_avc_line(line)
            if parsed:
                stype, ttype, tclass, perms = parsed
                rules[(stype, ttype, tclass)].update(perms)

    module_name = "nexplane-{service_name}"

    if not rules:
        return {
            "module_name": module_name,
            "module_source": f"module {module_name} 1.0;\n\nrequire {{\n}}\n",
        }

    all_types: set[str] = set()
    class_perms: dict[str, set[str]] = defaultdict(set)
    for (stype, ttype, tclass), perms in rules.items():
        all_types.add(stype)
        all_types.add(ttype)
        class_perms[tclass].update(perms)

    lines = [f"module {module_name} 1.0;", "", "require {"]
    for t in sorted(all_types):
        lines.append(f"    type {t};")
    for cls in sorted(class_perms):
        perm_str = " ".join(sorted(class_perms[cls]))
        lines.append(f"    class {cls} {{ {perm_str} }};")
    lines.append("}")
    lines.append("")

    for (stype, ttype, tclass), perms in sorted(rules.items()):
        perm_str = " ".join(sorted(perms))
        lines.append(f"allow {stype} {ttype}:{tclass} {{ {perm_str} }};")

    return {
        "module_name": module_name,
        "module_source": "\n".join(lines) + "\n",
    }


def _delta_extract(profile: dict) -> set:
    """Extract allow rules from .te module source as a comparable set."""
    source = profile.get("module_source", "")
    rules: set[str] = set()
    for line in source.splitlines():
        stripped = line.strip().rstrip(";")
        if stripped.startswith("allow "):
            rules.add(stripped)
    return rules


SELINUX_PLUGIN = PolicyPlugin(
    policy_type="selinux",
    learn_command="selinux_learn",
    synthesize=_synthesize,
    cr_change_type=ChangeType.configure_selinux,
    delta_extract=_delta_extract,
    cr_title_template="Apply SELinux policy module — {service_name}",
    cr_description_template=(
        "SELinux policy module synthesized from soak session. "
        "Allow rules: {rule_count}. Partial observation: {partial}."
    ),
)
