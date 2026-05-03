# Patch Management — Design Spec

**Date:** 2026-05-03
**Status:** Draft
**Scope:** Linux security patching, Windows KB/Windows Update patching, patch compliance baseline enforcement, and fleet-wide emergency patch campaigns. Covers agent commands (Go), connector actions (Python), change type definitions (JSON), and the AI planner integration.

---

## Background

Patch management is the single most common daily sysadmin task and is completely absent from Nexplane today. The platform already has:

- A Go agent with command packages for OS hardening (`ossecurity`, `winharden`, `linuxupgrade`, etc.) and a cross-platform software inventory (`audit_software_inventory`).
- A connector/executor system in Python under `backend/app/connectors/executors/` that orchestrates multi-step change plans.
- Change type definitions (JSON) that the AI planner uses to generate change requests.
- An asset inventory populated by agent discovery, including installed package metadata.

None of these have a patch-specific workflow: there is no way to apply security-only apt/yum updates, deploy a Windows KB, check patch age across a fleet, or fan out a CVE remediation across hundreds of hosts. This spec adds all four capabilities.

---

## Design Decisions

- **Execution in the agent, orchestration in connectors.** All system-level operations (running `apt-get`, `dnf`, `wusa.exe`, querying pending reboots) stay in Go agent commands. Python connector actions handle multi-host orchestration, compliance evaluation, CVE→host mapping, and change-request generation. This mirrors every existing capability in the platform.

- **Two change type names: `patch_packages` and `patch_campaign`.** `patch_packages` covers targeted patching of one host or a small explicit list (security-only update, specific package, or specific KB). `patch_campaign` covers fleet-wide CVE-driven remediation with rolling batches. A single name would mix two very different parameter shapes and UI experiences.

- **New agent package `winpatch`, not extending `winharden`.** `winharden` is for configuration hardening (registry, LAPS, AppLocker). Windows Update is a separate operational domain with its own result structure (KB numbers, reboot-pending state, scheduled-reboot time). A dedicated `winpatch` package keeps both packages focused and avoids cluttering `winharden` with state that is irrelevant to hardening.

- **CVE-to-host mapping uses existing asset metadata + software inventory.** The agent already runs `audit_software_inventory` (the `crossplatform` package), which stores installed package names and versions in asset metadata. The `patch_campaign` connector action queries this metadata to find hosts with a vulnerable package version, rather than calling an external vulnerability database. This keeps the feature self-contained. A future enhancement can add an NVD/OSV enrichment layer.

- **Dry-run support via a `dry_run: true` param.** Both Linux and Windows agent commands accept a `dry_run` boolean. When true, they report what would change without making any modifications. This follows the pattern used by `linuxupgrade`.

- **Reboot scheduling via maintenance windows stored in asset tags.** Rather than a new database table for MVP, Windows reboot scheduling uses an asset tag `maintenance_window` (cron expression, e.g. `"0 2 * * 0"`) already stored in asset metadata. The agent command reads this tag and defers the reboot using the Windows Task Scheduler. If no tag is present, reboot is deferred to the next available window or skipped if `defer_reboot: true` is set.

---

## Section 1: Linux Security Patching — Agent Command

**New package:** `agent/commands/linuxpatch/`

### Files

| File | Purpose |
|------|---------|
| `agent/commands/linuxpatch/linuxpatch.go` | Platform-agnostic entry points and param validation |
| `agent/commands/linuxpatch/linuxpatch_linux.go` | `apt`/`yum`/`dnf` execution |
| `agent/commands/linuxpatch/linuxpatch_other.go` | Stub returning `errNotSupported` on non-Linux |
| `agent/commands/linuxpatch/linuxpatch_test.go` | Unit tests |

### `agent/commands/linuxpatch/linuxpatch.go`

```go
package linuxpatch

import "fmt"

// valid values for the "mode" param
var validModes = map[string]bool{
    "security_only": true, // apply all available security updates
    "package":       true, // patch a specific named package
    "cve":           true, // patch the package(s) that fix a given CVE ID
}

// ApplyLinuxPatchesExecute applies security patches on the host.
//
// Params:
//   mode         string   required  "security_only" | "package" | "cve"
//   package_name string   optional  required when mode=package
//   cve_id       string   optional  required when mode=cve (e.g. "CVE-2024-3094")
//   dry_run      bool     optional  default false — report changes without applying
//
// Result keys:
//   packages_updated  []PackageDiff   list of {name, version_before, version_after}
//   reboot_required   bool
//   dry_run           bool
func ApplyLinuxPatchesExecute(params map[string]any) (map[string]any, error) {
    mode, _ := params["mode"].(string)
    if !validModes[mode] {
        return nil, fmt.Errorf("mode must be security_only, package, or cve; got %q", mode)
    }
    if mode == "package" {
        if pkg, _ := params["package_name"].(string); pkg == "" {
            return nil, fmt.Errorf("package_name is required when mode=package")
        }
    }
    if mode == "cve" {
        if cve, _ := params["cve_id"].(string); cve == "" {
            return nil, fmt.Errorf("cve_id is required when mode=cve")
        }
    }
    return applyPatchesOS(params)
}

// ApplyLinuxPatchesRollback downgrades packages to the versions captured in
// the execute result (packages_updated[].version_before). Only possible when
// the package manager retains old packages in its cache.
func ApplyLinuxPatchesRollback(params map[string]any) (map[string]any, error) {
    return rollbackPatchesOS(params)
}

// AuditLinuxPatchStatusExecute returns the current patch state without making
// any changes. Used by the compliance baseline and campaign discovery steps.
//
// Result keys:
//   installed_packages  []PackageInfo   {name, version, arch}
//   security_updates_available  []PackageUpdate   {name, current_version, available_version, advisories []string}
//   days_since_last_security_update  int
//   reboot_required  bool
func AuditLinuxPatchStatusExecute(params map[string]any) (map[string]any, error) {
    return auditPatchStatusOS(params)
}
```

### `agent/commands/linuxpatch/linuxpatch_linux.go` (key functions)

```go
package linuxpatch

import (
    "bufio"
    "fmt"
    "os/exec"
    "strings"
    "time"
)

// detectPackageManager returns "apt", "dnf", or "yum" based on what is available.
func detectPackageManager() (string, error) {
    for _, pm := range []string{"apt-get", "dnf", "yum"} {
        if path, err := exec.LookPath(pm); err == nil && path != "" {
            if pm == "apt-get" {
                return "apt", nil
            }
            return pm, nil
        }
    }
    return "", fmt.Errorf("no supported package manager found (apt-get, dnf, yum)")
}

func applyPatchesOS(params map[string]any) (map[string]any, error) {
    dryRun, _ := params["dry_run"].(bool)
    pm, err := detectPackageManager()
    if err != nil {
        return nil, err
    }

    // Snapshot pre-patch versions for rollback/diff
    before, err := listInstalledPackages(pm)
    if err != nil {
        return nil, fmt.Errorf("pre-patch inventory failed: %w", err)
    }

    if dryRun {
        available, err := listSecurityUpdates(pm, params)
        if err != nil {
            return nil, err
        }
        return map[string]any{
            "dry_run":          true,
            "packages_updated": available,
            "reboot_required":  false,
        }, nil
    }

    // Execute patch
    if err := runPatch(pm, params); err != nil {
        return nil, err
    }

    after, err := listInstalledPackages(pm)
    if err != nil {
        return nil, fmt.Errorf("post-patch inventory failed: %w", err)
    }

    diff := diffPackages(before, after)
    reboot := checkRebootRequired()

    return map[string]any{
        "dry_run":          false,
        "packages_updated": diff,
        "reboot_required":  reboot,
        "patched_at":       time.Now().UTC().Format(time.RFC3339),
    }, nil
}

// runPatch invokes the package manager with the appropriate arguments.
// For mode=security_only: apt-get -y --only-upgrade install $(apt-get -s dist-upgrade | grep "^Inst" | grep -i secur)
//                         dnf -y --security upgrade
// For mode=package:       apt-get -y install <pkg> / dnf -y upgrade <pkg>
// For mode=cve:           dnf -y --cve <cve_id> upgrade  (DNF only; apt: resolve via changelog grep)
func runPatch(pm string, params map[string]any) error { /* ... */ return nil }

// checkRebootRequired checks /var/run/reboot-required (Debian/Ubuntu) or
// the output of "needs-restarting -r" (RHEL/CentOS 7+).
func checkRebootRequired() bool { /* ... */ return false }
```

### Executor Registration (`agent/executor/executor.go` additions)

```go
import "nexplane-agent/commands/linuxpatch"

// In commands map:
"apply_linux_patches":     linuxpatch.ApplyLinuxPatchesExecute,
"audit_linux_patch_status": linuxpatch.AuditLinuxPatchStatusExecute,

// In rollbacks map:
"apply_linux_patches": linuxpatch.ApplyLinuxPatchesRollback,
```

---

## Section 2: Windows Security Patching — Agent Command

**New package:** `agent/commands/winpatch/`

### Files

| File | Purpose |
|------|---------|
| `agent/commands/winpatch/winpatch.go` | Entry points and validation |
| `agent/commands/winpatch/winpatch_windows.go` | PowerShell/WUA COM execution |
| `agent/commands/winpatch/winpatch_other.go` | Non-Windows stub |
| `agent/commands/winpatch/winpatch_test.go` | Unit tests |

### `agent/commands/winpatch/winpatch.go`

```go
package winpatch

import "fmt"

var validModes = map[string]bool{
    "security_only": true, // install all available security updates
    "kb":            true, // install a specific KB article
}

// ApplyWindowsPatchesExecute installs Windows Updates on the host.
//
// Params:
//   mode            string   required  "security_only" | "kb"
//   kb_id           string   optional  required when mode=kb (e.g. "KB5034441")
//   dry_run         bool     optional  default false
//   defer_reboot    bool     optional  default false — do not reboot even if required
//   reboot_window   string   optional  cron expression; overrides asset tag if present
//
// Result keys:
//   updates_installed  []KBResult   {kb_id, title, size_bytes, installed_at}
//   reboot_required    bool
//   reboot_scheduled   string   RFC3339 time of scheduled reboot, or "" if not scheduled
//   dry_run            bool
func ApplyWindowsPatchesExecute(params map[string]any) (map[string]any, error) {
    mode, _ := params["mode"].(string)
    if !validModes[mode] {
        return nil, fmt.Errorf("mode must be security_only or kb; got %q", mode)
    }
    if mode == "kb" {
        if kb, _ := params["kb_id"].(string); kb == "" {
            return nil, fmt.Errorf("kb_id is required when mode=kb")
        }
    }
    return applyPatchesOS(params)
}

// ApplyWindowsPatchesRollback uninstalls the KBs that were installed during
// execute, using wusa.exe /uninstall /kb:<number> /quiet /norestart.
func ApplyWindowsPatchesRollback(params map[string]any) (map[string]any, error) {
    return rollbackPatchesOS(params)
}

// AuditWindowsPatchStatusExecute returns installed KBs, pending updates,
// and reboot-pending state without making changes.
//
// Result keys:
//   installed_kbs              []string   e.g. ["KB5034441", "KB5032189"]
//   security_updates_pending   []KBInfo   {kb_id, title, severity, cve_ids []string}
//   last_update_check          string     RFC3339
//   reboot_pending             bool
func AuditWindowsPatchStatusExecute(params map[string]any) (map[string]any, error) {
    return auditPatchStatusOS(params)
}
```

### `agent/commands/winpatch/winpatch_windows.go` (key logic)

```go
package winpatch

// applyPatchesOS invokes a PowerShell script embedded as a string constant.
// The script uses the Windows Update Agent (WUA) COM API:
//   $Session   = New-Object -ComObject Microsoft.Update.Session
//   $Searcher  = $Session.CreateUpdateSearcher()
//   $Results   = $Searcher.Search("IsInstalled=0 and Type='Software' and BrowseOnly=0")
// For mode=security_only it filters: IsAssigned=1 and AutoSelectOnWebSites=1
// For mode=kb it filters by KBArticleID.
// Installation uses $Installer = $Session.CreateUpdateInstaller().
// Reboot scheduling: if reboot_required and not defer_reboot, creates a
// Windows scheduled task via schtasks.exe targeting the next maintenance window.
```

### Executor Registration (`agent/executor/executor.go` additions)

```go
import "nexplane-agent/commands/winpatch"

// In commands map:
"apply_windows_patches":      winpatch.ApplyWindowsPatchesExecute,
"audit_windows_patch_status": winpatch.AuditWindowsPatchStatusExecute,

// In rollbacks map:
"apply_windows_patches": winpatch.ApplyWindowsPatchesRollback,
```

---

## Section 3: Patch Compliance Baseline — Connector Action

This capability defines a compliance rule ("all production hosts must have security patches applied within N days") and detects violations, optionally generating a change request to remediate them.

### Change Type Definition

**New file:** `backend/app/connectors/change_type_definitions/patch_packages.json`

```json
{
  "change_type": "patch_packages",
  "display_name": "Patch Packages",
  "steps": [
    {"generic_action": "audit_patch_status",   "purpose": "preflight_validate", "required": true},
    {"generic_action": "apply_patches",        "purpose": "execute",            "required": true},
    {"generic_action": "verify_patch_status",  "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["agent_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["package_version_check", "output_check"],
  "parameters": {
    "os_family":      {"type": "string",  "enum": ["linux", "windows"],    "required": true},
    "mode":           {"type": "string",  "enum": ["security_only", "package", "kb", "cve"], "required": true},
    "package_name":   {"type": "string",  "required": false},
    "cve_id":         {"type": "string",  "required": false},
    "kb_id":          {"type": "string",  "required": false},
    "dry_run":        {"type": "boolean", "default": false},
    "defer_reboot":   {"type": "boolean", "default": false}
  }
}
```

### Connector Executor: Compliance Baseline

**New file:** `backend/app/connectors/executors/nexplane_agent/check_patch_compliance.py`

```python
"""
Discovers all assets that violate a patch age or version baseline and
optionally emits change requests to remediate them.
"""
from __future__ import annotations
from typing import Any
from datetime import datetime, timedelta, timezone


async def execute(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Params:
        max_patch_age_days   int      default 30   — flag hosts whose last
                                                     security update is older
        asset_filter         dict     optional     — e.g. {"tags": {"env": "prod"}}
        os_family            str      optional     — "linux" | "windows" | None (both)
        create_change_request bool    default False — auto-create a patch_packages
                                                      CR for each violating host

    Returns:
        compliant_hosts      list[str]   asset IDs that pass
        non_compliant_hosts  list[dict]  [{asset_id, hostname, os_family,
                                           days_since_patch, change_request_id?}]
        total_assets_checked int
    """
    max_age = int(params.get("max_patch_age_days", 30))
    asset_filter = params.get("asset_filter", {})
    os_family = params.get("os_family")
    auto_cr = bool(params.get("create_change_request", False))

    assets = await context.asset_repository.list_assets(filter=asset_filter)

    compliant: list[str] = []
    non_compliant: list[dict] = []
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age)

    for asset in assets:
        meta = asset.metadata or {}
        af = meta.get("os_family")
        if os_family and af != os_family:
            continue

        last_patched_raw = meta.get("last_security_patch_at")
        if not last_patched_raw:
            # No patch data → treat as non-compliant
            days_since = None
        else:
            last_patched = datetime.fromisoformat(last_patched_raw)
            days_since = (datetime.now(tz=timezone.utc) - last_patched).days

        if days_since is not None and days_since <= max_age:
            compliant.append(asset.id)
            continue

        entry: dict[str, Any] = {
            "asset_id": asset.id,
            "hostname": asset.hostname,
            "os_family": af,
            "days_since_patch": days_since,
        }

        if auto_cr:
            cr = await context.change_request_service.create(
                change_type="patch_packages",
                asset_id=asset.id,
                params={
                    "os_family": af,
                    "mode": "security_only",
                    "dry_run": False,
                },
                title=f"Security patch compliance: {asset.hostname}",
                description=(
                    f"Host is {days_since or 'unknown'} days behind on security patches "
                    f"(threshold: {max_age} days)."
                ),
            )
            entry["change_request_id"] = cr.id

        non_compliant.append(entry)

    return {
        "compliant_hosts": compliant,
        "non_compliant_hosts": non_compliant,
        "total_assets_checked": len(compliant) + len(non_compliant),
    }
```

### Asset Metadata Contract

The agent's `audit_linux_patch_status` and `audit_windows_patch_status` commands store the following keys into asset metadata after each audit run (via the existing agent→control-plane metadata push):

| Key | Type | Example |
|-----|------|---------|
| `last_security_patch_at` | RFC3339 string | `"2026-04-01T02:15:00Z"` |
| `reboot_pending` | bool | `true` |
| `security_updates_available_count` | int | `7` |
| `os_family` | string | `"linux"` \| `"windows"` |

---

## Section 4: Emergency Patch Campaign — Connector Action + Change Type

A campaign fans out patching of a CVE or named package across the entire affected fleet in rolling batches.

### Change Type Definition

**New file:** `backend/app/connectors/change_type_definitions/patch_campaign.json`

```json
{
  "change_type": "patch_campaign",
  "display_name": "Emergency Patch Campaign",
  "steps": [
    {"generic_action": "identify_affected_assets", "purpose": "preflight_validate", "required": true},
    {"generic_action": "apply_campaign_patches",   "purpose": "execute",            "required": true},
    {"generic_action": "verify_campaign_results",  "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["asset_exists", "no_concurrent_changes"],
  "verification_methods": ["package_version_check"],
  "parameters": {
    "cve_id":              {"type": "string",  "required": false, "description": "e.g. CVE-2024-3094"},
    "package_name":        {"type": "string",  "required": false, "description": "e.g. xz-utils"},
    "affected_version_lt": {"type": "string",  "required": false, "description": "patch all hosts running versions < this"},
    "asset_filter":        {"type": "object",  "required": false},
    "batch_size":          {"type": "integer", "default": 10,    "description": "hosts patched in parallel per wave"},
    "abort_error_threshold": {"type": "number", "default": 0.2,  "description": "abort campaign if failure rate exceeds this fraction"},
    "dry_run":             {"type": "boolean", "default": false},
    "defer_reboot":        {"type": "boolean", "default": false}
  }
}
```

### Connector Executor: Campaign Orchestration

**New file:** `backend/app/connectors/executors/nexplane_agent/run_patch_campaign.py`

```python
"""
Identifies all assets affected by a CVE or package vulnerability and applies
patches in rolling batches, aborting if the error rate exceeds the threshold.
"""
from __future__ import annotations
import asyncio
from typing import Any


async def execute(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Params: see patch_campaign.json parameter schema.

    Returns:
        affected_assets     int
        batches_completed   int
        hosts_patched       list[str]   asset IDs successfully patched
        hosts_failed        list[dict]  [{asset_id, hostname, error}]
        aborted             bool
        abort_reason        str | None
    """
    cve_id = params.get("cve_id")
    package_name = params.get("package_name")
    affected_version_lt = params.get("affected_version_lt")
    asset_filter = params.get("asset_filter", {})
    batch_size = int(params.get("batch_size", 10))
    abort_threshold = float(params.get("abort_error_threshold", 0.2))
    dry_run = bool(params.get("dry_run", False))
    defer_reboot = bool(params.get("defer_reboot", False))

    if not cve_id and not package_name:
        raise ValueError("At least one of cve_id or package_name is required")

    # 1. Find affected hosts using asset metadata software inventory
    affected = await _find_affected_assets(
        context, asset_filter, package_name, affected_version_lt, cve_id
    )

    if not affected:
        return {
            "affected_assets": 0,
            "batches_completed": 0,
            "hosts_patched": [],
            "hosts_failed": [],
            "aborted": False,
            "abort_reason": None,
        }

    # 2. Patch in batches
    patched: list[str] = []
    failed: list[dict] = []
    aborted = False
    abort_reason: str | None = None

    batches = [affected[i:i + batch_size] for i in range(0, len(affected), batch_size)]

    for batch_num, batch in enumerate(batches):
        results = await asyncio.gather(
            *[_patch_single_host(context, asset, params, dry_run, defer_reboot) for asset in batch],
            return_exceptions=True,
        )

        for asset, result in zip(batch, results):
            if isinstance(result, Exception):
                failed.append({"asset_id": asset["id"], "hostname": asset["hostname"], "error": str(result)})
            else:
                patched.append(asset["id"])

        total_attempted = len(patched) + len(failed)
        if total_attempted > 0 and len(failed) / total_attempted > abort_threshold:
            aborted = True
            abort_reason = (
                f"Error rate {len(failed)/total_attempted:.0%} exceeded threshold "
                f"{abort_threshold:.0%} after batch {batch_num + 1}"
            )
            break

    return {
        "affected_assets": len(affected),
        "batches_completed": batch_num + 1 if not aborted else batch_num,
        "hosts_patched": patched,
        "hosts_failed": failed,
        "aborted": aborted,
        "abort_reason": abort_reason,
    }


async def _find_affected_assets(
    context: Any,
    asset_filter: dict,
    package_name: str | None,
    affected_version_lt: str | None,
    cve_id: str | None,
) -> list[dict]:
    """
    Query asset metadata for installed software inventory.
    The inventory is populated by the agent's audit_software_inventory command
    and stored as asset.metadata["software_inventory"]:
      [{"name": "xz-utils", "version": "5.4.1", "arch": "amd64"}, ...]

    Filters:
      - If package_name is set: include assets that have that package installed.
      - If affected_version_lt is set: further filter to versions < that string
        using packaging.version.Version comparison.
      - If cve_id is set (and no package_name): search advisories stored in
        metadata["security_advisories"]: [{"cve_id": "CVE-...", "package": "..."}]
    """
    assets = await context.asset_repository.list_assets(filter=asset_filter)
    affected = []
    for asset in assets:
        meta = asset.metadata or {}
        inventory = meta.get("software_inventory", [])
        advisories = meta.get("security_advisories", [])

        pkg_to_check = package_name
        if not pkg_to_check and cve_id:
            # Find the package name from stored advisories
            for adv in advisories:
                if adv.get("cve_id") == cve_id:
                    pkg_to_check = adv.get("package")
                    break

        if not pkg_to_check:
            continue

        for item in inventory:
            if item.get("name") != pkg_to_check:
                continue
            if affected_version_lt:
                from packaging.version import Version  # type: ignore
                try:
                    if not (Version(item["version"]) < Version(affected_version_lt)):
                        continue
                except Exception:
                    pass  # unparseable version — include conservatively
            affected.append({
                "id": asset.id,
                "hostname": asset.hostname,
                "os_family": meta.get("os_family", "linux"),
                "package_name": pkg_to_check,
                "installed_version": item.get("version"),
            })
            break  # one match per asset is enough

    return affected


async def _patch_single_host(
    context: Any,
    asset: dict,
    params: dict,
    dry_run: bool,
    defer_reboot: bool,
) -> None:
    """
    Dispatches an agent command to patch a single host.
    Raises on failure so gather() can capture it as an exception.
    """
    os_family = asset["os_family"]
    if os_family == "windows":
        command = "apply_windows_patches"
        cmd_params: dict[str, Any] = {
            "mode": "security_only",
            "dry_run": dry_run,
            "defer_reboot": defer_reboot,
        }
        cve_id = params.get("cve_id")
        if cve_id:
            # Windows Update does not support CVE-targeted searches natively;
            # fall back to security_only and note the CVE in the description.
            cmd_params["note"] = f"Campaign targeting {cve_id}"
    else:
        command = "apply_linux_patches"
        cve_id = params.get("cve_id")
        pkg = params.get("package_name") or asset.get("package_name")
        if cve_id:
            cmd_params = {"mode": "cve", "cve_id": cve_id, "dry_run": dry_run}
        elif pkg:
            cmd_params = {"mode": "package", "package_name": pkg, "dry_run": dry_run}
        else:
            cmd_params = {"mode": "security_only", "dry_run": dry_run}

    result = await context.agent_client.run_command(
        asset_id=asset["id"],
        command=command,
        params=cmd_params,
        timeout_seconds=600,
    )
    if result.get("status") != "completed":
        raise RuntimeError(result.get("error", "unknown agent error"))
```

---

## Section 5: AI Planner Integration

The AI planner reads change type definition JSON files to understand available change types. No planner code changes are needed — adding the two JSON files is sufficient.

For the planner to produce good `patch_packages` plans, add a `description` block to the JSON (supported by the planner's schema reader):

```json
{
  "change_type": "patch_packages",
  "display_name": "Patch Packages",
  "description": "Apply security patches to a Linux or Windows host. Use mode=security_only for all available security updates, mode=package to update a specific package, mode=cve to patch the package that fixes a given CVE, or mode=kb to install a specific Windows KB article.",
  ...
}
```

```json
{
  "change_type": "patch_campaign",
  "display_name": "Emergency Patch Campaign",
  "description": "Fan out security patching across all hosts affected by a CVE or vulnerable package version. Patches are applied in rolling batches; the campaign aborts if the failure rate exceeds the configured threshold.",
  ...
}
```

---

## Section 6: Frontend Considerations

No new pages are required for MVP. Patch management surfaces through the existing change request UI:

- The **New Change Request** modal will offer `Patch Packages` and `Emergency Patch Campaign` as change types via the existing type picker.
- The change request detail view already renders step results as JSON; the `packages_updated` / `updates_installed` arrays will be human-readable without additional work.
- A future iteration can add a dedicated **Patch Compliance** dashboard showing the output of `check_patch_compliance` over time.

---

## Files Changed

| File | Change |
|------|--------|
| `agent/commands/linuxpatch/linuxpatch.go` | New — param validation entry points |
| `agent/commands/linuxpatch/linuxpatch_linux.go` | New — apt/yum/dnf execution |
| `agent/commands/linuxpatch/linuxpatch_other.go` | New — non-Linux stub |
| `agent/commands/linuxpatch/linuxpatch_test.go` | New — unit tests |
| `agent/commands/winpatch/winpatch.go` | New — param validation entry points |
| `agent/commands/winpatch/winpatch_windows.go` | New — WUA COM / PowerShell execution |
| `agent/commands/winpatch/winpatch_other.go` | New — non-Windows stub |
| `agent/commands/winpatch/winpatch_test.go` | New — unit tests |
| `agent/executor/executor.go` | Add 4 commands to `commands` map + 2 rollbacks |
| `backend/app/connectors/change_type_definitions/patch_packages.json` | New — change type definition |
| `backend/app/connectors/change_type_definitions/patch_campaign.json` | New — change type definition |
| `backend/app/connectors/executors/nexplane_agent/check_patch_compliance.py` | New — compliance baseline connector action |
| `backend/app/connectors/executors/nexplane_agent/run_patch_campaign.py` | New — campaign orchestration connector action |
