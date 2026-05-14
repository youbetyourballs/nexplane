def test_review_entry_has_remediation_cr_field():
    """Verify ReviewEntry model has remediation_cr_id."""
    from app.models.review_campaign import ReviewEntry
    assert hasattr(ReviewEntry, "remediation_cr_id")


def test_review_entry_remediation_cr_id_nullable():
    """remediation_cr_id should be nullable (optional field)."""
    from app.models.review_campaign import ReviewEntry
    col = ReviewEntry.__table__.columns.get("remediation_cr_id")
    assert col is not None
    assert col.nullable is True
