"""Unit tests for the seccomp profile synthesizer."""
import pytest
from app.services.security_policy.synthesizer import synthesize_seccomp, compute_delta


def test_synthesize_merges_all_asset_syscalls():
    raw = {
        "asset-1": ["read", "write", "open"],
        "asset-2": ["read", "close", "mmap"],
    }
    profile = synthesize_seccomp(raw)
    allowed = set(profile["syscalls"][0]["names"])
    assert allowed == {"read", "write", "open", "close", "mmap"}


def test_synthesize_deduplicates():
    raw = {"asset-1": ["read", "write", "read"], "asset-2": ["write"]}
    profile = synthesize_seccomp(raw)
    names = profile["syscalls"][0]["names"]
    assert names == sorted(set(names))
    assert names.count("read") == 1


def test_synthesize_structure():
    raw = {"asset-1": ["read"]}
    profile = synthesize_seccomp(raw)
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert "SCMP_ARCH_X86_64" in profile["architectures"]
    assert profile["syscalls"][0]["action"] == "SCMP_ACT_ALLOW"


def test_synthesize_empty_observations():
    profile = synthesize_seccomp({})
    assert profile["syscalls"][0]["names"] == []


def test_synthesize_empty_asset_list():
    raw = {"asset-1": [], "asset-2": []}
    profile = synthesize_seccomp(raw)
    assert profile["syscalls"][0]["names"] == []


def test_compute_delta_added_and_removed():
    prior = _make_profile(["read", "write", "open"])
    current = _make_profile(["read", "write", "mmap"])
    delta = compute_delta(prior, current)
    assert delta["added"] == ["mmap"]
    assert delta["removed"] == ["open"]


def test_compute_delta_no_change():
    prior = _make_profile(["read", "write"])
    current = _make_profile(["read", "write"])
    delta = compute_delta(prior, current)
    assert delta["added"] == []
    assert delta["removed"] == []


def test_compute_delta_only_additions():
    prior = _make_profile(["read"])
    current = _make_profile(["read", "write", "open"])
    delta = compute_delta(prior, current)
    assert set(delta["added"]) == {"write", "open"}
    assert delta["removed"] == []


def test_compute_delta_only_removals():
    prior = _make_profile(["read", "write", "open"])
    current = _make_profile(["read"])
    delta = compute_delta(prior, current)
    assert delta["added"] == []
    assert set(delta["removed"]) == {"write", "open"}


def _make_profile(syscalls: list[str]) -> dict:
    return {
        "defaultAction": "SCMP_ACT_ERRNO",
        "architectures": ["SCMP_ARCH_X86_64"],
        "syscalls": [{"names": sorted(syscalls), "action": "SCMP_ACT_ALLOW"}],
    }
