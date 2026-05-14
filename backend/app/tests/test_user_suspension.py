def test_user_suspension_executor_exists():
    from app.connectors.executors.nexplane_agent.user_suspension import execute, rollback
    assert callable(execute)
    assert callable(rollback)
