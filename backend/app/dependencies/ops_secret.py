import os
import logging
from functools import lru_cache
from fastapi import Header, HTTPException, status

_log = logging.getLogger(__name__)
_SSM_PARAM = "/nexplane/ops/instance-shared-secret"


@lru_cache(maxsize=1)
def _get_expected_secret() -> str:
    env_override = os.environ.get("NEXPLANE_OPS_SECRET")
    if env_override:
        return env_override
    try:
        import boto3
        ssm = boto3.client("ssm")
        resp = ssm.get_parameter(Name=_SSM_PARAM, WithDecryption=True)
        return resp["Parameter"]["Value"]
    except Exception as exc:
        _log.error("Failed to read ops shared secret from SSM: %s", exc)
        raise RuntimeError("Ops shared secret unavailable") from exc


async def require_ops_secret(x_ops_secret: str | None = Header(default=None, alias="X-Ops-Secret")) -> None:
    if not x_ops_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-Ops-Secret header")
    try:
        expected = _get_expected_secret()
    except RuntimeError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Ops secret configuration error")
    if x_ops_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ops secret")
