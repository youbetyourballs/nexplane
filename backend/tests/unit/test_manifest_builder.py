# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for CR manifest inference pipeline."""
import pytest
from app.services.manifest_builder import (
    build_manifest,
    get_manifest,
    _infer_domain,
    _infer_action_class,
    _infer_touches,
    _infer_rollback_type,
    _infer_preconditions,
    _infer_effects,
)


# ---- Minimal CR definition fixtures ----

def _defn(change_type, rollback_action=None, parameters=None, preflight_checks=None):
    d = {
        "change_type": change_type,
        "display_name": change_type.replace("_", " ").title(),
        "steps": [{"generic_action": change_type, "purpose": "execute", "required": True}],
        "preflight_checks": preflight_checks or ["connector_reachable"],
        "verification_methods": ["api_check"],
    }
    if rollback_action:
        d["rollback_action"] = rollback_action
        d["rollback_connector_type"] = "nexplane_agent"
    if parameters:
        d["parameters"] = parameters
    return d


# ---- Domain inference ----

def test_infer_domain_ec2():
    assert _infer_domain("ec2_stop") == "aws_compute"

def test_infer_domain_iam():
    assert _infer_domain("iam_user_create") == "aws_identity"

def test_infer_domain_attach_iam():
    assert _infer_domain("attach_iam_policy") == "aws_identity"

def test_infer_domain_rotate():
    assert _infer_domain("rotate_iam_key") == "credential_rotation"

def test_infer_domain_configure_selinux():
    assert _infer_domain("configure_selinux") == "hardening"

def test_infer_domain_configure_seccomp():
    assert _infer_domain("configure_seccomp") == "hardening"

def test_infer_domain_selinux_learn():
    assert _infer_domain("selinux_learn") == "hardening"

def test_infer_domain_isolate():
    assert _infer_domain("isolate_host") == "incident_response"

def test_infer_domain_patch():
    assert _infer_domain("patch_campaign") == "compliance"

def test_infer_domain_gcp():
    assert _infer_domain("gcp_firewall_create") == "gcp"

def test_infer_domain_gce():
    assert _infer_domain("gce_instance_create") == "gcp"

def test_infer_domain_azure():
    assert _infer_domain("azure_vm_create") == "azure"

def test_infer_domain_fallback():
    assert _infer_domain("remote_command") == "infrastructure"


# ---- Action class inference ----

def test_infer_action_class_create():
    assert _infer_action_class("ec2_launch") == "create"

def test_infer_action_class_delete():
    assert _infer_action_class("ec2_terminate") == "delete"

def test_infer_action_class_configure():
    assert _infer_action_class("configure_selinux") == "configure"

def test_infer_action_class_rotate():
    assert _infer_action_class("rotate_iam_key") == "rotate"

def test_infer_action_class_patch():
    assert _infer_action_class("patch_campaign") == "patch"

def test_infer_action_class_scan():
    assert _infer_action_class("trivy_scan") == "scan"

def test_infer_action_class_audit():
    assert _infer_action_class("authorized_keys_audit") == "audit"

def test_infer_action_class_fallback():
    assert _infer_action_class("remote_command") == "execute"


# ---- Rollback type inference ----

def test_infer_rollback_reversible():
    defn = _defn("configure_selinux", rollback_action="configure_selinux")
    assert _infer_rollback_type(defn) == "reversible"

def test_infer_rollback_permanent():
    defn = _defn("rotate_iam_key")
    assert _infer_rollback_type(defn) == "permanent"

def test_infer_rollback_snapshot_based():
    defn = _defn("rds_instance_delete", rollback_action="restore_rds_snapshot")
    assert _infer_rollback_type(defn) == "snapshot_based"


# ---- Precondition inference ----

def test_infer_preconditions_service_name():
    defn = _defn("configure_selinux", parameters={
        "service_name": {"type": "string", "required": True}
    })
    preconditions = _infer_preconditions(defn)
    assert "target service identified" in preconditions

def test_infer_preconditions_asset_exists():
    defn = _defn("configure_selinux", preflight_checks=["connector_reachable", "asset_exists"])
    preconditions = _infer_preconditions(defn)
    assert "target asset reachable" in preconditions

def test_infer_preconditions_connector():
    defn = _defn("configure_selinux", preflight_checks=["connector_reachable"])
    preconditions = _infer_preconditions(defn)
    assert "connector credential available" in preconditions

def test_infer_preconditions_unknown_required_param():
    defn = _defn("something", parameters={
        "widget_id": {"type": "string", "required": True}
    })
    preconditions = _infer_preconditions(defn)
    assert "widget_id provided" in preconditions


# ---- Effects inference ----

def test_infer_effects_configure_reversible():
    defn = _defn("configure_selinux", rollback_action="configure_selinux")
    effects = _infer_effects(defn)
    assert any("reversible" in e for e in effects)

def test_infer_effects_rotate():
    defn = _defn("rotate_iam_key")
    effects = _infer_effects(defn)
    assert any("replaced" in e or "rotated" in e for e in effects)

def test_infer_effects_delete_permanent():
    defn = _defn("ec2_terminate")
    effects = _infer_effects(defn)
    assert any("permanent" in e or "deleted" in e or "terminated" in e for e in effects)


# ---- Full manifest build ----

def test_full_manifest_count():
    entries = build_manifest()
    assert len(entries) >= 390

def test_manifest_entry_has_required_fields():
    entries = build_manifest()
    required = {"change_type", "display_name", "domain", "action_class",
                "touches", "preconditions", "effects", "rollback_type"}
    for entry in entries[:10]:
        missing = required - entry.keys()
        assert not missing, f"{entry['change_type']} missing {missing}"

def test_get_manifest_filter_domain():
    entries = get_manifest(domain="hardening")
    assert all(e["domain"] == "hardening" for e in entries)
    types = {e["change_type"] for e in entries}
    assert "configure_selinux" in types
    assert "configure_seccomp" in types

def test_get_manifest_filter_action_class():
    entries = get_manifest(action_class="rotate")
    assert all(e["action_class"] == "rotate" for e in entries)
    types = {e["change_type"] for e in entries}
    assert "rotate_iam_key" in types

def test_get_manifest_filter_rollback_type():
    entries = get_manifest(rollback_type="permanent")
    assert all(e["rollback_type"] == "permanent" for e in entries)

def test_get_manifest_filter_combined():
    entries = get_manifest(domain="hardening", rollback_type="reversible")
    assert all(e["domain"] == "hardening" and e["rollback_type"] == "reversible" for e in entries)
    assert len(entries) > 0
