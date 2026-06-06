

def test_verification_status_enum_values():
    from app.models.change_request import VerificationStatus
    assert VerificationStatus.passed.value == "passed"
    assert VerificationStatus.failed.value == "failed"
    assert VerificationStatus.unsupported.value == "unsupported"
    assert VerificationStatus.manual_required.value == "manual_required"
    assert VerificationStatus.skipped_development_only.value == "skipped_development_only"


def test_change_request_has_verification_status_field():
    from app.models.change_request import ChangeRequest
    assert hasattr(ChangeRequest, "verification_status")


def test_verification_status_column_is_nullable():
    from app.models.change_request import ChangeRequest
    col = ChangeRequest.__table__.columns.get("verification_status")
    assert col is not None
    assert col.nullable is True
