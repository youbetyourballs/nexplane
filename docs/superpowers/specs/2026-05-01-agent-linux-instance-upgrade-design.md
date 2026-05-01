# Agent: Linux Instance Upgrade — Design Spec (Spec 5e)

**Date:** 2026-05-01
**Status:** Approved
**Scope:** New agent command `upgrade_linux_instance` with two execution paths — in-place kernel/package upgrade with snapshot-based rollback, and containerize-and-migrate for major OS version upgrades. Linux-only. Becomes a catalog action in `nexplane_agent_mock.json`.

---

## Design Decisions

- **Privilege model:** Agent must run as root (requirement A). Upfront capability preflight before any state changes.
- **Rollback contract:** Execute captures a snapshot before changes. Rollback reads snapshot from merged params and restores prior state.
- **Path A (in-place):** Safe for patch-level and minor upgrades. Uses cloud snapshot (AWS/Azure) or local `dd` for disk backup. Always captures pre-upgrade snapshot before touching packages.
- **Path B (containerize-and-migrate):** For major OS version changes where in-place upgrade is not supported. Composes existing agent commands (`containerize_workload`, `virtualize_for_migration`, `upload_image`). Rollback is destroy-new + restore-original.
- **Health verification:** Both paths include post-upgrade health checks (service restart, HTTP probe on known ports, systemd unit status) before marking the job successful.
- **Reboot handling:** If a kernel upgrade occurred, the command schedules a reboot (via `systemctl reboot --when=+1` for a 1-minute delay by default) and returns `reboot_required: true` in the result. The operator can cancel the reboot within the delay window.

---

## Section 1: `upgrade_linux_instance`

**Parameters:**
- `path` (string, required) — `inplace` or `containerize`
- `snapshot_method` (string, default `cloud`) — `cloud` (AWS/Azure snapshot API via configured connector) or `dd` (local disk image to a target path); used for Path A
- `snapshot_target_path` (string, required if `snapshot_method=dd`) — destination path for the disk image (e.g. external mount)
- `upgrade_type` (string, default `security`) — `security` (security patches only), `packages` (all package upgrades), `dist` (major OS version upgrade via `do-release-upgrade` or `dnf system-upgrade`)
- `reboot_delay_seconds` (int, default 60) — seconds to wait before scheduled reboot (0 = immediate; only applies if reboot is needed)
- `health_check_url` (string, optional) — HTTP URL to probe post-upgrade to verify application health
- `health_check_timeout_seconds` (int, default 30) — seconds to wait for health check response
- **Path B only:**
  - `container_registry` (string, required for `path=containerize`) — registry URL to push the container image
  - `new_instance_type` (string, optional) — cloud instance type for the new VM (e.g. `t3.medium`)
  - `target_os` (string, required for `path=containerize`) — target OS image for the new instance (e.g. `ubuntu-24.04-lts`)

**Preflight:**
- Checks caller is root
- For `path=inplace`:
  - Checks package manager: detects `apt`/`apt-get` (Debian/Ubuntu) or `dnf`/`yum` (RHEL/CentOS)
  - For `upgrade_type=dist`: checks `do-release-upgrade` (Ubuntu) or `dnf system-upgrade` (Fedora/RHEL) is available
  - For `snapshot_method=cloud`: checks cloud connector credentials are configured and snapshot API is reachable
  - For `snapshot_method=dd`: checks target path has sufficient space
- For `path=containerize`:
  - Checks `containerize_workload`, `virtualize_for_migration`, and `upload_image` commands are available in the executor
  - Checks cloud connector credentials for the configured provider

**Execute (Path A — In-place):**
1. Snapshot:
   - For `snapshot_method=cloud`: call cloud connector snapshot API; store snapshot ID and region in result
   - For `snapshot_method=dd`: run `dd if=/dev/sda of={target_path} bs=4M status=progress` (or detected root device); store target path in result
   - Also record: kernel version (`uname -r`), package manager lock status, list of installed packages with versions
2. Update package index: `apt-get update -y` or `dnf check-update`
3. Run upgrade based on `upgrade_type`:
   - `security`: `apt-get install --only-upgrade $(apt-get --just-print upgrade 2>&1 | awk '/^Inst/{print $2}' | xargs apt-cache show 2>/dev/null | grep -B5 "Origin: Ubuntu\|Priority: important" | awk '/^Package:/{print $2}')` or `dnf update --security -y`
   - `packages`: `apt-get upgrade -y` or `dnf upgrade -y`
   - `dist`: `do-release-upgrade -f DistUpgradeViewNonInteractive` (Ubuntu) or `dnf system-upgrade download --releasever={next}` + `dnf system-upgrade reboot`
4. Determine if kernel changed: compare `uname -r` before vs after; set `kernel_upgraded = true` if changed
5. If `kernel_upgraded`: schedule reboot via `systemd-run --on-active={reboot_delay_seconds}s systemctl reboot`
6. Run health checks:
   - `systemctl --failed` — check for any newly failed units
   - If `health_check_url` supplied: HTTP GET probe with timeout
7. Return result including `kernel_upgraded`, `reboot_required`, `packages_upgraded_count`, `snapshot`

**Execute (Path B — Containerize and Migrate):**
1. Run `containerize_workload` command to produce a container image of the running application
2. Run `virtualize_for_migration` to build a new instance image using `target_os`
3. Run `upload_image` to push the new image to `container_registry` and provision the new instance in the cloud
4. Validate application health on the new instance (`health_check_url` probe)
5. If health check passes: return success with new instance ID; old instance remains running for operator to decommission
6. If health check fails: automatically run rollback (destroy new instance)
7. Snapshot: record old instance ID, new instance ID (if created), container registry tag

**Result (Path A):** `{ path, upgrade_type, packages_upgraded_count, kernel_upgraded, reboot_required, snapshot_method, snapshot_id (cloud) or snapshot_path (dd), snapshot }`

**Result (Path B):** `{ path, new_instance_id, container_image_tag, health_check_passed, old_instance_id, snapshot }`

**Rollback (Path A):**
- For `snapshot_method=cloud`: call cloud connector to restore instance from snapshot ID
- For `snapshot_method=dd`: write disk image back (`dd if={snapshot_path} of=/dev/sda bs=4M`) — requires booting from rescue media; return warning that this requires external boot
- Note: package-level rollback (individual package downgrades) is not supported; only full snapshot restore

**Rollback (Path B):**
- Destroy the new instance via cloud connector
- Old instance remains intact (never modified during Path B)
- Delete the pushed container image from registry if desired (optional cleanup)

**Catalog entry:**
```json
{
  "action_id": "upgrade_linux_instance",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server"],
  "rollback_action": "upgrade_linux_instance",
  "safety_notes": [
    "Always take a cloud snapshot before running — dd-based rollback requires booting from rescue media",
    "dist upgrade is potentially destructive; test on a non-production instance first",
    "Path B leaves the old instance running — operator must decommission it after validating the new instance",
    "Reboot is scheduled with a delay; cancel with 'systemctl stop systemd-run@*.service' within the delay window"
  ]
}
```

---

## Section 2: New Files

| File | Purpose |
|------|---------|
| `agent/commands/linuxupgrade/linuxupgrade.go` | Package entry: `Execute`/`Rollback` dispatch |
| `agent/commands/linuxupgrade/upgrade_linux.go` | `upgrade_linux_instance` implementation |
| `agent/commands/linuxupgrade/linuxupgrade_test.go` | Validation tests (param validation — no build tag needed) |

## Section 3: Modified Files

| File | Change |
|------|--------|
| `agent/executor/executor.go` | Add `upgrade_linux_instance` to `commands` and `rollbacks` maps |
| `backend/app/connectors/catalog/nexplane_agent_mock.json` | Add 1 catalog entry |
| `backend/app/connectors/executors/nexplane_agent_mock/` | Add 1 mock executor stub |
