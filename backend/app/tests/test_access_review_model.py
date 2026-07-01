# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from app.models.access_review import AccessReview


def test_access_review_tablename():
    assert AccessReview.__tablename__ == "access_reviews"


def test_access_review_has_required_columns():
    cols = {c.key for c in AccessReview.__table__.columns}
    assert "id" in cols
    assert "title" in cols
    assert "scope" in cols
    assert "status" in cols
    assert "snapshot" in cols
    assert "decisions" in cols
    assert "created_by" in cols
