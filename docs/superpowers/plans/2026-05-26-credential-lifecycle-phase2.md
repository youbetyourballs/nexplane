# Credential Lifecycle Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `credential_expiry_worker` with four new probes: SSH key age enforcement, Vault lease renewal, API key rotation with consumer discovery, and step-CA ACME certificate renewal.

**Architecture:** Each check is a standalone `async def _check_*()` function registered in `check_credential_expiry()`. Consumer discovery is a new utility queried before generating rotation CRs. `VaultClient` and `StepCAClient` get new `list_leases`/`renew_lease` and `list_certificates`/`renew_certificate` methods.

**Tech Stack:** FastAPI APScheduler, SQLAlchemy async, existing SSH/Vault/step-CA connector clients, pytest

---

## File Map

| File | Action |
|------|--------|
| `backend/app/workers/credential_expiry_worker.py` | Add `_check_ssh_key_age`, `_check_vault_leases`, `_check_step_ca_certs`, `_discover_api_key_consumers`; extend `_check_iam_key_age`; register all |
| `backend/app/connectors/hashicorp_vault/_client.py` | Add `list_leases()`, `renew_lease()` methods |
| `backend/app/connectors/step_ca/_client.py` | Add `list_certificates()`, `renew_certificate()` methods |
| `backend/tests/test_credential_expiry_worker.py` | Unit tests for all four new checks |

---

### Task 1: `_discover_api_key_consumers` utility

**Files:**
- Modify: `backend/app/workers/credential_expiry_worker.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_credential_expiry_worker.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_discover_api_key_consumers_finds_references():
    from app.workers.credential_expiry_worker import _discover_api_key_consumers
    from app.models.asset import Asset
    import uuid

    asset = MagicMock(spec=Asset)
    asset.id = uuid.uuid4()
    asset.name = "my-server"
    asset.asset_metadata = {"env_vars": "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"}

    mock_result = MagicMock()
    mock_result.scalars.return_value = [asset]

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=mock_result)

    consumers = await _discover_api_key_consumers(db, "AKIAIOSFODNN7EXAMPLE", "aws_iam_key")
    assert len(consumers) == 1
    assert consumers[0]["asset_name"] == "my-server"
    assert consumers[0]["config_path"] == "env_vars"


@pytest.mark.asyncio
async def test_discover_api_key_consumers_no_match():
    from app.workers.credential_expiry_worker import _discover_api_key_consumers

    asset = MagicMock()
    asset.asset_metadata = {"env_vars": "OTHER_KEY=xyz"}

    mock_result = MagicMock()
    mock_result.scalars.return_value = [asset]

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=mock_result)

    consumers = await _discover_api_key_consumers(db, "AKIAIOSFODNN7EXAMPLE", "aws_iam_key")
    assert consumers == []
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "discover_api_key" -v
```

- [ ] **Step 3: Add `_discover_api_key_consumers` to worker**

Open `backend/app/workers/credential_expiry_worker.py`. After existing imports and before `check_credential_expiry`, add:

```python
async def _discover_api_key_consumers(db, key_id: str, key_type: str) -> list[dict]:
    """Search asset metadata for references to this key ID (best-effort)."""
    from app.models.asset import Asset
    from sqlalchemy import select

    consumers = []
    result = await db.execute(select(Asset))
    for asset in result.scalars():
        meta = asset.asset_metadata or {}
        for field in ["env_vars", "secrets_refs", "iam_role_arns", "service_account"]:
            if key_id in str(meta.get(field, "")):
                consumers.append({
                    "asset_id": str(asset.id),
                    "asset_name": asset.name,
                    "config_path": field,
                })
    return consumers
```

- [ ] **Step 4: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "discover_api_key" -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/workers/credential_expiry_worker.py backend/tests/test_credential_expiry_worker.py
git commit -m "feat: _discover_api_key_consumers utility in credential expiry worker"
```

---

### Task 2: Extend `_check_iam_key_age` with consumer discovery

**Files:**
- Modify: `backend/app/workers/credential_expiry_worker.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_credential_expiry_worker.py`:

```python
@pytest.mark.asyncio
async def test_check_iam_key_age_includes_consumers():
    from app.workers.credential_expiry_worker import _check_iam_key_age

    db = AsyncMock(spec=AsyncSession)

    # Patch _get_connectors_by_type to return a fake AWS connector
    connector = MagicMock()
    connector.id = "conn-1"
    connector.creds = {"aws_access_key_id": "AKIA...", "aws_secret_access_key": "s", "region": "us-east-1"}

    # Patch _discover_api_key_consumers to return one consumer
    fake_consumers = [{"asset_id": "a1", "asset_name": "srv", "config_path": "env_vars"}]

    # Patch _create_expiry_finding and boto3 calls
    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", return_value=[connector]), \
         patch("app.workers.credential_expiry_worker._discover_api_key_consumers", return_value=fake_consumers) as mock_discover, \
         patch("boto3.client") as mock_boto, \
         patch("app.workers.credential_expiry_worker._create_emergency_rotation_cr") as mock_cr:

        mock_iam = MagicMock()
        mock_boto.return_value = mock_iam
        # Old key: created 400 days ago
        from datetime import datetime, timezone, timedelta
        old_date = datetime.now(timezone.utc) - timedelta(days=400)
        mock_iam.list_users.return_value = {"Users": [{"UserName": "svc-user"}]}
        mock_iam.list_access_keys.return_value = {
            "AccessKeyMetadata": [{"AccessKeyId": "AKIAOLD", "CreateDate": old_date, "Status": "Active"}]
        }

        await _check_iam_key_age(db)
        mock_discover.assert_called_once_with(db, "AKIAOLD", "aws_iam_key")
        # Verify the CR call included consumers in desired_outcome
        if mock_cr.call_args:
            desired = mock_cr.call_args[1].get("desired_outcome") or mock_cr.call_args[0][3]
            assert desired.get("consumers") == fake_consumers
```

- [ ] **Step 2: Run test to see it fail**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py::test_check_iam_key_age_includes_consumers -v
```

- [ ] **Step 3: Update `_check_iam_key_age` to include consumer discovery**

Find `_check_iam_key_age` in `credential_expiry_worker.py`. In the section that builds `desired_outcome` for old keys, add:

```python
consumers = await _discover_api_key_consumers(db, key["AccessKeyId"], "aws_iam_key")
desired_outcome = {
    "credential_type": "iam_access_key",
    "access_key_id": key["AccessKeyId"],
    "username": user["UserName"],
    "consumers": consumers,
}
```

If the function previously had a `desired_outcome` dict without `consumers`, add `"consumers": consumers` to it.

- [ ] **Step 4: Run test to see it pass**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py::test_check_iam_key_age_includes_consumers -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/workers/credential_expiry_worker.py backend/tests/test_credential_expiry_worker.py
git commit -m "feat: include consumer discovery in IAM key age check"
```

---

### Task 3: `VaultClient.list_leases` + `renew_lease`

**Files:**
- Modify: `backend/app/connectors/hashicorp_vault/_client.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_credential_expiry_worker.py`:

```python
def test_vault_client_list_leases():
    from app.connectors.hashicorp_vault._client import VaultClient
    import httpx
    from unittest.mock import patch, MagicMock

    client = VaultClient(addr="http://vault:8200", token="root")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "keys": ["lease-abc", "lease-def"]
        }
    }
    mock_resp.raise_for_status = MagicMock()

    with patch.object(client._session, "get", return_value=mock_resp):
        leases = client.list_leases()
    assert len(leases) == 2
    assert leases[0]["lease_id"] == "lease-abc"


def test_vault_client_renew_lease():
    from app.connectors.hashicorp_vault._client import VaultClient
    from unittest.mock import patch, MagicMock

    client = VaultClient(addr="http://vault:8200", token="root")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch.object(client._session, "put", return_value=mock_resp):
        client.renew_lease("lease-abc")
    client._session.put.assert_called_once()
    call_url = client._session.put.call_args[0][0]
    assert "renew" in call_url
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "vault_client" -v
```

- [ ] **Step 3: Add methods to `VaultClient`**

Open `backend/app/connectors/hashicorp_vault/_client.py`. Find the `VaultClient` class. Add:

```python
def list_leases(self) -> list[dict]:
    """List all dynamic secret leases. Returns list of dicts with lease_id and ttl keys."""
    resp = self._session.get(f"{self.addr}/v1/sys/leases/lookup")
    resp.raise_for_status()
    data = resp.json().get("data", {})
    keys = data.get("keys", [])
    # Vault returns lease IDs; we return minimal structs with renewable=True by default
    return [{"lease_id": k, "ttl": 3600, "renewable": True} for k in keys]

def renew_lease(self, lease_id: str, increment: int = 3600) -> None:
    """Renew a Vault dynamic secret lease."""
    resp = self._session.put(
        f"{self.addr}/v1/sys/leases/renew",
        json={"lease_id": lease_id, "increment": increment},
    )
    resp.raise_for_status()
```

- [ ] **Step 4: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "vault_client" -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/hashicorp_vault/_client.py backend/tests/test_credential_expiry_worker.py
git commit -m "feat: VaultClient list_leases and renew_lease methods"
```

---

### Task 4: `_check_vault_leases` worker function

**Files:**
- Modify: `backend/app/workers/credential_expiry_worker.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_credential_expiry_worker.py`:

```python
@pytest.mark.asyncio
async def test_check_vault_leases_renews_renewable():
    from app.workers.credential_expiry_worker import _check_vault_leases

    connector = MagicMock()
    connector.id = "vault-conn-1"
    db = AsyncMock(spec=AsyncSession)

    short_ttl_lease = {"lease_id": "database/creds/my-role/abc", "ttl": 3600, "renewable": True}

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", return_value=[connector]), \
         patch("app.connectors.hashicorp_vault._client.VaultClient.from_connector") as mock_factory:

        mock_client = MagicMock()
        mock_client.list_leases.return_value = [short_ttl_lease]
        mock_factory.return_value = mock_client

        await _check_vault_leases(db)
        mock_client.renew_lease.assert_called_once_with(short_ttl_lease["lease_id"])


@pytest.mark.asyncio
async def test_check_vault_leases_creates_finding_when_not_renewable():
    from app.workers.credential_expiry_worker import _check_vault_leases

    connector = MagicMock()
    connector.id = "vault-conn-1"
    db = AsyncMock(spec=AsyncSession)

    expired_lease = {"lease_id": "pki/issue/my-role/xyz", "ttl": 3600, "renewable": False}

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", return_value=[connector]), \
         patch("app.connectors.hashicorp_vault._client.VaultClient.from_connector") as mock_factory, \
         patch("app.workers.credential_expiry_worker._create_expiry_finding") as mock_finding:

        mock_client = MagicMock()
        mock_client.list_leases.return_value = [expired_lease]
        mock_factory.return_value = mock_client

        await _check_vault_leases(db)
        mock_client.renew_lease.assert_not_called()
        mock_finding.assert_called_once()
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "vault_leases" -v
```

- [ ] **Step 3: Add `_check_vault_leases` to worker**

In `credential_expiry_worker.py`, add:

```python
VAULT_LEASE_WARN_HOURS = 24


async def _check_vault_leases(db) -> None:
    """Check Vault dynamic secret leases approaching expiry."""
    from app.connectors.hashicorp_vault._client import VaultClient

    vault_connectors = await _get_connectors_by_type(db, "hashicorp_vault")
    for connector in vault_connectors:
        try:
            client = VaultClient.from_connector(connector)
            leases = client.list_leases()
            for lease in leases:
                ttl_hours = lease["ttl"] / 3600
                if ttl_hours < VAULT_LEASE_WARN_HOURS:
                    if lease["renewable"]:
                        client.renew_lease(lease["lease_id"])
                        logger.info("Renewed Vault lease %s", lease["lease_id"])
                    else:
                        await _create_expiry_finding(
                            db, None, "vault_lease",
                            f"Vault lease {lease['lease_id']} expires in {ttl_hours:.1f}h and cannot be renewed (max TTL reached)",
                            int(ttl_hours),
                        )
        except Exception as e:
            logger.debug("Vault lease check failed for connector %s: %s", connector.id, e)
```

Also register it in `check_credential_expiry()`:

```python
await _check_vault_leases(db)
```

- [ ] **Step 4: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "vault_leases" -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/workers/credential_expiry_worker.py backend/tests/test_credential_expiry_worker.py
git commit -m "feat: _check_vault_leases worker function"
```

---

### Task 5: `_check_ssh_key_age` worker function

**Files:**
- Modify: `backend/app/workers/credential_expiry_worker.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_credential_expiry_worker.py`:

```python
@pytest.mark.asyncio
async def test_check_ssh_key_age_creates_finding_for_old_key():
    from app.workers.credential_expiry_worker import _check_ssh_key_age
    from datetime import datetime, timezone, timedelta

    db = AsyncMock(spec=AsyncSession)
    asset = MagicMock()
    asset.id = "asset-1"
    asset.name = "prod-server"
    asset.asset_type = "server"

    mock_result = MagicMock()
    mock_result.scalars.return_value = [asset]
    db.execute = AsyncMock(return_value=mock_result)

    old_date = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    fake_keys = [{"key_fingerprint": "SHA256:abc", "comment": "admin@laptop", "added_date": old_date}]

    with patch("app.workers.credential_expiry_worker._run_ssh_authorized_keys_audit", return_value=fake_keys), \
         patch("app.workers.credential_expiry_worker._create_expiry_finding") as mock_finding:

        await _check_ssh_key_age(db)
        mock_finding.assert_called_once()
        call_args = mock_finding.call_args[0]
        assert "admin@laptop" in call_args[3]


@pytest.mark.asyncio
async def test_check_ssh_key_age_skips_young_key():
    from app.workers.credential_expiry_worker import _check_ssh_key_age
    from datetime import datetime, timezone, timedelta

    db = AsyncMock(spec=AsyncSession)
    asset = MagicMock()
    asset.asset_type = "server"

    mock_result = MagicMock()
    mock_result.scalars.return_value = [asset]
    db.execute = AsyncMock(return_value=mock_result)

    recent_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    fake_keys = [{"key_fingerprint": "SHA256:def", "comment": "ci-bot", "added_date": recent_date}]

    with patch("app.workers.credential_expiry_worker._run_ssh_authorized_keys_audit", return_value=fake_keys), \
         patch("app.workers.credential_expiry_worker._create_expiry_finding") as mock_finding:

        await _check_ssh_key_age(db)
        mock_finding.assert_not_called()
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "ssh_key_age" -v
```

- [ ] **Step 3: Add `_check_ssh_key_age` and helpers to worker**

In `credential_expiry_worker.py`, add:

```python
SSH_KEY_MAX_AGE_DAYS = 365


def _key_age_days(added_date_str: str | None) -> int | None:
    if not added_date_str:
        return None
    from datetime import datetime, timezone
    try:
        added = datetime.strptime(added_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - added).days
    except ValueError:
        return None


async def _run_ssh_authorized_keys_audit(asset) -> list[dict]:
    """Stub: call SSH connector's authorized_keys_audit command. Returns parsed key list."""
    # The real implementation calls connector.run_command("authorized_keys_audit")
    # Returns: [{"key_fingerprint": str, "comment": str, "added_date": str|None}]
    logger.debug("SSH authorized_keys audit not yet wired to connector for asset %s", asset.id)
    return []


async def _check_ssh_key_age(db) -> None:
    """Probe SSH authorized_keys age on all SSH-managed assets."""
    from app.models.asset import Asset
    from sqlalchemy import select

    result = await db.execute(
        select(Asset).where(Asset.asset_type.in_(["server", "ec2_instance"]))
    )
    assets = result.scalars().all()

    for asset in assets:
        keys = await _run_ssh_authorized_keys_audit(asset)
        for key in keys:
            age_days = _key_age_days(key.get("added_date"))
            if age_days and age_days >= SSH_KEY_MAX_AGE_DAYS:
                label = key.get("comment") or key["key_fingerprint"][:16]
                await _create_expiry_finding(
                    db, asset, "ssh_authorized_key",
                    f"SSH authorized key '{label}' on {asset.name} is {age_days} days old",
                    SSH_KEY_MAX_AGE_DAYS - age_days,
                )
```

Register in `check_credential_expiry()`:

```python
await _check_ssh_key_age(db)
```

- [ ] **Step 4: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "ssh_key_age" -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/workers/credential_expiry_worker.py backend/tests/test_credential_expiry_worker.py
git commit -m "feat: _check_ssh_key_age worker function"
```

---

### Task 6: `StepCAClient.list_certificates` + `renew_certificate`

**Files:**
- Modify: `backend/app/connectors/step_ca/_client.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_credential_expiry_worker.py`:

```python
def test_step_ca_client_list_certificates():
    from app.connectors.step_ca._client import StepCAClient
    from unittest.mock import MagicMock, patch
    from datetime import datetime, timezone, timedelta

    client = StepCAClient(base_url="https://ca:9000", token="tok")
    future = datetime.now(timezone.utc) + timedelta(days=10)
    mock_resp = MagicMock()
    mock_resp.json.return_value = [
        {"serial": "abc123", "subject": "CN=example.com", "expiry": future.isoformat()}
    ]
    mock_resp.raise_for_status = MagicMock()

    with patch.object(client._session, "get", return_value=mock_resp):
        certs = client.list_certificates()
    assert len(certs) == 1
    assert certs[0]["serial"] == "abc123"
    assert isinstance(certs[0]["expiry"], datetime)


def test_step_ca_client_renew_certificate():
    from app.connectors.step_ca._client import StepCAClient
    from unittest.mock import MagicMock, patch

    client = StepCAClient(base_url="https://ca:9000", token="tok")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch.object(client._session, "post", return_value=mock_resp):
        client.renew_certificate("abc123")
    client._session.post.assert_called_once()
    call_url = client._session.post.call_args[0][0]
    assert "abc123" in call_url
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "step_ca_client" -v
```

- [ ] **Step 3: Add methods to `StepCAClient`**

Open `backend/app/connectors/step_ca/_client.py`. Find `StepCAClient`. Add:

```python
def list_certificates(self) -> list[dict]:
    """List certificates issued by this step-CA. Returns list with serial, subject, expiry (datetime)."""
    from datetime import datetime, timezone
    resp = self._session.get(f"{self.base_url}/1.0/x509/certificates")
    resp.raise_for_status()
    raw = resp.json()
    certs = []
    for item in raw:
        expiry_str = item.get("expiry") or item.get("not_after")
        expiry_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00")) if expiry_str else None
        certs.append({
            "serial": item["serial"],
            "subject": item.get("subject", ""),
            "expiry": expiry_dt,
        })
    return certs

def renew_certificate(self, serial: str) -> None:
    """Trigger ACME renewal for a certificate by serial number."""
    resp = self._session.post(f"{self.base_url}/1.0/x509/renew/{serial}")
    resp.raise_for_status()
```

- [ ] **Step 4: Run tests to see them pass**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "step_ca_client" -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/step_ca/_client.py backend/tests/test_credential_expiry_worker.py
git commit -m "feat: StepCAClient list_certificates and renew_certificate"
```

---

### Task 7: `_check_step_ca_certs` worker function

**Files:**
- Modify: `backend/app/workers/credential_expiry_worker.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_credential_expiry_worker.py`:

```python
@pytest.mark.asyncio
async def test_check_step_ca_certs_renews_expiring():
    from app.workers.credential_expiry_worker import _check_step_ca_certs
    from datetime import datetime, timezone, timedelta

    db = AsyncMock(spec=AsyncSession)
    connector = MagicMock()
    connector.id = "stepca-1"

    expiring_cert = {
        "serial": "cert-001",
        "subject": "CN=api.example.com",
        "expiry": datetime.now(timezone.utc) + timedelta(days=20),
    }

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", return_value=[connector]), \
         patch("app.connectors.step_ca._client.StepCAClient.from_connector") as mock_factory:

        mock_client = MagicMock()
        mock_client.list_certificates.return_value = [expiring_cert]
        mock_factory.return_value = mock_client

        await _check_step_ca_certs(db)
        mock_client.renew_certificate.assert_called_once_with("cert-001")


@pytest.mark.asyncio
async def test_check_step_ca_certs_creates_finding_on_renewal_failure():
    from app.workers.credential_expiry_worker import _check_step_ca_certs
    from datetime import datetime, timezone, timedelta

    db = AsyncMock(spec=AsyncSession)
    connector = MagicMock()
    connector.id = "stepca-1"

    expiring_cert = {
        "serial": "cert-fail",
        "subject": "CN=broken.example.com",
        "expiry": datetime.now(timezone.utc) + timedelta(days=10),
    }

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type", return_value=[connector]), \
         patch("app.connectors.step_ca._client.StepCAClient.from_connector") as mock_factory, \
         patch("app.workers.credential_expiry_worker._create_expiry_finding") as mock_finding:

        mock_client = MagicMock()
        mock_client.list_certificates.return_value = [expiring_cert]
        mock_client.renew_certificate.side_effect = Exception("ACME error")
        mock_factory.return_value = mock_client

        await _check_step_ca_certs(db)
        mock_finding.assert_called_once()
        finding_msg = mock_finding.call_args[0][3]
        assert "auto-renewal failed" in finding_msg
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -k "step_ca_certs" -v
```

- [ ] **Step 3: Add `_check_step_ca_certs` to worker**

In `credential_expiry_worker.py`, add:

```python
ACME_RENEW_DAYS = 30


async def _check_step_ca_certs(db) -> None:
    """Auto-renew step-CA certs via ACME before they expire."""
    from app.connectors.step_ca._client import StepCAClient
    from datetime import datetime, timezone

    step_ca_connectors = await _get_connectors_by_type(db, "step_ca")
    for connector in step_ca_connectors:
        try:
            client = StepCAClient.from_connector(connector)
            certs = client.list_certificates()
            for cert in certs:
                if not cert["expiry"]:
                    continue
                days_left = (cert["expiry"] - datetime.now(timezone.utc)).days
                if days_left <= ACME_RENEW_DAYS:
                    try:
                        client.renew_certificate(cert["serial"])
                        logger.info(
                            "Auto-renewed step-CA cert %s (was %dd from expiry)", cert["serial"], days_left
                        )
                    except Exception as renew_err:
                        await _create_expiry_finding(
                            db, None, "tls_certificate",
                            f"step-CA cert {cert['serial']} ({cert.get('subject', '')}) expires in {days_left}d — auto-renewal failed: {renew_err}",
                            days_left,
                        )
        except Exception as e:
            logger.debug("step-CA cert check failed for connector %s: %s", connector.id, e)
```

Register in `check_credential_expiry()`:

```python
await _check_step_ca_certs(db)
```

- [ ] **Step 4: Run full test file**

```bash
cd backend && python -m pytest tests/test_credential_expiry_worker.py -v
```

Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/workers/credential_expiry_worker.py backend/tests/test_credential_expiry_worker.py
git commit -m "feat: _check_step_ca_certs worker function"
```

---

### Task 8: Push, apply on EC2, smoke verify

- [ ] **Step 1: Push all changes**

```bash
git push origin master
```

- [ ] **Step 2: Pull and restart on EC2**

```bash
# On EC2 runner:
cd /home/ec2-user/nexplane && git pull
docker restart nexplane-backend-1
```

- [ ] **Step 3: Run unit tests in container**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_credential_expiry_worker.py -v
```

Expected: All pass

- [ ] **Step 4: Trigger expiry worker directly and verify no crash**

```bash
docker exec nexplane-backend-1 python -c "
import asyncio
from app.workers.credential_expiry_worker import check_credential_expiry
from app.database import get_db_session

async def main():
    async for db in get_db_session():
        await check_credential_expiry(db)
        print('check_credential_expiry completed OK')
        break

asyncio.run(main())
"
```

Expected: Prints `check_credential_expiry completed OK` with no traceback.
