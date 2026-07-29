# Linux Parallel Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `linux_parallel_upgrade` CR type that migrates a Linux host to a new OS version by syncing data to a pre-provisioned dest host, cutting over traffic, and scheduling decommission of the old host.

**Architecture:** A 6-phase executor (preflight → snapshot → sync → health check → cutover → hold/decommission) paired with three new Go agent commands (rsync_push, add_authorized_key, remove_authorized_key, run_command). The executor holds all phase logic; rollback is implemented at the module level and uses execution_result checkpoints to determine how far to unwind.

**Tech Stack:** Python (asyncio, boto3, socket, subprocess), Go 1.22 (exec, os/exec), APScheduler DateTrigger, AWS EC2 boto3 waiters.

## Global Constraints

- All agent jobs dispatched via `dispatch_agent_job()` from `_dispatch.py` — no direct SSH from executor
- `dest` OS version must be strictly greater than `source` OS version; preflight fails otherwise
- Source instance must be stopped BEFORE traffic swap in Phase 5
- `decommission_after_hours=0` → no job created; record `decommission_manual=True`
- Snapshot helpers extracted from `os_upgrade.py` must not change `os_upgrade.py` behavior (all existing tests must pass)
- `ROLLBACK_CAPABILITY = "full"` until decommission fires; `"irreversible"` after
- On-prem hosts: snapshot skipped with warning (`snapshot_skipped=True`), not a failure; rollback still works via source restart
- All new Python files: `# SPDX-License-Identifier: AGPL-3.0-only` + `# Copyright (C) 2024-2026 Nexplane, Inc.` header
- Catalog JSON lives in `backend/app/connectors/change_type_definitions/`
- Go commands live in `agent/commands/<package>/`; registered in `agent/executor/executor.go`

---

### Task 1: Extract Snapshot Helpers

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/_snapshot_helpers.py`
- Modify: `backend/app/connectors/executors/nexplane_agent/os_upgrade.py`

**Interfaces:**
- Produces: `_take_snapshot(asset_id, instance_id, connector, organization_id=None) -> dict`, `_restore_snapshot(asset_id, snapshot_id, snapshot_meta, connector, organization_id=None) -> dict`, `_get_aws_creds(connector, organization_id=None) -> dict`, `_make_ec2_client(creds) -> boto3.client`
- Consumes: nothing from other tasks

- [ ] **Step 1: Write failing test — verify os_upgrade still imports correctly after refactor**

Create `backend/app/tests/test_snapshot_helpers_import.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def test_snapshot_helpers_importable():
    from app.connectors.executors.nexplane_agent._snapshot_helpers import (
        _take_snapshot,
        _restore_snapshot,
        _get_aws_creds,
        _make_ec2_client,
    )
    assert callable(_take_snapshot)
    assert callable(_restore_snapshot)
    assert callable(_get_aws_creds)
    assert callable(_make_ec2_client)


def test_os_upgrade_still_imports():
    import app.connectors.executors.nexplane_agent.os_upgrade as m
    assert hasattr(m, "_take_snapshot")
    assert hasattr(m, "_restore_snapshot")
```

- [ ] **Step 2: Run to confirm failure**

```
cd f:/Nexplane/nexplane
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_snapshot_helpers_import.py -v
```

Expected: `ModuleNotFoundError: No module named '..._snapshot_helpers'`

- [ ] **Step 3: Create `_snapshot_helpers.py`**

Copy `_get_aws_creds`, `_make_ec2_client`, `_take_snapshot`, and `_restore_snapshot` verbatim from `os_upgrade.py` into the new file. Add SPDX header. Ensure all imports that those functions need are present at the top of the new file.

`backend/app/connectors/executors/nexplane_agent/_snapshot_helpers.py`:
```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def _get_aws_creds(connector, organization_id=None) -> dict:
    # [paste verbatim from os_upgrade.py]


def _make_ec2_client(creds: dict):
    # [paste verbatim from os_upgrade.py]


async def _take_snapshot(asset_id: str, instance_id: str, connector, organization_id=None) -> dict:
    # [paste verbatim from os_upgrade.py]


async def _restore_snapshot(
    asset_id: str,
    snapshot_id: str,
    snapshot_meta: dict,
    connector,
    organization_id=None,
) -> dict:
    # [paste verbatim from os_upgrade.py]
```

- [ ] **Step 4: Update `os_upgrade.py` to import from `_snapshot_helpers`**

At the top of `os_upgrade.py`, replace the inline definitions of those four functions with:

```python
from app.connectors.executors.nexplane_agent._snapshot_helpers import (
    _get_aws_creds,
    _make_ec2_client,
    _take_snapshot,
    _restore_snapshot,
)
```

Remove the four inline function bodies from `os_upgrade.py`. Do not change anything else.

- [ ] **Step 5: Run import test**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_snapshot_helpers_import.py -v
```

Expected: 2 PASS

- [ ] **Step 6: Run os_upgrade existing tests to confirm no regression**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/ -k "os_upgrade" -v
```

Expected: all pass (or same pass/fail ratio as before the refactor)

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/_snapshot_helpers.py \
        backend/app/connectors/executors/nexplane_agent/os_upgrade.py \
        backend/app/tests/test_snapshot_helpers_import.py
git commit -m "refactor: extract snapshot helpers from os_upgrade into _snapshot_helpers.py"
```

---

### Task 2: Catalog Definition + Executor Skeleton

**Files:**
- Create: `backend/app/connectors/change_type_definitions/linux_parallel_upgrade.json`
- Create: `backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py`

**Interfaces:**
- Produces: `DEFINITION` dict, `async execute(parameters, asset_ids, connector) -> dict`, `async rollback(parameters, execution_result, asset_ids, connector) -> dict`
- Consumes: nothing from other tasks yet

- [ ] **Step 1: Write failing test — verify skeleton structure**

Create `backend/app/tests/test_linux_parallel_upgrade_skeleton.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu


def test_definition_has_required_keys():
    assert lpu.DEFINITION["name"] == "linux_parallel_upgrade"
    assert lpu.DEFINITION["rollback_supported"] is True


def test_execute_is_callable():
    assert callable(lpu.execute)


def test_rollback_is_callable():
    assert callable(lpu.rollback)
```

- [ ] **Step 2: Run to confirm failure**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_linux_parallel_upgrade_skeleton.py -v
```

Expected: ImportError

- [ ] **Step 3: Create `linux_parallel_upgrade.json`**

`backend/app/connectors/change_type_definitions/linux_parallel_upgrade.json`:
```json
{
  "change_type": "linux_parallel_upgrade",
  "display_name": "Linux Parallel Upgrade",
  "description": "Migrate a Linux host to a new OS version by syncing data to a pre-provisioned dest, cutting over traffic, and scheduling decommission of the old host.",
  "risk_level_default": "high",
  "incident_response": false,
  "parameters": {
    "source_asset_id": {
      "type": "string",
      "required": true,
      "label": "Source Asset ID",
      "description": "Asset ID of the old host currently serving traffic"
    },
    "dest_asset_id": {
      "type": "string",
      "required": true,
      "label": "Dest Asset ID",
      "description": "Asset ID of the new host (agent installed, app configured)"
    },
    "sync_paths": {
      "type": "array",
      "required": true,
      "label": "Sync Paths",
      "description": "Paths to rsync from source to dest, e.g. [\"/var/lib/app\", \"/etc/app\"]"
    },
    "sync_exclude": {
      "type": "array",
      "required": false,
      "label": "Sync Excludes",
      "description": "rsync exclude patterns, e.g. [\"*.log\", \"*.tmp\"]",
      "default": []
    },
    "pre_sync_runs": {
      "type": "integer",
      "required": false,
      "label": "Pre-Sync Runs",
      "description": "Run rsync N times before cutover to drain delta (default 1)",
      "default": 1
    },
    "health_check_command": {
      "type": "string",
      "required": false,
      "label": "Health Check Command",
      "description": "Shell command run on dest to verify app health, e.g. \"curl -sf http://localhost/health\""
    },
    "health_check_ports": {
      "type": "array",
      "required": false,
      "label": "Health Check Ports",
      "description": "TCP ports that must be listening on dest before cutover",
      "default": []
    },
    "cutover_method": {
      "type": "string",
      "required": true,
      "label": "Cutover Method",
      "description": "Traffic swap method: eip | alb | dns | static_ip"
    },
    "cutover_config": {
      "type": "object",
      "required": true,
      "label": "Cutover Config",
      "description": "Method-specific config (see docs for schema per method)"
    },
    "decommission_after_hours": {
      "type": "integer",
      "required": false,
      "label": "Decommission After Hours",
      "description": "Hours to wait before terminating source (0 = manual only, default 24)",
      "default": 24
    },
    "dry_run": {
      "type": "boolean",
      "required": false,
      "label": "Dry Run",
      "description": "If true, run preflight + sync report only, no cutover",
      "default": false
    }
  },
  "rollback": {
    "strategy": "traffic_reverse",
    "description": "Reverse traffic cutover, restart source, cancel decommission job if pending"
  }
}
```

- [ ] **Step 4: Create `linux_parallel_upgrade.py` skeleton**

`backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py`:
```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging

logger = logging.getLogger(__name__)

DEFINITION = {
    "name": "linux_parallel_upgrade",
    "display_name": "Linux Parallel Upgrade",
    "rollback_supported": True,
    "rollback_capability": "full",
}

ROLLBACK_CAPABILITY_FULL = "full"
ROLLBACK_CAPABILITY_IRREVERSIBLE = "irreversible"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Phase 1-6: preflight → snapshot → sync → health check → cutover → decommission scheduling."""
    raise NotImplementedError("linux_parallel_upgrade execute — not yet implemented")


async def rollback(parameters: dict, execution_result: dict, asset_ids: list, connector) -> dict:
    """Reverse cutover, restart source, cancel decommission job."""
    raise NotImplementedError("linux_parallel_upgrade rollback — not yet implemented")
```

- [ ] **Step 5: Run skeleton test**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_linux_parallel_upgrade_skeleton.py -v
```

Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/change_type_definitions/linux_parallel_upgrade.json \
        backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py \
        backend/app/tests/test_linux_parallel_upgrade_skeleton.py
git commit -m "feat: add linux_parallel_upgrade catalog definition and executor skeleton"
```

---

### Task 3: Go Agent Commands

**Files:**
- Create: `agent/commands/rsyncpush/rsync_push.go`
- Create: `agent/commands/authorizedkeys/authorized_keys.go`
- Create: `agent/commands/runcommand/run_command.go`
- Modify: `agent/executor/executor.go`

**Interfaces:**
- Produces:
  - `rsync_push` command: `Execute(params) -> {"bytes_transferred": int, "files_transferred": int, "duration_seconds": float, "output": string}`
  - `add_authorized_key` command: `AddAuthorizedKey(params) -> {"added": bool, "user": string}`
  - `remove_authorized_key` command: `RemoveAuthorizedKey(params) -> {"removed": bool, "user": string}`
  - `run_command` command: `RunCommand(params) -> {"output": string, "exit_code": int}`
- Consumes: nothing from other tasks

- [ ] **Step 1: Create `agent/commands/runcommand/run_command.go`**

```go
package runcommand

import (
	"context"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func RunCommand(params map[string]any) (map[string]any, error) {
	command, _ := params["command"].(string)
	if command == "" {
		return nil, fmt.Errorf("run_command: command is required")
	}
	timeoutSecs, _ := params["timeout"].(float64)
	if timeoutSecs <= 0 {
		timeoutSecs = 60
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutSecs)*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, "sh", "-c", command)
	out, err := cmd.CombinedOutput()
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			return nil, fmt.Errorf("run_command: exec failed: %w", err)
		}
	}
	return map[string]any{
		"output":    strings.TrimRight(string(out), "\n"),
		"exit_code": exitCode,
	}, nil
}
```

- [ ] **Step 2: Create `agent/commands/authorizedkeys/authorized_keys.go`**

```go
package authorizedkeys

import (
	"fmt"
	"os"
	"strings"
)

func _homeDir(user string) (string, error) {
	data, err := os.ReadFile("/etc/passwd")
	if err != nil {
		return "", fmt.Errorf("cannot read /etc/passwd: %w", err)
	}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Split(line, ":")
		if len(fields) >= 7 && fields[0] == user {
			return fields[5], nil
		}
	}
	return "", fmt.Errorf("user %q not found in /etc/passwd", user)
}

func AddAuthorizedKey(params map[string]any) (map[string]any, error) {
	pubKey, _ := params["public_key"].(string)
	user, _ := params["user"].(string)
	if user == "" {
		user = "root"
	}
	if strings.TrimSpace(pubKey) == "" {
		return nil, fmt.Errorf("add_authorized_key: public_key is required")
	}
	homeDir, err := _homeDir(user)
	if err != nil {
		return nil, err
	}
	sshDir := homeDir + "/.ssh"
	authFile := sshDir + "/authorized_keys"

	if err := os.MkdirAll(sshDir, 0700); err != nil {
		return nil, fmt.Errorf("add_authorized_key: mkdir .ssh: %w", err)
	}
	existing, _ := os.ReadFile(authFile)
	keyLine := strings.TrimSpace(pubKey)
	if strings.Contains(string(existing), keyLine) {
		return map[string]any{"added": false, "user": user, "reason": "already present"}, nil
	}
	f, err := os.OpenFile(authFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		return nil, fmt.Errorf("add_authorized_key: open authorized_keys: %w", err)
	}
	defer f.Close()
	if _, err := fmt.Fprintln(f, keyLine); err != nil {
		return nil, fmt.Errorf("add_authorized_key: write: %w", err)
	}
	return map[string]any{"added": true, "user": user}, nil
}

func RemoveAuthorizedKey(params map[string]any) (map[string]any, error) {
	pubKey, _ := params["public_key"].(string)
	user, _ := params["user"].(string)
	if user == "" {
		user = "root"
	}
	if strings.TrimSpace(pubKey) == "" {
		return nil, fmt.Errorf("remove_authorized_key: public_key is required")
	}
	homeDir, err := _homeDir(user)
	if err != nil {
		return nil, err
	}
	authFile := homeDir + "/.ssh/authorized_keys"
	existing, err := os.ReadFile(authFile)
	if err != nil {
		return map[string]any{"removed": false, "user": user, "reason": "file not found"}, nil
	}
	keyLine := strings.TrimSpace(pubKey)
	var kept []string
	removed := false
	for _, line := range strings.Split(string(existing), "\n") {
		if strings.TrimSpace(line) == keyLine {
			removed = true
			continue
		}
		kept = append(kept, line)
	}
	if err := os.WriteFile(authFile, []byte(strings.Join(kept, "\n")), 0600); err != nil {
		return nil, fmt.Errorf("remove_authorized_key: write: %w", err)
	}
	return map[string]any{"removed": removed, "user": user}, nil
}
```

- [ ] **Step 3: Create `agent/commands/rsyncpush/rsync_push.go`**

```go
package rsyncpush

import (
	"encoding/base64"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func Execute(params map[string]any) (map[string]any, error) {
	destHost, _ := params["dest_host"].(string)
	destUser, _ := params["dest_user"].(string)
	if destUser == "" {
		destUser = "root"
	}
	destSSHKeyB64, _ := params["dest_ssh_key"].(string)
	pathsRaw, _ := params["paths"].([]any)
	excludesRaw, _ := params["excludes"].([]any)
	deleteFlag, _ := params["delete"].(bool)

	if destHost == "" || destSSHKeyB64 == "" || len(pathsRaw) == 0 {
		return nil, fmt.Errorf("rsync_push: dest_host, dest_ssh_key, and paths are required")
	}
	keyBytes, err := base64.StdEncoding.DecodeString(destSSHKeyB64)
	if err != nil {
		return nil, fmt.Errorf("rsync_push: failed to decode SSH key: %w", err)
	}
	tmpDir, err := os.MkdirTemp("", "nexplane-rsync-*")
	if err != nil {
		return nil, fmt.Errorf("rsync_push: failed to create temp dir: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	keyPath := filepath.Join(tmpDir, "rsync_key")
	if err := os.WriteFile(keyPath, keyBytes, 0600); err != nil {
		return nil, fmt.Errorf("rsync_push: failed to write key: %w", err)
	}
	args := []string{
		"-az", "--checksum", "--stats",
		"-e", fmt.Sprintf("ssh -i %s -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null", keyPath),
	}
	if deleteFlag {
		args = append(args, "--delete")
	}
	for _, ex := range excludesRaw {
		if s, ok := ex.(string); ok {
			args = append(args, "--exclude="+s)
		}
	}
	for _, p := range pathsRaw {
		if s, ok := p.(string); ok {
			args = append(args, s)
		}
	}
	args = append(args, fmt.Sprintf("%s@%s:", destUser, destHost))

	start := time.Now()
	out, err := exec.Command("rsync", args...).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("rsync_push failed: %w\noutput: %s", err, string(out))
	}

	var bytesTransferred, filesTransferred int64
	for _, line := range strings.Split(string(out), "\n") {
		if strings.HasPrefix(line, "Number of regular files transferred:") {
			parts := strings.Fields(line)
			if len(parts) > 0 {
				n, _ := strconv.ParseInt(strings.ReplaceAll(parts[len(parts)-1], ",", ""), 10, 64)
				filesTransferred = n
			}
		}
		if strings.HasPrefix(line, "Total transferred file size:") {
			parts := strings.Fields(line)
			if len(parts) >= 5 {
				n, _ := strconv.ParseInt(strings.ReplaceAll(parts[4], ",", ""), 10, 64)
				bytesTransferred = n
			}
		}
	}
	return map[string]any{
		"bytes_transferred": bytesTransferred,
		"files_transferred": filesTransferred,
		"duration_seconds":  time.Since(start).Seconds(),
		"output":            string(out),
	}, nil
}
```

- [ ] **Step 4: Register commands in `executor.go`**

Add imports for the three new packages at the top of the imports block in `executor.go`:
```go
"github.com/nexplane/nexplane/agent/commands/authorizedkeys"
"github.com/nexplane/nexplane/agent/commands/rsyncpush"
"github.com/nexplane/nexplane/agent/commands/runcommand"
```

Add to the `commands` map:
```go
"rsync_push":            rsyncpush.Execute,
"add_authorized_key":    authorizedkeys.AddAuthorizedKey,
"remove_authorized_key": authorizedkeys.RemoveAuthorizedKey,
"run_command":           runcommand.RunCommand,
```

- [ ] **Step 5: Build the agent**

```bash
cd f:/Nexplane/nexplane
# SSH to EC2 and build from there since containers use the EC2 filesystem
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd ~/nexplane && go build ./agent/..."
```

Expected: no compilation errors

- [ ] **Step 6: Commit**

```bash
git add agent/commands/rsyncpush/rsync_push.go \
        agent/commands/authorizedkeys/authorized_keys.go \
        agent/commands/runcommand/run_command.go \
        agent/executor/executor.go
git commit -m "feat: add rsync_push, add/remove_authorized_key, run_command agent commands"
```

---

### Task 4: Phases 1-3 — Preflight, Snapshot, Sync

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py`

**Interfaces:**
- Consumes: `_take_snapshot` from `_snapshot_helpers`, `dispatch_agent_job` from `_dispatch`
- Produces: `execute()` that completes through Phase 3 (raising after if Phases 4-6 not implemented)

- [ ] **Step 1: Write failing tests for Phase 1-3**

Create `backend/app/tests/test_linux_parallel_upgrade_phases_1_3.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_params(**overrides):
    base = {
        "source_asset_id": str(uuid.uuid4()),
        "dest_asset_id": str(uuid.uuid4()),
        "sync_paths": ["/var/lib/testapp"],
        "sync_exclude": [],
        "pre_sync_runs": 1,
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "decommission_after_hours": 24,
        "dry_run": False,
    }
    base.update(overrides)
    return base


def _make_connector(is_ec2=True):
    c = MagicMock()
    c.credentials = {"access_key_id": "AKIATEST", "secret_access_key": "secret", "region": "us-east-1"} if is_ec2 else {}
    return c


@pytest.mark.asyncio
async def test_preflight_fails_if_dest_os_older(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    async def _mock_dispatch(command, parameters, asset_ids, timeout_seconds=30):
        asset_id = asset_ids[0]
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command" and "VERSION_ID" in parameters.get("command", ""):
            # source returns 20.04, dest also returns 20.04 — same version, should fail
            return {"output": "20.04", "exit_code": 0}
        if command == "run_command" and "du -sh" in parameters.get("command", ""):
            return {"output": "100M\t/var/lib/testapp", "exit_code": 0}
        if command == "run_command" and "df" in parameters.get("command", ""):
            return {"output": "50G available", "exit_code": 0}
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch)

    params = _make_params()
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result["error"] is not None
    assert "os version" in result["error"].lower() or "version" in result["error"].lower()


@pytest.mark.asyncio
async def test_preflight_fails_if_agent_unreachable(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    async def _mock_dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            raise RuntimeError("agent timeout")
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch)

    params = _make_params()
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result["error"] is not None
    assert "unreachable" in result["error"].lower() or "timeout" in result["error"].lower()


@pytest.mark.asyncio
async def test_dry_run_stops_after_preflight(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    dispatch_calls = []

    async def _mock_dispatch(command, parameters, asset_ids, timeout_seconds=30):
        dispatch_calls.append(command)
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            cmd = parameters.get("command", "")
            if "VERSION_ID" in cmd:
                # source gets 20.04, dest gets 22.04
                if asset_ids[0] == params["source_asset_id"]:
                    return {"output": "20.04", "exit_code": 0}
                return {"output": "22.04", "exit_code": 0}
            if "du -sh" in cmd:
                return {"output": "100M\t/var/lib/testapp", "exit_code": 0}
            if "df" in cmd:
                return {"output": "50G available", "exit_code": 0}
            if "test -d" in cmd or "test -e" in cmd:
                return {"output": "", "exit_code": 0}
        return {}

    params = _make_params(dry_run=True)
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch)
    monkeypatch.setattr(lpu, "_take_snapshot", AsyncMock(return_value={"snapshot_id": "snap-abc"}))
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result.get("dry_run") is True
    assert result.get("error") is None
    # Snapshot must NOT have been called
    assert not any(c == "_take_snapshot_internal" for c in dispatch_calls)
    assert "preflight" in result
```

- [ ] **Step 2: Run to confirm failure**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_linux_parallel_upgrade_phases_1_3.py -v
```

Expected: all FAIL (NotImplementedError)

- [ ] **Step 3: Implement Phases 1-3 in `linux_parallel_upgrade.py`**

Replace the stub with a full implementation. The complete file:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import base64
import logging
import os
import re
import socket
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
from app.connectors.executors.nexplane_agent._snapshot_helpers import (
    _get_aws_creds,
    _make_ec2_client,
    _take_snapshot,
)

logger = logging.getLogger(__name__)

DEFINITION = {
    "name": "linux_parallel_upgrade",
    "display_name": "Linux Parallel Upgrade",
    "rollback_supported": True,
    "rollback_capability": "full",
}

ROLLBACK_CAPABILITY_FULL = "full"
ROLLBACK_CAPABILITY_IRREVERSIBLE = "irreversible"


async def _check_agent(asset_id: str) -> None:
    """Raise RuntimeError if agent is unreachable."""
    try:
        await dispatch_agent_job("health_check", {}, [asset_id], timeout_seconds=30)
    except Exception as exc:
        raise RuntimeError(f"Agent on asset {asset_id} unreachable: {exc}") from exc


async def _get_os_version(asset_id: str) -> tuple:
    """Return OS version as comparable tuple, e.g. (20, 4) for Ubuntu 20.04."""
    result = await dispatch_agent_job(
        "run_command",
        {"command": "grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '\"'", "timeout": 15},
        [asset_id],
        timeout_seconds=20,
    )
    version_str = result.get("output", "").strip()
    parts = re.findall(r'\d+', version_str)
    if not parts:
        raise RuntimeError(f"Could not parse OS version from asset {asset_id}: {version_str!r}")
    return tuple(int(p) for p in parts)


async def _preflight(parameters: dict, asset_ids: list, connector) -> dict:
    """Phase 1. Returns preflight report dict. Raises RuntimeError on failure."""
    source_id = parameters["source_asset_id"]
    dest_id = parameters["dest_asset_id"]
    sync_paths = parameters["sync_paths"]
    cutover_method = parameters["cutover_method"]
    cutover_config = parameters["cutover_config"]

    await _check_agent(source_id)
    await _check_agent(dest_id)

    source_ver = await _get_os_version(source_id)
    dest_ver = await _get_os_version(dest_id)
    if dest_ver <= source_ver:
        raise RuntimeError(
            f"Dest OS version {dest_ver} must be strictly greater than source OS version {source_ver}"
        )

    # Verify sync_paths exist on source
    for path in sync_paths:
        result = await dispatch_agent_job(
            "run_command",
            {"command": f"test -e {path} && echo ok || echo missing", "timeout": 10},
            [source_id],
            timeout_seconds=15,
        )
        if "missing" in result.get("output", ""):
            raise RuntimeError(f"sync_paths entry {path!r} does not exist on source")

    # Estimate disk usage on source
    paths_arg = " ".join(sync_paths)
    du_result = await dispatch_agent_job(
        "run_command",
        {"command": f"du -sh {paths_arg} 2>/dev/null | tail -1", "timeout": 30},
        [source_id],
        timeout_seconds=35,
    )

    # Verify cutover method preconditions (best-effort; hard failures from AWS calls)
    if cutover_method == "eip":
        eip_id = cutover_config.get("eip_allocation_id")
        if not eip_id:
            raise RuntimeError("cutover_config.eip_allocation_id is required for method 'eip'")
    elif cutover_method == "alb":
        if not cutover_config.get("target_group_arn"):
            raise RuntimeError("cutover_config.target_group_arn is required for method 'alb'")
    elif cutover_method == "dns":
        for field in ("hosted_zone_id", "record_name", "record_type", "ttl"):
            if not cutover_config.get(field):
                raise RuntimeError(f"cutover_config.{field} is required for method 'dns'")
    elif cutover_method == "static_ip":
        for field in ("interface", "ip", "netmask", "gateway"):
            if not cutover_config.get(field):
                raise RuntimeError(f"cutover_config.{field} is required for method 'static_ip'")
    else:
        raise RuntimeError(f"Unknown cutover_method: {cutover_method!r}")

    return {
        "source_os_version": list(source_ver),
        "dest_os_version": list(dest_ver),
        "disk_estimate": du_result.get("output", "unknown"),
    }


async def _generate_temp_keypair() -> tuple:
    """Generate temp Ed25519 keypair. Returns (public_key_line, private_key_b64)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = os.path.join(tmpdir, "rsync_key")
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", "", "-C", "nexplane-rsync-temp"],
            check=True,
            capture_output=True,
        )
        with open(key_path, "rb") as f:
            private_key_b64 = base64.b64encode(f.read()).decode()
        with open(f"{key_path}.pub", "r") as f:
            public_key = f.read().strip()
    return public_key, private_key_b64


async def _run_rsync(source_id: str, dest_id: str, parameters: dict) -> dict:
    """Install temp key on dest, run rsync_push from source, remove temp key from dest."""
    dest_id = parameters["dest_asset_id"]
    sync_paths = parameters["sync_paths"]
    sync_exclude = parameters.get("sync_exclude", [])

    pub_key, priv_key_b64 = await _generate_temp_keypair()

    # Get dest host IP
    ip_result = await dispatch_agent_job(
        "run_command",
        {"command": "hostname -I | awk '{print $1}'", "timeout": 10},
        [dest_id],
        timeout_seconds=15,
    )
    dest_ip = ip_result.get("output", "").strip().split()[0]
    if not dest_ip:
        raise RuntimeError("Could not determine dest host IP for rsync")

    try:
        await dispatch_agent_job(
            "add_authorized_key",
            {"public_key": pub_key, "user": "root"},
            [dest_id],
            timeout_seconds=15,
        )
        result = await dispatch_agent_job(
            "rsync_push",
            {
                "dest_host": dest_ip,
                "dest_user": "root",
                "dest_ssh_key": priv_key_b64,
                "paths": sync_paths,
                "excludes": sync_exclude,
                "delete": True,
            },
            [source_id],
            timeout_seconds=600,
        )
    finally:
        try:
            await dispatch_agent_job(
                "remove_authorized_key",
                {"public_key": pub_key, "user": "root"},
                [dest_id],
                timeout_seconds=15,
            )
        except Exception as exc:
            logger.warning(f"Failed to remove temp authorized key from dest: {exc}")

    return {
        "bytes_transferred": result.get("bytes_transferred", 0),
        "files_transferred": result.get("files_transferred", 0),
        "duration_seconds": result.get("duration_seconds", 0),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Phase 1-6: preflight → snapshot → sync → health check → cutover → decommission scheduling."""
    source_id = parameters["source_asset_id"]
    dest_id = parameters["dest_asset_id"]
    pre_sync_runs = parameters.get("pre_sync_runs", 1)
    dry_run = parameters.get("dry_run", False)

    execution_result = {
        "snapshot_id": None,
        "snapshot_skipped": False,
        "cutover_completed": False,
        "source_stopped": False,
        "decommission_job_id": None,
        "decommission_manual": False,
        "rollback_capability": ROLLBACK_CAPABILITY_FULL,
        "cutover_method": parameters.get("cutover_method"),
        "cutover_config": parameters.get("cutover_config"),
        "error": None,
        "preflight": None,
        "sync_runs": [],
        "dry_run": dry_run,
    }

    try:
        # Phase 1 — Preflight
        logger.info(f"[linux_parallel_upgrade] Phase 1: preflight source={source_id} dest={dest_id}")
        preflight_report = await _preflight(parameters, asset_ids, connector)
        execution_result["preflight"] = preflight_report

        if dry_run:
            logger.info("[linux_parallel_upgrade] dry_run=True — stopping after preflight")
            return execution_result

        # Phase 2 — Snapshot source
        logger.info("[linux_parallel_upgrade] Phase 2: snapshot source")
        snap_result = await _phase2_snapshot(source_id, connector, execution_result)
        execution_result.update(snap_result)

        # Phase 3 — Sync data (N-1 pre-sync runs; final run is in Phase 5)
        pre_runs = max(pre_sync_runs - 1, 0)
        for i in range(pre_runs):
            logger.info(f"[linux_parallel_upgrade] Phase 3: pre-sync run {i + 1}/{pre_runs}")
            run_stats = await _run_rsync(source_id, dest_id, parameters)
            execution_result["sync_runs"].append(run_stats)

        # Phases 4-6 not yet implemented
        raise NotImplementedError("Phases 4-6 not implemented in this task")

    except NotImplementedError:
        raise
    except Exception as exc:
        logger.exception(f"[linux_parallel_upgrade] execute failed: {exc}")
        execution_result["error"] = str(exc)
        return execution_result


async def _phase2_snapshot(source_id: str, connector, execution_result: dict) -> dict:
    """Take EBS snapshot of source (EC2) or skip with warning (on-prem)."""
    if not connector.credentials or not connector.credentials.get("access_key_id"):
        logger.warning("[linux_parallel_upgrade] Non-EC2 host: skipping snapshot")
        return {"snapshot_id": None, "snapshot_skipped": True}
    try:
        creds = await _get_aws_creds(connector)
        ec2 = _make_ec2_client(creds)
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        instances = ec2.describe_instances(
            Filters=[{"Name": "ip-address", "Values": ["*"]}]  # placeholder — see note below
        )
        # NOTE: caller must pass instance_id via parameters["_source_instance_id"] or look it up.
        # For now, use the asset lookup helper from os_upgrade pattern.
        # _take_snapshot needs (asset_id, instance_id, connector). Look up instance_id via asset metadata.
        from app.connectors.executors.nexplane_agent.os_upgrade import _lookup_instance_id_by_ip
        meta = {}  # populated by caller with asset IP info
        instance_id = await _lookup_instance_id_by_ip(meta, connector)
        snap = await _take_snapshot(source_id, instance_id, connector)
        return {"snapshot_id": snap["snapshot_id"], "snapshot_meta": snap, "snapshot_skipped": False}
    except Exception as exc:
        logger.warning(f"[linux_parallel_upgrade] Snapshot failed (non-fatal for on-prem): {exc}")
        return {"snapshot_id": None, "snapshot_skipped": True}


async def rollback(parameters: dict, execution_result: dict, asset_ids: list, connector) -> dict:
    raise NotImplementedError("rollback not yet implemented")
```

**Note on instance ID lookup**: The `_phase2_snapshot` implementation above uses a placeholder. In Task 4 tests we mock `_take_snapshot` directly so the placeholder doesn't affect test results. The full instance ID lookup (via DB asset record → instance metadata) will be wired in Task 6 when rollback and Phase 5 also need it.

- [ ] **Step 4: Run Phase 1-3 tests**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_linux_parallel_upgrade_phases_1_3.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py \
        backend/app/tests/test_linux_parallel_upgrade_phases_1_3.py
git commit -m "feat: implement linux_parallel_upgrade phases 1-3 (preflight, snapshot, sync)"
```

---

### Task 5: Phases 4-5 — Health Check + Cutover

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py`

**Interfaces:**
- Consumes: Phase 1-3 from Task 4; `dispatch_agent_job`; boto3 EC2/ELB/Route53 clients
- Produces: `execute()` fully implemented through Phase 5; `_reverse_cutover()` helper for rollback

- [ ] **Step 1: Write failing tests for Phase 4-5**

Create `backend/app/tests/test_linux_parallel_upgrade_phases_4_5.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_params(**overrides):
    base = {
        "source_asset_id": str(uuid.uuid4()),
        "dest_asset_id": str(uuid.uuid4()),
        "sync_paths": ["/var/lib/testapp"],
        "sync_exclude": [],
        "pre_sync_runs": 1,
        "health_check_ports": [8080],
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "decommission_after_hours": 0,
        "dry_run": False,
    }
    base.update(overrides)
    return base


def _make_connector():
    c = MagicMock()
    c.credentials = {"access_key_id": "AKIATEST", "secret_access_key": "secret", "region": "us-east-1"}
    return c


@pytest.mark.asyncio
async def test_health_check_port_failure_blocks_cutover(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            if "VERSION_ID" in parameters.get("command", ""):
                if asset_ids[0] == params["source_asset_id"]:
                    return {"output": "20.04", "exit_code": 0}
                return {"output": "22.04", "exit_code": 0}
            return {"output": "", "exit_code": 0}
        return {}

    params = _make_params()
    monkeypatch.setattr(lpu, "dispatch_agent_job", _dispatch)
    monkeypatch.setattr(lpu, "_take_snapshot", AsyncMock(return_value={"snapshot_id": "snap-x", "snapshot_skipped": False}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 0, "files_transferred": 0, "duration_seconds": 1.0}))
    # Simulate port probe failure: port 8080 closed
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=False))

    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result["cutover_completed"] is False
    assert result["error"] is not None
    assert "8080" in result["error"] or "port" in result["error"].lower()


@pytest.mark.asyncio
async def test_health_check_command_failure_blocks_cutover(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            if "VERSION_ID" in parameters.get("command", ""):
                if asset_ids[0] == params["source_asset_id"]:
                    return {"output": "20.04", "exit_code": 0}
                return {"output": "22.04", "exit_code": 0}
            # health_check_command fails
            return {"output": "connection refused", "exit_code": 1}
        return {}

    params = _make_params(health_check_ports=[], health_check_command="curl -sf http://localhost/health")
    monkeypatch.setattr(lpu, "dispatch_agent_job", _dispatch)
    monkeypatch.setattr(lpu, "_take_snapshot", AsyncMock(return_value={"snapshot_id": "snap-x", "snapshot_skipped": False}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 0, "files_transferred": 0, "duration_seconds": 1.0}))
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=True))

    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result["cutover_completed"] is False
    assert result["error"] is not None
    assert "health" in result["error"].lower() or "exit" in result["error"].lower()


@pytest.mark.asyncio
async def test_cutover_records_checkpoint(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            if "VERSION_ID" in parameters.get("command", ""):
                if asset_ids[0] == params["source_asset_id"]:
                    return {"output": "20.04", "exit_code": 0}
                return {"output": "22.04", "exit_code": 0}
            return {"output": "", "exit_code": 0}
        return {}

    params = _make_params()
    monkeypatch.setattr(lpu, "dispatch_agent_job", _dispatch)
    monkeypatch.setattr(lpu, "_take_snapshot", AsyncMock(return_value={"snapshot_id": "snap-y"}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 100, "files_transferred": 1, "duration_seconds": 0.5}))
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=True))
    monkeypatch.setattr(lpu, "_stop_source", AsyncMock())
    monkeypatch.setattr(lpu, "_cutover_eip", AsyncMock())

    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert result["cutover_completed"] is True
    assert result["source_stopped"] is True
    assert result["snapshot_id"] == "snap-y"
    assert result["cutover_method"] == "eip"
    assert result["cutover_config"] == {"eip_allocation_id": "eipalloc-abc123"}
    assert result.get("error") is None
```

- [ ] **Step 2: Run to confirm failure**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_linux_parallel_upgrade_phases_4_5.py -v
```

Expected: all FAIL

- [ ] **Step 3: Implement Phase 4-5 helpers in `linux_parallel_upgrade.py`**

Add the following functions to the module (after the Phase 3 helpers):

```python
def _probe_tcp_port(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError, TimeoutError):
        return False


async def _verify_dest_health(parameters: dict, dest_ip: str) -> None:
    """Phase 4. Raises RuntimeError on any health check failure."""
    dest_id = parameters["dest_asset_id"]
    ports = parameters.get("health_check_ports") or []
    health_cmd = parameters.get("health_check_command")

    for port in ports:
        ok = False
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            if _probe_tcp_port(dest_ip, port):
                ok = True
                break
            await asyncio.sleep(3)
        if not ok:
            raise RuntimeError(f"Health check failed: port {port} unreachable on dest after 30s")

    if health_cmd:
        result = await dispatch_agent_job(
            "run_command",
            {"command": health_cmd, "timeout": 60},
            [dest_id],
            timeout_seconds=70,
        )
        if result.get("exit_code", 1) != 0:
            raise RuntimeError(
                f"Health check command exited {result.get('exit_code')}: {result.get('output', '')}"
            )

    await _check_agent(dest_id)


async def _stop_source(source_id: str, connector) -> None:
    """Stop source: EC2 stop_instances, or on-prem shutdown via run_command."""
    if connector.credentials and connector.credentials.get("access_key_id"):
        creds = await _get_aws_creds(connector)
        ec2 = _make_ec2_client(creds)
        loop = asyncio.get_event_loop()
        instances = await loop.run_in_executor(
            None,
            lambda: ec2.describe_instances(
                Filters=[{"Name": "tag:nexplane-asset-id", "Values": [source_id]}]
            ),
        )
        reservations = instances.get("Reservations", [])
        if reservations:
            instance_id = reservations[0]["Instances"][0]["InstanceId"]
            await loop.run_in_executor(None, lambda: ec2.stop_instances(InstanceIds=[instance_id]))
            await loop.run_in_executor(
                None,
                lambda: ec2.get_waiter("instance_stopped").wait(
                    InstanceIds=[instance_id],
                    WaiterConfig={"Delay": 10, "MaxAttempts": 30},
                ),
            )
    else:
        await dispatch_agent_job(
            "run_command",
            {"command": "shutdown -h now", "timeout": 10},
            [source_id],
            timeout_seconds=15,
        )


async def _cutover_eip(source_id: str, dest_id: str, cutover_config: dict, connector, reverse: bool = False) -> None:
    creds = await _get_aws_creds(connector)
    ec2 = _make_ec2_client(creds)
    loop = asyncio.get_event_loop()
    eip_id = cutover_config["eip_allocation_id"]

    def _get_instance_id(asset_id: str) -> str:
        result = ec2.describe_instances(
            Filters=[{"Name": "tag:nexplane-asset-id", "Values": [asset_id]}]
        )
        return result["Reservations"][0]["Instances"][0]["InstanceId"]

    if not reverse:
        # Forward: disassociate from source → associate to dest
        addr = ec2.describe_addresses(AllocationIds=[eip_id])["Addresses"][0]
        if addr.get("AssociationId"):
            await loop.run_in_executor(
                None, lambda: ec2.disassociate_address(AssociationId=addr["AssociationId"])
            )
        dest_instance_id = await loop.run_in_executor(None, lambda: _get_instance_id(dest_id))
        await loop.run_in_executor(
            None,
            lambda: ec2.associate_address(AllocationId=eip_id, InstanceId=dest_instance_id),
        )
    else:
        # Reverse: disassociate from dest → associate to source
        addr = ec2.describe_addresses(AllocationIds=[eip_id])["Addresses"][0]
        if addr.get("AssociationId"):
            await loop.run_in_executor(
                None, lambda: ec2.disassociate_address(AssociationId=addr["AssociationId"])
            )
        source_instance_id = await loop.run_in_executor(None, lambda: _get_instance_id(source_id))
        await loop.run_in_executor(
            None,
            lambda: ec2.associate_address(AllocationId=eip_id, InstanceId=source_instance_id),
        )


async def _cutover_alb(source_id: str, dest_id: str, cutover_config: dict, connector, reverse: bool = False) -> None:
    import boto3
    creds = await _get_aws_creds(connector)
    region = creds.get("region", "us-east-1")
    elbv2 = boto3.client(
        "elbv2",
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        aws_session_token=creds.get("session_token"),
        region_name=region,
    )
    loop = asyncio.get_event_loop()
    tg_arn = cutover_config["target_group_arn"]
    ec2 = _make_ec2_client(creds)

    def _get_instance_id(asset_id: str) -> str:
        result = ec2.describe_instances(
            Filters=[{"Name": "tag:nexplane-asset-id", "Values": [asset_id]}]
        )
        return result["Reservations"][0]["Instances"][0]["InstanceId"]

    if not reverse:
        new_id = await loop.run_in_executor(None, lambda: _get_instance_id(dest_id))
        old_id = await loop.run_in_executor(None, lambda: _get_instance_id(source_id))
        await loop.run_in_executor(
            None, lambda: elbv2.register_targets(TargetGroupArn=tg_arn, Targets=[{"Id": new_id}])
        )
        waiter = elbv2.get_waiter("target_in_service")
        await loop.run_in_executor(
            None,
            lambda: waiter.wait(TargetGroupArn=tg_arn, Targets=[{"Id": new_id}],
                                WaiterConfig={"Delay": 10, "MaxAttempts": 30}),
        )
        await loop.run_in_executor(
            None, lambda: elbv2.deregister_targets(TargetGroupArn=tg_arn, Targets=[{"Id": old_id}])
        )
    else:
        new_id = await loop.run_in_executor(None, lambda: _get_instance_id(source_id))
        old_id = await loop.run_in_executor(None, lambda: _get_instance_id(dest_id))
        await loop.run_in_executor(
            None, lambda: elbv2.register_targets(TargetGroupArn=tg_arn, Targets=[{"Id": new_id}])
        )
        waiter = elbv2.get_waiter("target_in_service")
        await loop.run_in_executor(
            None,
            lambda: waiter.wait(TargetGroupArn=tg_arn, Targets=[{"Id": new_id}],
                                WaiterConfig={"Delay": 10, "MaxAttempts": 30}),
        )
        await loop.run_in_executor(
            None, lambda: elbv2.deregister_targets(TargetGroupArn=tg_arn, Targets=[{"Id": old_id}])
        )


async def _cutover_dns(source_id: str, dest_id: str, cutover_config: dict, connector, reverse: bool = False) -> None:
    import boto3
    creds = await _get_aws_creds(connector)
    r53 = boto3.client(
        "route53",
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        aws_session_token=creds.get("session_token"),
    )
    loop = asyncio.get_event_loop()
    hosted_zone_id = cutover_config["hosted_zone_id"]
    record_name = cutover_config["record_name"]
    record_type = cutover_config["record_type"]
    ttl = cutover_config.get("ttl", 300)

    async def _get_host_ip(asset_id: str) -> str:
        result = await dispatch_agent_job(
            "run_command",
            {"command": "hostname -I | awk '{print $1}'", "timeout": 10},
            [asset_id],
            timeout_seconds=15,
        )
        return result.get("output", "").strip().split()[0]

    target_id = dest_id if not reverse else source_id
    target_ip = await _get_host_ip(target_id)

    await loop.run_in_executor(
        None,
        lambda: r53.change_resource_record_sets(
            HostedZoneId=hosted_zone_id,
            ChangeBatch={
                "Changes": [{
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": record_name,
                        "Type": record_type,
                        "TTL": 60 if not reverse else ttl,
                        "ResourceRecords": [{"Value": target_ip}],
                    },
                }]
            },
        ),
    )


async def _cutover_static_ip(source_id: str, dest_id: str, cutover_config: dict, reverse: bool = False) -> None:
    interface = cutover_config["interface"]
    ip = cutover_config["ip"]
    netmask = cutover_config["netmask"]
    gateway = cutover_config["gateway"]

    add_target = dest_id if not reverse else source_id
    remove_target = source_id if not reverse else dest_id

    add_cmd = f"ip addr add {ip}/{netmask} dev {interface} && ip route add default via {gateway} || true"
    remove_cmd = f"ip addr del {ip}/{netmask} dev {interface} || true"

    await dispatch_agent_job("run_command", {"command": add_cmd, "timeout": 15}, [add_target], timeout_seconds=20)
    await dispatch_agent_job("run_command", {"command": remove_cmd, "timeout": 15}, [remove_target], timeout_seconds=20)


async def _do_cutover(source_id: str, dest_id: str, parameters: dict, connector, reverse: bool = False) -> None:
    method = parameters["cutover_method"]
    config = parameters["cutover_config"]
    if method == "eip":
        await _cutover_eip(source_id, dest_id, config, connector, reverse=reverse)
    elif method == "alb":
        await _cutover_alb(source_id, dest_id, config, connector, reverse=reverse)
    elif method == "dns":
        await _cutover_dns(source_id, dest_id, config, connector, reverse=reverse)
    elif method == "static_ip":
        await _cutover_static_ip(source_id, dest_id, config, reverse=reverse)
    else:
        raise RuntimeError(f"Unknown cutover_method: {method!r}")
```

Now update the `execute()` function to call Phase 4-5 after Phase 3 (replace the `raise NotImplementedError` at the bottom of the Phase 3 section):

```python
        # Phase 4 — Verify dest health
        logger.info("[linux_parallel_upgrade] Phase 4: verify dest health")
        dest_ip_result = await dispatch_agent_job(
            "run_command",
            {"command": "hostname -I | awk '{print $1}'", "timeout": 10},
            [dest_id],
            timeout_seconds=15,
        )
        dest_ip = dest_ip_result.get("output", "").strip().split()[0]
        await _verify_dest_health(parameters, dest_ip)

        # Phase 5 — Cutover
        logger.info("[linux_parallel_upgrade] Phase 5: cutover")
        # Final rsync run (the Nth run)
        final_sync = await _run_rsync(source_id, dest_id, parameters)
        execution_result["sync_runs"].append(final_sync)

        await _stop_source(source_id, connector)
        execution_result["source_stopped"] = True

        await _do_cutover(source_id, dest_id, parameters, connector)
        execution_result["cutover_completed"] = True
        logger.info("[linux_parallel_upgrade] Phase 5: cutover complete")

        # Phase 6 handled in Task 6 — placeholder return
        return execution_result
```

- [ ] **Step 4: Run Phase 4-5 tests**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_linux_parallel_upgrade_phases_4_5.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py \
        backend/app/tests/test_linux_parallel_upgrade_phases_4_5.py
git commit -m "feat: implement linux_parallel_upgrade phases 4-5 (health check, cutover)"
```

---

### Task 6: Phase 6 + Rollback

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py`

**Interfaces:**
- Consumes: `_do_cutover`, `_stop_source`, `_check_agent` from Tasks 4-5; APScheduler `_scheduler`
- Produces: fully implemented `execute()` and `rollback()`

- [ ] **Step 1: Write failing tests for Phase 6 + rollback**

Create `backend/app/tests/test_linux_parallel_upgrade_phase6_rollback.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_params(**overrides):
    base = {
        "source_asset_id": str(uuid.uuid4()),
        "dest_asset_id": str(uuid.uuid4()),
        "sync_paths": ["/var/lib/testapp"],
        "sync_exclude": [],
        "pre_sync_runs": 1,
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "decommission_after_hours": 24,
        "dry_run": False,
    }
    base.update(overrides)
    return base


def _make_connector():
    c = MagicMock()
    c.credentials = {"access_key_id": "AKIATEST", "secret_access_key": "secret", "region": "us-east-1"}
    return c


@pytest.mark.asyncio
async def test_decommission_job_scheduled(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    scheduled_jobs = []

    class FakeScheduler:
        def add_job(self, fn, trigger, id, **kwargs):
            scheduled_jobs.append({"id": id, "trigger": trigger})
            return MagicMock(id=id)

    monkeypatch.setattr(lpu, "_get_scheduler", lambda: FakeScheduler())
    params = _make_params(decommission_after_hours=24)
    execution_result = {
        "cutover_completed": True,
        "source_stopped": True,
        "snapshot_id": "snap-abc",
        "decommission_job_id": None,
        "decommission_manual": False,
    }
    result = await lpu._phase6_decommission(params, execution_result, _make_connector())
    assert result["decommission_job_id"] is not None
    assert result["decommission_manual"] is False
    assert len(scheduled_jobs) == 1


@pytest.mark.asyncio
async def test_manual_decommission_no_job(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    monkeypatch.setattr(lpu, "_get_scheduler", lambda: MagicMock())
    params = _make_params(decommission_after_hours=0)
    execution_result = {
        "cutover_completed": True,
        "source_stopped": True,
        "snapshot_id": None,
        "decommission_job_id": None,
        "decommission_manual": False,
    }
    result = await lpu._phase6_decommission(params, execution_result, _make_connector())
    assert result["decommission_manual"] is True
    assert result["decommission_job_id"] is None


@pytest.mark.asyncio
async def test_rollback_pre_cutover_no_traffic_change(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    reverse_calls = []
    monkeypatch.setattr(lpu, "_do_cutover", AsyncMock(side_effect=lambda *a, **kw: reverse_calls.append(1)))

    params = _make_params()
    execution_result = {
        "cutover_completed": False,
        "source_stopped": False,
        "snapshot_id": None,
        "decommission_job_id": None,
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "rollback_capability": "full",
    }
    result = await lpu.rollback(params, execution_result, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert len(reverse_calls) == 0
    assert result.get("error") is None


@pytest.mark.asyncio
async def test_rollback_post_cutover_reverses_traffic(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu

    reverse_calls = []

    async def _mock_do_cutover(src, dst, params, connector, reverse=False):
        reverse_calls.append(reverse)

    async def _mock_start_source(asset_id, connector):
        pass

    monkeypatch.setattr(lpu, "_do_cutover", _mock_do_cutover)
    monkeypatch.setattr(lpu, "_start_source", _mock_start_source)
    monkeypatch.setattr(lpu, "_check_agent", AsyncMock())

    params = _make_params()
    execution_result = {
        "cutover_completed": True,
        "source_stopped": True,
        "snapshot_id": "snap-abc",
        "decommission_job_id": None,
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "rollback_capability": "full",
    }
    result = await lpu.rollback(params, execution_result, [params["source_asset_id"], params["dest_asset_id"]], _make_connector())
    assert True in reverse_calls
    assert result.get("error") is None
```

- [ ] **Step 2: Run to confirm failure**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_linux_parallel_upgrade_phase6_rollback.py -v
```

Expected: all FAIL

- [ ] **Step 3: Implement Phase 6 and rollback in `linux_parallel_upgrade.py`**

Add these functions to the module:

```python
def _get_scheduler():
    from app.services.recurring_job_service import _scheduler
    return _scheduler


async def _phase6_decommission(parameters: dict, execution_result: dict, connector) -> dict:
    """Phase 6: schedule decommission or record manual-only."""
    source_id = parameters["source_asset_id"]
    hours = parameters.get("decommission_after_hours", 24)

    if hours == 0:
        execution_result["decommission_manual"] = True
        execution_result["rollback_capability"] = ROLLBACK_CAPABILITY_FULL
        return execution_result

    from apscheduler.triggers.date import DateTrigger
    job_id = str(uuid.uuid4())
    fire_time = datetime.now(timezone.utc) + timedelta(hours=hours)

    scheduler = _get_scheduler()
    scheduler.add_job(
        lambda: asyncio.ensure_future(_terminate_source(source_id, execution_result.get("snapshot_id"), connector)),
        DateTrigger(run_date=fire_time),
        id=f"decommission_{job_id}",
    )
    execution_result["decommission_job_id"] = job_id
    execution_result["rollback_capability"] = ROLLBACK_CAPABILITY_FULL
    logger.info(f"[linux_parallel_upgrade] decommission scheduled in {hours}h — job_id={job_id}")
    return execution_result


async def _terminate_source(source_id: str, snapshot_id: str | None, connector) -> None:
    """Terminate source instance and delete snapshot. Sets rollback_capability=irreversible."""
    logger.info(f"[linux_parallel_upgrade] decommission: terminating source {source_id}")
    if connector.credentials and connector.credentials.get("access_key_id"):
        creds = await _get_aws_creds(connector)
        ec2 = _make_ec2_client(creds)
        loop = asyncio.get_event_loop()
        instances = await loop.run_in_executor(
            None,
            lambda: ec2.describe_instances(
                Filters=[{"Name": "tag:nexplane-asset-id", "Values": [source_id]}]
            ),
        )
        reservations = instances.get("Reservations", [])
        if reservations:
            instance_id = reservations[0]["Instances"][0]["InstanceId"]
            await loop.run_in_executor(None, lambda: ec2.terminate_instances(InstanceIds=[instance_id]))
        if snapshot_id:
            try:
                await loop.run_in_executor(None, lambda: ec2.delete_snapshot(SnapshotId=snapshot_id))
            except Exception as exc:
                logger.warning(f"[linux_parallel_upgrade] Failed to delete snapshot {snapshot_id}: {exc}")
    else:
        await dispatch_agent_job("run_command", {"command": "shutdown -h now"}, [source_id], timeout_seconds=15)


async def _start_source(source_id: str, connector) -> None:
    """Restart stopped source instance (EC2 or on-prem)."""
    if connector.credentials and connector.credentials.get("access_key_id"):
        creds = await _get_aws_creds(connector)
        ec2 = _make_ec2_client(creds)
        loop = asyncio.get_event_loop()
        instances = await loop.run_in_executor(
            None,
            lambda: ec2.describe_instances(
                Filters=[{"Name": "tag:nexplane-asset-id", "Values": [source_id]}]
            ),
        )
        reservations = instances.get("Reservations", [])
        if reservations:
            instance_id = reservations[0]["Instances"][0]["InstanceId"]
            await loop.run_in_executor(None, lambda: ec2.start_instances(InstanceIds=[instance_id]))
            await loop.run_in_executor(
                None,
                lambda: ec2.get_waiter("instance_running").wait(
                    InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 30}
                ),
            )
```

Update `execute()` to call `_phase6_decommission` (replace the `return execution_result` placeholder at the end of Phase 5):

```python
        # Phase 6 — Hold / schedule decommission
        logger.info("[linux_parallel_upgrade] Phase 6: decommission scheduling")
        await _phase6_decommission(parameters, execution_result, connector)
        return execution_result
```

Implement `rollback()`:

```python
async def rollback(parameters: dict, execution_result: dict, asset_ids: list, connector) -> dict:
    """Reverse cutover, restart source, cancel decommission job."""
    source_id = parameters["source_asset_id"]
    dest_id = parameters["dest_asset_id"]
    rollback_result = {"error": None, "actions": []}

    try:
        # Cancel decommission job if pending
        job_id = execution_result.get("decommission_job_id")
        if job_id:
            try:
                scheduler = _get_scheduler()
                scheduler.remove_job(f"decommission_{job_id}")
                rollback_result["actions"].append("cancelled_decommission_job")
            except Exception as exc:
                logger.warning(f"[linux_parallel_upgrade] Could not cancel decommission job {job_id}: {exc}")

        # If cutover completed, reverse traffic
        if execution_result.get("cutover_completed"):
            await _do_cutover(source_id, dest_id, parameters, connector, reverse=True)
            rollback_result["actions"].append("reversed_cutover")

        # If source was stopped, restart it
        if execution_result.get("source_stopped"):
            await _start_source(source_id, connector)
            await _check_agent(source_id)
            rollback_result["actions"].append("restarted_source")

    except Exception as exc:
        logger.exception(f"[linux_parallel_upgrade] rollback failed: {exc}")
        rollback_result["error"] = str(exc)

    return rollback_result
```

- [ ] **Step 4: Run Phase 6 + rollback tests**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_linux_parallel_upgrade_phase6_rollback.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py \
        backend/app/tests/test_linux_parallel_upgrade_phase6_rollback.py
git commit -m "feat: implement linux_parallel_upgrade phase 6 and rollback"
```

---

### Task 7: Unit Tests

**Files:**
- Create: `backend/app/tests/test_linux_parallel_upgrade.py`

**Interfaces:**
- Consumes: fully implemented `linux_parallel_upgrade.py` from Tasks 4-6
- Produces: 11 tests from spec

- [ ] **Step 1: Write all 11 tests**

Create `backend/app/tests/test_linux_parallel_upgrade.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _params(**overrides):
    src = str(uuid.uuid4())
    dst = str(uuid.uuid4())
    base = {
        "source_asset_id": src,
        "dest_asset_id": dst,
        "sync_paths": ["/var/lib/testapp"],
        "sync_exclude": [],
        "pre_sync_runs": 1,
        "health_check_ports": [],
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "decommission_after_hours": 24,
        "dry_run": False,
    }
    base.update(overrides)
    return base


def _conn_ec2():
    c = MagicMock()
    c.credentials = {"access_key_id": "AKIATEST", "secret_access_key": "secret", "region": "us-east-1"}
    return c


def _conn_onprem():
    c = MagicMock()
    c.credentials = {}
    return c


def _mock_dispatch_versions(src_id, src_ver="20.04", dst_ver="22.04"):
    """Return an async dispatch mock that answers version queries."""
    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            cmd = parameters.get("command", "")
            if "VERSION_ID" in cmd:
                return {"output": src_ver if asset_ids[0] == src_id else dst_ver, "exit_code": 0}
            if "du -sh" in cmd:
                return {"output": "10M\t/var/lib/testapp", "exit_code": 0}
            if "test -e" in cmd:
                return {"output": "", "exit_code": 0}
            if "hostname -I" in cmd:
                return {"output": "10.0.0.5", "exit_code": 0}
            return {"output": "", "exit_code": 0}
        return {}
    return _dispatch


# 1. test_preflight_fails_if_dest_os_older
@pytest.mark.asyncio
async def test_preflight_fails_if_dest_os_older(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params()
    # Both source and dest report 20.04 — dest not strictly greater
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch_versions(params["source_asset_id"], "20.04", "20.04"))
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["error"] is not None
    assert result["cutover_completed"] is False


# 2. test_preflight_fails_if_agent_unreachable
@pytest.mark.asyncio
async def test_preflight_fails_if_agent_unreachable(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params()

    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            raise RuntimeError("connection timeout")
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _dispatch)
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["error"] is not None
    assert result["cutover_completed"] is False


# 3. test_dry_run_stops_after_preflight
@pytest.mark.asyncio
async def test_dry_run_stops_after_preflight(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params(dry_run=True)
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch_versions(params["source_asset_id"]))
    snap_mock = AsyncMock(return_value={"snapshot_id": "snap-x"})
    monkeypatch.setattr(lpu, "_take_snapshot", snap_mock)
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["dry_run"] is True
    assert result["error"] is None
    snap_mock.assert_not_called()
    assert result["cutover_completed"] is False


# 4. test_pre_sync_runs_respected
@pytest.mark.asyncio
async def test_pre_sync_runs_respected(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params(pre_sync_runs=3, decommission_after_hours=0)
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch_versions(params["source_asset_id"]))
    monkeypatch.setattr(lpu, "_take_snapshot", AsyncMock(return_value={"snapshot_id": "snap-x"}))
    rsync_mock = AsyncMock(return_value={"bytes_transferred": 0, "files_transferred": 0, "duration_seconds": 0.1})
    monkeypatch.setattr(lpu, "_run_rsync", rsync_mock)
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=True))
    monkeypatch.setattr(lpu, "_stop_source", AsyncMock())
    monkeypatch.setattr(lpu, "_cutover_eip", AsyncMock())
    monkeypatch.setattr(lpu, "_get_scheduler", lambda: MagicMock())
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    # pre_sync_runs=3 → 2 pre-sync runs (Phase 3) + 1 final run (Phase 5) = 3 total
    assert rsync_mock.call_count == 3


# 5. test_health_check_port_failure_blocks_cutover
@pytest.mark.asyncio
async def test_health_check_port_failure_blocks_cutover(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params(health_check_ports=[8080])
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch_versions(params["source_asset_id"]))
    monkeypatch.setattr(lpu, "_take_snapshot", AsyncMock(return_value={"snapshot_id": "snap-x"}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 0, "files_transferred": 0, "duration_seconds": 0.1}))
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=False))
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["cutover_completed"] is False
    assert result["error"] is not None
    assert "8080" in result["error"] or "port" in result["error"].lower()


# 6. test_health_check_command_failure_blocks_cutover
@pytest.mark.asyncio
async def test_health_check_command_failure_blocks_cutover(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params(health_check_command="curl -sf http://localhost/health")

    async def _dispatch(command, parameters, asset_ids, timeout_seconds=30):
        if command == "health_check":
            return {"status": "ok"}
        if command == "run_command":
            cmd = parameters.get("command", "")
            if "VERSION_ID" in cmd:
                return {"output": "20.04" if asset_ids[0] == params["source_asset_id"] else "22.04", "exit_code": 0}
            if "hostname -I" in cmd:
                return {"output": "10.0.0.5", "exit_code": 0}
            # health_check_command fails
            return {"output": "connection refused", "exit_code": 1}
        return {}

    monkeypatch.setattr(lpu, "dispatch_agent_job", _dispatch)
    monkeypatch.setattr(lpu, "_take_snapshot", AsyncMock(return_value={"snapshot_id": "snap-x"}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 0, "files_transferred": 0, "duration_seconds": 0.1}))
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=True))
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["cutover_completed"] is False
    assert result["error"] is not None


# 7. test_cutover_records_checkpoint
@pytest.mark.asyncio
async def test_cutover_records_checkpoint(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    params = _params(decommission_after_hours=0)
    monkeypatch.setattr(lpu, "dispatch_agent_job", _mock_dispatch_versions(params["source_asset_id"]))
    monkeypatch.setattr(lpu, "_take_snapshot", AsyncMock(return_value={"snapshot_id": "snap-chk"}))
    monkeypatch.setattr(lpu, "_run_rsync", AsyncMock(return_value={"bytes_transferred": 100, "files_transferred": 1, "duration_seconds": 0.5}))
    monkeypatch.setattr(lpu, "_probe_tcp_port", MagicMock(return_value=True))
    monkeypatch.setattr(lpu, "_stop_source", AsyncMock())
    monkeypatch.setattr(lpu, "_cutover_eip", AsyncMock())
    monkeypatch.setattr(lpu, "_get_scheduler", lambda: MagicMock())
    result = await lpu.execute(params, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert result["cutover_completed"] is True
    assert result["source_stopped"] is True
    assert result["snapshot_id"] == "snap-chk"
    assert result["cutover_method"] == "eip"
    assert result["cutover_config"] == {"eip_allocation_id": "eipalloc-abc123"}
    assert result["error"] is None


# 8. test_rollback_pre_cutover_no_traffic_change
@pytest.mark.asyncio
async def test_rollback_pre_cutover_no_traffic_change(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    reverse_calls = []
    monkeypatch.setattr(lpu, "_do_cutover", AsyncMock(side_effect=lambda *a, **kw: reverse_calls.append(kw.get("reverse"))))
    params = _params()
    execution_result = {
        "cutover_completed": False, "source_stopped": False,
        "snapshot_id": None, "decommission_job_id": None,
        "cutover_method": "eip", "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "rollback_capability": ROLLBACK_CAPABILITY_FULL,
    }
    from app.connectors.executors.nexplane_agent.linux_parallel_upgrade import ROLLBACK_CAPABILITY_FULL
    result = await lpu.rollback(params, execution_result, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert not reverse_calls
    assert result["error"] is None


# 9. test_rollback_post_cutover_reverses_traffic
@pytest.mark.asyncio
async def test_rollback_post_cutover_reverses_traffic(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    reverse_args = []

    async def _mock_cutover(src, dst, params, connector, reverse=False):
        reverse_args.append(reverse)

    monkeypatch.setattr(lpu, "_do_cutover", _mock_cutover)
    monkeypatch.setattr(lpu, "_start_source", AsyncMock())
    monkeypatch.setattr(lpu, "_check_agent", AsyncMock())
    params = _params()
    execution_result = {
        "cutover_completed": True, "source_stopped": True,
        "snapshot_id": "snap-abc", "decommission_job_id": None,
        "cutover_method": "eip", "cutover_config": {"eip_allocation_id": "eipalloc-abc123"},
        "rollback_capability": "full",
    }
    result = await lpu.rollback(params, execution_result, [params["source_asset_id"], params["dest_asset_id"]], _conn_ec2())
    assert True in reverse_args
    assert result["error"] is None


# 10. test_decommission_job_scheduled
@pytest.mark.asyncio
async def test_decommission_job_scheduled(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    jobs = []

    class FakeSched:
        def add_job(self, fn, trigger, id, **kw):
            jobs.append(id)
            return MagicMock(id=id)

    monkeypatch.setattr(lpu, "_get_scheduler", lambda: FakeSched())
    params = _params(decommission_after_hours=24)
    execution_result = {"decommission_job_id": None, "decommission_manual": False}
    result = await lpu._phase6_decommission(params, execution_result, _conn_ec2())
    assert result["decommission_job_id"] is not None
    assert len(jobs) == 1
    assert result["decommission_manual"] is False


# 11. test_manual_decommission_no_job
@pytest.mark.asyncio
async def test_manual_decommission_no_job(monkeypatch):
    from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu
    monkeypatch.setattr(lpu, "_get_scheduler", lambda: MagicMock())
    params = _params(decommission_after_hours=0)
    execution_result = {"decommission_job_id": None, "decommission_manual": False}
    result = await lpu._phase6_decommission(params, execution_result, _conn_ec2())
    assert result["decommission_manual"] is True
    assert result["decommission_job_id"] is None
```

- [ ] **Step 2: Run all 11 tests**

```
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_linux_parallel_upgrade.py -v
```

Expected: 11 PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/tests/test_linux_parallel_upgrade.py
git commit -m "test: add 11 unit tests for linux_parallel_upgrade"
```

---

### Task 8: Smoke Test

**Files:**
- Create: `backend/tests/smoke/test_linux_parallel_upgrade_smoke.py`

**Interfaces:**
- Consumes: live AWS EC2 (two AMIs), live Nexplane platform API, EIP from test pool
- Produces: end-to-end smoke test (provision → sync → cutover → rollback → teardown)

- [ ] **Step 1: Create the smoke test file**

`backend/tests/smoke/test_linux_parallel_upgrade_smoke.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Smoke test: linux_parallel_upgrade CR
- Source: Ubuntu 20.04 AMI with Nexplane agent (SSM: /nexplane/smoke-amis/ubuntu-20-agent/latest)
- Dest:   Ubuntu 22.04 AMI with Nexplane agent (SSM: /nexplane/smoke-amis/ubuntu-22-agent/latest)
- Cutover method: EIP

Phases:
  1. Provision source (Ubuntu 20.04), write test data, associate EIP
  2. Provision dest (Ubuntu 22.04), install nc listener on port 8080
  3. Execute CR (sync + cutover via EIP)
  4. Assert: EIP on dest, source stopped, /var/lib/testapp/data.txt on dest
  5. Rollback: assert EIP back on source, source running, dest stopped
  6. Teardown: terminate both instances, release EIP, delete snapshot
"""

import asyncio
import boto3
import json
import os
import time
import uuid
import pytest
import requests

PLATFORM_URL = os.environ.get("NEXPLANE_URL", "http://localhost:8000")
PLATFORM_TOKEN = os.environ.get("NEXPLANE_TOKEN", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SSM_PATH_SOURCE_AMI = "/nexplane/smoke-amis/ubuntu-20-agent/latest"
SSM_PATH_DEST_AMI = "/nexplane/smoke-amis/ubuntu-22-agent/latest"
SMOKE_SG = os.environ.get("NEXPLANE_SMOKE_SG", "")
SMOKE_SUBNET = os.environ.get("NEXPLANE_SMOKE_SUBNET", "")
AWS_CONNECTOR_ID = os.environ.get("NEXPLANE_AWS_CONNECTOR_ID", "")

HEADERS = {"Authorization": f"Bearer {PLATFORM_TOKEN}", "Content-Type": "application/json"}


def _ec2():
    return boto3.client("ec2", region_name=AWS_REGION)


def _ssm():
    return boto3.client("ssm", region_name=AWS_REGION)


def _get_ami(ssm_path: str) -> str:
    resp = _ssm().get_parameter(Name=ssm_path)
    return resp["Parameter"]["Value"]


def _launch_instance(ami_id: str, name: str) -> dict:
    ec2 = _ec2()
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType="t3.micro",
        MinCount=1, MaxCount=1,
        SecurityGroupIds=[SMOKE_SG],
        SubnetId=SMOKE_SUBNET,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": name},
            {"Key": "nexplane-smoke", "Value": "true"},
        ]}],
    )
    instance = resp["Instances"][0]
    instance_id = instance["InstanceId"]
    # Wait for running
    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 30})
    return {"instance_id": instance_id}


def _wait_for_asset(name_tag: str, timeout: int = 300) -> str:
    """Poll platform API until asset with matching Name tag appears. Returns asset_id."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(f"{PLATFORM_URL}/api/assets", headers=HEADERS)
        for asset in resp.json().get("items", []):
            if asset.get("name") == name_tag and asset.get("status") == "active":
                return asset["id"]
        time.sleep(10)
    raise TimeoutError(f"Asset {name_tag!r} did not appear in platform within {timeout}s")


def _run_cr(change_type: str, parameters: dict) -> dict:
    """Create CR → plan → approve → execute. Returns execution_result."""
    # Create
    cr_resp = requests.post(f"{PLATFORM_URL}/api/change-requests", headers=HEADERS, json={
        "change_type": change_type,
        "desired_outcome": parameters,
    })
    cr_resp.raise_for_status()
    cr_id = cr_resp.json()["id"]

    # Plan
    plan_resp = requests.post(f"{PLATFORM_URL}/api/change-requests/{cr_id}/plan", headers=HEADERS)
    plan_resp.raise_for_status()

    # Approve
    approve_resp = requests.post(f"{PLATFORM_URL}/api/change-requests/{cr_id}/approve", headers=HEADERS)
    approve_resp.raise_for_status()

    # Execute and poll
    exec_resp = requests.post(f"{PLATFORM_URL}/api/change-requests/{cr_id}/execute", headers=HEADERS)
    exec_resp.raise_for_status()

    deadline = time.time() + 600
    while time.time() < deadline:
        status_resp = requests.get(f"{PLATFORM_URL}/api/change-requests/{cr_id}", headers=HEADERS)
        cr = status_resp.json()
        if cr["status"] in ("completed", "failed"):
            return {"cr_id": cr_id, "status": cr["status"], "execution_result": cr.get("execution_result", {})}
        time.sleep(10)
    raise TimeoutError(f"CR {cr_id} did not complete within 600s")


def _get_instance_state(instance_id: str) -> str:
    ec2 = _ec2()
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    return resp["Reservations"][0]["Instances"][0]["State"]["Name"]


def _get_eip_association(eip_allocation_id: str) -> str | None:
    """Return InstanceId currently associated with the EIP, or None."""
    ec2 = _ec2()
    addrs = ec2.describe_addresses(AllocationIds=[eip_allocation_id])["Addresses"]
    return addrs[0].get("InstanceId") if addrs else None


@pytest.fixture(scope="module")
def smoke_resources():
    """Provision source + dest + EIP. Yield resource dict. Teardown on exit."""
    ec2 = _ec2()
    source_ami = _get_ami(SSM_PATH_SOURCE_AMI)
    dest_ami = _get_ami(SSM_PATH_DEST_AMI)
    source_name = f"smoke-lpu-source-{uuid.uuid4().hex[:8]}"
    dest_name = f"smoke-lpu-dest-{uuid.uuid4().hex[:8]}"

    source_info = _launch_instance(source_ami, source_name)
    dest_info = _launch_instance(dest_ami, dest_name)

    # Allocate EIP
    eip = ec2.allocate_address(Domain="vpc", TagSpecifications=[{
        "ResourceType": "elastic-ip", "Tags": [{"Key": "nexplane-smoke", "Value": "true"}]
    }])
    eip_id = eip["AllocationId"]

    # Associate EIP to source
    ec2.associate_address(AllocationId=eip_id, InstanceId=source_info["instance_id"])

    # Wait for assets to appear in platform
    source_asset_id = _wait_for_asset(source_name)
    dest_asset_id = _wait_for_asset(dest_name)

    # Write test data on source via SSM run command
    boto3.client("ssm", region_name=AWS_REGION).send_command(
        InstanceIds=[source_info["instance_id"]],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [
            "mkdir -p /var/lib/testapp",
            "echo 'hello-from-source' > /var/lib/testapp/data.txt",
        ]},
    )

    # Start nc listener on dest via SSM
    boto3.client("ssm", region_name=AWS_REGION).send_command(
        InstanceIds=[dest_info["instance_id"]],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["nohup nc -k -l 8080 </dev/null >/dev/null 2>&1 &"]},
    )

    resources = {
        "source_instance_id": source_info["instance_id"],
        "dest_instance_id": dest_info["instance_id"],
        "source_asset_id": source_asset_id,
        "dest_asset_id": dest_asset_id,
        "eip_id": eip_id,
    }
    yield resources

    # Teardown
    try:
        addr = ec2.describe_addresses(AllocationIds=[eip_id])["Addresses"][0]
        if addr.get("AssociationId"):
            ec2.disassociate_address(AssociationId=addr["AssociationId"])
        ec2.release_address(AllocationId=eip_id)
    except Exception:
        pass
    try:
        ec2.terminate_instances(InstanceIds=[source_info["instance_id"], dest_info["instance_id"]])
    except Exception:
        pass


def test_linux_parallel_upgrade_full_flow(smoke_resources):
    r = smoke_resources

    # --- Execute CR ---
    cr_result = _run_cr("linux_parallel_upgrade", {
        "source_asset_id": r["source_asset_id"],
        "dest_asset_id": r["dest_asset_id"],
        "sync_paths": ["/var/lib/testapp"],
        "sync_exclude": [],
        "pre_sync_runs": 1,
        "health_check_ports": [8080],
        "cutover_method": "eip",
        "cutover_config": {"eip_allocation_id": r["eip_id"]},
        "decommission_after_hours": 0,
        "dry_run": False,
    })
    cr_id = cr_result["cr_id"]
    execution_result = cr_result["execution_result"]

    assert cr_result["status"] == "completed", f"CR failed: {execution_result}"
    assert execution_result.get("cutover_completed") is True
    assert execution_result.get("source_stopped") is True

    # Assert EIP now on dest
    dest_instance_id = r["dest_instance_id"]
    eip_instance = _get_eip_association(r["eip_id"])
    assert eip_instance == dest_instance_id, f"EIP should be on dest {dest_instance_id}, got {eip_instance}"

    # Assert source is stopped
    assert _get_instance_state(r["source_instance_id"]) == "stopped"

    # Assert data was synced
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    cmd = ssm.send_command(
        InstanceIds=[dest_instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["cat /var/lib/testapp/data.txt"]},
    )
    cmd_id = cmd["Command"]["CommandId"]
    time.sleep(5)
    output = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=dest_instance_id)
    assert "hello-from-source" in output["StandardOutputContent"], \
        f"Expected synced data on dest, got: {output['StandardOutputContent']}"

    # --- Rollback ---
    rollback_resp = requests.post(f"{PLATFORM_URL}/api/change-requests/{cr_id}/rollback", headers=HEADERS)
    rollback_resp.raise_for_status()

    deadline = time.time() + 300
    while time.time() < deadline:
        cr = requests.get(f"{PLATFORM_URL}/api/change-requests/{cr_id}", headers=HEADERS).json()
        if cr["status"] == "rolled_back":
            break
        time.sleep(10)
    else:
        raise TimeoutError("Rollback did not complete within 300s")

    # Assert EIP back on source
    source_instance_id = r["source_instance_id"]
    eip_instance = _get_eip_association(r["eip_id"])
    assert eip_instance == source_instance_id, f"EIP should be back on source {source_instance_id}, got {eip_instance}"

    # Assert source is running again
    ec2 = _ec2()
    ec2.get_waiter("instance_running").wait(
        InstanceIds=[source_instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 30}
    )
    assert _get_instance_state(source_instance_id) == "running"
```

- [ ] **Step 2: Verify smoke test structure is correct (dry parse)**

```
docker exec nexplane-backend-1 python -m py_compile backend/tests/smoke/test_linux_parallel_upgrade_smoke.py && echo "syntax ok"
```

Expected: `syntax ok`

- [ ] **Step 3: Run smoke test against live infrastructure**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "
  cd ~/nexplane
  export NEXPLANE_URL=http://localhost:8000
  export NEXPLANE_TOKEN=$(cat /tmp/nexplane_smoke_token)
  export AWS_REGION=us-east-1
  export NEXPLANE_SMOKE_SG=sg-REPLACE
  export NEXPLANE_SMOKE_SUBNET=subnet-REPLACE
  pytest backend/tests/smoke/test_linux_parallel_upgrade_smoke.py -v -s 2>&1 | tee /tmp/lpu_smoke.log
  echo SMOKE_DONE
"
```

Read `/tmp/lpu_smoke.log` to verify all phases pass.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_linux_parallel_upgrade_smoke.py
git commit -m "test: add linux_parallel_upgrade smoke test (Ubuntu 20.04→22.04, EIP cutover)"
```

---

## Final Checklist

- [ ] All 11 unit tests pass: `pytest backend/app/tests/test_linux_parallel_upgrade.py -v`
- [ ] Import test passes: `pytest backend/app/tests/test_snapshot_helpers_import.py -v`
- [ ] `os_upgrade.py` existing tests still pass (no regression)
- [ ] Go agent builds without errors: `go build ./agent/...`
- [ ] Smoke test passes against live EC2 infrastructure
- [ ] SPDX headers on all new Python files
- [ ] `decommission_after_hours=0` creates no job and sets `decommission_manual=True`
- [ ] Rollback before cutover does NOT call reverse-cutover
- [ ] Rollback after cutover reverses EIP/ALB/DNS/static_ip and restarts source
