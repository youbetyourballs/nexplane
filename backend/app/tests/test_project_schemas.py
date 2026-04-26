import uuid
import pytest
from pydantic import ValidationError
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectMemberCreate, ProjectMemberUpdate
from app.models.project import ProjectStatus


def test_project_create_requires_name():
    with pytest.raises(ValidationError):
        ProjectCreate()


def test_project_create_defaults():
    p = ProjectCreate(name="Test Project")
    assert p.name == "Test Project"
    assert p.description == ""
    assert p.goal == ""


def test_project_update_all_optional():
    u = ProjectUpdate()
    assert u.name is None
    assert u.status is None


def test_project_update_status_validates():
    u = ProjectUpdate(status=ProjectStatus.in_progress)
    assert u.status == ProjectStatus.in_progress


def test_project_update_invalid_status():
    with pytest.raises(ValidationError):
        ProjectUpdate(status="not_a_status")


def test_member_create_defaults():
    cr_id = uuid.uuid4()
    m = ProjectMemberCreate(change_request_id=cr_id)
    assert m.sequence_order == 0
    assert m.depends_on == []


def test_member_update_all_optional():
    u = ProjectMemberUpdate()
    assert u.sequence_order is None
    assert u.depends_on is None


def test_member_create_depends_on_accepts_uuid_list():
    pcr_id = uuid.uuid4()
    m = ProjectMemberCreate(change_request_id=uuid.uuid4(), depends_on=[pcr_id])
    assert m.depends_on == [pcr_id]
