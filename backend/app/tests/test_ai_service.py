import pytest
from app.services.ai_service import AIService
from app.services.secrets_service import SecretsService


def make_service():
    return AIService(SecretsService("test-key-32-chars-minimum-pad!!"))


def test_parse_proposal_extracts_json():
    svc = make_service()
    text = (
        "Here is my proposed plan based on the information provided.\n\n"
        "<nexplane-proposal>\n"
        '[{"title": "Update firewall", "change_type": "security_group_update", '
        '"suggested_assets": ["fw-01"], "desired_outcome_sketch": {}, "notes": "First"}]\n'
        "</nexplane-proposal>"
    )
    result = svc._parse_proposal(text)
    assert result is not None
    assert len(result) == 1
    assert result[0]["title"] == "Update firewall"
    assert result[0]["change_type"] == "security_group_update"


def test_parse_proposal_returns_none_when_absent():
    svc = make_service()
    result = svc._parse_proposal("No proposal here, just a clarifying question.")
    assert result is None


def test_parse_proposal_returns_none_on_invalid_json():
    svc = make_service()
    text = "<nexplane-proposal>not valid json</nexplane-proposal>"
    result = svc._parse_proposal(text)
    assert result is None


def test_strip_proposal_tags_removes_block():
    svc = make_service()
    text = "Here is my plan.\n\n<nexplane-proposal>[{}]</nexplane-proposal>"
    result = svc._strip_proposal_tags(text)
    assert "<nexplane-proposal>" not in result
    assert "Here is my plan." in result


def test_build_system_prompt_includes_goal():
    svc = make_service()
    prompt = svc._build_system_prompt("Microsegmentation for payments", [])
    assert "Microsegmentation for payments" in prompt
    assert "<nexplane-proposal>" in prompt


def test_build_system_prompt_includes_assets():
    svc = make_service()
    assets = [{"name": "payments-fw-01", "asset_type": "firewall", "environment": "prod", "tags": ["payments"]}]
    prompt = svc._build_system_prompt("Test goal", assets)
    assert "payments-fw-01" in prompt
    assert "firewall" in prompt
