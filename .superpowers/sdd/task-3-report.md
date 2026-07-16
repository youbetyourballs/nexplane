# Task 3 Report — Certificate Rotation Executor (Phases 1–3)

## Status: DONE

## Commit
`2b14fea` — feat(cert-rotation): executor helpers + phases 1-3 (scan, snapshot, rotate)

## Files Created

**`backend/app/services/certificate_rotation_executor.py`**

Key function signatures:
```python
async def _scan_dependents(desired: dict, organization_id: uuid.UUID, db: AsyncSession) -> list
async def _snapshot_dependents(dependents: list, db: AsyncSession) -> list
async def _rotate_certificate(desired: dict, connector) -> dict
def _tls_probe_pem(host: str, port: int, timeout: int = 10) -> Optional[str]
def _pem_fingerprint(pem: str) -> str
async def _load_connector(connector_type: str, connector_id: Optional[str], db: AsyncSession)
async def _load_step_ca_connector(organization_id: uuid.UUID, db: AsyncSession)
```

## Deviations from Brief

1. **No `hostname` field on Asset** — `Asset` model has only `name`. Replaced all `getattr(asset, "hostname", "")` with `asset.name`.

2. **Port not a top-level Asset field** — `port` read from `asset.asset_metadata.get("port", 443)` instead of `getattr(asset, "port", 443)`.

3. **`asset_type` is an enum** — Changed string comparisons (`Asset.asset_type == "server"`) to enum comparisons (`Asset.asset_type == AssetType.server`).

4. **`k8s_secret` and `aws_secret` not in `AssetType` enum** — Mapped to closest available types:
   - `k8s_secret` -> `AssetType.kubernetes_workload`
   - `aws_secret` -> `AssetType.storage_bucket` (matched only when `asset_metadata.cert_subject` is set)
   - Both deviations annotated with comments in source.

5. **`metadata_` attribute** — Brief used `getattr(asset, "metadata_", {})` but actual attribute is `asset_metadata`. Used `asset.asset_metadata` directly.

## Import Verification

```
docker exec nexplane-backend-1 python -c 'from app.services.certificate_rotation_executor import _pem_fingerprint, _rotate_certificate; print("OK")'
```
Output: `OK`

## Self-Review

- No `from __future__ import annotations` (rule complied with).
- All lazy imports inside functions to avoid circular imports at module load time.
- `_tls_probe_pem` is a fresh inline implementation — `_check_via_ssl` from `check_expiry.py` is not imported.
- `_pem_fingerprint` uses `cryptography` library with SHA-256 raw-PEM fallback.
- `_rotate_certificate` uses `get_step_ca_client` factory correctly.
- The `AssetType` mapping for k8s/aws is the main design gap — if the model gains `k8s_secret`/`aws_secret` enum values, those WHERE clauses should be updated.

---

## Fix Round 1

**Commit:** `6822be6` — fix(cert-rotation): fix SAN signature, asset types, org filter, dead imports

### Issue 1 — `issue_certificate` SAN signature mismatch [Critical]

**Approach chosen:** Option B — modified `StepCAClient.issue_certificate` in
`backend/app/connectors/executors/step_ca/_client.py` to accept `san: str | list[str]`.
When a list is passed, each entry is expanded into a separate `--san` flag on the
`step ca certificate` command (the step CLI supports `--san` being repeated).
`_rotate_certificate` now passes `san` (already a list) directly without the
`if isinstance(san, list) else [san]` guard.

### Issue 2 — Type-B asset scan uses wrong AssetType values [Important]

**Approach chosen:** Option A — added `k8s_secret = "k8s_secret"` and
`aws_secret = "aws_secret"` to the `AssetType` enum in `backend/app/models/asset.py`.
Updated `_scan_dependents` to query `AssetType.k8s_secret` (K8s Secrets Manager
entries) and `AssetType.aws_secret` (AWS Secrets Manager entries) instead of the
`kubernetes_workload` / `storage_bucket` placeholders.

Two Alembic migrations added:
- `cert002_assettype_secrets` (`backend/alembic/versions/cert002_asset_type_k8s_aws_secret.py`):
  `ALTER TYPE asset_type ADD VALUE IF NOT EXISTS 'k8s_secret'` and `'aws_secret'`
- `cert003_merge_heads` (`backend/alembic/versions/cert003_merge_heads.py`):
  merges `cert002_assettype_secrets` + `merge002_ct001_presst001` into single head

Note: revision ID for cert002 shortened to `cert002_assettype_secrets` (25 chars)
because `alembic_version.version_num` is `varchar(32)`.

### Issue 3 — `_load_connector` org filter [Minor]

Added `organization_id: Optional[uuid.UUID] = None` parameter to `_load_connector`.
When present, appended `Connector.organization_id == organization_id` to the
fallback type-based query. `_snapshot_dependents` updated to accept and forward
`organization_id` to both `_load_connector` call sites (k8s_secret and aws_secret
branches).

### Issue 4 — Dead imports [Minor]

Removed `ChangeRequest`, `ChangeRequestStatus`, `ExecutionRun`, `ExecutionStatus`
from module-level imports in `certificate_rotation_executor.py`.

### Verification

```
docker exec -w /app nexplane-backend-1 python verify_import.py
```
Output: `IMPORT_OK`

Alembic migration:
```
Running upgrade cert001_certificate_rotation -> cert002_assettype_secrets, Add k8s_secret and aws_secret to asset_type enum.
Running upgrade cert002_assettype_secrets, merge002_ct001_presst001 -> cert003_merge_heads, Merge cert002 and merge002 heads.
```
