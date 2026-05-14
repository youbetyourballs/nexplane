"""Tests for the onboarding router."""
import pytest


def test_onboarding_checklist_endpoint_importable():
    from app.routers.onboarding import router
    routes = [r.path for r in router.routes]
    assert any("checklist" in p for p in routes)


def test_onboarding_connector_endpoint_importable():
    from app.routers.onboarding import router
    routes = [r.path for r in router.routes]
    assert any("connector" in p for p in routes)


def test_cis_summary_policy_baseline_scoring():
    """compute_cis_summary correctly scores Control 4 from policy_baseline_counts."""
    from app.compliance.cis_v8_map import compute_cis_summary

    summary = compute_cis_summary(
        assets=[],
        policy_baseline_counts={"total_servers": 10, "hardened_count": 8},
    )
    ctrl4 = next(c for c in summary["controls"] if c["id"] == 4)
    assert ctrl4["score"] == pytest.approx(0.8)
    assert ctrl4["assets_passing"] == 8
    assert ctrl4["assets_total"] == 10


def test_cis_summary_vuln_sla_scoring():
    """compute_cis_summary correctly scores Control 7 from vuln_sla_data."""
    from app.compliance.cis_v8_map import compute_cis_summary

    summary = compute_cis_summary(
        assets=[],
        vuln_sla_data={"total": 20, "within_sla": 15},
    )
    ctrl7 = next(c for c in summary["controls"] if c["id"] == 7)
    assert ctrl7["score"] == pytest.approx(0.75)
    assert ctrl7["assets_passing"] == 15
    assert ctrl7["assets_total"] == 20


def test_cis_summary_edr_coverage():
    """compute_cis_summary correctly scores Control 10 from edr_asset_ids."""
    from app.compliance.cis_v8_map import compute_cis_summary

    assets = [
        {"id": "a1", "name": "server-1", "connector_id": "c1", "asset_metadata": {}},
        {"id": "a2", "name": "server-2", "connector_id": "c1", "asset_metadata": {}},
        {"id": "a3", "name": "server-3", "connector_id": None, "asset_metadata": {}},
    ]
    summary = compute_cis_summary(
        assets=assets,
        edr_asset_ids={"a1", "a2"},
    )
    ctrl10 = next(c for c in summary["controls"] if c["id"] == 10)
    assert ctrl10["score"] == pytest.approx(2 / 3)
    assert ctrl10["assets_passing"] == 2
    assert ctrl10["assets_total"] == 3
