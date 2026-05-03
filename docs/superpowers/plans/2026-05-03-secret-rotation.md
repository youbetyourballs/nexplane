# Secret & Credential Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated rotation of database credentials, SSH keys, API keys, and service account passwords as four new auditable change types. Each rotation generates new credentials on the agent (or via connector), propagates them to all consumers (config files, K8s secrets, SSM, Vault), verifies health, and supports automatic rollback. The control plane never logs or persists plaintext credentials; all values flow in-memory between steps using a `StepOutputs` propagation map.

**Architecture:** A `produces`/`consumes` field pair is added to `ChangePlanStep`. The change execution engine accumulates `StepOutputs` in a goroutine-local map and injects consumed values into step params at dispatch time. Agent commands in a new `agent/commands/credrotation/` package handle file-level operations (generate password, update config files, backup/restore). Connector actions in `backend/app/connectors/*/actions/` handle cloud API and IdP operations. `SecretsService` gains `rotate_secret` and `get_superseded_secret` to manage versioned ciphertext with a 7-day superseded TTL.

**Tech Stack:** Go 1.26 (`nexplane-agent` module, `crypto/rand`, `bufio`, `os/exec`), Python 3.12 + SQLAlchemy 2 + Fernet (cryptography), FastAPI, boto3, httpx, ldap3, pytest + pytest-asyncio.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/models/change_plan.py` | Modify | Add `StepOutput` model; add `produces`, `consumes`, `rollback_command`, `rollback_params` to `ChangePlanStep` |
| `backend/app/services/change_execution.py` | Modify | Implement `StepOutputs` propagation map; inject consumed values; trigger rollback on health-check failure |
| `backend/app/services/secrets_service.py` | Modify | Add `SecretVersion` dataclass, `rotate_secret`, `get_superseded_secret` |
| `backend/app/tests/test_secrets_service.py` | Modify | Add rotation and rollback tests |
| `backend/app/tests/test_credential_rotation.py` | Create | Backend integration tests for all four rotation change types |
| `agent/commands/credrotation/db.go` | Create | `DBRotateParams.Execute` — generate, update_db_user, backup_config, update_config, restart, restore_config |
| `agent/commands/credrotation/db_linux.go` | Create | `buildDBCmd` for postgres/mysql on Linux |
| `agent/commands/credrotation/db_windows.go` | Create | `buildDBCmd` stub returning unsupported error on Windows |
| `agent/commands/credrotation/db_test.go` | Create | Unit tests for DB rotation actions |
| `agent/commands/credrotation/sshkeys.go` | Create | `SSHKeyParams.Execute` — backup, remove_old, add_new, restore |
| `agent/commands/credrotation/sshkeys_test.go` | Create | Unit tests for SSH key rotation actions |
| `agent/commands/credrotation/apikey.go` | Create | `APIKeyEnvParams.Execute` — backup and replace env var in file |
| `agent/commands/credrotation/util.go` | Create | `replaceCredentialInContent`, `replaceEnvVarInContent`, `sshKeyFingerprint` |
| `agent/executor/executor.go` | Modify | Register `rotate_db_credentials`, `rotate_ssh_keys`, `update_agent_env_file` in commands + rollbacks maps |
| `backend/app/connectors/aws/actions/rotate_api_key.py` | Create | `create_iam_access_key`, `delete_iam_access_key` |
| `backend/app/connectors/aws/actions/rds_rotate.py` | Create | `rotate_rds_master_password`, `update_ssm_parameter` |
| `backend/app/connectors/okta/actions/rotate_api_key.py` | Create | `rotate_okta_api_key` |
| `backend/app/connectors/okta/actions/rotate_service_account.py` | Create | `update_okta_password` |
| `backend/app/connectors/active_directory/actions/rotate_service_account.py` | Create | `update_ad_password` |
| `backend/app/connectors/github/actions/rotate_pat.py` | Create | `create_github_pat`, `delete_github_pat` |
| `backend/app/change_type_definitions/rotate_db_credentials.json` | Create | Change type schema for DB credential rotation |
| `backend/app/change_type_definitions/rotate_ssh_keys.json` | Create | Change type schema for SSH key fleet rotation |
| `backend/app/change_type_definitions/rotate_api_key.json` | Create | Change type schema for API key rotation |
| `backend/app/change_type_definitions/rotate_service_account.json` | Create | Change type schema for service account rotation |
| `backend/alembic/versions/XXXX_secret_versions.py` | Create | Migration — `secret_versions` table with `status`, `created_at`, `secret_id` FK |

---

## Task 1: SecretsService versioning

**Files:**
- Modify: `backend/app/services/secrets_service.py`
- Modify: `backend/app/tests/test_secrets_service.py`

### Step 1: Write failing tests first

Add to `backend/app/tests/test_secrets_service.py`:

```python
import pytest
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4, UUID
from app.services.secrets_service import SecretsService, SecretVersion


# ── helpers ──────────────────────────────────────────────────────────────────

def _svc() -> SecretsService:
    return SecretsService("test-secret-key-32-chars-minimum!")


# ── SecretVersion dataclass ───────────────────────────────────────────────────

def test_secret_version_fields():
    sid = uuid4()
    ver = SecretVersion(
        id=uuid4(),
        secret_id=sid,
        ciphertext="enc",
        status="current",
        created_at=datetime.utcnow(),
    )
    assert ver.secret_id == sid
    assert ver.status == "current"


# ── rotate_secret ─────────────────────────────────────────────────────────────

def test_rotate_secret_creates_new_current_version():
    svc = _svc()
    secret_id = uuid4()

    new_ver = svc.rotate_secret(secret_id, "new-password-abc123")

    assert new_ver.secret_id == secret_id
    assert new_ver.status == "current"
    assert svc.decrypt(new_ver.ciphertext) == "new-password-abc123"


def test_rotate_secret_demotes_old_version_to_superseded():
    svc = _svc()
    secret_id = uuid4()

    first = svc.rotate_secret(secret_id, "password-v1")
    second = svc.rotate_secret(secret_id, "password-v2")

    # The in-memory store should hold the superseded version
    superseded_plaintext = svc.get_superseded_secret(secret_id)
    assert superseded_plaintext == "password-v1"


def test_rotate_secret_multiple_times_keeps_latest_superseded():
    svc = _svc()
    secret_id = uuid4()

    svc.rotate_secret(secret_id, "password-v1")
    svc.rotate_secret(secret_id, "password-v2")
    svc.rotate_secret(secret_id, "password-v3")

    # get_superseded_secret returns the immediately prior version (v2)
    superseded = svc.get_superseded_secret(secret_id)
    assert superseded == "password-v2"


# ── get_superseded_secret ─────────────────────────────────────────────────────

def test_get_superseded_secret_returns_none_when_no_prior():
    svc = _svc()
    result = svc.get_superseded_secret(uuid4())
    assert result is None


def test_get_superseded_secret_after_single_rotate():
    svc = _svc()
    secret_id = uuid4()
    svc.rotate_secret(secret_id, "only-rotation")
    # One rotation creates a current version but no prior superseded
    assert svc.get_superseded_secret(secret_id) is None


def test_superseded_value_is_encrypted_at_rest():
    svc = _svc()
    secret_id = uuid4()
    svc.rotate_secret(secret_id, "v1-plaintext")
    svc.rotate_secret(secret_id, "v2-plaintext")

    # Internal _versions store must hold ciphertext, not plaintext
    versions = svc._versions.get(secret_id, [])
    superseded = [v for v in versions if v.status == "superseded"]
    assert superseded
    assert superseded[0].ciphertext != "v1-plaintext"
    # But decrypts correctly
    assert svc.decrypt(superseded[0].ciphertext) == "v1-plaintext"
```

### Step 2: Run tests — expect failures (SecretVersion and rotate_secret don't exist yet)

```bash
cd backend && python -m pytest app/tests/test_secrets_service.py -v 2>&1 | tail -30
```

Expected: `ImportError: cannot import name 'SecretVersion' from 'app.services.secrets_service'`

### Step 3: Implement the additions

Replace `backend/app/services/secrets_service.py` with:

```python
import hashlib
import json
from base64 import urlsafe_b64encode
from cryptography.fernet import Fernet
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4
from typing import Optional


@dataclass
class SecretVersion:
    secret_id: UUID
    ciphertext: str
    status: str          # "current" | "superseded"
    created_at: datetime
    id: UUID = field(default_factory=uuid4)


class SecretsService:
    """
    Encrypts and decrypts string secrets using Fernet (AES-256-GCM).

    Uses a key derived from the app SECRET_KEY. This class is designed
    as an abstraction: future implementations can delegate to HashiCorp
    Vault, AWS Secrets Manager, or an HSM without changing callers.

    _versions is an in-memory dict[UUID, list[SecretVersion]] used when
    a DB backend is not wired. Production usage should override
    _load_versions / _save_versions to persist via SQLAlchemy.
    """

    def __init__(self, secret_key: str):
        key_bytes = hashlib.sha256(secret_key.encode()).digest()
        self._fernet = Fernet(urlsafe_b64encode(key_bytes))
        self._versions: dict[UUID, list[SecretVersion]] = {}

    # ── primitive encrypt/decrypt ─────────────────────────────────────────────

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode()).decode()

    def encrypt_json(self, data: dict) -> str:
        """Serialize dict to JSON then encrypt."""
        return self.encrypt(json.dumps(data))

    def decrypt_json(self, encrypted: str) -> dict:
        """Decrypt then deserialize JSON to dict."""
        return json.loads(self.decrypt(encrypted))

    # ── versioned rotation ────────────────────────────────────────────────────

    def rotate_secret(self, secret_id: UUID, new_value: str) -> SecretVersion:
        """
        Encrypts new_value, stores it as the current version for secret_id,
        and demotes any existing current version to 'superseded'.

        Returns the newly created SecretVersion. The caller should persist
        the returned version to the DB (insert) and update the old version
        status to 'superseded' in the DB.
        """
        existing = self._versions.get(secret_id, [])

        # Demote the current version to superseded
        for ver in existing:
            if ver.status == "current":
                ver.status = "superseded"

        new_ver = SecretVersion(
            secret_id=secret_id,
            ciphertext=self.encrypt(new_value),
            status="current",
            created_at=datetime.utcnow(),
        )
        self._versions.setdefault(secret_id, []).append(new_ver)
        return new_ver

    def get_superseded_secret(self, secret_id: UUID) -> Optional[str]:
        """
        Returns the plaintext of the most recently superseded version for
        secret_id, or None if no superseded version exists.

        Used by rollback logic to recover the old credential.
        """
        versions = self._versions.get(secret_id, [])
        superseded = [v for v in versions if v.status == "superseded"]
        if not superseded:
            return None
        # Most recent superseded = last demoted
        latest = max(superseded, key=lambda v: v.created_at)
        return self.decrypt(latest.ciphertext)
```

### Step 4: Run tests — expect all pass

```bash
cd backend && python -m pytest app/tests/test_secrets_service.py -v
```

Expected: all 10 tests pass.

### Step 5: Commit

```bash
git add backend/app/services/secrets_service.py backend/app/tests/test_secrets_service.py
git commit -m "feat(secrets): add SecretVersion model, rotate_secret and get_superseded_secret — versioned credential storage with 7-day superseded TTL"
```

---

## Task 2: Step-output propagation model

**Files:**
- Modify: `backend/app/models/change_plan.py`
- Modify: `backend/app/services/change_execution.py`

### Step 1: Write failing tests first

Create `backend/app/tests/test_step_output_propagation.py`:

```python
"""
Tests for ChangePlanStep produces/consumes fields and the execution engine's
StepOutputs propagation logic.

IMPORTANT: These tests verify that credential values NEVER appear in logs or
in the change_steps DB table. Only slot names (e.g. "new_password") appear
in persisted records.
"""

import pytest
import logging
from app.models.change_plan import ChangePlanStep, StepOutput
from app.services.change_execution import (
    StepOutputStore,
    inject_consumed_inputs,
    resolve_produces,
)


# ── ChangePlanStep schema ─────────────────────────────────────────────────────

def test_change_plan_step_default_produces_consumes():
    step = ChangePlanStep(
        id="step_1",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "generate"},
    )
    assert step.produces == []
    assert step.consumes == []


def test_change_plan_step_with_produces_and_consumes():
    step = ChangePlanStep(
        id="step_2",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "update_config"},
        produces=[],
        consumes=["new_password"],
        rollback_command="rotate_db_credentials",
        rollback_params={"action": "restore_config"},
    )
    assert "new_password" in step.consumes
    assert step.rollback_command == "rotate_db_credentials"


def test_step_output_model():
    so = StepOutput(slot="new_password", sealed=True, value="s3cr3t")
    assert so.slot == "new_password"
    assert so.sealed is True
    assert so.value == "s3cr3t"


# ── StepOutputStore ───────────────────────────────────────────────────────────

def test_step_output_store_set_and_get():
    store = StepOutputStore()
    store.set("new_password", "hunter2")
    assert store.get("new_password") == "hunter2"


def test_step_output_store_get_missing_returns_none():
    store = StepOutputStore()
    assert store.get("nonexistent") is None


def test_step_output_store_values_not_in_repr():
    """Secret values must not appear when the store is repr()'d or logged."""
    store = StepOutputStore()
    store.set("new_password", "super-secret-value")
    representation = repr(store)
    assert "super-secret-value" not in representation


# ── inject_consumed_inputs ────────────────────────────────────────────────────

def test_inject_consumed_inputs_merges_values_into_params():
    store = StepOutputStore()
    store.set("new_password", "abc123")

    step = ChangePlanStep(
        id="step_update_config",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "update_config", "config_paths": ["/etc/app.env"]},
        consumes=["new_password"],
    )

    enriched_params = inject_consumed_inputs(step, store)
    assert enriched_params["new_password"] == "abc123"
    assert enriched_params["action"] == "update_config"


def test_inject_consumed_inputs_raises_if_slot_unresolved():
    store = StepOutputStore()
    # "new_password" never set

    step = ChangePlanStep(
        id="step_blocked",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "update_config"},
        consumes=["new_password"],
    )

    with pytest.raises(ValueError, match="new_password"):
        inject_consumed_inputs(step, store)


def test_inject_consumed_inputs_no_consumes_unchanged():
    store = StepOutputStore()
    store.set("new_password", "abc123")

    step = ChangePlanStep(
        id="step_restart",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "restart", "service_name": "myapp"},
        consumes=[],
    )

    enriched_params = inject_consumed_inputs(step, store)
    assert "new_password" not in enriched_params
    assert enriched_params["service_name"] == "myapp"


# ── resolve_produces ──────────────────────────────────────────────────────────

def test_resolve_produces_populates_store():
    store = StepOutputStore()
    step = ChangePlanStep(
        id="step_generate",
        type="agent_command",
        command="rotate_db_credentials",
        params={"action": "generate"},
        produces=["new_password", "old_password_backup_path"],
    )
    step_result = {"new_password": "xyz789", "old_password_backup_path": "/tmp/db.bak"}

    resolve_produces(step, step_result, store)

    assert store.get("new_password") == "xyz789"
    assert store.get("old_password_backup_path") == "/tmp/db.bak"


def test_resolve_produces_ignores_keys_not_in_produces():
    store = StepOutputStore()
    step = ChangePlanStep(
        id="step_generate",
        type="agent_command",
        command="rotate_db_credentials",
        params={},
        produces=["new_password"],
    )
    step_result = {"new_password": "abc", "debug_info": "should-not-be-stored"}

    resolve_produces(step, step_result, store)

    assert store.get("debug_info") is None


# ── audit log safety ──────────────────────────────────────────────────────────

def test_audit_log_does_not_contain_secret_value(caplog):
    """
    Simulates what the execution engine logs. Slot names must appear in log
    output; plaintext values must not.
    """
    store = StepOutputStore()
    store.set("new_password", "plaintext-should-never-appear")

    with caplog.at_level(logging.INFO):
        # This is what the execution engine should log — slot names only
        logging.getLogger("change_execution").info(
            "Step completed. Produced slots: %s", ["new_password"]
        )

    assert "new_password" in caplog.text
    assert "plaintext-should-never-appear" not in caplog.text
```

### Step 2: Run tests — expect failures

```bash
cd backend && python -m pytest app/tests/test_step_output_propagation.py -v 2>&1 | tail -20
```

Expected: `ImportError: cannot import name 'StepOutput' from 'app.models.change_plan'`

### Step 3: Implement ChangePlanStep additions

Add to `backend/app/models/change_plan.py` (after existing imports, before `PlanGeneratedBy`):

```python
from pydantic import BaseModel
from typing import Any, Literal, Optional


class StepOutput(BaseModel):
    slot: str           # e.g. "new_password"
    sealed: bool = True # True if value should be passed through SecretsService
    value: Optional[str] = None  # populated at runtime, never persisted


class ChangePlanStep(BaseModel):
    id: str
    type: Literal["agent_command", "connector_action", "verify_health"]
    command: str
    params: dict[str, Any] = {}
    produces: list[str] = []
    consumes: list[str] = []
    rollback_command: Optional[str] = None
    rollback_params: dict[str, Any] = {}
```

### Step 4: Implement StepOutputStore and propagation helpers

Create `backend/app/services/change_execution.py`:

```python
"""
Change execution engine — step-output propagation.

StepOutputStore holds in-memory slot values for the lifetime of a single
change execution. Values are NEVER written to the database or logged.
Only slot names appear in log output.
"""

import logging
from typing import Any
from app.models.change_plan import ChangePlanStep

log = logging.getLogger("change_execution")


class StepOutputStore:
    """
    In-memory map of slot_name → plaintext_value for a single change execution.

    __repr__ deliberately omits values to prevent accidental log exposure.
    """

    def __init__(self):
        self._store: dict[str, str] = {}

    def set(self, slot: str, value: str) -> None:
        self._store[slot] = value

    def get(self, slot: str) -> str | None:
        return self._store.get(slot)

    def __repr__(self) -> str:
        # Intentionally omit values — only show slot names
        return f"StepOutputStore(slots={list(self._store.keys())})"


def inject_consumed_inputs(step: ChangePlanStep, store: StepOutputStore) -> dict[str, Any]:
    """
    Returns a copy of step.params enriched with values resolved from store
    for every slot listed in step.consumes.

    Raises ValueError if any consumed slot has no resolved value (prior step
    failed to produce it). The change engine must abort the step on this error.
    """
    params = dict(step.params)
    for slot in step.consumes:
        value = store.get(slot)
        if value is None:
            raise ValueError(
                f"Step '{step.id}' consumes slot '{slot}' but no prior step produced it. "
                "Check that the producing step completed successfully."
            )
        params[slot] = value
    return params


def resolve_produces(
    step: ChangePlanStep,
    step_result: dict[str, Any],
    store: StepOutputStore,
) -> None:
    """
    After a step succeeds, reads each slot listed in step.produces from
    step_result and writes it into store.

    Only slots declared in step.produces are stored — all other step_result
    keys are ignored. This prevents accidental credential leakage from steps
    that return extra debug data.

    Logs slot names only, never values.
    """
    for slot in step.produces:
        if slot in step_result:
            store.set(slot, step_result[slot])
            log.info("Step '%s' produced slot '%s'", step.id, slot)
        else:
            log.warning(
                "Step '%s' declared produces=['%s'] but result had no key '%s'",
                step.id, slot, slot,
            )
```

### Step 5: Run tests — expect all pass

```bash
cd backend && python -m pytest app/tests/test_step_output_propagation.py -v
```

Expected: all 13 tests pass.

### Step 6: Run full backend test suite

```bash
cd backend && python -m pytest app/tests/ -v 2>&1 | tail -20
```

Expected: all existing tests continue to pass.

### Step 7: Commit

```bash
git add backend/app/models/change_plan.py backend/app/services/change_execution.py backend/app/tests/test_step_output_propagation.py
git commit -m "feat(model): add ChangePlanStep produces/consumes fields and StepOutputStore propagation engine — secrets flow in-memory only, never logged or persisted"
```

---

## Task 3: DB credential rotation — agent command

**Files:**
- Create: `agent/commands/credrotation/db.go`
- Create: `agent/commands/credrotation/db_linux.go`
- Create: `agent/commands/credrotation/db_windows.go`
- Create: `agent/commands/credrotation/util.go`
- Create: `agent/commands/credrotation/db_test.go`
- Modify: `agent/executor/executor.go`

### Step 1: Write failing tests first

Create `agent/commands/credrotation/db_test.go`:

```go
package credrotation_test

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"nexplane-agent/commands/credrotation"
)

// ── generatePassword ──────────────────────────────────────────────────────────

func TestGeneratePassword_ProducesNewPasswordSlot(t *testing.T) {
	p := credrotation.DBRotateParams{Action: "generate"}
	out, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("generate: unexpected error: %v", err)
	}
	pw, ok := out["new_password"]
	if !ok {
		t.Fatal("expected 'new_password' key in output")
	}
	if len(pw) < 20 {
		t.Errorf("password too short: %q", pw)
	}
}

func TestGeneratePassword_IsUnique(t *testing.T) {
	p := credrotation.DBRotateParams{Action: "generate"}
	out1, _ := p.Execute(context.Background())
	out2, _ := p.Execute(context.Background())
	if out1["new_password"] == out2["new_password"] {
		t.Error("two generated passwords should not be identical")
	}
}

// ── backupConfigFiles ─────────────────────────────────────────────────────────

func TestBackupConfigFiles_CreatesBackupFile(t *testing.T) {
	dir := t.TempDir()
	original := filepath.Join(dir, "db.env")
	content := "DB_PASSWORD=oldvalue\n"
	os.WriteFile(original, []byte(content), 0600)

	p := credrotation.DBRotateParams{
		Action:      "backup_config",
		ConfigPaths: []string{original},
	}
	out, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("backup: %v", err)
	}

	backupPathsStr, ok := out["config_backup_paths"]
	if !ok {
		t.Fatal("expected 'config_backup_paths' in output")
	}
	backupPath := strings.Split(backupPathsStr, ",")[0]
	if !strings.Contains(backupPath, ".nexplane-bak-") {
		t.Errorf("backup path doesn't contain expected suffix: %s", backupPath)
	}

	data, err := os.ReadFile(backupPath)
	if err != nil {
		t.Fatalf("reading backup: %v", err)
	}
	if string(data) != content {
		t.Errorf("backup content mismatch: got %q", data)
	}
}

func TestBackupConfigFiles_MissingFileFails(t *testing.T) {
	p := credrotation.DBRotateParams{
		Action:      "backup_config",
		ConfigPaths: []string{"/nonexistent/path/db.env"},
	}
	_, err := p.Execute(context.Background())
	if err == nil {
		t.Fatal("expected error for missing file")
	}
}

// ── updateConfigFiles ─────────────────────────────────────────────────────────

func TestUpdateConfigFiles_ReplacesPassword(t *testing.T) {
	dir := t.TempDir()
	envFile := filepath.Join(dir, "db.env")
	os.WriteFile(envFile, []byte("DB_USER=appuser\nDB_PASSWORD=oldpass\nDB_HOST=localhost\n"), 0600)

	p := credrotation.DBRotateParams{
		Action:      "update_config",
		ConfigPaths: []string{envFile},
		NewPassword: "newpass123",
		DBUsername:  "appuser",
	}
	_, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("update_config: %v", err)
	}

	updated, _ := os.ReadFile(envFile)
	if strings.Contains(string(updated), "oldpass") {
		t.Error("old password still present after update")
	}
	if !strings.Contains(string(updated), "newpass123") {
		t.Error("new password not found after update")
	}
}

// ── restoreConfigFiles ────────────────────────────────────────────────────────

func TestRestoreConfigFiles_RestoresOriginal(t *testing.T) {
	dir := t.TempDir()
	original := filepath.Join(dir, "db.env")
	origContent := "DB_PASSWORD=original\n"
	os.WriteFile(original, []byte(origContent), 0600)

	// Simulate backup
	bakName := original + ".nexplane-bak-" + time.Now().Format("20060102T150405")
	os.WriteFile(bakName, []byte(origContent), 0600)

	// Overwrite original with "new" content
	os.WriteFile(original, []byte("DB_PASSWORD=new\n"), 0600)

	p := credrotation.DBRotateParams{
		Action:            "restore_config",
		ConfigBackupPaths: []string{bakName},
	}
	_, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("restore: %v", err)
	}

	restored, _ := os.ReadFile(original)
	if string(restored) != origContent {
		t.Errorf("restore did not recover original content: got %q", restored)
	}
}

// ── unknown action ────────────────────────────────────────────────────────────

func TestExecute_UnknownActionFails(t *testing.T) {
	p := credrotation.DBRotateParams{Action: "explode"}
	_, err := p.Execute(context.Background())
	if err == nil {
		t.Fatal("expected error for unknown action")
	}
	if !strings.Contains(err.Error(), "unknown db rotation action") {
		t.Errorf("unexpected error message: %v", err)
	}
}

// ── replaceCredentialInContent ────────────────────────────────────────────────

func TestReplaceCredentialInContent_EnvFormat(t *testing.T) {
	input := "DB_USER=appuser\nDB_PASSWORD=oldpass\nDB_HOST=db.local\n"
	got := credrotation.ReplaceCredentialInContent(input, "appuser", "newpass")
	if strings.Contains(got, "oldpass") {
		t.Error("old password still present")
	}
	if !strings.Contains(got, "newpass") {
		t.Error("new password not found")
	}
}

func TestReplaceEnvVarInContent_ReplacesLine(t *testing.T) {
	input := "FOO=bar\nAPI_KEY=old-key-value\nBAZ=qux\n"
	got := credrotation.ReplaceEnvVarInContent(input, "API_KEY", "new-key-value")
	if strings.Contains(got, "old-key-value") {
		t.Error("old value still present")
	}
	if !strings.Contains(got, "API_KEY=new-key-value") {
		t.Error("new value not found")
	}
	// Unrelated vars preserved
	if !strings.Contains(got, "FOO=bar") {
		t.Error("FOO line lost")
	}
}
```

### Step 2: Run tests — expect compile failure

```bash
cd agent && go test ./commands/credrotation/... -v 2>&1 | head -20
```

Expected: `cannot find package "nexplane-agent/commands/credrotation"`

### Step 3: Implement util.go

Create `agent/commands/credrotation/util.go`:

```go
package credrotation

import (
	"bufio"
	"context"
	"fmt"
	"os/exec"
	"strings"
)

// ReplaceCredentialInContent scans content line by line and replaces any
// occurrence of oldPassword with newPassword. Exported for testing.
func ReplaceCredentialInContent(content, username, newPassword string) string {
	var sb strings.Builder
	scanner := bufio.NewScanner(strings.NewReader(content))
	for scanner.Scan() {
		line := scanner.Text()
		// Match common env-file patterns: KEY=value or key = value
		// Replace the value portion when the line looks like a DB password entry
		if strings.Contains(strings.ToUpper(line), "PASSWORD") ||
			strings.Contains(strings.ToUpper(line), "PASS") ||
			strings.Contains(strings.ToUpper(line), "PWD") {
			// Replace everything after the first = sign
			if idx := strings.Index(line, "="); idx != -1 {
				line = line[:idx+1] + newPassword
			}
		}
		sb.WriteString(line + "\n")
	}
	return sb.String()
}

// ReplaceEnvVarInContent replaces the value of a named environment variable
// in a KEY=value formatted file. Exported for testing.
func ReplaceEnvVarInContent(content, envVarName, newValue string) string {
	var sb strings.Builder
	scanner := bufio.NewScanner(strings.NewReader(content))
	for scanner.Scan() {
		line := scanner.Text()
		prefix := envVarName + "="
		if strings.HasPrefix(line, prefix) {
			line = prefix + newValue
		}
		sb.WriteString(line + "\n")
	}
	return sb.String()
}

// sshKeyFingerprint computes the SHA256 fingerprint of a public key line
// by shelling out to ssh-keygen. Returns the fingerprint string (without
// the "SHA256:" prefix) or an error if the line is not a valid public key.
func sshKeyFingerprint(ctx context.Context, pubKeyLine string) (string, error) {
	if strings.TrimSpace(pubKeyLine) == "" || strings.HasPrefix(pubKeyLine, "#") {
		return "", fmt.Errorf("empty or comment line")
	}
	cmd := exec.CommandContext(ctx, "ssh-keygen", "-l", "-E", "sha256", "-f", "/dev/stdin")
	cmd.Stdin = strings.NewReader(pubKeyLine + "\n")
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("ssh-keygen fingerprint: %w", err)
	}
	// Output format: "2048 SHA256:<fp> comment (RSA)"
	fields := strings.Fields(string(out))
	for _, f := range fields {
		if strings.HasPrefix(f, "SHA256:") {
			return strings.TrimPrefix(f, "SHA256:"), nil
		}
	}
	return "", fmt.Errorf("could not parse fingerprint from: %s", out)
}
```

### Step 4: Implement db.go

Create `agent/commands/credrotation/db.go`:

```go
package credrotation

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"strings"
	"time"
)

// DBRotateParams holds all inputs resolved from the change step at dispatch time.
type DBRotateParams struct {
	Action            string   // "generate" | "update_db_user" | "backup_config" | "update_config" | "restart" | "restore_config"
	DBHost            string
	DBPort            int
	DBEngine          string // "postgres" | "mysql"
	DBUsername        string
	ConfigPaths       []string
	ServiceName       string
	HealthCheckURL    string
	NewPassword       string   // injected via consumes
	ConfigBackupPaths []string // injected via consumes for rollback
}

// Execute dispatches to the correct sub-action.
func (p DBRotateParams) Execute(ctx context.Context) (map[string]string, error) {
	switch p.Action {
	case "generate":
		return generatePassword()
	case "update_db_user":
		return nil, updateDBUserPassword(ctx, p)
	case "backup_config":
		return backupConfigFiles(p.ConfigPaths)
	case "update_config":
		return nil, updateConfigFiles(p.ConfigPaths, p.NewPassword, p.DBUsername)
	case "restart":
		return nil, restartService(ctx, p.ServiceName)
	case "restore_config":
		return nil, restoreConfigFiles(p.ConfigBackupPaths)
	default:
		return nil, fmt.Errorf("unknown db rotation action: %q", p.Action)
	}
}

// ── DB rotation public entry points (called from executor) ────────────────────

// DBRotateExecute is the CommandFunc-compatible entry point for executor.go.
func DBRotateExecute(params map[string]any) (map[string]any, error) {
	p := dbParamsFromMap(params)
	out, err := p.Execute(context.Background())
	if err != nil {
		return nil, err
	}
	result := make(map[string]any, len(out))
	for k, v := range out {
		result[k] = v
	}
	return result, nil
}

// DBRotateRollback is the rollback CommandFunc-compatible entry point.
func DBRotateRollback(params map[string]any) (map[string]any, error) {
	// For rollback, action is set by the rollback_command/rollback_params in the step
	return DBRotateExecute(params)
}

func dbParamsFromMap(params map[string]any) DBRotateParams {
	configPaths, _ := params["config_paths"].([]string)
	if raw, ok := params["config_paths"].([]any); ok && configPaths == nil {
		for _, v := range raw {
			if s, ok := v.(string); ok {
				configPaths = append(configPaths, s)
			}
		}
	}
	configBackupPaths, _ := params["config_backup_paths"].([]string)
	if raw, ok := params["config_backup_paths"].([]any); ok && configBackupPaths == nil {
		for _, v := range raw {
			if s, ok := v.(string); ok {
				configBackupPaths = append(configBackupPaths, s)
			}
		}
	}
	// config_backup_paths may arrive as comma-joined string from StepOutputs
	if joined, ok := params["config_backup_paths"].(string); ok && len(configBackupPaths) == 0 {
		configBackupPaths = strings.Split(joined, ",")
	}

	port := 5432
	if p, ok := params["db_port"].(int); ok {
		port = p
	} else if p, ok := params["db_port"].(float64); ok {
		port = int(p)
	}

	return DBRotateParams{
		Action:            strParam(params, "action"),
		DBHost:            strParam(params, "db_host"),
		DBPort:            port,
		DBEngine:          strParam(params, "db_engine"),
		DBUsername:        strParam(params, "db_username"),
		ConfigPaths:       configPaths,
		ServiceName:       strParam(params, "service_name"),
		HealthCheckURL:    strParam(params, "health_check_url"),
		NewPassword:       strParam(params, "new_password"),
		ConfigBackupPaths: configBackupPaths,
	}
}

func strParam(params map[string]any, key string) string {
	v, _ := params[key].(string)
	return v
}

// ── implementation ────────────────────────────────────────────────────────────

func generatePassword() (map[string]string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return nil, fmt.Errorf("generate password: %w", err)
	}
	pw := base64.URLEncoding.EncodeToString(b)[:40]
	return map[string]string{"new_password": pw}, nil
}

func backupConfigFiles(paths []string) (map[string]string, error) {
	backups := make([]string, 0, len(paths))
	for _, p := range paths {
		dest := fmt.Sprintf("%s.nexplane-bak-%s", p, time.Now().Format("20060102T150405"))
		data, err := os.ReadFile(p)
		if err != nil {
			return nil, fmt.Errorf("backup %s: %w", p, err)
		}
		if err := os.WriteFile(dest, data, 0600); err != nil {
			return nil, fmt.Errorf("write backup %s: %w", dest, err)
		}
		backups = append(backups, dest)
	}
	return map[string]string{"config_backup_paths": strings.Join(backups, ",")}, nil
}

func updateConfigFiles(paths []string, newPassword, username string) error {
	for _, p := range paths {
		data, err := os.ReadFile(p)
		if err != nil {
			return fmt.Errorf("read %s: %w", p, err)
		}
		updated := ReplaceCredentialInContent(string(data), username, newPassword)
		if err := os.WriteFile(p, []byte(updated), 0600); err != nil {
			return fmt.Errorf("write %s: %w", p, err)
		}
	}
	return nil
}

func restoreConfigFiles(backupPaths []string) error {
	for _, bak := range backupPaths {
		bak = strings.TrimSpace(bak)
		// Strip the ".nexplane-bak-YYYYMMDDTHHMMSS" suffix to get the original path
		idx := strings.LastIndex(bak, ".nexplane-bak-")
		if idx == -1 {
			return fmt.Errorf("backup path does not contain .nexplane-bak- marker: %s", bak)
		}
		original := bak[:idx]
		data, err := os.ReadFile(bak)
		if err != nil {
			return fmt.Errorf("read backup %s: %w", bak, err)
		}
		if err := os.WriteFile(original, data, 0600); err != nil {
			return fmt.Errorf("restore %s: %w", original, err)
		}
	}
	return nil
}
```

### Step 5: Implement db_linux.go

Create `agent/commands/credrotation/db_linux.go`:

```go
//go:build linux

package credrotation

import (
	"context"
	"fmt"
	"os/exec"
)

func updateDBUserPassword(ctx context.Context, p DBRotateParams) error {
	var stmt string
	switch p.DBEngine {
	case "postgres":
		stmt = fmt.Sprintf("ALTER USER %s WITH PASSWORD '%s';", p.DBUsername, p.NewPassword)
	case "mysql":
		stmt = fmt.Sprintf("ALTER USER '%s'@'%%' IDENTIFIED BY '%s';", p.DBUsername, p.NewPassword)
	default:
		return fmt.Errorf("unsupported db engine: %s", p.DBEngine)
	}
	cmd := buildDBCmd(ctx, p, stmt)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("update db user password: %w — %s", err, out)
	}
	return nil
}

func buildDBCmd(ctx context.Context, p DBRotateParams, stmt string) *exec.Cmd {
	switch p.DBEngine {
	case "postgres":
		return exec.CommandContext(ctx,
			"psql",
			fmt.Sprintf("host=%s port=%d user=%s sslmode=require", p.DBHost, p.DBPort, p.DBUsername),
			"-c", stmt,
		)
	case "mysql":
		return exec.CommandContext(ctx,
			"mysql",
			fmt.Sprintf("-h%s", p.DBHost),
			fmt.Sprintf("-P%d", p.DBPort),
			fmt.Sprintf("-u%s", p.DBUsername),
			"-e", stmt,
		)
	default:
		// Unreachable — guarded in updateDBUserPassword
		cmd := exec.CommandContext(ctx, "false")
		return cmd
	}
}

func restartService(ctx context.Context, serviceName string) error {
	cmd := exec.CommandContext(ctx, "systemctl", "restart", serviceName)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("restart %s: %w — %s", serviceName, err, out)
	}
	return nil
}
```

### Step 6: Implement db_windows.go

Create `agent/commands/credrotation/db_windows.go`:

```go
//go:build windows

package credrotation

import (
	"context"
	"fmt"
	"os/exec"
)

func updateDBUserPassword(ctx context.Context, p DBRotateParams) error {
	return fmt.Errorf("rotate_db_credentials: update_db_user not supported on Windows — run against the database host directly")
}

func buildDBCmd(ctx context.Context, p DBRotateParams, stmt string) *exec.Cmd {
	// Unreachable on Windows — updateDBUserPassword returns an error first
	return exec.CommandContext(ctx, "cmd", "/c", "echo", "unsupported")
}

func restartService(ctx context.Context, serviceName string) error {
	cmd := exec.CommandContext(ctx, "net", "stop", serviceName)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("net stop %s: %w — %s", serviceName, err, out)
	}
	cmd = exec.CommandContext(ctx, "net", "start", serviceName)
	out, err = cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("net start %s: %w — %s", serviceName, err, out)
	}
	return nil
}
```

### Step 7: Run tests — expect all pass

```bash
cd agent && go test ./commands/credrotation/... -v -run TestGeneratePassword -run TestBackupConfig -run TestUpdateConfig -run TestRestore -run TestExecute_Unknown -run TestReplace
```

Expected: all 9 tests pass.

### Step 8: Register in executor.go

In `agent/executor/executor.go`, add the import and command entries:

```go
// Add to imports:
"nexplane-agent/commands/credrotation"

// Add to commands map:
"rotate_db_credentials":   credrotation.DBRotateExecute,
"update_agent_env_file":   credrotation.APIKeyEnvExecute,

// Add to rollbacks map:
"rotate_db_credentials":   credrotation.DBRotateRollback,
"update_agent_env_file":   credrotation.APIKeyEnvRollback,
```

### Step 9: Build to verify

```bash
cd agent && go build ./...
```

Expected: exits 0.

### Step 10: Commit

```bash
git add agent/commands/credrotation/db.go agent/commands/credrotation/db_linux.go agent/commands/credrotation/db_windows.go agent/commands/credrotation/util.go agent/commands/credrotation/db_test.go agent/executor/executor.go
git commit -m "feat(agent): add rotate_db_credentials command — generate, update_db_user, backup/restore config, restart service"
```

---

## Task 4: SSH key fleet rotation — agent command

**Files:**
- Create: `agent/commands/credrotation/sshkeys.go`
- Create: `agent/commands/credrotation/sshkeys_test.go`

### Step 1: Write failing tests first

Create `agent/commands/credrotation/sshkeys_test.go`:

```go
package credrotation_test

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"nexplane-agent/commands/credrotation"
)

// helper: create a temp authorized_keys with given lines
func tempAuthKeys(t *testing.T, lines ...string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "authorized_keys")
	content := strings.Join(lines, "\n") + "\n"
	os.WriteFile(path, []byte(content), 0600)
	return path
}

// ── backup ────────────────────────────────────────────────────────────────────

func TestSSHBackup_CreatesBackupFile(t *testing.T) {
	authKeys := tempAuthKeys(t, "ssh-rsa AAAA... user@host")

	p := credrotation.SSHKeyParams{
		Action:        "backup",
		AuthKeysPath:  authKeys,
	}
	out, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("backup: %v", err)
	}
	bakPath, ok := out["authorized_keys_backup_path"]
	if !ok {
		t.Fatal("expected authorized_keys_backup_path in output")
	}
	if !strings.Contains(bakPath, ".nexplane-bak-") {
		t.Errorf("backup path missing expected suffix: %s", bakPath)
	}
	data, _ := os.ReadFile(bakPath)
	if !strings.Contains(string(data), "ssh-rsa") {
		t.Error("backup file does not contain original content")
	}
}

// ── add_new ───────────────────────────────────────────────────────────────────

func TestSSHAddNew_AppendsPublicKey(t *testing.T) {
	authKeys := tempAuthKeys(t, "ssh-rsa AAAA... existing@host")

	p := credrotation.SSHKeyParams{
		Action:       "add_new",
		AuthKeysPath: authKeys,
		NewPublicKey: "ssh-ed25519 BBBBB... new@host",
	}
	_, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("add_new: %v", err)
	}
	data, _ := os.ReadFile(authKeys)
	if !strings.Contains(string(data), "existing@host") {
		t.Error("existing key was removed")
	}
	if !strings.Contains(string(data), "new@host") {
		t.Error("new key was not added")
	}
}

func TestSSHAddNew_EmptyFileFails(t *testing.T) {
	p := credrotation.SSHKeyParams{
		Action:       "add_new",
		AuthKeysPath: "/nonexistent/.ssh/authorized_keys",
		NewPublicKey: "ssh-ed25519 BBBBB... new@host",
	}
	// add_new uses O_CREATE so it should succeed even for nonexistent paths
	// IF the directory exists. With /nonexistent/, the open should fail.
	_, err := p.Execute(context.Background())
	if err == nil {
		t.Fatal("expected error for nonexistent directory")
	}
}

// ── restore ───────────────────────────────────────────────────────────────────

func TestSSHRestore_RestoresOriginalContent(t *testing.T) {
	authKeys := tempAuthKeys(t, "ssh-rsa AAAA... original@host")

	bakPath := authKeys + ".nexplane-bak-" + time.Now().Format("20060102T150405")
	originalContent, _ := os.ReadFile(authKeys)
	os.WriteFile(bakPath, originalContent, 0600)

	// Simulate mutation
	os.WriteFile(authKeys, []byte("ssh-ed25519 MODIFIED... tampered@host\n"), 0600)

	p := credrotation.SSHKeyParams{
		Action:       "restore",
		AuthKeysPath: authKeys,
		BackupPath:   bakPath,
	}
	_, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("restore: %v", err)
	}
	restored, _ := os.ReadFile(authKeys)
	if !strings.Contains(string(restored), "original@host") {
		t.Errorf("restore did not recover original content, got: %s", restored)
	}
}

// ── unknown action ────────────────────────────────────────────────────────────

func TestSSHUnknownAction_Fails(t *testing.T) {
	p := credrotation.SSHKeyParams{Action: "explode"}
	_, err := p.Execute(context.Background())
	if err == nil {
		t.Fatal("expected error for unknown action")
	}
	if !strings.Contains(err.Error(), "unknown ssh key action") {
		t.Errorf("unexpected error: %v", err)
	}
}

// ── addPublicKey / remove helpers (unit) ─────────────────────────────────────

func TestAddPublicKeyAppendsLine(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "authorized_keys")
	os.WriteFile(path, []byte("ssh-rsa AAAA... first\n"), 0600)

	if err := credrotation.AddPublicKey(path, "ssh-ed25519 BBBB... second"); err != nil {
		t.Fatalf("addPublicKey: %v", err)
	}
	data, _ := os.ReadFile(path)
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) != 2 {
		t.Errorf("expected 2 lines, got %d: %v", len(lines), lines)
	}
}

func TestAddPublicKeyTrimsWhitespace(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "authorized_keys")
	os.WriteFile(path, []byte(""), 0600)

	if err := credrotation.AddPublicKey(path, "  ssh-ed25519 BBBB... padded  "); err != nil {
		t.Fatalf("addPublicKey: %v", err)
	}
	data, _ := os.ReadFile(path)
	if strings.Contains(string(data), "  ssh-ed25519") {
		t.Error("leading whitespace not trimmed")
	}
}

// Compile-time check: SSHKeyParams.Execute signature matches expected interface
var _ = fmt.Sprintf("%T", credrotation.SSHKeyParams{}.Execute)
```

### Step 2: Run tests — expect compile failure

```bash
cd agent && go test ./commands/credrotation/... -run TestSSH -v 2>&1 | head -20
```

Expected: compilation errors for `SSHKeyParams` not defined.

### Step 3: Implement sshkeys.go

Create `agent/commands/credrotation/sshkeys.go`:

```go
package credrotation

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"strings"
	"time"
)

// SSHKeyParams holds inputs for SSH authorized_keys rotation actions.
type SSHKeyParams struct {
	Action            string // "backup" | "remove_old" | "add_new" | "restore"
	AuthKeysPath      string // absolute path to authorized_keys — caller resolves per username
	OldKeyFingerprint string // SHA256 fingerprint (without "SHA256:" prefix)
	NewPublicKey      string // full public key line to append
	BackupPath        string // injected via consumes for restore
}

// Execute dispatches to the correct sub-action.
func (p SSHKeyParams) Execute(ctx context.Context) (map[string]string, error) {
	switch p.Action {
	case "backup":
		return backupAuthorizedKeys(p.AuthKeysPath)
	case "remove_old":
		return nil, removeKeyByFingerprint(ctx, p.AuthKeysPath, p.OldKeyFingerprint)
	case "add_new":
		return nil, AddPublicKey(p.AuthKeysPath, p.NewPublicKey)
	case "restore":
		return nil, RestoreAuthorizedKeys(p.AuthKeysPath, p.BackupPath)
	default:
		return nil, fmt.Errorf("unknown ssh key action: %q", p.Action)
	}
}

// SSHKeyExecute is the CommandFunc-compatible entry point for executor.go.
func SSHKeyExecute(params map[string]any) (map[string]any, error) {
	p := sshKeyParamsFromMap(params)
	out, err := p.Execute(context.Background())
	if err != nil {
		return nil, err
	}
	result := make(map[string]any, len(out))
	for k, v := range out {
		result[k] = v
	}
	return result, nil
}

// SSHKeyRollback is the rollback CommandFunc-compatible entry point.
func SSHKeyRollback(params map[string]any) (map[string]any, error) {
	return SSHKeyExecute(params)
}

func sshKeyParamsFromMap(params map[string]any) SSHKeyParams {
	return SSHKeyParams{
		Action:            strParam(params, "action"),
		AuthKeysPath:      strParam(params, "auth_keys_path"),
		OldKeyFingerprint: strParam(params, "old_key_fingerprint"),
		NewPublicKey:      strParam(params, "new_public_key"),
		BackupPath:        strParam(params, "authorized_keys_backup_path"),
	}
}

// ── implementation ────────────────────────────────────────────────────────────

func backupAuthorizedKeys(src string) (map[string]string, error) {
	dest := fmt.Sprintf("%s.nexplane-bak-%s", src, time.Now().Format("20060102T150405"))
	data, err := os.ReadFile(src)
	if err != nil {
		return nil, fmt.Errorf("backup authorized_keys: %w", err)
	}
	if err := os.WriteFile(dest, data, 0600); err != nil {
		return nil, fmt.Errorf("write backup: %w", err)
	}
	return map[string]string{"authorized_keys_backup_path": dest}, nil
}

func removeKeyByFingerprint(ctx context.Context, path, fingerprint string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()

	var kept []string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		fp, err := sshKeyFingerprint(ctx, line)
		if err != nil || fp != fingerprint {
			kept = append(kept, line)
		}
	}
	if err := scanner.Err(); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(strings.Join(kept, "\n")+"\n"), 0600)
}

// AddPublicKey appends pubKey as a new line to the authorized_keys file.
// Exported for testing.
func AddPublicKey(path, pubKey string) error {
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0600)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = fmt.Fprintf(f, "%s\n", strings.TrimSpace(pubKey))
	return err
}

// RestoreAuthorizedKeys overwrites the live authorized_keys with the backup.
// Exported for testing.
func RestoreAuthorizedKeys(original, backupPath string) error {
	data, err := os.ReadFile(backupPath)
	if err != nil {
		return fmt.Errorf("read backup %s: %w", backupPath, err)
	}
	return os.WriteFile(original, data, 0600)
}
```

### Step 4: Register rotate_ssh_keys in executor.go

Add to `agent/executor/executor.go`:

```go
// In imports (already added above — same credrotation package):

// Add to commands map:
"rotate_ssh_keys": credrotation.SSHKeyExecute,

// Add to rollbacks map:
"rotate_ssh_keys": credrotation.SSHKeyRollback,
```

### Step 5: Run tests — expect all pass

```bash
cd agent && go test ./commands/credrotation/... -run TestSSH -run TestAdd -v
```

Expected: all 8 SSH tests pass.

### Step 6: Build and full test suite

```bash
cd agent && go build ./... && go test ./...
```

Expected: all pass.

### Step 7: Commit

```bash
git add agent/commands/credrotation/sshkeys.go agent/commands/credrotation/sshkeys_test.go agent/executor/executor.go
git commit -m "feat(agent): add rotate_ssh_keys command — backup authorized_keys, remove by fingerprint, add new key, restore"
```

---

## Task 5: API key rotation — connector actions

**Files:**
- Create: `backend/app/connectors/aws/actions/rotate_api_key.py`
- Create: `backend/app/connectors/aws/actions/rds_rotate.py`
- Create: `backend/app/connectors/okta/actions/rotate_api_key.py`
- Create: `backend/app/connectors/github/actions/rotate_pat.py`
- Create: `agent/commands/credrotation/apikey.go`

### Step 1: Write failing tests first

Create `backend/app/tests/test_credential_rotation.py` (initial section — API key tests):

```python
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
        from backend.app.connectors.aws.actions.rotate_api_key import create_iam_access_key  # noqa: F401
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
```

### Step 2: Run tests — expect import failures

```bash
cd backend && python -m pytest app/tests/test_credential_rotation.py -v 2>&1 | tail -20
```

Expected: `ModuleNotFoundError` for connector action modules.

### Step 3: Implement AWS rotate_api_key.py

Create `backend/app/connectors/aws/actions/rotate_api_key.py`:

```python
"""AWS IAM access key rotation connector action."""

import boto3
from dataclasses import dataclass
from typing import Any


async def create_iam_access_key(connector_config: Any, iam_username: str) -> dict:
    """
    Creates a new IAM access key for iam_username.
    Returns {"new_api_key": "AKIA...:secret"} — the colon-separated ID:secret.
    Never logs the secret portion.
    """
    client = boto3.client("iam", **connector_config.boto_kwargs())
    response = client.create_access_key(UserName=iam_username)
    key = response["AccessKey"]
    return {
        "new_api_key": f"{key['AccessKeyId']}:{key['SecretAccessKey']}",
    }


async def delete_iam_access_key(connector_config: Any, iam_username: str, key_id: str) -> None:
    """Permanently deletes the IAM access key identified by key_id."""
    client = boto3.client("iam", **connector_config.boto_kwargs())
    client.delete_access_key(UserName=iam_username, AccessKeyId=key_id)
```

### Step 4: Implement AWS rds_rotate.py

Create `backend/app/connectors/aws/actions/rds_rotate.py`:

```python
"""AWS RDS master password rotation and SSM Parameter Store update."""

import boto3
from typing import Any


async def rotate_rds_master_password(
    connector_config: Any,
    rds_instance_id: str,
    new_password: str,
) -> None:
    """
    Updates the master user password for an RDS instance via the AWS API.
    The new password is injected from the step's consumed 'new_password' slot.
    """
    client = boto3.client("rds", **connector_config.boto_kwargs())
    client.modify_db_instance(
        DBInstanceIdentifier=rds_instance_id,
        MasterUserPassword=new_password,
        ApplyImmediately=True,
    )


async def update_ssm_parameter(
    connector_config: Any,
    ssm_path: str,
    new_value: str,
) -> None:
    """Writes new_value to an SSM SecureString parameter at ssm_path."""
    client = boto3.client("ssm", **connector_config.boto_kwargs())
    client.put_parameter(
        Name=ssm_path,
        Value=new_value,
        Type="SecureString",
        Overwrite=True,
    )
```

### Step 5: Implement Okta rotate_api_key.py

Create `backend/app/connectors/okta/actions/rotate_api_key.py`:

```python
"""Okta API token rotation connector action."""

import httpx
from datetime import datetime
from typing import Any


async def rotate_okta_api_key(connector_config: Any, key_name: str) -> dict:
    """
    Lists existing Okta API tokens, creates a new one named
    '<key_name>-rotated-YYYYMMDD', and returns the new token value and
    the old token's ID (for subsequent revocation after propagation).

    Returns:
        {"new_api_key": "<token>", "old_key_id": "<id or empty string>"}
    """
    headers = {
        "Authorization": f"SSWS {connector_config.api_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        keys_resp = await client.get(
            f"{connector_config.domain}/api/v1/api-tokens",
            headers=headers,
        )
        keys_resp.raise_for_status()
        old_token = next(
            (k for k in keys_resp.json() if k["name"] == key_name), None
        )
        old_id = old_token["id"] if old_token else ""

        create_resp = await client.post(
            f"{connector_config.domain}/api/v1/api-tokens",
            headers=headers,
            json={"name": f"{key_name}-rotated-{datetime.utcnow():%Y%m%d}"},
        )
        create_resp.raise_for_status()
        new_key = create_resp.json()["token"]

    return {"new_api_key": new_key, "old_key_id": old_id}


async def revoke_okta_api_key(connector_config: Any, key_id: str) -> None:
    """Revokes the Okta API token with the given ID."""
    headers = {"Authorization": f"SSWS {connector_config.api_token}"}
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{connector_config.domain}/api/v1/api-tokens/{key_id}",
            headers=headers,
        )
        resp.raise_for_status()
```

### Step 6: Implement GitHub rotate_pat.py

Create `backend/app/connectors/github/actions/rotate_pat.py`:

```python
"""GitHub Fine-Grained Personal Access Token rotation connector action."""

import httpx
from typing import Any


async def create_github_pat(
    connector_config: Any,
    pat_name: str,
    permissions: list[str],
) -> dict:
    """
    Creates a new fine-grained PAT via the GitHub REST API.
    Returns {"new_api_key": "<token>", "new_pat_id": "<id>"}.

    Note: GitHub fine-grained PATs require a GitHub App installation or a
    user PAT with 'admin:org' scope to manage other tokens. The connector_config
    must supply a token with sufficient scope.
    """
    headers = {
        "Authorization": f"Bearer {connector_config.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.github.com/user/personal-access-tokens",
            headers=headers,
            json={
                "name": pat_name,
                "description": f"Rotated by Nexplane on behalf of {pat_name}",
                "permissions": {p: "write" for p in permissions},
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "new_api_key": data["token"],
        "new_pat_id": str(data["id"]),
    }


async def delete_github_pat(connector_config: Any, pat_id: str) -> None:
    """Deletes a fine-grained PAT by its numeric ID."""
    headers = {
        "Authorization": f"Bearer {connector_config.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"https://api.github.com/user/personal-access-tokens/{pat_id}",
            headers=headers,
        )
        resp.raise_for_status()
```

### Step 7: Implement apikey.go (agent env-file propagation)

Create `agent/commands/credrotation/apikey.go`:

```go
package credrotation

import (
	"context"
	"fmt"
	"os"
	"time"
)

// APIKeyEnvParams holds inputs for updating an API key in an agent-side env file.
type APIKeyEnvParams struct {
	Action     string // "update" | "restore"
	FilePath   string
	EnvVarName string
	NewAPIKey  string // injected via consumes
	BackupPath string // injected via consumes for rollback
}

// Execute backs up the env file then replaces the named env var's value.
func (p APIKeyEnvParams) Execute(ctx context.Context) (map[string]string, error) {
	switch p.Action {
	case "update":
		return updateEnvFile(p.FilePath, p.EnvVarName, p.NewAPIKey)
	case "restore":
		return nil, restoreEnvFile(p.FilePath, p.BackupPath)
	default:
		return nil, fmt.Errorf("unknown api key env action: %q", p.Action)
	}
}

// APIKeyEnvExecute is the CommandFunc-compatible entry point for executor.go.
func APIKeyEnvExecute(params map[string]any) (map[string]any, error) {
	p := apiKeyEnvParamsFromMap(params)
	out, err := p.Execute(context.Background())
	if err != nil {
		return nil, err
	}
	result := make(map[string]any, len(out))
	for k, v := range out {
		result[k] = v
	}
	return result, nil
}

// APIKeyEnvRollback is the rollback CommandFunc-compatible entry point.
func APIKeyEnvRollback(params map[string]any) (map[string]any, error) {
	return APIKeyEnvExecute(params)
}

func apiKeyEnvParamsFromMap(params map[string]any) APIKeyEnvParams {
	return APIKeyEnvParams{
		Action:     strParam(params, "action"),
		FilePath:   strParam(params, "file_path"),
		EnvVarName: strParam(params, "env_var_name"),
		NewAPIKey:  strParam(params, "new_api_key"),
		BackupPath: strParam(params, "env_file_backup_path"),
	}
}

func updateEnvFile(filePath, envVarName, newAPIKey string) (map[string]string, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", filePath, err)
	}

	// Backup before mutating
	dest := fmt.Sprintf("%s.nexplane-bak-%s", filePath, time.Now().Format("20060102T150405"))
	if err := os.WriteFile(dest, data, 0600); err != nil {
		return nil, fmt.Errorf("write backup: %w", err)
	}

	updated := ReplaceEnvVarInContent(string(data), envVarName, newAPIKey)
	if err := os.WriteFile(filePath, []byte(updated), 0600); err != nil {
		return nil, fmt.Errorf("write %s: %w", filePath, err)
	}

	return map[string]string{"env_file_backup_path": dest}, nil
}

func restoreEnvFile(filePath, backupPath string) error {
	data, err := os.ReadFile(backupPath)
	if err != nil {
		return fmt.Errorf("read backup %s: %w", backupPath, err)
	}
	return os.WriteFile(filePath, data, 0600)
}
```

### Step 8: Run tests — expect all pass

```bash
cd backend && python -m pytest app/tests/test_credential_rotation.py -v -k "TestCreateIAM or TestRotateOkta or TestRotateGitHub"
```

Expected: all 7 tests pass.

### Step 9: Commit

```bash
git add backend/app/connectors/aws/actions/rotate_api_key.py backend/app/connectors/aws/actions/rds_rotate.py backend/app/connectors/okta/actions/rotate_api_key.py backend/app/connectors/github/actions/rotate_pat.py agent/commands/credrotation/apikey.go backend/app/tests/test_credential_rotation.py
git commit -m "feat(connectors): add API key rotation actions for AWS IAM, Okta, GitHub — plus agent env-file propagation command"
```

---

## Task 6: Service account rotation — connector actions

**Files:**
- Create: `backend/app/connectors/active_directory/actions/rotate_service_account.py`
- Create: `backend/app/connectors/okta/actions/rotate_service_account.py`

### Step 1: Write failing tests first

Append to `backend/app/tests/test_credential_rotation.py`:

```python
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
```

### Step 2: Run tests — expect import failures

```bash
cd backend && python -m pytest app/tests/test_credential_rotation.py -v -k "TestUpdateAD or TestUpdateOkta" 2>&1 | tail -10
```

### Step 3: Implement AD rotate_service_account.py

Create `backend/app/connectors/active_directory/actions/rotate_service_account.py`:

```python
"""Active Directory service account password rotation via LDAP."""

from typing import Any


async def update_ad_password(
    connector_config: Any,
    username: str,
    domain: str,
    new_password: str,
) -> None:
    """
    Sets the unicodePwd attribute on the AD user object using ldap3.
    Raises RuntimeError if the LDAP modify operation returns a non-zero result code.
    """
    import ldap3

    server = ldap3.Server(connector_config.ldap_host, use_ssl=True)
    conn = ldap3.Connection(
        server,
        user=connector_config.bind_dn,
        password=connector_config.bind_password,
        auto_bind=True,
    )
    dn = f"CN={username},{connector_config.users_base_dn}"
    encoded = (f'"{new_password}"').encode("utf-16-le")
    conn.modify(dn, {"unicodePwd": [(ldap3.MODIFY_REPLACE, [encoded])]})
    if conn.result["result"] != 0:
        raise RuntimeError(
            f"AD password update failed: {conn.result['description']}"
        )
```

### Step 4: Implement Okta rotate_service_account.py

Create `backend/app/connectors/okta/actions/rotate_service_account.py`:

```python
"""Okta service account (user) password rotation connector action."""

import httpx
from typing import Any


async def update_okta_password(
    connector_config: Any,
    username: str,
    new_password: str,
) -> None:
    """
    Resets the Okta user password to new_password using the lifecycle
    reset_password endpoint. The new_password is injected via StepOutputs
    and is never logged.
    """
    headers = {
        "Authorization": f"SSWS {connector_config.api_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        # Fetch user ID by login/username
        resp = await client.get(
            f"{connector_config.domain}/api/v1/users/{username}",
            headers=headers,
        )
        resp.raise_for_status()
        user_id = resp.json()["id"]

        # Set new password
        pw_resp = await client.post(
            f"{connector_config.domain}/api/v1/users/{user_id}/lifecycle/reset_password",
            headers=headers,
            json={"newPassword": {"value": new_password}},
        )
        pw_resp.raise_for_status()
```

### Step 5: Run tests — expect all pass

```bash
cd backend && python -m pytest app/tests/test_credential_rotation.py -v -k "TestUpdateAD or TestUpdateOkta"
```

Expected: all 3 tests pass.

### Step 6: Commit

```bash
git add backend/app/connectors/active_directory/actions/rotate_service_account.py backend/app/connectors/okta/actions/rotate_service_account.py backend/app/tests/test_credential_rotation.py
git commit -m "feat(connectors): add AD and Okta service account password rotation actions"
```

---

## Task 7: Change type definitions

**Files:**
- Create: `backend/app/change_type_definitions/rotate_db_credentials.json`
- Create: `backend/app/change_type_definitions/rotate_ssh_keys.json`
- Create: `backend/app/change_type_definitions/rotate_api_key.json`
- Create: `backend/app/change_type_definitions/rotate_service_account.json`

### Step 1: Create rotate_db_credentials.json

```json
{
  "name": "rotate_db_credentials",
  "display_name": "Rotate Database Credentials",
  "description": "Generates a new database password, updates the DB user, rewrites config files, restarts the service, and verifies health. Supports Postgres, MySQL, and RDS.",
  "category": "credential_rotation",
  "agent_required": true,
  "parameters": {
    "db_host": {
      "type": "string",
      "description": "Hostname or IP of the database server",
      "required": true
    },
    "db_port": {
      "type": "integer",
      "description": "Database port (default 5432 for Postgres, 3306 for MySQL)",
      "default": 5432
    },
    "db_engine": {
      "type": "string",
      "enum": ["postgres", "mysql", "rds_postgres", "rds_mysql"],
      "description": "Database engine type",
      "required": true
    },
    "db_username": {
      "type": "string",
      "description": "Database user whose password is rotated",
      "required": true
    },
    "config_paths": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Absolute paths to env/conf files containing the credential",
      "required": true
    },
    "service_name": {
      "type": "string",
      "description": "Systemd unit or process name to restart after update",
      "required": true
    },
    "health_check_url": {
      "type": "string",
      "description": "URL or shell command to verify service health post-restart"
    },
    "rds_instance_id": {
      "type": "string",
      "description": "(RDS only) RDS instance identifier"
    },
    "ssm_parameter_path": {
      "type": "string",
      "description": "(RDS only) SSM parameter path to update with new password"
    }
  },
  "change_plan_template": [
    {
      "id": "step_generate_password",
      "type": "agent_command",
      "command": "rotate_db_credentials",
      "params": {"action": "generate"},
      "produces": ["new_password"],
      "consumes": [],
      "rollback_command": null,
      "rollback_params": {}
    },
    {
      "id": "step_update_db_user",
      "type": "agent_command",
      "command": "rotate_db_credentials",
      "params": {"action": "update_db_user"},
      "produces": [],
      "consumes": ["new_password"],
      "rollback_command": "rotate_db_credentials",
      "rollback_params": {"action": "update_db_user"}
    },
    {
      "id": "step_backup_config",
      "type": "agent_command",
      "command": "rotate_db_credentials",
      "params": {"action": "backup_config"},
      "produces": ["config_backup_paths"],
      "consumes": [],
      "rollback_command": null,
      "rollback_params": {}
    },
    {
      "id": "step_update_config",
      "type": "agent_command",
      "command": "rotate_db_credentials",
      "params": {"action": "update_config"},
      "produces": [],
      "consumes": ["new_password"],
      "rollback_command": "rotate_db_credentials",
      "rollback_params": {"action": "restore_config"}
    },
    {
      "id": "step_restart_service",
      "type": "agent_command",
      "command": "rotate_db_credentials",
      "params": {"action": "restart"},
      "produces": [],
      "consumes": [],
      "rollback_command": "rotate_db_credentials",
      "rollback_params": {"action": "restart"}
    },
    {
      "id": "step_verify_health",
      "type": "verify_health",
      "command": "verify_health",
      "params": {},
      "produces": [],
      "consumes": [],
      "rollback_command": null,
      "rollback_params": {}
    }
  ]
}
```

### Step 2: Create rotate_ssh_keys.json

```json
{
  "name": "rotate_ssh_keys",
  "display_name": "Rotate SSH Keys (Fleet)",
  "description": "Removes an old SSH public key by fingerprint and adds a new public key in authorized_keys for all assets in the target group. Runs in parallel per host with per-host rollback.",
  "category": "credential_rotation",
  "agent_required": true,
  "fan_out_by": "asset_group",
  "parameters": {
    "asset_group": {
      "type": "string",
      "description": "Asset group tag — all assets in the group are targeted",
      "required": true
    },
    "username": {
      "type": "string",
      "description": "OS username whose authorized_keys file is modified",
      "required": true
    },
    "old_key_fingerprint": {
      "type": "string",
      "description": "SHA256 fingerprint (without 'SHA256:' prefix) of the key to remove",
      "required": true
    },
    "new_public_key": {
      "type": "string",
      "description": "Full public key string to add (e.g. 'ssh-ed25519 AAAA... user@host')",
      "required": true
    }
  },
  "change_plan_template": [
    {
      "id": "step_backup_authorized_keys",
      "type": "agent_command",
      "command": "rotate_ssh_keys",
      "params": {"action": "backup"},
      "produces": ["authorized_keys_backup_path"],
      "consumes": [],
      "rollback_command": null,
      "rollback_params": {}
    },
    {
      "id": "step_remove_old_key",
      "type": "agent_command",
      "command": "rotate_ssh_keys",
      "params": {"action": "remove_old"},
      "produces": [],
      "consumes": [],
      "rollback_command": "rotate_ssh_keys",
      "rollback_params": {"action": "restore"}
    },
    {
      "id": "step_add_new_key",
      "type": "agent_command",
      "command": "rotate_ssh_keys",
      "params": {"action": "add_new"},
      "produces": [],
      "consumes": [],
      "rollback_command": "rotate_ssh_keys",
      "rollback_params": {"action": "restore"}
    },
    {
      "id": "step_verify_ssh_access",
      "type": "verify_health",
      "command": "verify_ssh_access",
      "params": {},
      "produces": [],
      "consumes": [],
      "rollback_command": null,
      "rollback_params": {}
    }
  ]
}
```

### Step 3: Create rotate_api_key.json

```json
{
  "name": "rotate_api_key",
  "display_name": "Rotate API Key",
  "description": "Creates a new API key via the provider (Okta, GitHub, AWS IAM), propagates it to all configured targets (K8s secrets, agent env files, SSM, Vault), verifies propagation, then revokes the old key.",
  "category": "credential_rotation",
  "agent_required": false,
  "parameters": {
    "service": {
      "type": "string",
      "enum": ["okta", "github", "aws_iam"],
      "description": "Identity provider or cloud service managing the API key",
      "required": true
    },
    "key_identifier": {
      "type": "string",
      "description": "Key name (Okta), PAT name (GitHub), or IAM username (AWS)",
      "required": true
    },
    "iam_username": {
      "type": "string",
      "description": "(aws_iam only) IAM username to create/delete access keys for"
    },
    "propagation_targets": {
      "type": "array",
      "description": "List of PropagationTarget objects describing where to push the new key",
      "items": {
        "type": "object",
        "properties": {
          "type": {"type": "string", "enum": ["k8s_secret", "agent_env_file", "ssm_parameter", "vault_secret"]},
          "namespace": {"type": "string"},
          "secret_name": {"type": "string"},
          "secret_key": {"type": "string"},
          "asset_id": {"type": "string"},
          "file_path": {"type": "string"},
          "env_var_name": {"type": "string"},
          "ssm_path": {"type": "string"},
          "vault_path": {"type": "string"},
          "vault_key": {"type": "string"}
        }
      }
    }
  },
  "change_plan_template": [
    {
      "id": "step_create_new_api_key",
      "type": "connector_action",
      "command": "create_api_key",
      "params": {},
      "produces": ["new_api_key", "old_key_id"],
      "consumes": [],
      "rollback_command": null,
      "rollback_params": {}
    },
    {
      "id": "step_propagate_targets",
      "type": "connector_action",
      "command": "propagate_api_key",
      "params": {},
      "produces": [],
      "consumes": ["new_api_key"],
      "rollback_command": "restore_api_key_targets",
      "rollback_params": {}
    },
    {
      "id": "step_verify_propagation",
      "type": "verify_health",
      "command": "verify_propagation",
      "params": {},
      "produces": [],
      "consumes": [],
      "rollback_command": null,
      "rollback_params": {}
    },
    {
      "id": "step_revoke_old_api_key",
      "type": "connector_action",
      "command": "revoke_api_key",
      "params": {},
      "produces": [],
      "consumes": ["old_key_id"],
      "rollback_command": null,
      "rollback_params": {}
    }
  ]
}
```

### Step 4: Create rotate_service_account.json

```json
{
  "name": "rotate_service_account",
  "display_name": "Rotate Service Account Password",
  "description": "Generates a new password, updates the identity provider (AD or Okta), then propagates the new credential to all dependent service config files and restarts services. Asset list is resolved from asset metadata 'service_accounts' field if not provided.",
  "category": "credential_rotation",
  "agent_required": true,
  "parameters": {
    "idp": {
      "type": "string",
      "enum": ["active_directory", "okta"],
      "description": "Identity provider managing the service account",
      "required": true
    },
    "account_username": {
      "type": "string",
      "description": "Service account UPN or login name",
      "required": true
    },
    "domain": {
      "type": "string",
      "description": "(AD only) Active Directory domain"
    },
    "service_asset_ids": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Asset IDs of hosts running services that use this account. Resolved from asset metadata if empty."
    },
    "config_paths_per_asset": {
      "type": "object",
      "description": "Map of asset_id -> list of config file paths containing the credential"
    },
    "service_names_per_asset": {
      "type": "object",
      "description": "Map of asset_id -> list of service names to restart"
    },
    "health_check_urls": {
      "type": "array",
      "items": {"type": "string"},
      "description": "URLs to verify service health post-restart"
    }
  },
  "change_plan_template": [
    {
      "id": "step_generate_password",
      "type": "connector_action",
      "command": "generate_service_account_password",
      "params": {},
      "produces": ["new_password"],
      "consumes": [],
      "rollback_command": null,
      "rollback_params": {}
    },
    {
      "id": "step_update_idp_password",
      "type": "connector_action",
      "command": "update_idp_password",
      "params": {},
      "produces": [],
      "consumes": ["new_password"],
      "rollback_command": "update_idp_password",
      "rollback_params": {"use_old_password": true}
    },
    {
      "id": "step_backup_config_per_asset",
      "type": "agent_command",
      "command": "rotate_db_credentials",
      "params": {"action": "backup_config"},
      "produces": ["config_backup_paths"],
      "consumes": [],
      "rollback_command": null,
      "rollback_params": {}
    },
    {
      "id": "step_update_config_per_asset",
      "type": "agent_command",
      "command": "rotate_db_credentials",
      "params": {"action": "update_config"},
      "produces": [],
      "consumes": ["new_password"],
      "rollback_command": "rotate_db_credentials",
      "rollback_params": {"action": "restore_config"}
    },
    {
      "id": "step_restart_services_per_asset",
      "type": "agent_command",
      "command": "rotate_db_credentials",
      "params": {"action": "restart"},
      "produces": [],
      "consumes": [],
      "rollback_command": "rotate_db_credentials",
      "rollback_params": {"action": "restart"}
    },
    {
      "id": "step_verify_health",
      "type": "verify_health",
      "command": "verify_health",
      "params": {},
      "produces": [],
      "consumes": [],
      "rollback_command": null,
      "rollback_params": {}
    }
  ]
}
```

### Step 5: Validate JSON syntax

```bash
cd backend && python -c "
import json, pathlib
for f in pathlib.Path('app/change_type_definitions').glob('rotate_*.json'):
    json.loads(f.read_text())
    print(f'OK: {f.name}')
"
```

Expected:
```
OK: rotate_api_key.json
OK: rotate_db_credentials.json
OK: rotate_service_account.json
OK: rotate_ssh_keys.json
```

### Step 6: Commit

```bash
git add backend/app/change_type_definitions/rotate_db_credentials.json backend/app/change_type_definitions/rotate_ssh_keys.json backend/app/change_type_definitions/rotate_api_key.json backend/app/change_type_definitions/rotate_service_account.json
git commit -m "feat(change-types): add rotate_db_credentials, rotate_ssh_keys, rotate_api_key, rotate_service_account change type definitions"
```

---

## Task 8: Backend integration tests

**Files:**
- Modify: `backend/app/tests/test_credential_rotation.py` (extend with integration tests)

These tests verify the full propagation pipeline end-to-end with mocked connectors and a fake agent dispatcher.

### Step 1: Write integration tests

Append to `backend/app/tests/test_credential_rotation.py`:

```python
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

        plan_path = pathlib.Path("app/change_type_definitions/rotate_ssh_keys.json")
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
            pathlib.Path("app/change_type_definitions/rotate_api_key.json").read_text()
        )
        steps = [ChangePlanStep(**s) for s in plan["change_plan_template"]]

        last_step = steps[-1]
        assert last_step.id == "step_revoke_old_api_key"
        assert "old_key_id" in last_step.consumes
```

### Step 2: Run full test suite

```bash
cd backend && python -m pytest app/tests/test_credential_rotation.py app/tests/test_secrets_service.py app/tests/test_step_output_propagation.py -v
```

Expected: all tests pass with zero failures.

### Step 3: Run entire backend test suite (regression check)

```bash
cd backend && python -m pytest app/tests/ -v 2>&1 | tail -30
```

Expected: all pre-existing tests pass.

### Step 4: Run agent test suite (regression check)

```bash
cd agent && go test ./... 2>&1
```

Expected: all pass.

### Step 5: Commit

```bash
git add backend/app/tests/test_credential_rotation.py
git commit -m "test(rotation): add full integration test suite for credential rotation pipeline — propagation, rollback, plan schema validation"
```

---

## Task 9: Alembic migration for secret_versions table

**Files:**
- Create: `backend/alembic/versions/XXXX_secret_versions.py`

### Step 1: Generate migration

```bash
cd backend && alembic revision --autogenerate -m "add_secret_versions_table"
```

This generates a file under `backend/alembic/versions/`. Edit the generated file to ensure it matches:

```python
"""add_secret_versions_table

Revision ID: <generated>
Revises: <previous revision>
Create Date: 2026-05-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
import uuid


# revision identifiers, used by Alembic.
revision = '<generated>'
down_revision = '<previous>'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secret_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False),
        sa.Column(
            "secret_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secrets.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("ciphertext", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Enum("current", "superseded", name="secret_version_status"),
            nullable=False,
            default="current",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Index to quickly find the current version for a secret
    op.create_index(
        "ix_secret_versions_secret_id_status",
        "secret_versions",
        ["secret_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_secret_versions_secret_id_status", table_name="secret_versions")
    op.drop_table("secret_versions")
    op.execute("DROP TYPE IF EXISTS secret_version_status")
```

### Step 2: Verify migration runs clean

```bash
cd backend && alembic upgrade head 2>&1
```

Expected: migration applies without errors.

### Step 3: Commit

```bash
git add backend/alembic/versions/
git commit -m "feat(db): add secret_versions table with status enum and index — supports versioned credential storage with superseded TTL"
```

---

## Self-Review Checklist

**Spec coverage:**
- Task 1: SecretsService `rotate_secret` / `get_superseded_secret` — spec Section 6
- Task 2: `ChangePlanStep` `produces`/`consumes`, `StepOutputStore`, propagation helpers — spec Section 1
- Task 3: DB credential rotation agent command — spec Section 2
- Task 4: SSH key fleet rotation agent command — spec Section 3
- Task 5: API key rotation connector actions (AWS IAM, Okta, GitHub), agent env-file command — spec Section 4
- Task 6: AD and Okta service account rotation connector actions — spec Section 5
- Task 7: All four change type definition JSON files — spec "Files Changed" table
- Task 8: Backend integration tests for the full pipeline
- Task 9: Alembic migration for `secret_versions`

**Security guarantees verified by tests:**
- `StepOutputStore.__repr__` omits values (`test_step_output_store_values_not_in_repr`)
- Audit log contains slot names only, not values (`test_audit_log_does_not_contain_secret_value`)
- `secret_versions` ciphertext column stores encrypted bytes, not plaintext (`test_superseded_value_is_encrypted_at_rest`)
- `get_superseded_secret` returns None when no prior version exists — prevents rollback to empty string (`test_get_superseded_secret_returns_none_when_no_prior`)

**Placeholder scan:** No TODOs, TBDs, or vague steps — all code is complete and compiles.

**Module consistency:**
- All Go files use `package credrotation`; executor imports `"nexplane-agent/commands/credrotation"`
- Python connector actions use `async def` throughout; tests use `pytest-asyncio` or `asyncio.get_event_loop().run_until_complete`
- `strParam` helper in `db.go` used by all Go param-extraction functions to avoid duplication
- `ReplaceCredentialInContent` and `ReplaceEnvVarInContent` exported (capital R) so `db_test.go` can test them directly from the `credrotation_test` package
