# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def test_project_has_risk_context_field():
    from app.models.project import Project
    assert hasattr(Project, 'risk_context')
