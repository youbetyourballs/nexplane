# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import os
from functools import lru_cache
from app.services.secret_backend import SecretBackend


@lru_cache(maxsize=1)
def get_secret_backend() -> SecretBackend:
    backend = os.getenv("SECRET_BACKEND", "fernet")

    if backend == "fernet":
        from app.services.backends.fernet_backend import FernetBackend
        from app import config as app_config
        secret_key = os.getenv("SECRET_KEY", app_config.settings.SECRET_KEY)
        return FernetBackend(secret_key=secret_key)

    if backend == "vault":
        from app.services.backends.vault_kv_backend import VaultKVBackend
        return VaultKVBackend(
            addr=os.environ["VAULT_ADDR"],
            token=os.environ["VAULT_TOKEN"],
        )

    if backend == "aws_secrets_manager":
        from app.services.backends.aws_secrets_manager_backend import AWSSecretsManagerBackend
        return AWSSecretsManagerBackend(region=os.environ["AWS_SECRETS_REGION"])

    raise ValueError(f"Unknown SECRET_BACKEND: {backend!r}. Valid values: fernet, vault, aws_secrets_manager")
