# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from app.services.ai_service import AIService, _build_asset_context_text, _SYSTEM_PROMPT_TEMPLATE
from app.services.secrets_service import SecretsService


def _make_asset(name, asset_type, environment, criticality=None, connector_type=None, tags=None):
    return {
        "name": name,
        "asset_type": asset_type,
        "environment": environment,
        "criticality": criticality,
        "connector_type": connector_type,
        "tags": tags or [],
    }


def test_build_asset_context_text_groups_servers_by_environment():
    assets = [
        _make_asset("prod-web-01", "server", "prod", "critical", "aws", ["web"]),
        _make_asset("staging-web-01", "server", "staging", "medium", "aws"),
        _make_asset("prod-db-01", "server", "prod", "critical", "aws", ["db"]),
    ]
    text = _build_asset_context_text(assets)
    assert "Servers — Production (2)" in text
    assert "Servers — Staging (1)" in text
    assert "prod-web-01" in text
    assert "prod-db-01" in text
    assert "staging-web-01" in text
    assert text.index("Production") < text.index("Staging")


def test_build_asset_context_text_groups_cloud_accounts():
    assets = [
        _make_asset("AWS Prod", "cloud_account", "prod", connector_type="aws"),
        _make_asset("GCP Prod", "cloud_account", "prod", connector_type="gcp"),
    ]
    text = _build_asset_context_text(assets)
    assert "Cloud Accounts (2)" in text
    assert "AWS Prod" in text
    assert "GCP Prod" in text


def test_build_asset_context_text_puts_other_types_in_other_group():
    assets = [
        _make_asset("prod-alb-01", "load_balancer", "prod"),
        _make_asset("nexplane.internal", "dns_zone", "prod"),
    ]
    text = _build_asset_context_text(assets)
    assert "Other (" in text
    assert "prod-alb-01" in text
    assert "nexplane.internal" in text


def test_build_asset_context_text_empty_returns_placeholder():
    text = _build_asset_context_text([])
    assert "No assets" in text


def test_build_asset_context_text_includes_tags():
    assets = [_make_asset("prod-web-01", "server", "prod", tags=["nginx", "web"])]
    text = _build_asset_context_text(assets)
    assert "nginx" in text
    assert "web" in text


def test_system_prompt_template_contains_required_sections():
    from app.services.ai_service import AIService
    from unittest.mock import MagicMock
    svc = AIService(MagicMock())
    assets = [_make_asset("prod-web-01", "server", "prod", "critical", "aws")]
    prompt = svc._build_system_prompt("Harden prod servers", assets)
    assert "Harden prod servers" in prompt
    assert "prod-web-01" in prompt
    assert "nexplane-proposal" in prompt
    assert '"seq"' in prompt
    assert '"target_assets"' in prompt
    assert '"depends_on"' in prompt
    assert '"desired_outcome"' in prompt


def test_parse_proposal_handles_new_field_names():
    from app.services.ai_service import AIService
    from unittest.mock import MagicMock
    svc = AIService(MagicMock())
    text = '''
Some explanation.
<nexplane-proposal>
[{"seq": 1, "title": "Harden SSH", "change_type": "agent_ossecurity",
  "target_assets": ["prod-web-01"], "desired_outcome": {"dry_run": false},
  "depends_on": [], "notes": "CIS 5.2"}]
</nexplane-proposal>
'''
    result = svc._parse_proposal(text)
    assert result is not None
    assert result[0]["title"] == "Harden SSH"
    assert result[0]["target_assets"] == ["prod-web-01"]
    assert result[0]["desired_outcome"] == {"dry_run": False}
    assert result[0]["seq"] == 1
    assert result[0]["depends_on"] == []


def test_parse_proposal_handles_old_field_names_for_backward_compat():
    from app.services.ai_service import AIService
    from unittest.mock import MagicMock
    svc = AIService(MagicMock())
    text = '''
<nexplane-proposal>
[{"title": "Old format", "change_type": "ec2_stop",
  "suggested_assets": ["prod-web-01"], "desired_outcome_sketch": {"dry_run": true}}]
</nexplane-proposal>
'''
    result = svc._parse_proposal(text)
    assert result is not None
    assert result[0]["title"] == "Old format"
    assert result[0]["target_assets"] == ["prod-web-01"]
    assert result[0]["desired_outcome"] == {"dry_run": True}
    assert result[0].get("seq") is None
    assert result[0].get("depends_on") == []


def test_build_prompt_preview_returns_full_assembled_prompt():
    from app.services.ai_service import AIService
    from unittest.mock import MagicMock
    svc = AIService(MagicMock())
    assets = [_make_asset("prod-web-01", "server", "prod", "critical", "aws")]
    conversation = [{"role": "user", "content": "First message"}]
    preview = svc.build_prompt_preview(
        goal="Harden prod servers",
        asset_context=assets,
        conversation=conversation,
        draft_message="What should I do next?",
    )
    assert "Harden prod servers" in preview
    assert "prod-web-01" in preview
    assert "First message" in preview
    assert "What should I do next?" in preview


def test_build_system_prompt_handles_braces_in_goal():
    from app.services.ai_service import AIService
    from unittest.mock import MagicMock
    svc = AIService(MagicMock())
    assets = []
    # Should not raise KeyError even when goal contains curly braces
    prompt = svc._build_system_prompt("Deploy {service} to {env}", assets)
    assert "Deploy {service} to {env}" in prompt


def test_parse_proposal_returns_none_for_non_list_json():
    from app.services.ai_service import AIService
    from unittest.mock import MagicMock
    svc = AIService(MagicMock())
    text = '<nexplane-proposal>{"not": "a list"}</nexplane-proposal>'
    assert svc._parse_proposal(text) is None


def test_resolve_target_assets_to_ids():
    from app.routers.projects import _resolve_asset_ids

    name_to_id = {
        "prod-web-01": "uuid-1111",
        "prod-db-01": "uuid-2222",
    }
    proposed = [
        {"title": "Harden SSH", "change_type": "agent_ossecurity",
         "target_assets": ["prod-web-01", "prod-db-01"], "desired_outcome": {}, "seq": 1, "depends_on": []},
        {"title": "Unknown asset CR", "change_type": "ec2_stop",
         "target_assets": ["nonexistent-server"], "desired_outcome": {}, "seq": 2, "depends_on": [1]},
    ]
    resolved = _resolve_asset_ids(proposed, name_to_id)
    assert resolved[0]["target_asset_ids"] == ["uuid-1111", "uuid-2222"]
    assert resolved[1]["target_asset_ids"] == []


def test_resolve_asset_ids_handles_empty_input():
    from app.routers.projects import _resolve_asset_ids
    result = _resolve_asset_ids([], {})
    assert result == []


def test_resolve_asset_ids_handles_no_target_assets_key():
    from app.routers.projects import _resolve_asset_ids
    proposed = [{"title": "No targets", "change_type": "ec2_stop", "seq": 1}]
    result = _resolve_asset_ids(proposed, {"prod-web-01": "uuid-1"})
    assert result[0]["target_asset_ids"] == []
