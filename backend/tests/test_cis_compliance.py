import pytest
from app.compliance.cis_v8_map import CIS_V8_CONTROLS, compute_cis_summary


def test_control_map_has_18_controls():
    assert len(CIS_V8_CONTROLS) == 18


def test_control_ids_are_1_through_18():
    ids = [c["id"] for c in CIS_V8_CONTROLS]
    assert ids == list(range(1, 19))


def test_control_1_is_asset_coverage():
    ctrl = next(c for c in CIS_V8_CONTROLS if c["id"] == 1)
    assert ctrl["method"] == "asset_coverage"


def test_controls_4_to_8_are_agent_audit():
    for cid in [4, 5, 6, 7, 8]:
        ctrl = next(c for c in CIS_V8_CONTROLS if c["id"] == cid)
        assert ctrl["method"] == "agent_audit", f"Control {cid} should be agent_audit"


def test_controls_2_3_and_9_to_18_are_not_tracked():
    not_tracked_ids = [2, 3] + list(range(9, 19))
    for cid in not_tracked_ids:
        ctrl = next(c for c in CIS_V8_CONTROLS if c["id"] == cid)
        assert ctrl["method"] == "not_tracked", f"Control {cid} should be not_tracked"


def test_compute_cis_summary_asset_coverage_all_pass():
    assets = [
        {"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": {}},
        {"id": "a2", "name": "web-02", "connector_id": "c2", "asset_metadata": {}},
    ]
    summary = compute_cis_summary(assets)
    ctrl1 = next(c for c in summary["controls"] if c["id"] == 1)
    assert ctrl1["score"] == 1.0
    assert ctrl1["assets_passing"] == 2
    assert ctrl1["assets_total"] == 2
    assert len(ctrl1["checks"][0]["failing_assets"]) == 0


def test_compute_cis_summary_asset_coverage_one_fail():
    assets = [
        {"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": {}},
        {"id": "a2", "name": "db-01",  "connector_id": None,  "asset_metadata": {}},
    ]
    summary = compute_cis_summary(assets)
    ctrl1 = next(c for c in summary["controls"] if c["id"] == 1)
    assert ctrl1["score"] == pytest.approx(0.5)
    assert ctrl1["assets_passing"] == 1
    assert ctrl1["assets_total"] == 2
    failing = ctrl1["checks"][0]["failing_assets"]
    assert len(failing) == 1
    assert failing[0]["name"] == "db-01"


def test_compute_cis_summary_agent_audit_aggregates_checks():
    cis_data = {
        "cis_compliance": {
            "latest": {
                "collected_at": "2026-05-08T00:00:00Z",
                "controls": [
                    {"id": "1.1.1", "section": "1.1", "title": "Disable unused filesystems",
                     "status": "pass", "expected": "disabled", "actual": "disabled"},
                    {"id": "3.1.1", "section": "3.1", "title": "Disable IP forwarding",
                     "status": "fail", "expected": "0", "actual": "1"},
                ]
            }
        }
    }
    assets = [
        {"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": cis_data},
    ]
    summary = compute_cis_summary(assets)
    ctrl4 = next(c for c in summary["controls"] if c["id"] == 4)
    assert ctrl4["score"] == 0.0
    assert ctrl4["assets_passing"] == 0
    assert ctrl4["assets_total"] == 1
    check_titles = [ch["title"] for ch in ctrl4["checks"]]
    assert "Disable unused filesystems" in check_titles
    assert "Disable IP forwarding" in check_titles
    fail_check = next(ch for ch in ctrl4["checks"] if ch["title"] == "Disable IP forwarding")
    assert fail_check["fail_count"] == 1
    assert fail_check["failing_assets"][0]["name"] == "web-01"
    assert "Expected: 0" in fail_check["failing_assets"][0]["detail"]


def test_compute_cis_summary_asset_all_checks_pass():
    cis_data = {
        "cis_compliance": {
            "latest": {
                "collected_at": "2026-05-08T00:00:00Z",
                "controls": [
                    {"id": "1.1.1", "section": "1.1", "title": "Disable unused filesystems",
                     "status": "pass", "expected": "disabled", "actual": "disabled"},
                ]
            }
        }
    }
    assets = [{"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": cis_data}]
    summary = compute_cis_summary(assets)
    ctrl4 = next(c for c in summary["controls"] if c["id"] == 4)
    assert ctrl4["score"] == 1.0
    assert ctrl4["assets_passing"] == 1


def test_compute_cis_summary_not_tracked_controls_have_null_score():
    assets = [{"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": {}}]
    summary = compute_cis_summary(assets)
    for cid in [2, 3] + list(range(9, 19)):
        ctrl = next(c for c in summary["controls"] if c["id"] == cid)
        assert ctrl["score"] is None
        assert ctrl["checks"] == []


def test_compute_cis_summary_overall_score_is_mean_of_tracked():
    assets = [{"id": "a1", "name": "web-01", "connector_id": "c1", "asset_metadata": {}}]
    summary = compute_cis_summary(assets)
    assert summary["overall_score"] == 1.0
    assert summary["tracked_controls"] == 6


def test_compute_cis_summary_last_updated_is_most_recent():
    assets = [
        {"id": "a1", "name": "web-01", "connector_id": "c1",
         "asset_metadata": {"cis_compliance": {"latest": {"collected_at": "2026-04-01T00:00:00Z", "controls": []}}}},
        {"id": "a2", "name": "web-02", "connector_id": "c2",
         "asset_metadata": {"cis_compliance": {"latest": {"collected_at": "2026-05-08T12:00:00Z", "controls": []}}}},
    ]
    summary = compute_cis_summary(assets)
    assert summary["last_updated"] == "2026-05-08T12:00:00Z"


def test_compute_cis_summary_no_assets_returns_null_scores():
    summary = compute_cis_summary([])
    assert summary["overall_score"] is None
    assert summary["last_updated"] is None
    for ctrl in summary["controls"]:
        if ctrl["method"] != "not_tracked":
            assert ctrl["score"] is None
