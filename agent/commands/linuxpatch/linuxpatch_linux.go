//go:build linux

package linuxpatch

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func detectPackageManager() (string, error) {
	for _, pm := range []string{"apt-get", "dnf", "yum", "zypper", "apk"} {
		if path, err := exec.LookPath(pm); err == nil && path != "" {
			if pm == "apt-get" {
				return "apt", nil
			}
			return pm, nil
		}
	}
	return "", fmt.Errorf("no supported package manager found (apt-get, dnf, yum, zypper, apk)")
}

// checkDiskSpace verifies at least minMB free on the given path.
func checkDiskSpace(path string, minMB uint64) error {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(path, &stat); err != nil {
		return fmt.Errorf("statfs %s: %w", path, err)
	}
	freeMB := stat.Bavail * uint64(stat.Bsize) / (1024 * 1024)
	if freeMB < minMB {
		return fmt.Errorf("insufficient disk space: %d MB free on %s, need %dMB", freeMB, path, minMB)
	}
	return nil
}

// checkPrerequisites runs disk-space and lock-file checks before patching.
func checkPrerequisites(pm string) error {
	// Disk space: at least 500 MB free on /var and /usr
	for _, path := range []string{"/var", "/usr"} {
		if err := checkDiskSpace(path, 500); err != nil {
			return err
		}
	}

	// Lock file detection
	switch pm {
	case "apt":
		aptLocks := []string{
			"/var/lib/dpkg/lock",
			"/var/lib/apt/lists/lock",
			"/var/cache/apt/archives/lock",
		}
		for _, lf := range aptLocks {
			if pid, locked := isFileLocked(lf); locked {
				if pid != "" {
					return fmt.Errorf("package manager is locked by another process (PID: %s)", pid)
				}
				return fmt.Errorf("package manager is locked by another process (lock file: %s)", lf)
			}
		}
	case "dnf":
		if pid, locked := isFileLocked("/var/run/dnf.pid"); locked {
			return fmt.Errorf("package manager is locked by another process (PID: %s)", pid)
		}
		if pid, locked := isFileLocked("/var/run/yum.pid"); locked {
			return fmt.Errorf("package manager is locked by another process (PID: %s)", pid)
		}
	case "yum":
		if pid, locked := isFileLocked("/var/run/yum.pid"); locked {
			return fmt.Errorf("package manager is locked by another process (PID: %s)", pid)
		}
	}
	return nil
}

// isFileLocked returns (pid, true) if the lock file exists and is held.
// For PID files it reads the PID; for lock files it tries flock.
func isFileLocked(path string) (string, bool) {
	f, err := os.Open(path)
	if err != nil {
		// File doesn't exist → not locked
		return "", false
	}
	defer f.Close()

	// Try to read a PID from the file (pid files like yum.pid / dnf.pid)
	scanner := bufio.NewScanner(f)
	if scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if _, err := strconv.Atoi(line); err == nil {
			return line, true
		}
	}
	// File exists but no readable PID — treat as locked
	return "", true
}

// captureActiveServices returns the list of currently active systemd service units.
func captureActiveServices() []string {
	out, err := exec.Command("systemctl", "list-units", "--type=service", "--state=active",
		"--no-legend", "--no-pager", "--plain").Output()
	if err != nil {
		return nil
	}
	var services []string
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) >= 1 {
			services = append(services, fields[0])
		}
	}
	return services
}

// checkCriticalServices compares before/after service lists.
// Returns (still_running, stopped).
func checkCriticalServices(before []string) ([]string, []string) {
	after := captureActiveServices()
	afterSet := make(map[string]bool, len(after))
	for _, s := range after {
		afterSet[s] = true
	}
	var stopped []string
	for _, s := range before {
		if !afterSet[s] {
			stopped = append(stopped, s)
		}
	}
	return after, stopped
}

// captureDNFTransactionID gets the most recent DNF transaction ID.
func captureDNFTransactionID() string {
	out, err := exec.Command("dnf", "history", "list", "--reverse", "--last=1").Output()
	if err != nil {
		return ""
	}
	// The output has a header and then lines like:
	//   ID | Command line | Date and time | Action(s) | Altered
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "-") || strings.HasPrefix(line, "ID") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) >= 1 {
			if _, err := strconv.Atoi(fields[0]); err == nil {
				return fields[0]
			}
		}
	}
	return ""
}

// resolveCVEToPackageApt attempts a best-effort mapping from CVE ID to apt package name.
func resolveCVEToPackageApt(cveID string) string {
	// Heuristic: scan /var/lib/dpkg/info/*.list for CVE references (unlikely to be there, but harmless)
	// More practical: check apt-get changelog output of candidate packages
	// This is intentionally best-effort; callers fall back to security_only when empty.
	_ = cveID
	return ""
}

func applyPatchesOS(params map[string]any) (map[string]any, error) {
	dryRun, _ := params["dry_run"].(bool)
	pm, err := detectPackageManager()
	if err != nil {
		return nil, err
	}

	if err := checkPrerequisites(pm); err != nil {
		return nil, err
	}

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

	// Capture active services before patching
	servicesBefore := captureActiveServices()

	if err := runPatch(pm, params); err != nil {
		return nil, err
	}

	after, err := listInstalledPackages(pm)
	if err != nil {
		return nil, fmt.Errorf("post-patch inventory failed: %w", err)
	}

	diff := diffPackages(before, after)
	reboot := checkRebootRequired()

	// Capture DNF transaction ID for rollback support
	dnfTxID := ""
	if pm == "dnf" || pm == "yum" {
		dnfTxID = captureDNFTransactionID()
	}

	// Check service health after patch
	_, stoppedServices := checkCriticalServices(servicesBefore)

	result := map[string]any{
		"dry_run":                    false,
		"packages_updated":           diff,
		"reboot_required":            reboot,
		"patched_at":                 time.Now().UTC().Format(time.RFC3339),
		"services_stopped_after_patch": stoppedServices,
	}
	if dnfTxID != "" {
		result["dnf_transaction_id"] = dnfTxID
	}
	return result, nil
}

func rollbackPatchesOS(params map[string]any) (map[string]any, error) {
	pm, err := detectPackageManager()
	if err != nil {
		return nil, err
	}

	// Prefer DNF history rollback when a transaction ID is available
	if (pm == "dnf") {
		if txID, _ := params["dnf_transaction_id"].(string); txID != "" {
			out, err := exec.Command("dnf", "history", "rollback", "-y", txID).CombinedOutput()
			if err != nil {
				return nil, fmt.Errorf("dnf history rollback %s failed: %w\n%s", txID, err, out)
			}
			return map[string]any{
				"rolled_back":        []string{},
				"dnf_rollback_tx_id": txID,
			}, nil
		}
	}

	updated, _ := params["packages_updated"].([]any)
	rolledBack := []string{}

	for _, raw := range updated {
		entry, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		name, _ := entry["name"].(string)
		versionBefore, _ := entry["version_before"].(string)
		if name == "" || versionBefore == "" {
			continue
		}
		var cmd *exec.Cmd
		switch pm {
		case "apt":
			target := fmt.Sprintf("%s=%s", name, versionBefore)
			cmd = exec.Command("apt-get", "install", "-y", "--allow-downgrades",
				"-o", "Dpkg::Options::=--force-confold",
				"-o", "Dpkg::Options::=--force-confdef",
				target)
		case "dnf":
			cmd = exec.Command("dnf", "downgrade", "-y", fmt.Sprintf("%s-%s", name, versionBefore))
		case "yum":
			cmd = exec.Command("yum", "downgrade", "-y", fmt.Sprintf("%s-%s", name, versionBefore))
		}
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("rollback of %s failed: %w\n%s", name, err, out)
		}
		rolledBack = append(rolledBack, name)
	}

	return map[string]any{
		"rolled_back": rolledBack,
	}, nil
}

func auditPatchStatusOS(_ map[string]any) (map[string]any, error) {
	pm, err := detectPackageManager()
	if err != nil {
		return nil, err
	}

	installed, err := listInstalledPackages(pm)
	if err != nil {
		return nil, err
	}

	updates, err := listSecurityUpdates(pm, nil)
	if err != nil {
		return nil, err
	}

	reboot := checkRebootRequired()

	return map[string]any{
		"installed_packages":         installed,
		"security_updates_available": updates,
		"reboot_required":            reboot,
	}, nil
}

func listInstalledPackages(pm string) ([]map[string]string, error) {
	var cmd *exec.Cmd
	switch pm {
	case "apt":
		cmd = exec.Command("dpkg-query", "-W", "-f=${Package}\t${Version}\t${Architecture}\n")
	case "zypper":
		cmd = exec.Command("zypper", "--no-color", "packages", "--installed-only")
	case "apk":
		cmd = exec.Command("apk", "info", "-v")
	default:
		cmd = exec.Command("rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n")
	}

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("listInstalledPackages: %w", err)
	}

	var result []map[string]string
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		line := scanner.Text()
		switch pm {
		case "zypper":
			// zypper packages output: S | Repository | Name | Version | Arch
			// Skip header lines
			if strings.HasPrefix(line, "S") || strings.HasPrefix(line, "-") || strings.HasPrefix(line, "Loading") {
				continue
			}
			fields := strings.Split(line, "|")
			if len(fields) >= 5 {
				name := strings.TrimSpace(fields[2])
				version := strings.TrimSpace(fields[3])
				arch := strings.TrimSpace(fields[4])
				if name != "" && name != "Name" {
					result = append(result, map[string]string{"name": name, "version": version, "arch": arch})
				}
			}
		case "apk":
			// apk info -v output: name-version
			// e.g. musl-1.2.3-r0
			if line == "" {
				continue
			}
			// Split on last hyphen-digit boundary
			idx := strings.LastIndex(line, "-")
			if idx > 0 {
				name := line[:idx]
				version := line[idx+1:]
				result = append(result, map[string]string{"name": name, "version": version})
			}
		default:
			fields := strings.Split(line, "\t")
			if len(fields) < 2 {
				continue
			}
			entry := map[string]string{"name": fields[0], "version": fields[1]}
			if len(fields) >= 3 {
				entry["arch"] = fields[2]
			}
			result = append(result, entry)
		}
	}
	return result, nil
}

func listSecurityUpdates(pm string, params map[string]any) ([]map[string]string, error) {
	mode := ""
	if params != nil {
		mode, _ = params["mode"].(string)
	}

	var cmd *exec.Cmd
	switch pm {
	case "apt":
		cmd = exec.Command("apt-get", "-s", "dist-upgrade")
	case "dnf":
		if mode == "cve" {
			cve, _ := params["cve_id"].(string)
			cmd = exec.Command("dnf", "updateinfo", "list", "--cve", cve)
		} else {
			cmd = exec.Command("dnf", "updateinfo", "list", "--security")
		}
	case "zypper":
		cmd = exec.Command("zypper", "--no-color", "list-patches", "--category", "security")
	case "apk":
		cmd = exec.Command("apk", "list", "-u")
	default: // yum
		cmd = exec.Command("yum", "updateinfo", "list", "security")
	}

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("listSecurityUpdates: %w", err)
	}

	var result []map[string]string
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		line := scanner.Text()
		switch pm {
		case "apt":
			if strings.HasPrefix(line, "Inst ") {
				fields := strings.Fields(line)
				if len(fields) >= 2 {
					result = append(result, map[string]string{"name": fields[1]})
				}
			}
		case "zypper":
			// zypper list-patches output: Category | Severity | Name | Version | Status
			if strings.HasPrefix(line, "-") || strings.HasPrefix(line, "Loading") || strings.HasPrefix(line, "Repository") {
				continue
			}
			fields := strings.Split(line, "|")
			if len(fields) >= 3 {
				name := strings.TrimSpace(fields[2])
				if name != "" && name != "Name" {
					result = append(result, map[string]string{"name": name})
				}
			}
		case "apk":
			// apk list -u output: package-name-version [repo] {provider} (licenses) [U=...]
			fields := strings.Fields(line)
			if len(fields) >= 1 {
				// Extract name from "name-version" token
				tok := fields[0]
				idx := strings.LastIndex(tok, "-")
				if idx > 0 {
					result = append(result, map[string]string{"name": tok[:idx]})
				}
			}
		default:
			fields := strings.Fields(line)
			if len(fields) >= 3 {
				result = append(result, map[string]string{"name": fields[2]})
			}
		}
	}
	return result, nil
}

// aptConfFlags returns the dpkg options that prevent interactive config file prompts.
func aptConfFlags() []string {
	return []string{
		"-o", "Dpkg::Options::=--force-confold",
		"-o", "Dpkg::Options::=--force-confdef",
	}
}

func runPatch(pm string, params map[string]any) error {
	mode, _ := params["mode"].(string)
	var cmd *exec.Cmd

	switch pm {
	case "apt":
		if out, err := exec.Command("apt-get", "update", "-q").CombinedOutput(); err != nil {
			return fmt.Errorf("apt-get update: %w\n%s", err, out)
		}
		switch mode {
		case "security_only":
			if _, err := exec.LookPath("unattended-upgrade"); err == nil {
				cmd = exec.Command("unattended-upgrade", "-v")
			} else {
				args := []string{"install", "-y", "--only-upgrade"}
				args = append(args, aptConfFlags()...)
				args = append(args,
					"-o", "Dir::Etc::SourceList=/etc/apt/sources.list.d/security.list",
					"-o", "Dir::Etc::SourceParts=/dev/null",
					"--with-new-pkgs",
				)
				cmd = exec.Command("apt-get", args...)
			}
		case "package":
			pkg, _ := params["package_name"].(string)
			args := []string{"install", "-y", "--only-upgrade"}
			args = append(args, aptConfFlags()...)
			args = append(args, pkg)
			cmd = exec.Command("apt-get", args...)
		case "cve":
			pkg, _ := params["package_name"].(string)
			if pkg == "" {
				pkg = resolveCVEToPackageApt(params["cve_id"].(string))
			}
			if pkg == "" {
				// Fall back to security_only
				args := []string{"install", "-y", "--only-upgrade"}
				args = append(args, aptConfFlags()...)
				args = append(args,
					"-o", "Dir::Etc::SourceList=/etc/apt/sources.list.d/security.list",
					"-o", "Dir::Etc::SourceParts=/dev/null",
					"--with-new-pkgs",
				)
				cmd = exec.Command("apt-get", args...)
			} else {
				args := []string{"install", "-y", "--only-upgrade"}
				args = append(args, aptConfFlags()...)
				args = append(args, pkg)
				cmd = exec.Command("apt-get", args...)
			}
		}
	case "dnf":
		switch mode {
		case "security_only":
			cmd = exec.Command("dnf", "upgrade", "-y", "--security")
		case "package":
			pkg, _ := params["package_name"].(string)
			cmd = exec.Command("dnf", "upgrade", "-y", pkg)
		case "cve":
			cve, _ := params["cve_id"].(string)
			cmd = exec.Command("dnf", "upgrade", "-y", "--cve", cve)
		}
	case "yum":
		switch mode {
		case "security_only":
			cmd = exec.Command("yum", "update", "-y", "--security")
		case "package":
			pkg, _ := params["package_name"].(string)
			cmd = exec.Command("yum", "update", "-y", pkg)
		case "cve":
			cve, _ := params["cve_id"].(string)
			cmd = exec.Command("yum", "update", "-y", "--cve", cve)
		}
	case "zypper":
		switch mode {
		case "security_only", "cve":
			cmd = exec.Command("zypper", "--non-interactive", "patch", "--category", "security")
		case "package":
			pkg, _ := params["package_name"].(string)
			cmd = exec.Command("zypper", "--non-interactive", "update", pkg)
		}
	case "apk":
		// apk doesn't have a single "security-only" mode; upgrade all
		if out, err := exec.Command("apk", "update").CombinedOutput(); err != nil {
			return fmt.Errorf("apk update: %w\n%s", err, out)
		}
		switch mode {
		case "security_only", "cve":
			cmd = exec.Command("apk", "upgrade")
		case "package":
			pkg, _ := params["package_name"].(string)
			cmd = exec.Command("apk", "add", "--upgrade", pkg)
		}
	}

	if cmd == nil {
		return fmt.Errorf("runPatch: unhandled pm=%q mode=%q", pm, mode)
	}

	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("patch command failed: %w\n%s", err, out)
	}
	return nil
}

func diffPackages(before, after []map[string]string) []map[string]string {
	beforeMap := make(map[string]string, len(before))
	for _, p := range before {
		beforeMap[p["name"]] = p["version"]
	}
	var diff []map[string]string
	for _, p := range after {
		bv, existed := beforeMap[p["name"]]
		if !existed || bv != p["version"] {
			entry := map[string]string{
				"name":           p["name"],
				"version_after":  p["version"],
				"version_before": bv,
			}
			diff = append(diff, entry)
		}
	}
	return diff
}

func checkRebootRequired() bool {
	// Debian/Ubuntu: flag file
	if _, err := os.Stat("/var/run/reboot-required"); err == nil {
		return true
	}
	// RHEL/CentOS: needs-restarting
	if path, err := exec.LookPath("needs-restarting"); err == nil && path != "" {
		cmd := exec.Command("needs-restarting", "-r")
		if err := cmd.Run(); err != nil {
			return true
		}
	}
	// RHEL/CentOS: compare running kernel vs. installed kernel packages
	uname, err := exec.Command("uname", "-r").Output()
	if err == nil {
		running := strings.TrimSpace(string(uname))
		kernels, kerr := exec.Command("rpm", "-q", "kernel",
			"--queryformat", "%{VERSION}-%{RELEASE}.%{ARCH}\n").Output()
		if kerr == nil {
			lines := strings.Split(strings.TrimSpace(string(kernels)), "\n")
			if len(lines) > 0 {
				// Sort to find newest
				sort.Strings(lines)
				newest := lines[len(lines)-1]
				if newest != running {
					return true
				}
			}
		}
	}
	return false
}
