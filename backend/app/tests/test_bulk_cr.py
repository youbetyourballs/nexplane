def test_batch_create_schema():
    from app.schemas.change_request import BatchCreateRequest, BatchCreateItem
    req = BatchCreateRequest(items=[
        BatchCreateItem(
            title="Patch 1", change_type="agent_linux_patch",
            target_asset_ids=["asset-1"], desired_outcome={}
        ),
        BatchCreateItem(
            title="Patch 2", change_type="agent_linux_patch",
            target_asset_ids=["asset-2"], desired_outcome={}
        ),
    ])
    assert len(req.items) == 2


def test_batch_response_schema():
    import uuid
    from app.schemas.change_request import BatchCreateResponse
    resp = BatchCreateResponse(batch_id=uuid.uuid4(), cr_ids=[uuid.uuid4(), uuid.uuid4()])
    assert len(resp.cr_ids) == 2
