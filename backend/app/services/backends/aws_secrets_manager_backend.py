import json
import boto3


class AWSSecretsManagerBackend:
    """AWS Secrets Manager backend. Stores credentials at nexplane/connectors/{connector_id}."""

    def __init__(self, region: str, _client=None):
        if _client is not None:
            self._client = _client
        else:
            self._client = boto3.client("secretsmanager", region_name=region)

    def _secret_name(self, connector_id: str) -> str:
        return f"nexplane/connectors/{connector_id}"

    def encrypt_json(self, data: dict, connector_id: str = "") -> str:
        name = self._secret_name(connector_id)
        payload = json.dumps(data)
        try:
            self._client.create_secret(Name=name, SecretString=payload)
        except self._client.exceptions.ResourceExistsException:
            self._client.put_secret_value(SecretId=name, SecretString=payload)
        return name

    def decrypt_json(self, stored: str) -> dict:
        resp = self._client.get_secret_value(SecretId=stored)
        return json.loads(resp["SecretString"])

    def encrypt(self, value: str, connector_id: str = "") -> str:
        return self.encrypt_json({"v": value}, connector_id=connector_id)

    def decrypt(self, stored: str) -> str:
        return self.decrypt_json(stored)["v"]

    def delete_secret(self, stored: str) -> None:
        """Delete a secret by its stored name/path. Used by smoke test teardown."""
        self._client.delete_secret(SecretId=stored, ForceDeleteWithoutRecovery=True)
