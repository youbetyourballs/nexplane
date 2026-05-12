import pytest
from app.services.vuln_campaign_service import _build_batches, _should_abort
import uuid

def test_build_batches_rolling():
    ids = [str(uuid.uuid4()) for _ in range(7)]
    batches = _build_batches(ids, 3, "rolling")
    assert len(batches) == 3
    assert len(batches[0]) == 3
    assert len(batches[2]) == 1

def test_build_batches_canary():
    ids = [str(uuid.uuid4()) for _ in range(6)]
    batches = _build_batches(ids, 3, "canary")
    assert batches[0] == [ids[0]]
    assert batches[1] == ids[1:]

def test_should_abort_over_threshold():
    result = {"asset_ids": ["a", "b", "c"], "failed_ids": ["a", "b"]}
    assert _should_abort(result, 0.2) is True

def test_should_abort_under_threshold():
    result = {"asset_ids": ["a", "b", "c"], "failed_ids": ["a"]}
    assert _should_abort(result, 0.5) is False
