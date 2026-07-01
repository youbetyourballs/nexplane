# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Backend integration tests for credential rotation connector actions.
Uses pytest-asyncio and unittest.mock to avoid live cloud calls.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


# ══════════════════════════════════════════════════════════════════════════════
# Task 5: AWS IAM key rotation
# ══════════════════════════════════════════════════════════════════════════════

class TestCreateIAMAccessKey:
    @patch("boto3.client")
    def test_returns_new_api_key_slot(self, mock_boto):
        mock_iam = MagicMock()
        mock_boto.return_value = mock_iam
        mock_iam.create_access_key.return_value = {
            "AccessKey": {
                "AccessKeyId": "AKIAFAKE123",
                "SecretAccessKey": "supersecret",
            }
        }

        config = MagicMock()
        config.boto_kwargs.return_value = {}

        import asyncio
        from app.connectors.aws.actions.rotate_api_key import create_iam_access_key
        result = asyncio.get_event_loop().run_until_complete(
            create_iam_access_key(config, "myuser")
        )

        assert "new_api_key" in result
        assert "AKIAFAKE123" in result["new_api_key"]
        assert "supersecret" in result["new_api_key"]

    @patch("boto3.client")
    def test_delete_iam_access_key_calls_api(self, mock_boto):
        mock_iam = MagicMock()
        mock_boto.return_value = mock_iam

        import asyncio
        from app.connectors.aws.actions.rotate_api_key import delete_iam_access_key
        config = MagicMock()
        config.boto_kwargs.return_value = {}

        asyncio.get_event_loop().run_until_complete(
            delete_iam_access_key(config, "myuser", "AKIAFAKE123")
        )

        mock_iam.delete_access_key.assert_called_once_with(
            UserName="myuser", AccessKeyId="AKIAFAKE123"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Task 5: Okta API key rotation
# ══════════════════════════════════════════════════════════════════════════════

class TestRotateOktaAPIKey:
    @pytest.mark.asyncio
    async def test_returns_new_api_key_and_old_key_id(self):
        from app.connectors.okta.actions.rotate_api_key import rotate_okta_api_key

        mock_response_list = MagicMock()
        mock_response_list.raise_for_status = MagicMock()
        mock_response_list.json.return_value = [{"name": "my-key", "id": "old-id-123"}]

        mock_response_create = MagicMock()
        mock_response_create.raise_for_status = MagicMock()
        mock_response_create.json.return_value = {"token": "new-okta-token-abc"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response_list)
        mock_client.post = AsyncMock(return_value=mock_response_create)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        config = MagicMock()
        config.domain = "https://example.okta.com"
        config.api_token = "fake-token"

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await rotate_okta_api_key(config, "my-key")

        assert result["new_api_key"] == "new-okta-token-abc"
        assert result["old_key_id"] == "old-id-123"

    @pytest.mark.asyncio
    async def test_old_key_id_empty_when_not_found(self):
        from app.connectors.okta.actions.rotate_api_key import rotate_okta_api_key

        mock_response_list = MagicMock()
        mock_response_list.raise_for_status = MagicMock()
        mock_response_list.json.return_value = []  # key not found

        mock_response_create = MagicMock()
        mock_response_create.raise_for_status = MagicMock()
        mock_response_create.json.return_value = {"token": "new-token"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response_list)
        mock_client.post = AsyncMock(return_value=mock_response_create)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        config = MagicMock()
        config.domain = "https://example.okta.com"
        config.api_token = "fake-token"

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await rotate_okta_api_key(config, "nonexistent-key")

        assert result["old_key_id"] == ""


# ══════════════════════════════════════════════════════════════════════════════
# Task 5: GitHub PAT rotation
# ══════════════════════════════════════════════════════════════════════════════

class TestRotateGitHubPAT:
    @pytest.mark.asyncio
    async def test_create_github_pat_returns_token(self):
        from app.connectors.github.actions.rotate_pat import create_github_pat

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"token": "ghp_newtoken123", "id": 42}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        config = MagicMock()
        config.token = "ghp_admintoken"

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await create_github_pat(config, "my-pat", ["repo", "read:org"])

        assert result["new_api_key"] == "ghp_newtoken123"
        assert result["new_pat_id"] == "42"

    @pytest.mark.asyncio
    async def test_delete_github_pat_calls_delete(self):
        from app.connectors.github.actions.rotate_pat import delete_github_pat

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 204

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        config = MagicMock()
        config.token = "ghp_admintoken"

        with patch("httpx.AsyncClient", return_value=mock_client):
            await delete_github_pat(config, "99")

        mock_client.delete.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# Task 6: Service account rotation
# ══════════════════════════════════════════════════════════════════════════════

class TestUpdateADPassword:
    def test_raises_on_ldap_failure(self):
        """update_ad_password raises RuntimeError when LDAP result is non-zero."""
        from app.connectors.active_directory.actions.rotate_service_account import (
            update_ad_password,
        )

        mock_conn = MagicMock()
        mock_conn.result = {"result": 49, "description": "invalidCredentials"}
        mock_conn.modify.return_value = None

        mock_server = MagicMock()

        with patch("ldap3.Server", return_value=mock_server), \
             patch("ldap3.Connection", return_value=mock_conn):
            mock_conn.auto_bind = True

            import asyncio
            config = MagicMock()
            config.ldap_host = "dc.example.com"
            config.bind_dn = "CN=admin,DC=example,DC=com"
            config.bind_password = "adminpass"
            config.users_base_dn = "OU=ServiceAccounts,DC=example,DC=com"

            with pytest.raises(RuntimeError, match="AD password update failed"):
                asyncio.get_event_loop().run_until_complete(
                    update_ad_password(config, "svc_myapp", "example.com", "NewPass123!")
                )

    def test_calls_ldap_modify(self):
        """update_ad_password calls ldap3 modify with unicodePwd."""
        from app.connectors.active_directory.actions.rotate_service_account import (
            update_ad_password,
        )

        mock_conn = MagicMock()
        mock_conn.result = {"result": 0, "description": "success"}

        with patch("ldap3.Server"), \
             patch("ldap3.Connection", return_value=mock_conn):
            import asyncio
            config = MagicMock()
            config.ldap_host = "dc.example.com"
            config.bind_dn = "CN=admin,DC=example,DC=com"
            config.bind_password = "adminpass"
            config.users_base_dn = "OU=ServiceAccounts,DC=example,DC=com"

            asyncio.get_event_loop().run_until_complete(
                update_ad_password(config, "svc_myapp", "example.com", "NewPass123!")
            )

        mock_conn.modify.assert_called_once()
        call_kwargs = mock_conn.modify.call_args
        # First arg is the DN
        assert "svc_myapp" in call_kwargs[0][0]


class TestUpdateOktaPassword:
    @pytest.mark.asyncio
    async def test_calls_reset_password_endpoint(self):
        from app.connectors.okta.actions.rotate_service_account import update_okta_password

        mock_user_resp = MagicMock()
        mock_user_resp.raise_for_status = MagicMock()
        mock_user_resp.json.return_value = {"id": "00u1234abcd"}

        mock_pw_resp = MagicMock()
        mock_pw_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_user_resp)
        mock_client.post = AsyncMock(return_value=mock_pw_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        config = MagicMock()
        config.domain = "https://example.okta.com"
        config.api_token = "fake-token"

        with patch("httpx.AsyncClient", return_value=mock_client):
            await update_okta_password(config, "svc_myapp@example.com", "NewSecurePass123!")

        mock_client.post.assert_called_once()
        post_url = mock_client.post.call_args[0][0]
        assert "00u1234abcd" in post_url
        assert "reset_password" in post_url


# ══════════════════════════════════════════════════════════════════════════════
# Task 8: End-to-end propagation integration tests
# ══════════════════════════════════════════════════════════════════════════════

class TestStepOutputPropagationIntegration:
    """
    Full pipeline: StepOutputStore accumulates produces, inject_consumed_inputs
    resolves them, and credential values never appear in the change_steps record.
    """

    def test_generate_propagates_to_update_config(self):
        from app.services.change_execution import (
            StepOutputStore,
            inject_consumed_inputs,
            resolve_produces,
        )
        from app.models.change_plan import ChangePlanStep

        store = StepOutputStore()

        # Simulate step_generate_password completing
        generate_step = ChangePlanStep(
            id="step_generate_password",
            type="agent_command",
            command="rotate_db_credentials",
            params={"action": "generate"},
            produces=["new_password"],
        )
        fake_agent_result = {"new_password": "generated-secret-xyz789"}
        resolve_produces(generate_step, fake_agent_result, store)

        # Simulate step_update_config being dispatched
        update_step = ChangePlanStep(
            id="step_update_config",
            type="agent_command",
            command="rotate_db_credentials",
            params={"action": "update_config", "config_paths": ["/etc/app/db.env"]},
            consumes=["new_password"],
        )
        enriched = inject_consumed_inputs(update_step, store)

        assert enriched["new_password"] == "generated-secret-xyz789"
        assert enriched["config_paths"] == ["/etc/app/db.env"]

    def test_blocked_step_raises_when_producer_failed(self):
        from app.services.change_execution import StepOutputStore, inject_consumed_inputs
        from app.models.change_plan import ChangePlanStep

        store = StepOutputStore()
        # Intentionally do not call resolve_produces — simulate producer step failure

        blocked_step = ChangePlanStep(
            id="step_update_db_user",
            type="agent_command",
            command="rotate_db_credentials",
            params={"action": "update_db_user"},
            consumes=["new_password"],
        )

        with pytest.raises(ValueError, match="new_password"):
            inject_consumed_inputs(blocked_step, store)

    def test_credential_value_absent_from_store_repr(self):
        from app.services.change_execution import StepOutputStore, resolve_produces
        from app.models.change_plan import ChangePlanStep

        store = StepOutputStore()
        step = ChangePlanStep(
            id="step_generate",
            type="agent_command",
            command="rotate_db_credentials",
            params={},
            produces=["new_password"],
        )
        resolve_produces(step, {"new_password": "ultra-secret-do-not-log"}, store)

        assert "ultra-secret-do-not-log" not in repr(store)
        assert "ultra-secret-do-not-log" not in str(store)

    def test_rotate_secret_then_retrieve_superseded_for_rollback(self):
        from app.services.secrets_service import SecretsService

        svc = SecretsService("test-secret-key-32-chars-minimum!")
        from uuid import uuid4
        secret_id = uuid4()

        svc.rotate_secret(secret_id, "original-password")
        svc.rotate_secret(secret_id, "rotated-password")

        # Rollback retrieves the old password
        old = svc.get_superseded_secret(secret_id)
        assert old == "original-password"

    def test_multi_step_ssh_rotation_plan_schema(self):
        """ChangePlanStep schema correctly loads SSH rotation plan from JSON."""
        import json
        import pathlib
        from app.models.change_plan import ChangePlanStep

        plan_path = pathlib.Path("app/connectors/change_type_definitions/rotate_ssh_keys.json")
        plan = json.loads(plan_path.read_text())

        steps = [ChangePlanStep(**s) for s in plan["change_plan_template"]]
        assert len(steps) == 4

        backup_step = steps[0]
        assert "authorized_keys_backup_path" in backup_step.produces

        remove_step = steps[1]
        assert remove_step.rollback_command == "rotate_ssh_keys"
        assert remove_step.rollback_params["action"] == "restore"

    def test_rotate_api_key_plan_step_ordering(self):
        """Revoke step is last, ensuring old key valid throughout propagation."""
        import json, pathlib
        from app.models.change_plan import ChangePlanStep

        plan = json.loads(
            pathlib.Path("app/connectors/change_type_definitions/rotate_api_key.json").read_text()
        )
        steps = [ChangePlanStep(**s) for s in plan["change_plan_template"]]

        last_step = steps[-1]
        assert last_step.id == "step_revoke_old_api_key"
        assert "old_key_id" in last_step.consumes
