# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import uuid

TEMPLATES = {
    "seccomp_rollout": {
        "description": "Learn syscall profile → apply audit mode → enforce → drift watch",
        "phases": [
            {"name": "Observe", "soak_hours": 0, "cr_types": ["seccomp_learn"]},
            {"name": "Apply Staging", "soak_hours": 72, "cr_types": ["configure_seccomp"]},
            {"name": "Apply Production", "soak_hours": 0, "cr_types": ["configure_seccomp"]},
        ],
    },
    "microsegmentation": {
        "description": "Map service traffic → define policies → pilot → full rollout",
        "phases": [
            {"name": "Traffic Mapping", "soak_hours": 0, "cr_types": ["iptables_log_baseline"]},
            {"name": "Pilot", "soak_hours": 168, "cr_types": ["configure_host_firewall"]},
            {"name": "Full Rollout", "soak_hours": 0, "cr_types": ["configure_host_firewall"]},
        ],
    },
    "apparmor_rollout": {
        "description": "AppArmor complain mode → collect denials → enforce profile",
        "phases": [
            {"name": "Complain Mode", "soak_hours": 168, "cr_types": ["configure_apparmor"]},
            {"name": "Enforce Staging", "soak_hours": 72, "cr_types": ["configure_apparmor"]},
            {"name": "Enforce Production", "soak_hours": 0, "cr_types": ["configure_apparmor"]},
        ],
    },
    "least_privilege_iam": {
        "description": "Audit IAM → generate minimal policies → apply staging → prod",
        "phases": [
            {"name": "Audit", "soak_hours": 0, "cr_types": ["audit_users_and_groups"]},
            {"name": "Apply Staging", "soak_hours": 168, "cr_types": ["user_scope_reduction"]},
            {"name": "Apply Production", "soak_hours": 0, "cr_types": ["user_scope_reduction"]},
        ],
    },
    "zero_trust_network": {
        "description": "Discover dependencies → default-deny → explicit allows",
        "phases": [
            {"name": "Map Dependencies", "soak_hours": 0, "cr_types": ["deep_discover"]},
            {"name": "Apply Default-Deny", "soak_hours": 336, "cr_types": ["configure_host_firewall"]},
            {"name": "Verify & Lock", "soak_hours": 0, "cr_types": ["configure_host_firewall"]},
        ],
    },
}


async def create_project_from_template(
    template_name: str,
    project_name: str,
    target_asset_ids: list[str],
    user_id: str,
    organization_id: str,
    db,
) -> dict:
    from app.models.project import Project
    from app.models.project_phase import ProjectPhase

    template = TEMPLATES.get(template_name)
    if not template:
        raise ValueError(f"Unknown template: {template_name}")

    project = Project(
        id=uuid.uuid4(),
        name=project_name,
        organization_id=uuid.UUID(organization_id),
        created_by=uuid.UUID(user_id),
        template=template_name,
    )
    if hasattr(project, 'goal'):
        project.goal = template["description"]
    if hasattr(project, 'description'):
        project.description = template["description"]
    db.add(project)

    phases = []
    for i, phase_def in enumerate(template["phases"]):
        phase = ProjectPhase(
            id=uuid.uuid4(),
            project_id=project.id,
            name=phase_def["name"],
            sequence=i,
            soak_hours=phase_def.get("soak_hours", 0),
            status="pending" if i > 0 else "in_progress",
        )
        db.add(phase)
        phases.append({"id": str(phase.id), "name": phase.name, "soak_hours": phase.soak_hours})

    await db.commit()
    return {"id": str(project.id), "name": project.name, "template": template_name, "phases": phases}
