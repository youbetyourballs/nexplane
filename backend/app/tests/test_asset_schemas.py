import pytest
from pydantic import ValidationError
from app.schemas.asset import AssetUpdate, BulkTagOperation


def test_asset_update_all_fields_optional():
    u = AssetUpdate()
    assert u.name is None
    assert u.tags is None


def test_asset_update_tags_accepts_list():
    u = AssetUpdate(tags=["pci-scope", "payments"])
    assert u.tags == ["pci-scope", "payments"]


def test_asset_update_tags_empty_list_clears_tags():
    u = AssetUpdate(tags=[])
    assert u.tags == []


def test_bulk_tag_operation_valid_add():
    import uuid
    op = BulkTagOperation(
        asset_ids=[uuid.uuid4(), uuid.uuid4()],
        operation="add",
        tags=["pci-scope"],
    )
    assert op.operation == "add"
    assert len(op.tags) == 1


def test_bulk_tag_operation_requires_at_least_one_tag():
    import uuid
    with pytest.raises(ValidationError, match="tags"):
        BulkTagOperation(
            asset_ids=[uuid.uuid4()],
            operation="add",
            tags=[],
        )


def test_bulk_tag_operation_invalid_operation():
    import uuid
    with pytest.raises(ValidationError):
        BulkTagOperation(
            asset_ids=[uuid.uuid4()],
            operation="replace_all",
            tags=["x"],
        )
