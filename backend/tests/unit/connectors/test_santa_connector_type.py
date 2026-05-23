from app.models.connector import ConnectorType


def test_santa_sync_server_in_enum():
    assert ConnectorType.santa_sync_server.value == "santa_sync_server"
