def test_policy_generate_router_exists():
    from app.routers.policy_generate import router, POLICY_PROMPTS
    assert "seccomp" in POLICY_PROMPTS
    assert "apparmor" in POLICY_PROMPTS
    assert "iptables" in POLICY_PROMPTS
    routes = [r.path for r in router.routes]
    assert any("generate" in p for p in routes)
