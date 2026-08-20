# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from app.models.drift import ResourceState, DriftPolicy, DriftEvent


def test_models_importable():
    assert ResourceState.__tablename__ == "resource_states"
    assert DriftPolicy.__tablename__ == "drift_policies"
    assert DriftEvent.__tablename__ == "drift_events"


import pytest
from app.services.drift_service import (
    compute_diff, normalize_state, SURFACE_SEVERITY,
    HOST_SURFACES, CLOUD_SURFACES,
)


def test_compute_diff_empty_when_identical():
    state = {"PermitRootLogin": "no", "Port": "22"}
    assert compute_diff(state, state.copy()) == {}


def test_compute_diff_added():
    baseline = {"Port": "22"}
    observed = {"Port": "22", "PermitRootLogin": "no"}
    diff = compute_diff(baseline, observed)
    assert diff == {"added": {"PermitRootLogin": "no"}, "removed": {}, "changed": {}}


def test_compute_diff_removed():
    baseline = {"Port": "22", "X11Forwarding": "yes"}
    observed = {"Port": "22"}
    diff = compute_diff(baseline, observed)
    assert diff == {"added": {}, "removed": {"X11Forwarding": "yes"}, "changed": {}}


def test_compute_diff_changed():
    baseline = {"Port": "22"}
    observed = {"Port": "2222"}
    diff = compute_diff(baseline, observed)
    assert diff == {"added": {}, "removed": {}, "changed": {"Port": {"from": "22", "to": "2222"}}}


def test_compute_diff_all_three():
    baseline = {"a": "1", "b": "2", "c": "3"}
    observed = {"a": "X", "b": "2", "d": "4"}
    diff = compute_diff(baseline, observed)
    assert diff["added"] == {"d": "4"}
    assert diff["removed"] == {"c": "3"}
    assert diff["changed"] == {"a": {"from": "1", "to": "X"}}


def test_normalize_state_sorts_keys():
    raw = {"z": "last", "a": "first"}
    result = normalize_state("ssh_config", raw)
    assert list(result.keys()) == ["a", "z"]


def test_surface_severity_all_surfaces_covered():
    all_surfaces = HOST_SURFACES | CLOUD_SURFACES
    for s in all_surfaces:
        assert s in SURFACE_SEVERITY, f"Missing severity for {s}"
        assert SURFACE_SEVERITY[s] in ("high", "medium", "low")
