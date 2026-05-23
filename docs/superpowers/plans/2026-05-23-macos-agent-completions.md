# macOS Agent Completions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 9 new macOS agent commands (profiles_install/remove, homebrew_list, 7 Santa commands), wire all 11 missing commands into the backend (change type defs + Python executors + catalog), and extend the MAC_AGENT_BOOTSTRAP smoke phase to cover all new commands.

**Architecture:** Go agent commands live in `agent/commands/macos/` with Darwin/non-Darwin build tags. Each command has a matching Python executor in `backend/app/connectors/executors/nexplane_agent/`, a JSON change type definition, and a catalog entry. The smoke phase uses a mac2.metal EC2 instance on a pre-allocated Dedicated Host.

**Tech Stack:** Go (agent commands), Python/asyncio (backend executors), JSON (change type defs + catalog), pytest (smoke tests)

---

## File Map

**Go agent — new files (modify existing to add new functions):**
- Modify: `agent/commands/macos/macos.go` — add 9 new public Execute/Rollback wrappers
- Modify: `agent/commands/macos/macos_darwin.go` — add 9 new implementations
- Modify: `agent/commands/macos/macos_other.go` — add 9 new stubs
- Modify: `agent/commands/macos/rollbacks_darwin.go` — add 5 new rollback implementations
- Modify: `agent/commands/macos/rollbacks_other.go` — add 5 new rollback stubs
- Modify: `agent/executor/executor.go` — register 9 new execute + 5 rollback handlers

**Backend wiring — 22 new files (11 commands × 2 files each):**
- Create: `backend/app/connectors/change_type_definitions/macos_defaults_write.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_defaults_write.py`
- Create: `backend/app/connectors/change_type_definitions/macos_santa_check.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_santa_check.py`
- Create: `backend/app/connectors/change_type_definitions/macos_profiles_install.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_profiles_install.py`
- Create: `backend/app/connectors/change_type_definitions/macos_profiles_remove.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_profiles_remove.py`
- Create: `backend/app/connectors/change_type_definitions/macos_homebrew_list.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_homebrew_list.py`
- Create: `backend/app/connectors/change_type_definitions/macos_santa_rule_add.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_santa_rule_add.py`
- Create: `backend/app/connectors/change_type_definitions/macos_santa_rule_remove.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_santa_rule_remove.py`
- Create: `backend/app/connectors/change_type_definitions/macos_santa_rule_list.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_santa_rule_list.py`
- Create: `backend/app/connectors/change_type_definitions/macos_santa_mode_set.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_santa_mode_set.py`
- Create: `backend/app/connectors/change_type_definitions/macos_santa_sync_trigger.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_santa_sync_trigger.py`
- Create: `backend/app/connectors/change_type_definitions/macos_santa_event_export.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_santa_event_export.py`
- Create: `backend/app/connectors/change_type_definitions/macos_santa_binary_check.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_santa_binary_check.py`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json` — add 11 catalog entries

**Smoke:**
- Modify: `backend/tests/smoke/test_aws_live.py` — extend `run_phase_mac_agent_bootstrap`

---

## Task 1: Backend wiring for `defaults_write` and `santa_check`

These agent commands exist; they just need backend plumbing.

**Files:**
- Create: `backend/app/connectors/change_type_definitions/macos_defaults_write.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_defaults_write.py`
- Create: `backend/app/connectors/change_type_definitions/macos_santa_check.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_santa_check.py`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`

- [ ] **Step 1: Create `macos_defaults_write.json`**

```json
{
  "change_type": "macos_defaults_write",
  "display_name": "macOS Defaults Write",
  "description": "Writes a macOS defaults preference value. Captures previous value for rollback.",
  "steps": [
    {
      "generic_action": "macos_defaults_write",
      "purpose": "execute",
      "required": true
    }
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "macos_defaults_write",
  "rollback_connector_type": "macos"
}
```

Save to: `backend/app/connectors/change_type_definitions/macos_defaults_write.json`

- [ ] **Step 2: Create `macos_defaults_write.py`**

```python
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="defaults_write",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    rollback_params = {
        "domain": parameters.get("domain"),
        "key": parameters.get("key"),
        "previous_value": execution_result.get("previous_value"),
        "_rollback": True,
    }
    return await _dispatch.dispatch_agent_job(
        command="defaults_write",
        parameters=rollback_params,
        asset_ids=asset_ids,
        timeout_seconds=30,
    )
```

Save to: `backend/app/connectors/executors/nexplane_agent/macos_defaults_write.py`

- [ ] **Step 3: Create `macos_santa_check.json`**

```json
{
  "change_type": "macos_santa_check",
  "display_name": "Santa Status Check",
  "description": "Audits Google Santa binary allowlisting status on macOS. Returns installed status, mode, and configuration.",
  "steps": [
    {
      "generic_action": "macos_santa_check",
      "purpose": "execute",
      "required": true
    }
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

Save to: `backend/app/connectors/change_type_definitions/macos_santa_check.json`

- [ ] **Step 4: Create `macos_santa_check.py`**

```python
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await _dispatch.dispatch_agent_job(
        command="santa_check",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )
```

Save to: `backend/app/connectors/executors/nexplane_agent/macos_santa_check.py`

- [ ] **Step 5: Add catalog entries to `nexplane_agent.json`**

In `backend/app/connectors/catalog/nexplane_agent.json`, find the closing `]` of the `"actions"` array (after the `macos_sysinfo` entry around line 1995) and add these two entries before it:

```json
,{
    "action_id": "macos_defaults_write",
    "generic_action": "macos_defaults_write",
    "display_name": "macOS Defaults Write",
    "description": "Writes a macOS defaults preference key/value. Captures previous value for rollback.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [
        {"name": "domain", "type": "string", "required": true, "description": "Defaults domain, e.g. com.apple.finder"},
        {"name": "key", "type": "string", "required": true, "description": "Preference key name"},
        {"name": "value", "type": "string", "required": true, "description": "Value to write"},
        {"name": "type", "type": "string", "required": false, "description": "Value type: string (default), bool, int, float"}
    ],
    "action_type": "change",
    "executor": "nexplane_agent.macos_defaults_write",
    "estimated_duration_seconds": 10,
    "execution_tier": 2
},
{
    "action_id": "macos_santa_check",
    "generic_action": "macos_santa_check",
    "display_name": "Santa Status Check",
    "description": "Audits Google Santa binary allowlisting status. Returns installed flag, mode, and configuration.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [],
    "action_type": "read",
    "executor": "nexplane_agent.macos_santa_check",
    "estimated_duration_seconds": 10,
    "execution_tier": 1
}
```

- [ ] **Step 6: Verify backend imports the new executors**

Run from `backend/`:
```bash
python -c "from app.connectors.executors.nexplane_agent import macos_defaults_write, macos_santa_check; print('OK')"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/change_type_definitions/macos_defaults_write.json \
        backend/app/connectors/change_type_definitions/macos_santa_check.json \
        backend/app/connectors/executors/nexplane_agent/macos_defaults_write.py \
        backend/app/connectors/executors/nexplane_agent/macos_santa_check.py \
        backend/app/connectors/catalog/nexplane_agent.json
git commit -m "feat: wire defaults_write and santa_check into backend catalog"
```

---

## Task 2: Go agent — `profiles_install` and `profiles_remove`

**Files:**
- Modify: `agent/commands/macos/macos.go`
- Modify: `agent/commands/macos/macos_darwin.go`
- Modify: `agent/commands/macos/macos_other.go`
- Modify: `agent/commands/macos/rollbacks_darwin.go`
- Modify: `agent/commands/macos/rollbacks_other.go`
- Modify: `agent/executor/executor.go`
- Test: `agent/commands/macos/macos_test.go` (create if absent)

- [ ] **Step 1: Add stubs to `macos_other.go`**

Append to `agent/commands/macos/macos_other.go`:

```go
func profilesInstall(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("profiles_install is only supported on macOS")
}

func profilesRemove(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("profiles_remove is only supported on macOS")
}
```

Also append to `agent/commands/macos/rollbacks_other.go`:

```go
// RollbackProfilesInstall is a stub; profiles_install rollback is only supported on macOS.
func RollbackProfilesInstall(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("profiles_install rollback is only supported on macOS")
}

// RollbackProfilesRemove is a stub; profiles_remove rollback is only supported on macOS.
func RollbackProfilesRemove(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("profiles_remove rollback is only supported on macOS")
}
```

- [ ] **Step 2: Add Darwin implementations to `macos_darwin.go`**

Append to `agent/commands/macos/macos_darwin.go`:

```go
// profilesInstall writes plist_b64 to a temp file and runs profiles install.
// Returns the PayloadIdentifier parsed from the plist for rollback.
func profilesInstall(params map[string]any) (map[string]any, error) {
	plistB64, _ := params["plist_b64"].(string)
	if plistB64 == "" {
		return nil, fmt.Errorf("profiles_install requires plist_b64")
	}

	plistBytes, err := base64.StdEncoding.DecodeString(plistB64)
	if err != nil {
		return nil, fmt.Errorf("profiles_install: invalid base64: %w", err)
	}

	// Extract PayloadIdentifier from plist XML before installing.
	identifier := extractPlistKey(string(plistBytes), "PayloadIdentifier")
	if identifier == "" {
		return nil, fmt.Errorf("profiles_install: PayloadIdentifier not found in plist")
	}

	tmp, err := os.CreateTemp("", "nexplane-profile-*.mobileconfig")
	if err != nil {
		return nil, fmt.Errorf("profiles_install: temp file: %w", err)
	}
	defer os.Remove(tmp.Name())

	if _, err := tmp.Write(plistBytes); err != nil {
		return nil, fmt.Errorf("profiles_install: write temp: %w", err)
	}
	tmp.Close()

	out, err := run("profiles", "install", "-path", tmp.Name())
	if err != nil {
		return nil, fmt.Errorf("profiles install: %s: %w", out, err)
	}
	return map[string]any{"identifier": identifier, "installed": true, "output": out}, nil
}

// profilesRemove reads the current plist for the profile (for rollback), then removes it.
func profilesRemove(params map[string]any) (map[string]any, error) {
	identifier, _ := params["identifier"].(string)
	if identifier == "" {
		return nil, fmt.Errorf("profiles_remove requires identifier")
	}

	// Capture current plist for rollback.
	listOut, _ := run("profiles", "list", "-output", "stdout-xml")
	prevPlistB64 := base64.StdEncoding.EncodeToString([]byte(listOut))

	out, err := run("profiles", "remove", "-identifier", identifier)
	if err != nil {
		return nil, fmt.Errorf("profiles remove %s: %s: %w", identifier, out, err)
	}
	return map[string]any{
		"identifier":       identifier,
		"removed":          true,
		"previous_plist_b64": prevPlistB64,
		"output":           out,
	}, nil
}

// extractPlistKey extracts a string value for a key from an XML plist.
// Looks for <key>keyName</key>\n\t<string>value</string> pattern.
func extractPlistKey(plist, key string) string {
	lines := strings.Split(plist, "\n")
	for i, line := range lines {
		if strings.Contains(line, "<key>"+key+"</key>") && i+1 < len(lines) {
			val := strings.TrimSpace(lines[i+1])
			val = strings.TrimPrefix(val, "<string>")
			val = strings.TrimSuffix(val, "</string>")
			if val != lines[i+1] {
				return val
			}
		}
	}
	return ""
}
```

Add `"encoding/base64"` and `"os"` to the imports in `macos_darwin.go`.

- [ ] **Step 3: Add rollback implementations to `rollbacks_darwin.go`**

Append to `agent/commands/macos/rollbacks_darwin.go`:

```go
// RollbackProfilesInstall removes the profile that was installed, using the stored identifier.
func RollbackProfilesInstall(params map[string]any) (map[string]any, error) {
	identifier, _ := params["identifier"].(string)
	if identifier == "" {
		return nil, fmt.Errorf("profiles_install rollback requires identifier in params")
	}
	out, err := run("profiles", "remove", "-identifier", identifier)
	if err != nil {
		return nil, fmt.Errorf("profiles remove %s (rollback): %s: %w", identifier, out, err)
	}
	return map[string]any{"rolled_back": true, "identifier": identifier, "output": out}, nil
}

// RollbackProfilesRemove reinstalls the profile from the stored plist base64.
func RollbackProfilesRemove(params map[string]any) (map[string]any, error) {
	plistB64, _ := params["previous_plist_b64"].(string)
	if plistB64 == "" {
		return nil, fmt.Errorf("profiles_remove rollback requires previous_plist_b64 in params")
	}
	plistBytes, err := base64.StdEncoding.DecodeString(plistB64)
	if err != nil {
		return nil, fmt.Errorf("profiles_remove rollback: invalid base64: %w", err)
	}
	tmp, err := os.CreateTemp("", "nexplane-profile-rollback-*.mobileconfig")
	if err != nil {
		return nil, fmt.Errorf("profiles_remove rollback: temp file: %w", err)
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(plistBytes); err != nil {
		return nil, fmt.Errorf("profiles_remove rollback: write temp: %w", err)
	}
	tmp.Close()
	out, err := run("profiles", "install", "-path", tmp.Name())
	if err != nil {
		return nil, fmt.Errorf("profiles install (rollback): %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true, "output": out}, nil
}
```

Add `"encoding/base64"` and `"os"` to imports in `rollbacks_darwin.go`.

- [ ] **Step 4: Add public wrappers to `macos.go`**

Append to `agent/commands/macos/macos.go`:

```go
// ProfilesInstallExecute installs a configuration profile from a base64-encoded plist.
func ProfilesInstallExecute(params map[string]any) (map[string]any, error) {
	return profilesInstall(params)
}

// ProfilesRemoveExecute removes a configuration profile by its PayloadIdentifier.
func ProfilesRemoveExecute(params map[string]any) (map[string]any, error) {
	return profilesRemove(params)
}

// ProfilesInstallRollback removes the profile installed by profiles_install.
func ProfilesInstallRollback(params map[string]any) (map[string]any, error) {
	return RollbackProfilesInstall(params)
}

// ProfilesRemoveRollback reinstalls the profile removed by profiles_remove.
func ProfilesRemoveRollback(params map[string]any) (map[string]any, error) {
	return RollbackProfilesRemove(params)
}
```

- [ ] **Step 5: Register in `executor.go`**

In `agent/executor/executor.go`, in the `commands` map after `"santa_check"`:

```go
"profiles_install": macos.ProfilesInstallExecute,
"profiles_remove":  macos.ProfilesRemoveExecute,
```

In the `rollbacks` map after `"gatekeeper_disable"`:

```go
"profiles_install": macos.ProfilesInstallRollback,
"profiles_remove":  macos.ProfilesRemoveRollback,
```

- [ ] **Step 6: Build to verify no compile errors**

```bash
cd agent && go build ./...
```
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add agent/commands/macos/ agent/executor/executor.go
git commit -m "feat(agent): add profiles_install and profiles_remove macOS commands"
```

---

## Task 3: Backend wiring for `profiles_install` and `profiles_remove`

**Files:**
- Create: `backend/app/connectors/change_type_definitions/macos_profiles_install.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_profiles_install.py`
- Create: `backend/app/connectors/change_type_definitions/macos_profiles_remove.json`
- Create: `backend/app/connectors/executors/nexplane_agent/macos_profiles_remove.py`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`

- [ ] **Step 1: Create `macos_profiles_install.json`**

```json
{
  "change_type": "macos_profiles_install",
  "display_name": "Install Configuration Profile",
  "description": "Installs a macOS configuration profile from a base64-encoded .mobileconfig plist. Captures profile identifier for rollback.",
  "steps": [
    {
      "generic_action": "macos_profiles_install",
      "purpose": "execute",
      "required": true
    }
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "macos_profiles_install",
  "rollback_connector_type": "macos"
}
```

- [ ] **Step 2: Create `macos_profiles_install.py`**

```python
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="profiles_install",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=60,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="profiles_install",
        parameters={"identifier": execution_result.get("identifier"), "_rollback": True},
        asset_ids=asset_ids,
        timeout_seconds=60,
    )
```

- [ ] **Step 3: Create `macos_profiles_remove.json`**

```json
{
  "change_type": "macos_profiles_remove",
  "display_name": "Remove Configuration Profile",
  "description": "Removes a macOS configuration profile by its PayloadIdentifier. Captures the profile plist for rollback reinstallation.",
  "steps": [
    {
      "generic_action": "macos_profiles_remove",
      "purpose": "execute",
      "required": true
    }
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "macos_profiles_remove",
  "rollback_connector_type": "macos"
}
```

- [ ] **Step 4: Create `macos_profiles_remove.py`**

```python
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="profiles_remove",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=60,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="profiles_remove",
        parameters={"previous_plist_b64": execution_result.get("previous_plist_b64"), "_rollback": True},
        asset_ids=asset_ids,
        timeout_seconds=60,
    )
```

- [ ] **Step 5: Add catalog entries to `nexplane_agent.json`**

After the `macos_santa_check` entry added in Task 1:

```json
,{
    "action_id": "macos_profiles_install",
    "generic_action": "macos_profiles_install",
    "display_name": "Install Configuration Profile",
    "description": "Installs a macOS configuration profile from a base64-encoded .mobileconfig. Rolls back by removing the profile.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [
        {"name": "plist_b64", "type": "string", "required": true, "description": "Base64-encoded .mobileconfig plist content"}
    ],
    "action_type": "change",
    "executor": "nexplane_agent.macos_profiles_install",
    "estimated_duration_seconds": 30,
    "execution_tier": 2
},
{
    "action_id": "macos_profiles_remove",
    "generic_action": "macos_profiles_remove",
    "display_name": "Remove Configuration Profile",
    "description": "Removes a macOS configuration profile by its PayloadIdentifier. Captures plist for rollback.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [
        {"name": "identifier", "type": "string", "required": true, "description": "Profile PayloadIdentifier string"}
    ],
    "action_type": "change",
    "executor": "nexplane_agent.macos_profiles_remove",
    "estimated_duration_seconds": 30,
    "execution_tier": 3
}
```

- [ ] **Step 6: Verify imports**

```bash
cd backend && python -c "from app.connectors.executors.nexplane_agent import macos_profiles_install, macos_profiles_remove; print('OK')"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/change_type_definitions/macos_profiles_install.json \
        backend/app/connectors/change_type_definitions/macos_profiles_remove.json \
        backend/app/connectors/executors/nexplane_agent/macos_profiles_install.py \
        backend/app/connectors/executors/nexplane_agent/macos_profiles_remove.py \
        backend/app/connectors/catalog/nexplane_agent.json
git commit -m "feat: wire profiles_install and profiles_remove into backend catalog"
```

---

## Task 4: Go agent — `homebrew_list`

**Files:**
- Modify: `agent/commands/macos/macos.go`
- Modify: `agent/commands/macos/macos_darwin.go`
- Modify: `agent/commands/macos/macos_other.go`
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Add stub to `macos_other.go`**

Append to `agent/commands/macos/macos_other.go`:

```go
func homebrewList(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("homebrew_list is only supported on macOS")
}
```

- [ ] **Step 2: Add Darwin implementation to `macos_darwin.go`**

Append to `agent/commands/macos/macos_darwin.go`:

```go
// homebrewList returns installed Homebrew packages. Returns installed:false if brew not found.
func homebrewList(_ map[string]any) (map[string]any, error) {
	brewPath, err := exec.LookPath("brew")
	if err != nil {
		return map[string]any{"installed": false, "packages": []map[string]any{}}, nil
	}

	out, err := run(brewPath, "list", "--versions")
	if err != nil {
		return nil, fmt.Errorf("brew list --versions: %s: %w", out, err)
	}

	var packages []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.Fields(line)
		name := parts[0]
		version := ""
		if len(parts) > 1 {
			version = parts[len(parts)-1]
		}
		packages = append(packages, map[string]any{"name": name, "version": version})
	}
	if packages == nil {
		packages = []map[string]any{}
	}
	return map[string]any{"installed": true, "packages": packages}, nil
}
```

- [ ] **Step 3: Add public wrapper to `macos.go`**

Append to `agent/commands/macos/macos.go`:

```go
// HomebrewListExecute returns installed Homebrew packages.
func HomebrewListExecute(params map[string]any) (map[string]any, error) {
	return homebrewList(params)
}
```

- [ ] **Step 4: Register in `executor.go`**

In the `commands` map after `"profiles_remove"`:

```go
"homebrew_list": macos.HomebrewListExecute,
```

- [ ] **Step 5: Build**

```bash
cd agent && go build ./...
```
Expected: no errors

- [ ] **Step 6: Create change type def and executor**

`backend/app/connectors/change_type_definitions/macos_homebrew_list.json`:

```json
{
  "change_type": "macos_homebrew_list",
  "display_name": "Homebrew Package Inventory",
  "description": "Lists all installed Homebrew packages and versions on macOS.",
  "steps": [
    {
      "generic_action": "macos_homebrew_list",
      "purpose": "execute",
      "required": true
    }
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

`backend/app/connectors/executors/nexplane_agent/macos_homebrew_list.py`:

```python
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await _dispatch.dispatch_agent_job(
        command="homebrew_list",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=60,
    )
```

- [ ] **Step 7: Add catalog entry to `nexplane_agent.json`**

```json
,{
    "action_id": "macos_homebrew_list",
    "generic_action": "macos_homebrew_list",
    "display_name": "Homebrew Package Inventory",
    "description": "Lists all installed Homebrew packages and versions. Returns installed:false if Homebrew is not present.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [],
    "action_type": "read",
    "executor": "nexplane_agent.macos_homebrew_list",
    "estimated_duration_seconds": 30,
    "execution_tier": 1
}
```

- [ ] **Step 8: Commit**

```bash
git add agent/commands/macos/ agent/executor/executor.go \
        backend/app/connectors/change_type_definitions/macos_homebrew_list.json \
        backend/app/connectors/executors/nexplane_agent/macos_homebrew_list.py \
        backend/app/connectors/catalog/nexplane_agent.json
git commit -m "feat(agent): add homebrew_list macOS command"
```

---

## Task 5: Go agent — Santa commands (rule management)

`santa_rule_add`, `santa_rule_remove`, `santa_rule_list`

**Files:**
- Modify: `agent/commands/macos/macos.go`
- Modify: `agent/commands/macos/macos_darwin.go`
- Modify: `agent/commands/macos/macos_other.go`
- Modify: `agent/commands/macos/rollbacks_darwin.go`
- Modify: `agent/commands/macos/rollbacks_other.go`
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Add stubs to `macos_other.go`**

Append:

```go
func santaRuleAdd(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("santa_rule_add is only supported on macOS")
}

func santaRuleRemove(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("santa_rule_remove is only supported on macOS")
}

func santaRuleList(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("santa_rule_list is only supported on macOS")
}
```

Append to `rollbacks_other.go`:

```go
// RollbackSantaRuleAdd is a stub; santa_rule_add rollback is only supported on macOS.
func RollbackSantaRuleAdd(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("santa_rule_add rollback is only supported on macOS")
}

// RollbackSantaRuleRemove is a stub; santa_rule_remove rollback is only supported on macOS.
func RollbackSantaRuleRemove(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("santa_rule_remove rollback is only supported on macOS")
}
```

- [ ] **Step 2: Add Darwin implementations to `macos_darwin.go`**

Append:

```go
// santaRuleCheck returns the current rule state for an identifier: "absent", "allow", or "deny".
func santaRuleCheck(identifierType, identifier string) string {
	out, err := run("santactl", "rule", "--check", "--"+identifierType, identifier)
	if err != nil || strings.Contains(strings.ToLower(out), "no rule") || strings.Contains(strings.ToLower(out), "unknown") {
		return "absent"
	}
	lower := strings.ToLower(out)
	if strings.Contains(lower, "allowlist") || strings.Contains(lower, "allow") {
		return "allow"
	}
	if strings.Contains(lower, "denylist") || strings.Contains(lower, "deny") || strings.Contains(lower, "blocklist") {
		return "deny"
	}
	return "absent"
}

// ruleTypeFlag maps rule_type param to santactl flag.
func ruleTypeFlag(ruleType string) string {
	switch ruleType {
	case "allowlist":
		return "--allowlist"
	case "silent_blocklist":
		return "--silent-blocklist"
	default:
		return "--denylist"
	}
}

func santaRuleAdd(params map[string]any) (map[string]any, error) {
	ruleType, _ := params["rule_type"].(string)
	identifierType, _ := params["identifier_type"].(string)
	identifier, _ := params["identifier"].(string)
	customMessage, _ := params["custom_message"].(string)

	if identifier == "" || identifierType == "" {
		return nil, fmt.Errorf("santa_rule_add requires identifier_type and identifier")
	}
	if ruleType == "" {
		ruleType = "denylist"
	}

	previousState := santaRuleCheck(identifierType, identifier)

	args := []string{"rule", "--add", ruleTypeFlag(ruleType), "--" + identifierType, identifier}
	if customMessage != "" {
		args = append(args, "--message", customMessage)
	}
	out, err := run("santactl", args...)
	if err != nil {
		return nil, fmt.Errorf("santactl rule --add: %s: %w", out, err)
	}
	return map[string]any{
		"added":          true,
		"rule_type":      ruleType,
		"identifier_type": identifierType,
		"identifier":     identifier,
		"previous_state": previousState,
		"output":         out,
	}, nil
}

func santaRuleRemove(params map[string]any) (map[string]any, error) {
	identifierType, _ := params["identifier_type"].(string)
	identifier, _ := params["identifier"].(string)
	if identifier == "" || identifierType == "" {
		return nil, fmt.Errorf("santa_rule_remove requires identifier_type and identifier")
	}

	previousState := santaRuleCheck(identifierType, identifier)
	if previousState == "absent" {
		return map[string]any{"removed": false, "reason": "rule not found", "previous_state": "absent"}, nil
	}

	out, err := run("santactl", "rule", "--remove", "--"+identifierType, identifier)
	if err != nil {
		return nil, fmt.Errorf("santactl rule --remove: %s: %w", out, err)
	}
	return map[string]any{
		"removed":         true,
		"identifier_type": identifierType,
		"identifier":      identifier,
		"previous_state":  previousState,
		"output":          out,
	}, nil
}

func santaRuleList(_ map[string]any) (map[string]any, error) {
	out, err := run("santactl", "rule", "--list")
	if err != nil {
		if strings.Contains(strings.ToLower(err.Error()), "not installed") || strings.Contains(strings.ToLower(out), "not found") {
			return map[string]any{"installed": false, "rules": []map[string]any{}}, nil
		}
		return nil, fmt.Errorf("santactl rule --list: %s: %w", out, err)
	}
	var rules []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "Rule") {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) >= 2 {
			rules = append(rules, map[string]any{"identifier": parts[0], "type": parts[1]})
		}
	}
	if rules == nil {
		rules = []map[string]any{}
	}
	return map[string]any{"installed": true, "rules": rules, "rule_count": len(rules)}, nil
}
```

- [ ] **Step 3: Add rollback implementations to `rollbacks_darwin.go`**

Append:

```go
// RollbackSantaRuleAdd removes the rule if it was absent before, or restores the previous rule type.
func RollbackSantaRuleAdd(params map[string]any) (map[string]any, error) {
	identifierType, _ := params["identifier_type"].(string)
	identifier, _ := params["identifier"].(string)
	previousState, _ := params["previous_state"].(string)

	if identifierType == "" || identifier == "" {
		return nil, fmt.Errorf("santa_rule_add rollback requires identifier_type, identifier, and previous_state")
	}

	if previousState == "absent" || previousState == "" {
		out, err := run("santactl", "rule", "--remove", "--"+identifierType, identifier)
		if err != nil {
			return nil, fmt.Errorf("santactl rule --remove (rollback): %s: %w", out, err)
		}
		return map[string]any{"rolled_back": true, "action": "removed", "output": out}, nil
	}

	// Restore previous rule type (allow or deny).
	flag := "--denylist"
	if previousState == "allow" {
		flag = "--allowlist"
	}
	out, err := run("santactl", "rule", "--add", flag, "--"+identifierType, identifier)
	if err != nil {
		return nil, fmt.Errorf("santactl rule --add %s (rollback): %s: %w", flag, out, err)
	}
	return map[string]any{"rolled_back": true, "action": "restored", "previous_state": previousState, "output": out}, nil
}

// RollbackSantaRuleRemove re-adds the rule with its previous rule type.
func RollbackSantaRuleRemove(params map[string]any) (map[string]any, error) {
	identifierType, _ := params["identifier_type"].(string)
	identifier, _ := params["identifier"].(string)
	previousState, _ := params["previous_state"].(string)

	if identifierType == "" || identifier == "" || previousState == "" || previousState == "absent" {
		return nil, fmt.Errorf("santa_rule_remove rollback requires identifier_type, identifier, and previous_state")
	}

	flag := "--denylist"
	if previousState == "allow" {
		flag = "--allowlist"
	}
	out, err := run("santactl", "rule", "--add", flag, "--"+identifierType, identifier)
	if err != nil {
		return nil, fmt.Errorf("santactl rule --add (rollback): %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true, "output": out}, nil
}
```

- [ ] **Step 4: Add public wrappers to `macos.go`**

Append:

```go
// SantaRuleAddExecute adds a Santa binary rule (allow/deny).
func SantaRuleAddExecute(params map[string]any) (map[string]any, error) {
	return santaRuleAdd(params)
}

// SantaRuleRemoveExecute removes a Santa binary rule.
func SantaRuleRemoveExecute(params map[string]any) (map[string]any, error) {
	return santaRuleRemove(params)
}

// SantaRuleListExecute lists all current Santa rules.
func SantaRuleListExecute(params map[string]any) (map[string]any, error) {
	return santaRuleList(params)
}

// SantaRuleAddRollback rolls back a santa_rule_add.
func SantaRuleAddRollback(params map[string]any) (map[string]any, error) {
	return RollbackSantaRuleAdd(params)
}

// SantaRuleRemoveRollback rolls back a santa_rule_remove.
func SantaRuleRemoveRollback(params map[string]any) (map[string]any, error) {
	return RollbackSantaRuleRemove(params)
}
```

- [ ] **Step 5: Register in `executor.go`**

In `commands` map:
```go
"santa_rule_add":    macos.SantaRuleAddExecute,
"santa_rule_remove": macos.SantaRuleRemoveExecute,
"santa_rule_list":   macos.SantaRuleListExecute,
```

In `rollbacks` map:
```go
"santa_rule_add":    macos.SantaRuleAddRollback,
"santa_rule_remove": macos.SantaRuleRemoveRollback,
```

- [ ] **Step 6: Build**

```bash
cd agent && go build ./...
```
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add agent/commands/macos/ agent/executor/executor.go
git commit -m "feat(agent): add santa_rule_add, santa_rule_remove, santa_rule_list"
```

---

## Task 6: Go agent — `santa_mode_set`, `santa_sync_trigger`, `santa_event_export`, `santa_binary_check`

**Files:** Same as Task 5 (same file set)

- [ ] **Step 1: Add stubs to `macos_other.go`**

Append:

```go
func santaModeSet(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("santa_mode_set is only supported on macOS")
}

func santaSyncTrigger(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("santa_sync_trigger is only supported on macOS")
}

func santaEventExport(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("santa_event_export is only supported on macOS")
}

func santaBinaryCheck(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("santa_binary_check is only supported on macOS")
}
```

Append to `rollbacks_other.go`:

```go
// RollbackSantaModeSet is a stub; santa_mode_set rollback is only supported on macOS.
func RollbackSantaModeSet(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("santa_mode_set rollback is only supported on macOS")
}
```

- [ ] **Step 2: Add Darwin implementations to `macos_darwin.go`**

Append:

```go
// santa mode constants: 1 = MONITOR, 2 = LOCKDOWN
func santaModeSet(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if mode != "monitor" && mode != "lockdown" {
		return nil, fmt.Errorf("santa_mode_set: mode must be 'monitor' or 'lockdown'")
	}

	// Capture current mode for rollback.
	statusOut, _ := run("santactl", "status")
	previousMode := "monitor"
	for _, line := range strings.Split(statusOut, "\n") {
		parts := strings.SplitN(line, "|", 2)
		if len(parts) == 2 && strings.TrimSpace(parts[0]) == "Mode" {
			v := strings.ToLower(strings.TrimSpace(parts[1]))
			if strings.Contains(v, "lockdown") {
				previousMode = "lockdown"
			}
		}
	}

	modeInt := "1" // MONITOR
	if mode == "lockdown" {
		modeInt = "2"
	}
	out, err := run("defaults", "write", "/Library/Preferences/com.google.santa", "ClientMode", "-int", modeInt)
	if err != nil {
		return nil, fmt.Errorf("santa_mode_set defaults write: %s: %w", out, err)
	}
	// Apply via sync --clean (non-fatal if no sync server).
	run("santactl", "sync", "--clean")

	return map[string]any{"mode": mode, "previous_mode": previousMode, "output": out}, nil
}

func santaSyncTrigger(_ map[string]any) (map[string]any, error) {
	out, err := run("santactl", "sync")
	if err != nil {
		return map[string]any{"synced": false, "error": out}, nil
	}
	return map[string]any{"synced": true, "output": out}, nil
}

func santaEventExport(params map[string]any) (map[string]any, error) {
	limit := 100
	if l, ok := params["limit"].(float64); ok && l > 0 {
		limit = int(l)
	}

	logPath := "/var/db/santa/santa.log"
	out, err := run("santactl", "log")
	if err != nil {
		// Fall back to reading the log file directly.
		data, ferr := os.ReadFile(logPath)
		if ferr != nil {
			return map[string]any{"events": []map[string]any{}, "error": "santactl log not available"}, nil
		}
		out = string(data)
	}

	var events []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		if len(events) >= limit {
			break
		}
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		event := map[string]any{"raw": line}
		// Parse common fields if present.
		if strings.Contains(line, "DENY") {
			event["decision"] = "DENY"
		} else if strings.Contains(line, "ALLOW") {
			event["decision"] = "ALLOW"
		}
		events = append(events, event)
	}
	if events == nil {
		events = []map[string]any{}
	}
	return map[string]any{"events": events, "count": len(events)}, nil
}

func santaBinaryCheck(params map[string]any) (map[string]any, error) {
	path, _ := params["path"].(string)
	if path == "" {
		return nil, fmt.Errorf("santa_binary_check requires path")
	}
	out, err := run("santactl", "check", "--path", path)
	if err != nil {
		return map[string]any{"path": path, "decision": "UNKNOWN", "error": out}, nil
	}

	result := map[string]any{"path": path, "decision": "UNKNOWN", "sha256": "", "rule_type": ""}
	for _, line := range strings.Split(out, "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		k := strings.TrimSpace(parts[0])
		v := strings.TrimSpace(parts[1])
		switch k {
		case "Decision":
			result["decision"] = strings.ToUpper(v)
		case "SHA-256":
			result["sha256"] = v
		case "Rule":
			result["rule_type"] = v
		}
	}
	return result, nil
}
```

- [ ] **Step 3: Add rollback to `rollbacks_darwin.go`**

Append:

```go
// RollbackSantaModeSet restores the previous Santa operating mode.
func RollbackSantaModeSet(params map[string]any) (map[string]any, error) {
	previousMode, _ := params["previous_mode"].(string)
	if previousMode == "" {
		return nil, fmt.Errorf("santa_mode_set rollback requires previous_mode in params")
	}
	modeInt := "1"
	if previousMode == "lockdown" {
		modeInt = "2"
	}
	out, err := run("defaults", "write", "/Library/Preferences/com.google.santa", "ClientMode", "-int", modeInt)
	if err != nil {
		return nil, fmt.Errorf("santa_mode_set rollback defaults write: %s: %w", out, err)
	}
	run("santactl", "sync", "--clean")
	return map[string]any{"rolled_back": true, "mode": previousMode, "output": out}, nil
}
```

- [ ] **Step 4: Add public wrappers to `macos.go`**

Append:

```go
// SantaModeSetExecute sets Santa operating mode (monitor or lockdown).
func SantaModeSetExecute(params map[string]any) (map[string]any, error) {
	return santaModeSet(params)
}

// SantaSyncTriggerExecute triggers a Santa sync.
func SantaSyncTriggerExecute(params map[string]any) (map[string]any, error) {
	return santaSyncTrigger(params)
}

// SantaEventExportExecute exports recent Santa block/allow events.
func SantaEventExportExecute(params map[string]any) (map[string]any, error) {
	return santaEventExport(params)
}

// SantaBinaryCheckExecute checks whether a binary is allowed or denied by Santa.
func SantaBinaryCheckExecute(params map[string]any) (map[string]any, error) {
	return santaBinaryCheck(params)
}

// SantaModeSetRollback restores the previous Santa mode.
func SantaModeSetRollback(params map[string]any) (map[string]any, error) {
	return RollbackSantaModeSet(params)
}
```

- [ ] **Step 5: Register in `executor.go`**

In `commands` map:
```go
"santa_mode_set":      macos.SantaModeSetExecute,
"santa_sync_trigger":  macos.SantaSyncTriggerExecute,
"santa_event_export":  macos.SantaEventExportExecute,
"santa_binary_check":  macos.SantaBinaryCheckExecute,
```

In `rollbacks` map:
```go
"santa_mode_set": macos.SantaModeSetRollback,
```

- [ ] **Step 6: Build**

```bash
cd agent && go build ./...
```
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add agent/commands/macos/ agent/executor/executor.go
git commit -m "feat(agent): add santa_mode_set, santa_sync_trigger, santa_event_export, santa_binary_check"
```

---

## Task 7: Backend wiring for all 9 new agent commands

This task creates the 18 remaining backend files (9 commands × JSON + Python) and adds their catalog entries.

**Files:** See File Map above for the full list of 18 files.

- [ ] **Step 1: Create `macos_santa_rule_add.json`**

```json
{
  "change_type": "macos_santa_rule_add",
  "display_name": "Add Santa Rule",
  "description": "Adds an allowlist or denylist rule to Google Santa. Captures previous rule state for rollback.",
  "steps": [{"generic_action": "macos_santa_rule_add", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "macos_santa_rule_add",
  "rollback_connector_type": "macos"
}
```

- [ ] **Step 2: Create `macos_santa_rule_add.py`**

```python
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="santa_rule_add",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    rollback_params = {
        "identifier_type": execution_result.get("identifier_type"),
        "identifier": execution_result.get("identifier"),
        "previous_state": execution_result.get("previous_state"),
        "_rollback": True,
    }
    return await _dispatch.dispatch_agent_job(
        command="santa_rule_add",
        parameters=rollback_params,
        asset_ids=asset_ids,
        timeout_seconds=30,
    )
```

- [ ] **Step 3: Create `macos_santa_rule_remove.json`**

```json
{
  "change_type": "macos_santa_rule_remove",
  "display_name": "Remove Santa Rule",
  "description": "Removes a Santa rule by identifier. Captures previous state for rollback restoration.",
  "steps": [{"generic_action": "macos_santa_rule_remove", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "macos_santa_rule_remove",
  "rollback_connector_type": "macos"
}
```

- [ ] **Step 4: Create `macos_santa_rule_remove.py`**

```python
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="santa_rule_remove",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    rollback_params = {
        "identifier_type": execution_result.get("identifier_type"),
        "identifier": execution_result.get("identifier"),
        "previous_state": execution_result.get("previous_state"),
        "_rollback": True,
    }
    return await _dispatch.dispatch_agent_job(
        command="santa_rule_remove",
        parameters=rollback_params,
        asset_ids=asset_ids,
        timeout_seconds=30,
    )
```

- [ ] **Step 5: Create remaining read-only JSON defs** (no rollback key needed)

`macos_santa_rule_list.json`:
```json
{
  "change_type": "macos_santa_rule_list",
  "display_name": "List Santa Rules",
  "description": "Lists all current Google Santa rules on the device.",
  "steps": [{"generic_action": "macos_santa_rule_list", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

`macos_santa_mode_set.json`:
```json
{
  "change_type": "macos_santa_mode_set",
  "display_name": "Set Santa Mode",
  "description": "Sets Santa operating mode to MONITOR or LOCKDOWN. Captures previous mode for rollback.",
  "steps": [{"generic_action": "macos_santa_mode_set", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "macos_santa_mode_set",
  "rollback_connector_type": "macos"
}
```

`macos_santa_sync_trigger.json`:
```json
{
  "change_type": "macos_santa_sync_trigger",
  "display_name": "Trigger Santa Sync",
  "description": "Triggers a Santa sync with the configured sync server. No-op if no sync server configured.",
  "steps": [{"generic_action": "macos_santa_sync_trigger", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

`macos_santa_event_export.json`:
```json
{
  "change_type": "macos_santa_event_export",
  "display_name": "Export Santa Events",
  "description": "Exports recent Santa block/allow events from the local Santa log.",
  "steps": [{"generic_action": "macos_santa_event_export", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

`macos_santa_binary_check.json`:
```json
{
  "change_type": "macos_santa_binary_check",
  "display_name": "Santa Binary Check",
  "description": "Checks whether a binary at a given path is allowed or denied by Santa.",
  "steps": [{"generic_action": "macos_santa_binary_check", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 6: Create remaining Python executors**

`macos_santa_rule_list.py`:
```python
from app.connectors.executors.nexplane_agent import _dispatch

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await _dispatch.dispatch_agent_job(
        command="santa_rule_list", parameters=parameters,
        asset_ids=list(asset_ids), timeout_seconds=30)
```

`macos_santa_mode_set.py`:
```python
from app.connectors.executors.nexplane_agent import _dispatch

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="santa_mode_set", parameters=parameters,
        asset_ids=list(asset_ids), timeout_seconds=30)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="santa_mode_set",
        parameters={"previous_mode": execution_result.get("previous_mode"), "_rollback": True},
        asset_ids=asset_ids, timeout_seconds=30)
```

`macos_santa_sync_trigger.py`:
```python
from app.connectors.executors.nexplane_agent import _dispatch

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await _dispatch.dispatch_agent_job(
        command="santa_sync_trigger", parameters=parameters,
        asset_ids=list(asset_ids), timeout_seconds=120)
```

`macos_santa_event_export.py`:
```python
from app.connectors.executors.nexplane_agent import _dispatch

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await _dispatch.dispatch_agent_job(
        command="santa_event_export", parameters=parameters,
        asset_ids=list(asset_ids), timeout_seconds=60)
```

`macos_santa_binary_check.py`:
```python
from app.connectors.executors.nexplane_agent import _dispatch

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await _dispatch.dispatch_agent_job(
        command="santa_binary_check", parameters=parameters,
        asset_ids=list(asset_ids), timeout_seconds=30)
```

- [ ] **Step 7: Add all 9 catalog entries to `nexplane_agent.json`**

After the `macos_homebrew_list` entry added in Task 4:

```json
,{
    "action_id": "macos_santa_rule_add",
    "generic_action": "macos_santa_rule_add",
    "display_name": "Add Santa Rule",
    "description": "Adds an allowlist or denylist rule to Google Santa. Captures previous state for rollback.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [
        {"name": "rule_type", "type": "string", "required": true, "description": "allowlist, denylist, or silent_blocklist"},
        {"name": "identifier_type", "type": "string", "required": true, "description": "binary, certificate, teamid, or signingid"},
        {"name": "identifier", "type": "string", "required": true, "description": "SHA-256 hash or cert/team/signing ID"},
        {"name": "custom_message", "type": "string", "required": false, "description": "Message shown when binary is blocked"}
    ],
    "action_type": "change",
    "executor": "nexplane_agent.macos_santa_rule_add",
    "estimated_duration_seconds": 10,
    "execution_tier": 2
},
{
    "action_id": "macos_santa_rule_remove",
    "generic_action": "macos_santa_rule_remove",
    "display_name": "Remove Santa Rule",
    "description": "Removes a Santa rule by identifier. Captures previous state for rollback.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [
        {"name": "identifier_type", "type": "string", "required": true, "description": "binary, certificate, teamid, or signingid"},
        {"name": "identifier", "type": "string", "required": true, "description": "SHA-256 hash or cert/team/signing ID"}
    ],
    "action_type": "change",
    "executor": "nexplane_agent.macos_santa_rule_remove",
    "estimated_duration_seconds": 10,
    "execution_tier": 3
},
{
    "action_id": "macos_santa_rule_list",
    "generic_action": "macos_santa_rule_list",
    "display_name": "List Santa Rules",
    "description": "Lists all current Google Santa allow/deny rules on the device.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [],
    "action_type": "read",
    "executor": "nexplane_agent.macos_santa_rule_list",
    "estimated_duration_seconds": 10,
    "execution_tier": 1
},
{
    "action_id": "macos_santa_mode_set",
    "generic_action": "macos_santa_mode_set",
    "display_name": "Set Santa Mode",
    "description": "Sets Santa to MONITOR (allow with logging) or LOCKDOWN (block unknowns). Captures previous mode for rollback.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [
        {"name": "mode", "type": "string", "required": true, "description": "monitor or lockdown"}
    ],
    "action_type": "change",
    "executor": "nexplane_agent.macos_santa_mode_set",
    "estimated_duration_seconds": 15,
    "execution_tier": 3
},
{
    "action_id": "macos_santa_sync_trigger",
    "generic_action": "macos_santa_sync_trigger",
    "display_name": "Trigger Santa Sync",
    "description": "Triggers a Santa sync with the configured sync server. Graceful if no server configured.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [],
    "action_type": "change",
    "executor": "nexplane_agent.macos_santa_sync_trigger",
    "estimated_duration_seconds": 60,
    "execution_tier": 1
},
{
    "action_id": "macos_santa_event_export",
    "generic_action": "macos_santa_event_export",
    "display_name": "Export Santa Events",
    "description": "Exports recent Santa block/allow events from the device log.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [
        {"name": "limit", "type": "integer", "required": false, "description": "Max events to return (default 100)"}
    ],
    "action_type": "read",
    "executor": "nexplane_agent.macos_santa_event_export",
    "estimated_duration_seconds": 15,
    "execution_tier": 1
},
{
    "action_id": "macos_santa_binary_check",
    "generic_action": "macos_santa_binary_check",
    "display_name": "Santa Binary Check",
    "description": "Checks whether a binary path is allowed or denied by Santa.",
    "applicable_asset_types": ["server", "workstation"],
    "parameters": [
        {"name": "path", "type": "string", "required": true, "description": "Full path to the binary, e.g. /usr/bin/curl"}
    ],
    "action_type": "read",
    "executor": "nexplane_agent.macos_santa_binary_check",
    "estimated_duration_seconds": 10,
    "execution_tier": 1
}
```

- [ ] **Step 8: Verify all imports**

```bash
cd backend && python -c "
from app.connectors.executors.nexplane_agent import (
    macos_santa_rule_add, macos_santa_rule_remove, macos_santa_rule_list,
    macos_santa_mode_set, macos_santa_sync_trigger,
    macos_santa_event_export, macos_santa_binary_check
)
print('OK')
"
```
Expected: `OK`

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/change_type_definitions/macos_santa_*.json \
        backend/app/connectors/executors/nexplane_agent/macos_santa_*.py \
        backend/app/connectors/catalog/nexplane_agent.json
git commit -m "feat: wire all Santa agent commands into backend catalog"
```

---

## Task 8: Extend MAC_AGENT_BOOTSTRAP smoke phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py` — function `run_phase_mac_agent_bootstrap`

Find the function `run_phase_mac_agent_bootstrap` (around line 15542). After the existing `santa_check` CR block and before the final AMI snapshot section, add:

- [ ] **Step 1: Add `profiles_install` + rollback test**

After the `santa_check` log line:

```python
# --- profiles_install: install test profile + verify + rollback ---
TEST_PROFILE_ID = "com.nexplane.smoke.test"
TEST_PROFILE_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <array/>
    <key>PayloadDisplayName</key>
    <string>Nexplane Smoke Test Profile</string>
    <key>PayloadIdentifier</key>
    <string>com.nexplane.smoke.test</string>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>12345678-1234-1234-1234-123456789012</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
</dict>
</plist>"""

import base64 as _base64
plist_b64 = _base64.b64encode(TEST_PROFILE_PLIST.encode()).decode()

log("MAC_AGENT_BOOTSTRAP: running profiles_install CR...")
result_pi = client.run_cr(
    "[MAC_AGENT_BOOTSTRAP] profiles_install",
    "macos_profiles_install",
    endpoint_asset_id,
    {"plist_b64": plist_b64},
    connector_id=agent_connector_id,
)
step_result_pi = client.get_cr_step_result(result_pi)
if step_result_pi.get("identifier") != TEST_PROFILE_ID:
    fail(f"MAC_AGENT_BOOTSTRAP: profiles_install identifier mismatch: {step_result_pi}")
log(f"MAC_AGENT_BOOTSTRAP: profiles_install OK — identifier={step_result_pi.get('identifier')}")

# Rollback
rb_pi = client.post(f"/change-requests/{result_pi}/rollback", json={})
log(f"MAC_AGENT_BOOTSTRAP: profiles_install rollback submitted")
```

- [ ] **Step 2: Add `homebrew_list` test**

```python
log("MAC_AGENT_BOOTSTRAP: running homebrew_list CR...")
result_hb = client.run_cr(
    "[MAC_AGENT_BOOTSTRAP] homebrew_list",
    "macos_homebrew_list",
    endpoint_asset_id,
    {},
    connector_id=agent_connector_id,
)
step_result_hb = client.get_cr_step_result(result_hb)
if "packages" not in step_result_hb:
    fail(f"MAC_AGENT_BOOTSTRAP: homebrew_list missing 'packages' key: {step_result_hb}")
log(f"MAC_AGENT_BOOTSTRAP: homebrew_list OK — installed={step_result_hb.get('installed')}, packages={len(step_result_hb.get('packages', []))}")
```

- [ ] **Step 3: Add `santa_rule_add` + verify + rollback**

```python
SMOKE_SHA256 = "a" * 64  # synthetic test hash
log("MAC_AGENT_BOOTSTRAP: running santa_rule_add CR...")
result_sra = client.run_cr(
    "[MAC_AGENT_BOOTSTRAP] santa_rule_add",
    "macos_santa_rule_add",
    endpoint_asset_id,
    {"rule_type": "denylist", "identifier_type": "binary", "identifier": SMOKE_SHA256, "custom_message": "nexplane smoke test"},
    connector_id=agent_connector_id,
)
step_result_sra = client.get_cr_step_result(result_sra)
if not step_result_sra.get("added"):
    fail(f"MAC_AGENT_BOOTSTRAP: santa_rule_add failed: {step_result_sra}")
log(f"MAC_AGENT_BOOTSTRAP: santa_rule_add OK — previous_state={step_result_sra.get('previous_state')}")

# Verify via santa_rule_list
result_srl = client.run_cr(
    "[MAC_AGENT_BOOTSTRAP] santa_rule_list verify",
    "macos_santa_rule_list",
    endpoint_asset_id, {},
    connector_id=agent_connector_id,
)
step_srl = client.get_cr_step_result(result_srl)
rule_ids = [r.get("identifier", "") for r in step_srl.get("rules", [])]
if SMOKE_SHA256 not in rule_ids and step_srl.get("installed", True):
    log(f"MAC_AGENT_BOOTSTRAP: WARNING santa_rule_list did not find smoke rule (Santa may not be installed)")

# Rollback santa_rule_add
client.post(f"/change-requests/{result_sra}/rollback", json={})
log("MAC_AGENT_BOOTSTRAP: santa_rule_add rollback submitted")
```

- [ ] **Step 4: Add `santa_mode_set` + rollback**

```python
log("MAC_AGENT_BOOTSTRAP: running santa_mode_set CR (monitor)...")
result_sms = client.run_cr(
    "[MAC_AGENT_BOOTSTRAP] santa_mode_set",
    "macos_santa_mode_set",
    endpoint_asset_id,
    {"mode": "monitor"},
    connector_id=agent_connector_id,
)
step_sms = client.get_cr_step_result(result_sms)
if "previous_mode" not in step_sms:
    fail(f"MAC_AGENT_BOOTSTRAP: santa_mode_set missing previous_mode: {step_sms}")
log(f"MAC_AGENT_BOOTSTRAP: santa_mode_set OK — mode=monitor, previous_mode={step_sms.get('previous_mode')}")
client.post(f"/change-requests/{result_sms}/rollback", json={})
log("MAC_AGENT_BOOTSTRAP: santa_mode_set rollback submitted")
```

- [ ] **Step 5: Add `santa_event_export`, `santa_binary_check`, `santa_sync_trigger`**

```python
log("MAC_AGENT_BOOTSTRAP: running santa_event_export CR...")
result_see = client.run_cr(
    "[MAC_AGENT_BOOTSTRAP] santa_event_export",
    "macos_santa_event_export",
    endpoint_asset_id, {"limit": 10},
    connector_id=agent_connector_id,
)
step_see = client.get_cr_step_result(result_see)
if "events" not in step_see:
    fail(f"MAC_AGENT_BOOTSTRAP: santa_event_export missing 'events': {step_see}")
log(f"MAC_AGENT_BOOTSTRAP: santa_event_export OK — count={step_see.get('count', 0)}")

log("MAC_AGENT_BOOTSTRAP: running santa_binary_check CR...")
result_sbc = client.run_cr(
    "[MAC_AGENT_BOOTSTRAP] santa_binary_check",
    "macos_santa_binary_check",
    endpoint_asset_id, {"path": "/usr/bin/true"},
    connector_id=agent_connector_id,
)
step_sbc = client.get_cr_step_result(result_sbc)
if "decision" not in step_sbc:
    fail(f"MAC_AGENT_BOOTSTRAP: santa_binary_check missing 'decision': {step_sbc}")
log(f"MAC_AGENT_BOOTSTRAP: santa_binary_check OK — /usr/bin/true decision={step_sbc.get('decision')}")

log("MAC_AGENT_BOOTSTRAP: running santa_sync_trigger CR...")
result_sst = client.run_cr(
    "[MAC_AGENT_BOOTSTRAP] santa_sync_trigger",
    "macos_santa_sync_trigger",
    endpoint_asset_id, {},
    connector_id=agent_connector_id,
)
step_sst = client.get_cr_step_result(result_sst)
log(f"MAC_AGENT_BOOTSTRAP: santa_sync_trigger OK — synced={step_sst.get('synced')}")

log("MAC_AGENT_BOOTSTRAP: all CRs passed")
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): extend MAC_AGENT_BOOTSTRAP with profiles, homebrew, and Santa commands"
```

---

## Task 9: macOS Dedicated Host runbook (documentation)

This is documentation for the one-time mac2.metal setup. No code.

**Files:**
- Create: `docs/runbooks/mac-smoke-dedicated-host-setup.md`

- [ ] **Step 1: Write the runbook**

```markdown
# macOS Smoke Dedicated Host Setup

One-time setup to enable the MAC_AGENT_BOOTSTRAP smoke phase.

## Prerequisites

- AWS CLI configured with access to the Nexplane AWS account
- Nexplane backend running (for agent registration)

## Step 1: Allocate mac2.metal Dedicated Host

```bash
aws ec2 allocate-hosts \
  --instance-type mac2.metal \
  --availability-zone us-east-1a \
  --quantity 1 \
  --auto-placement on \
  --region us-east-1
```

Note the `HostId` (e.g. `h-0abc123def456789`). Store it:

```bash
aws ssm put-parameter \
  --name /nexplane/smoke/mac/dedicated-host-id \
  --value h-0abc123def456789 \
  --type String \
  --overwrite
```

## Step 2: Launch mac2.metal instance on the host

```bash
# Find the latest macOS AMI
AMI=$(aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=amzn-ec2-macos-*" "Name=architecture,Values=arm64_mac" \
  --query 'reverse(sort_by(Images, &CreationDate))[0].ImageId' \
  --output text)

# Launch on the dedicated host
INSTANCE=$(aws ec2 run-instances \
  --image-id $AMI \
  --instance-type mac2.metal \
  --placement "HostId=h-0abc123def456789" \
  --key-name your-key-pair \
  --security-group-ids sg-xxxxxxxx \
  --region us-east-1 \
  --query 'Instances[0].InstanceId' \
  --output text)

echo "Instance: $INSTANCE"
```

Wait ~15 minutes for the instance to boot (mac2.metal has a slow first boot).

## Step 3: Install Nexplane agent

```bash
PUBLIC_IP=$(aws ec2 describe-instances --instance-ids $INSTANCE \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

# Get agent version
VERSION=$(curl -sf https://nexplane-agent-downloads.s3.amazonaws.com/version-darwin-arm64)
curl -sf -o /tmp/nexplane-agent "https://nexplane-agent-downloads.s3.amazonaws.com/nexplane-agent-darwin-arm64-$VERSION"

scp -i ~/.ssh/your-key.pem /tmp/nexplane-agent ec2-user@$PUBLIC_IP:~/nexplane-agent
ssh -i ~/.ssh/your-key.pem ec2-user@$PUBLIC_IP "chmod +x ~/nexplane-agent && sudo ~/nexplane-agent install --backend-url https://your-nexplane-instance --token your-token"
```

## Step 4: Snapshot as AMI

Once the agent is registered, create the AMI:

```bash
AMI_ID=$(aws ec2 create-image \
  --instance-id $INSTANCE \
  --name "nexplane-smoke-mac-arm64-$(date +%Y%m%d)" \
  --no-reboot \
  --query 'ImageId' --output text)

# Wait for AMI to be available (~10-20 min)
aws ec2 wait image-available --image-ids $AMI_ID

# Store in SSM
aws ssm put-parameter \
  --name /nexplane/smoke-amis/mac/arm64/base \
  --value $AMI_ID \
  --type String \
  --overwrite
```

## Step 5: Run MAC_AGENT_BOOTSTRAP to verify

```bash
python tests/smoke/test_aws_live.py \
  --phases MAC_AGENT_BOOTSTRAP \
  --dedicated-host-id h-0abc123def456789
```

## Cost notes

- Dedicated Host: ~$0.906/hr ($21.74/day) ongoing
- Run MAC_AGENT_BOOTSTRAP nightly, not per-commit
- Terminate instances after each run; keep the host allocated
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/mac-smoke-dedicated-host-setup.md
git commit -m "docs: add mac2.metal smoke dedicated host setup runbook"
```
