def test_project_phase_model_exists():
    from app.models.project_phase import ProjectPhase
    assert hasattr(ProjectPhase, 'soak_hours')
    assert hasattr(ProjectPhase, 'status')
    assert hasattr(ProjectPhase, 'soak_started_at')
