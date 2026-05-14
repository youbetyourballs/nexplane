def test_project_has_risk_context_field():
    from app.models.project import Project
    assert hasattr(Project, 'risk_context')
