# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def test_templates_have_required_keys():
    from app.services.project_template_service import TEMPLATES
    for name, tmpl in TEMPLATES.items():
        assert "description" in tmpl
        assert "phases" in tmpl
        assert len(tmpl["phases"]) >= 2, f"Template {name} should have >= 2 phases"
