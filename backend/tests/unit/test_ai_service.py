import pytest
from app.services.ai_service import _build_change_types_text, AIService
from app.services.secrets_service import SecretsService


def test_build_change_types_text_contains_manifest_types():
    text = _build_change_types_text()
    assert "configure_selinux" in text
    assert "rotate_iam_key" in text
    assert "## hardening" in text


def test_build_change_types_text_no_hardcoded_remnants():
    text = _build_change_types_text()
    assert "EC2 lifecycle:" not in text
    assert "Agent commands (run directly on hosts" not in text


def test_build_system_prompt_includes_manifest():
    svc = AIService(SecretsService("dummy-key-for-test"))
    prompt = svc._build_system_prompt("harden nginx", [])
    assert "configure_selinux" in prompt
