# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Cross-cloud container image transfer executor.

Transfers a container image from any of ECR/OCIR/ACR/GCR to any other.
ACR destinations use the ACR import API (server-side, no agent).
All other destinations use a Nexplane agent running docker pull/tag/push.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


def _run(fn):
    return asyncio.get_running_loop().run_in_executor(None, fn)


async def _load_creds_by_connector_id(connector_id: str) -> tuple[str, dict]:
    """Return (connector_type, credentials) for the given connector UUID."""
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(Connector).where(Connector.id == connector_id)
        )).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Connector {connector_id} not found")
        connector_type = row.connector_type.value

        cred_row = (await db.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.connector_id == row.id
            ).limit(1)
        )).scalar_one_or_none()
        if cred_row is None or not cred_row.credentials_encrypted:
            raise ValueError(f"No credentials found for connector {connector_id}")

        backend = get_secret_backend()
        creds = backend.decrypt_json(cred_row.credentials_encrypted)
    return connector_type, creds


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    # Mock path — no real connector credentials
    source_connector_id = parameters.get("source_connector_id")
    dest_connector_id = parameters.get("dest_connector_id")
    if not source_connector_id or not dest_connector_id:
        return {
            "mock": True,
            "phases": [{"phase": p, "status": "mock"} for p in
                       ["preflight", "snapshot", "transfer", "verify", "report"]],
            "summary": {
                "source_image": parameters.get("source_image"),
                "destination_image": None,
                "transfer_method": "mock",
                "digest_matched": True,
            },
            "promote_to": "container_image_transfer",
            "rollback_data": {"snapshot": {}, "dest_image": None, "dst_connector_type": None},
        }

    src_connector_type, src_creds = await _load_creds_by_connector_id(source_connector_id)
    dst_connector_type, dst_creds = await _load_creds_by_connector_id(dest_connector_id)

    source_image = parameters["source_image"]
    dest_repo = parameters["dest_repo"]
    overwrite_existing = parameters.get("overwrite_existing", False)

    phases = []
    rollback_data = {"snapshot": {}, "dest_image": None, "dst_connector_type": dst_connector_type}

    phase1 = await _preflight(src_connector_type, src_creds, source_image,
                               dst_connector_type, dst_creds, dest_repo, overwrite_existing)
    phases.append(phase1)
    if phase1["status"] == "failed":
        return {"phases": phases, "failed": True, "rollback_data": rollback_data}

    src_repo = phase1["src_repo"]
    src_tag = phase1["src_tag"]
    src_hostname = phase1["src_hostname"]
    dst_hostname = phase1["dst_hostname"]

    phase2 = await _snapshot(dst_connector_type, dst_creds, dest_repo, src_tag,
                              phase1["dest_tag_exists"])
    phases.append(phase2)
    rollback_data["snapshot"] = phase2["destination_state"]
    rollback_data["dest_image"] = f"{dest_repo}:{src_tag}"

    if dst_connector_type == "azure":
        phase3 = await _transfer_acr_import(src_connector_type, src_creds,
                                             src_hostname, src_repo, src_tag,
                                             dst_creds, dest_repo)
    else:
        phase3 = await _transfer_via_agent(asset_ids, src_connector_type, src_creds,
                                           src_hostname, src_repo, src_tag,
                                           dst_connector_type, dst_creds,
                                           dst_hostname, dest_repo)
    phases.append(phase3)

    phase4 = await _verify(dst_connector_type, dst_creds, dest_repo, src_tag,
                            src_connector_type, src_creds, src_repo)
    phases.append(phase4)

    src_full = f"{src_hostname}/{source_image}"
    dst_full = f"{dst_hostname}/{dest_repo}:{src_tag}"
    summary = {
        "source_image": src_full,
        "destination_image": dst_full,
        "transfer_method": phase3.get("method"),
        "digest_matched": phase4.get("digest_matched", False),
        "overwrote_existing": phase1["dest_tag_exists"],
    }
    phases.append({"phase": "report", "status": "ok", "summary": summary})

    return {
        "phases": phases,
        "summary": summary,
        "promote_to": "container_image_transfer",
        "rollback_data": rollback_data,
    }


async def _preflight(src_connector_type, src_creds, source_image,
                     dst_connector_type, dst_creds, dest_repo, overwrite_existing) -> dict:
    try:
        def _do():
            from ._registry_client import check_tag_exists, get_registry_hostname
            if ":" not in source_image:
                raise ValueError(f"source_image must include tag: '{source_image}'")
            src_repo, src_tag = source_image.rsplit(":", 1)
            if not check_tag_exists(src_connector_type, src_creds, src_repo, src_tag):
                raise ValueError(f"Source image not found: {source_image}")
            dest_tag_exists = check_tag_exists(dst_connector_type, dst_creds, dest_repo, src_tag)
            if dest_tag_exists and not overwrite_existing:
                raise ValueError(
                    f"Destination tag '{dest_repo}:{src_tag}' already exists. "
                    "Set overwrite_existing=true to overwrite."
                )
            src_hostname = get_registry_hostname(src_connector_type, src_creds)
            dst_hostname = get_registry_hostname(dst_connector_type, dst_creds)
            return src_repo, src_tag, dest_tag_exists, src_hostname, dst_hostname

        src_repo, src_tag, dest_tag_exists, src_hostname, dst_hostname = await _run(_do)
        return {
            "phase": "preflight", "status": "ok",
            "src_repo": src_repo, "src_tag": src_tag,
            "dest_tag_exists": dest_tag_exists,
            "src_hostname": src_hostname, "dst_hostname": dst_hostname,
        }
    except Exception as e:
        return {"phase": "preflight", "status": "failed", "error": str(e)}


async def _snapshot(dst_connector_type, dst_creds, dest_repo, src_tag, dest_tag_exists) -> dict:
    def _do():
        if not dest_tag_exists:
            return {"exists": False, "digest": None}
        from ._registry_client import get_manifest_digest
        digest = get_manifest_digest(dst_connector_type, dst_creds, dest_repo, src_tag)
        return {"exists": True, "digest": digest}
    snap = await _run(_do)
    return {"phase": "snapshot", "status": "ok", "destination_state": snap}


async def _transfer_acr_import(src_connector_type, src_creds, src_hostname,
                                src_repo, src_tag, dst_creds, dest_repo) -> dict:
    """Use ACR import API — Azure pulls the image server-side, no Docker daemon needed."""
    import requests as _requests

    def _do_post():
        from azure.identity import ClientSecretCredential
        from ._registry_client import get_auth_token

        registry_name = dst_creds["registry_name"]
        subscription_id = dst_creds["subscription_id"]
        resource_group = dst_creds["resource_group"]

        aad_creds = ClientSecretCredential(
            dst_creds["tenant_id"], dst_creds["client_id"], dst_creds["client_secret"]
        )
        mgmt_token = aad_creds.get_token("https://management.azure.com/.default").token
        src_token = get_auth_token(src_connector_type, src_creds, src_repo)

        # Username conventions differ by registry:
        # ECR: "AWS", GCR: "oauth2accesstoken", OCIR: "<tenancy>/<user>", Azure: "token"
        if src_connector_type == "aws":
            src_username = "AWS"
        elif src_connector_type == "gcp":
            src_username = "oauth2accesstoken"
        elif src_connector_type == "oci":
            src_username = f"{src_creds.get('tenancy_namespace')}/{src_creds.get('username')}"
        else:
            src_username = "token"
        body = {
            "source": {
                "registryUri": src_hostname,
                "sourceImage": f"{src_repo}:{src_tag}",
                "credentials": {"username": src_username, "password": src_token},
            },
            "targetTags": [f"{dest_repo}:{src_tag}"],
            "mode": "Force",
        }
        url = (f"https://management.azure.com/subscriptions/{subscription_id}"
               f"/resourceGroups/{resource_group}/providers/Microsoft.ContainerRegistry"
               f"/registries/{registry_name}/importImage?api-version=2019-05-01")
        r = _requests.post(url, json=body,
                           headers={"Authorization": f"Bearer {mgmt_token}"}, timeout=60)
        return r, mgmt_token

    r, mgmt_token = await _run(_do_post)

    if r.status_code == 200:
        return {"phase": "transfer", "status": "ok", "method": "acr_import"}
    if r.status_code == 202:
        operation_url = r.headers.get("Location")
        for _ in range(60):  # 5 min max (60 × 5s)
            await asyncio.sleep(5)

            def _do_poll(url=operation_url, tok=mgmt_token):
                return _requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=30)

            poll = await _run(_do_poll)
            if poll.status_code == 200:
                return {"phase": "transfer", "status": "ok", "method": "acr_import"}
            status = poll.json().get("status", "")
            if status == "Succeeded":
                return {"phase": "transfer", "status": "ok", "method": "acr_import"}
            if status == "Failed":
                raise RuntimeError(f"ACR import failed: {poll.json()}")
        raise TimeoutError("ACR import timed out after 5 minutes")
    r.raise_for_status()


async def _transfer_via_agent(asset_ids, src_connector_type, src_creds,
                               src_hostname, src_repo, src_tag,
                               dst_connector_type, dst_creds,
                               dst_hostname, dest_repo) -> dict:
    """Send docker pull/tag/push to a Nexplane agent host via dispatch_agent_job."""
    from ._registry_client import get_auth_token

    src_token = get_auth_token(src_connector_type, src_creds, src_repo)
    dst_token = get_auth_token(dst_connector_type, dst_creds, dest_repo)

    src_full = f"{src_hostname}/{src_repo}:{src_tag}"
    dst_full = f"{dst_hostname}/{dest_repo}:{src_tag}"

    # Username conventions differ by registry:
    # ECR: "AWS"
    # GCR/Artifact Registry: "oauth2accesstoken"
    # OCIR: "<tenancy_namespace>/<username>"
    def _docker_user(ctype, creds_):
        if ctype == "aws":
            return "AWS"
        if ctype == "gcp":
            return "oauth2accesstoken"
        return f"{creds_.get('tenancy_namespace')}/{creds_.get('username')}"

    # For OCIR, docker login wants the raw auth_token (not base64-encoded)
    def _docker_pass(ctype, creds_, token_):
        if ctype == "oci":
            return creds_.get("auth_token", token_)
        return token_

    src_user = _docker_user(src_connector_type, src_creds)
    dst_user = _docker_user(dst_connector_type, dst_creds)
    src_pass = _docker_pass(src_connector_type, src_creds, src_token)
    dst_pass = _docker_pass(dst_connector_type, dst_creds, dst_token)

    script = (
        f"echo \"$SRC_PASS\" | docker login {src_hostname} -u {src_user} --password-stdin && "
        f"echo \"$DST_PASS\" | docker login {dst_hostname} -u {dst_user} --password-stdin && "
        f"docker pull {src_full} && "
        f"docker tag {src_full} {dst_full} && "
        f"docker push {dst_full} && "
        f"docker rmi {src_full} {dst_full} || true"
    )

    from app.connectors.executors.nexplane_agent.app_upgrade_base import dispatch_agent_job
    result = await dispatch_agent_job(
        command="run_command",
        parameters={"command": script, "timeout": 600, "env": {"SRC_PASS": src_pass, "DST_PASS": dst_pass}},
        asset_ids=asset_ids,
        timeout_seconds=660,
    )
    if result.get("exit_code", 1) != 0:
        raise RuntimeError(f"Agent docker transfer failed: {result.get('stderr', '')[:500]}")
    return {"phase": "transfer", "status": "ok", "method": "agent_docker"}


async def _verify(dst_connector_type, dst_creds, dest_repo, src_tag,
                  src_connector_type, src_creds, src_repo) -> dict:
    def _do():
        from ._registry_client import get_manifest_digest
        dst_digest = get_manifest_digest(dst_connector_type, dst_creds, dest_repo, src_tag)
        if dst_digest is None:
            return False, None, None
        src_digest = get_manifest_digest(src_connector_type, src_creds, src_repo, src_tag)
        return True, src_digest, dst_digest

    tag_exists, src_digest, dst_digest = await _run(_do)
    digest_matched = tag_exists and src_digest == dst_digest
    return {
        "phase": "verify",
        "status": "ok" if tag_exists else "failed",
        "tag_exists_at_destination": tag_exists,
        "digest_matched": digest_matched,
        "source_digest": src_digest,
        "destination_digest": dst_digest,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    rd = execution_result.get("rollback_data", {})
    snap = rd.get("snapshot", {})
    dest_image = rd.get("dest_image")
    dst_connector_type = rd.get("dst_connector_type")
    dest_connector_id = parameters.get("dest_connector_id")

    if not dest_image or not dest_connector_id:
        return {"rolled_back": True, "note": "nothing_to_undo"}

    _, dst_creds = await _load_creds_by_connector_id(dest_connector_id)
    dest_repo, dest_tag = dest_image.rsplit(":", 1)

    def _do():
        from ._registry_client import delete_tag, restore_tag_by_digest
        if not snap.get("exists"):
            delete_tag(dst_connector_type, dst_creds, dest_repo, dest_tag)
            return True, "deleted_net_new_tag"
        original_digest = snap["digest"]
        restored = restore_tag_by_digest(dst_connector_type, dst_creds,
                                          dest_repo, dest_tag, original_digest)
        if restored:
            return True, "restored_original_digest"
        try:
            delete_tag(dst_connector_type, dst_creds, dest_repo, dest_tag)
        except Exception:
            pass
        return False, "partial_original_manifest_gc_deleted"

    try:
        ok, note = await _run(_do)
        return {"rolled_back": ok, "note": note,
                "rolled_back_image": dest_image, "partial": not ok}
    except Exception as e:
        logger.error("Image transfer rollback failed: %s", e)
        return {"rolled_back": False, "error": str(e)}
