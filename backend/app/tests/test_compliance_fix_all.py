# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def test_compliance_attestation_model_exists():
    from app.models.compliance_attestation import ComplianceAttestation
    assert hasattr(ComplianceAttestation, "control_id")
    assert hasattr(ComplianceAttestation, "evidence_description")
    assert hasattr(ComplianceAttestation, "expires_at")
    assert hasattr(ComplianceAttestation, "attested_by")
    assert hasattr(ComplianceAttestation, "organization_id")


def test_compliance_attestation_table_name():
    from app.models.compliance_attestation import ComplianceAttestation
    assert ComplianceAttestation.__tablename__ == "compliance_attestations"
