# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Per-cloud container registry helpers for container_image_transfer executor."""

OCIR_REGION_MAP = {
    "us-ashburn-1": "iad", "us-phoenix-1": "phx", "eu-frankfurt-1": "fra",
    "ap-tokyo-1": "nrt", "ap-sydney-1": "syd", "uk-london-1": "lhr",
    "ca-toronto-1": "yyz", "sa-saopaulo-1": "gru", "ap-mumbai-1": "bom",
    "ap-seoul-1": "icn", "ap-osaka-1": "kix", "me-jeddah-1": "jed",
}


def get_registry_hostname(connector_type: str, creds: dict) -> str:
    if connector_type == "aws":
        return f"{creds['account_id']}.dkr.ecr.{creds['region']}.amazonaws.com"
    if connector_type == "azure":
        return f"{creds['registry_name']}.azurecr.io"
    if connector_type == "gcp":
        region = creds.get("region", "us")
        return f"{region}-docker.pkg.dev/{creds['project_id']}"
    if connector_type == "oci":
        prefix = OCIR_REGION_MAP.get(creds["region"], creds["region"].split("-")[0])
        return f"{prefix}.ocir.io/{creds['tenancy_namespace']}"
    raise ValueError(f"Unsupported connector_type: {connector_type}")


def _get_acr_token(creds: dict, repo: str) -> str:
    """Exchange AAD token for an ACR scope token for the given repo."""
    import requests
    from azure.identity import ClientSecretCredential
    hostname = get_registry_hostname("azure", creds)
    aad_token = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    ).get_token("https://management.azure.com/.default").token
    r = requests.post(f"https://{hostname}/oauth2/exchange",
                      data={"grant_type": "access_token", "service": hostname,
                            "access_token": aad_token}, timeout=30)
    r.raise_for_status()
    refresh_token = r.json()["refresh_token"]
    r2 = requests.post(f"https://{hostname}/oauth2/token",
                       data={"grant_type": "refresh_token", "service": hostname,
                             "scope": f"repository:{repo}:pull,push",
                             "refresh_token": refresh_token}, timeout=30)
    r2.raise_for_status()
    return r2.json()["access_token"]


def _get_gcr_token(creds: dict) -> str:
    import json as _json
    from google.oauth2 import service_account
    import google.auth.transport.requests
    key_json = creds.get("service_account_key_json", {})
    if isinstance(key_json, str):
        key_json = _json.loads(key_json)
    sa_creds = service_account.Credentials.from_service_account_info(
        key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    sa_creds.refresh(google.auth.transport.requests.Request())
    return sa_creds.token


def _get_ocir_raw_token(creds: dict) -> str:
    """Return raw auth_token for docker login (not base64-encoded)."""
    return creds["auth_token"]


def get_auth_token(connector_type: str, creds: dict, repo: str = "") -> str:
    """Return a short-lived Docker-compatible bearer token for the registry."""
    if connector_type == "aws":
        import boto3, base64
        ecr = boto3.client("ecr", region_name=creds["region"],
                           aws_access_key_id=creds["access_key_id"],
                           aws_secret_access_key=creds["secret_access_key"])
        token_b64 = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
        # ECR returns base64("AWS:password") — split to get just the password
        return base64.b64decode(token_b64).decode().split(":", 1)[1]
    if connector_type == "azure":
        return _get_acr_token(creds, repo)
    if connector_type == "gcp":
        return _get_gcr_token(creds)
    if connector_type == "oci":
        return _get_ocir_raw_token(creds)
    raise ValueError(f"Unsupported connector_type: {connector_type}")


def get_manifest_digest(connector_type: str, creds: dict, repo: str, tag: str) -> "str | None":
    """Return SHA256 digest for repo:tag, or None if not found."""
    if connector_type == "aws":
        import boto3
        ecr = boto3.client("ecr", region_name=creds["region"],
                           aws_access_key_id=creds["access_key_id"],
                           aws_secret_access_key=creds["secret_access_key"])
        try:
            resp = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])
            return resp["imageDetails"][0]["imageDigest"]
        except Exception as exc:
            # Catch ImageNotFoundException from botocore or test mocks
            exc_name = type(exc).__name__
            if exc_name == "ImageNotFoundException":
                return None
            # Check botocore ClientError response
            response = getattr(exc, "response", None)
            if response and response.get("Error", {}).get("Code") == "ImageNotFoundException":
                return None
            raise
    if connector_type in ("azure", "gcp"):
        import requests
        hostname = get_registry_hostname(connector_type, creds)
        token = get_auth_token(connector_type, creds, repo)
        r = requests.get(f"https://{hostname}/v2/{repo}/manifests/{tag}",
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                         timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.headers.get("Docker-Content-Digest")
    if connector_type == "oci":
        import requests, base64
        namespace = creds["tenancy_namespace"]
        region_prefix = OCIR_REGION_MAP.get(creds["region"], creds["region"].split("-")[0])
        hostname = f"{region_prefix}.ocir.io"
        username = f"{namespace}/{creds['username']}"
        auth = base64.b64encode(f"{username}:{creds['auth_token']}".encode()).decode()
        r = requests.get(f"https://{hostname}/v2/{namespace}/{repo}/manifests/{tag}",
                         headers={"Authorization": f"Basic {auth}",
                                  "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                         timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.headers.get("Docker-Content-Digest")
    raise ValueError(f"Unsupported connector_type: {connector_type}")


def check_tag_exists(connector_type: str, creds: dict, repo: str, tag: str) -> bool:
    return get_manifest_digest(connector_type, creds, repo, tag) is not None


def delete_tag(connector_type: str, creds: dict, repo: str, tag: str) -> None:
    """Delete repo:tag from the registry. No-op if tag doesn't exist."""
    if connector_type == "aws":
        import boto3
        ecr = boto3.client("ecr", region_name=creds["region"],
                           aws_access_key_id=creds["access_key_id"],
                           aws_secret_access_key=creds["secret_access_key"])
        ecr.batch_delete_image(repositoryName=repo, imageIds=[{"imageTag": tag}])
        return
    if connector_type in ("azure", "gcp", "oci"):
        import requests
        hostname = get_registry_hostname(connector_type, creds)
        if connector_type == "oci":
            import base64
            namespace = creds["tenancy_namespace"]
            username = f"{namespace}/{creds['username']}"
            token = base64.b64encode(f"{username}:{creds['auth_token']}".encode()).decode()
            auth_type = "Basic"
        else:
            token = get_auth_token(connector_type, creds, repo)
            auth_type = "Bearer"
        digest = get_manifest_digest(connector_type, creds, repo, tag)
        if digest is None:
            return
        r = requests.delete(f"https://{hostname}/v2/{repo}/manifests/{digest}",
                            headers={"Authorization": f"{auth_type} {token}"}, timeout=30)
        if r.status_code not in (200, 202, 404):
            r.raise_for_status()
        return
    raise ValueError(f"Unsupported connector_type: {connector_type}")


def restore_tag_by_digest(connector_type: str, creds: dict, repo: str, tag: str, digest: str) -> bool:
    """Re-point repo:tag to original digest. Returns False if manifest was GC'd."""
    if connector_type == "aws":
        import boto3
        ecr = boto3.client("ecr", region_name=creds["region"],
                           aws_access_key_id=creds["access_key_id"],
                           aws_secret_access_key=creds["secret_access_key"])
        try:
            resp = ecr.batch_get_image(repositoryName=repo, imageIds=[{"imageDigest": digest}])
            if not resp.get("images"):
                return False
            manifest = resp["images"][0]["imageManifest"]
            ecr.put_image(repositoryName=repo, imageManifest=manifest, imageTag=tag)
            return True
        except Exception as exc:
            exc_name = type(exc).__name__
            if exc_name == "ImageNotFoundException":
                return False
            response = getattr(exc, "response", None)
            if response and response.get("Error", {}).get("Code") == "ImageNotFoundException":
                return False
            raise
    if connector_type in ("azure", "gcp", "oci"):
        import requests
        hostname = get_registry_hostname(connector_type, creds)
        if connector_type == "oci":
            import base64
            namespace = creds["tenancy_namespace"]
            username = f"{namespace}/{creds['username']}"
            token = base64.b64encode(f"{username}:{creds['auth_token']}".encode()).decode()
            auth_type = "Basic"
        else:
            token = get_auth_token(connector_type, creds, repo)
            auth_type = "Bearer"
        r = requests.get(f"https://{hostname}/v2/{repo}/manifests/{digest}",
                         headers={"Authorization": f"{auth_type} {token}",
                                  "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                         timeout=30)
        if r.status_code == 404:
            return False
        r.raise_for_status()
        manifest_body = r.content
        content_type = r.headers.get("Content-Type", "application/vnd.docker.distribution.manifest.v2+json")
        r2 = requests.put(f"https://{hostname}/v2/{repo}/manifests/{tag}",
                          headers={"Authorization": f"{auth_type} {token}",
                                   "Content-Type": content_type},
                          data=manifest_body, timeout=30)
        r2.raise_for_status()
        return True
    raise ValueError(f"Unsupported connector_type: {connector_type}")
