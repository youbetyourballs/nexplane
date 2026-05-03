//go:build linux

package linuxpatch

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

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

func rollbackPatchesOS(params map[string]any) (map[string]any, error) {
	pm, err := detectPackageManager()
	if err != nil {
		return nil, err
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
			cmd = exec.Command("apt-get", "install", "-y", "--allow-downgrades", target)
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
		fields := strings.Split(scanner.Text(), "\t")
		if len(fields) < 2 {
			continue
		}
		entry := map[string]string{"name": fields[0], "version": fields[1]}
		if len(fields) >= 3 {
			entry["arch"] = fields[2]
		}
		result = append(result, entry)
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
	default:
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
		if pm == "apt" && strings.HasPrefix(line, "Inst ") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				result = append(result, map[string]string{"name": fields[1]})
			}
			continue
		}
		fields := strings.Fields(line)
		if len(fields) >= 3 && (pm == "dnf" || pm == "yum") {
			result = append(result, map[string]string{"name": fields[2]})
		}
	}
	return result, nil
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
				cmd = exec.Command("apt-get", "install", "-y", "--only-upgrade",
					"-o", "Dir::Etc::SourceList=/etc/apt/sources.list.d/security.list",
					"-o", "Dir::Etc::SourceParts=/dev/null",
					"--with-new-pkgs")
			}
		case "package":
			pkg, _ := params["package_name"].(string)
			cmd = exec.Command("apt-get", "install", "-y", "--only-upgrade", pkg)
		case "cve":
			pkg, _ := params["package_name"].(string)
			if pkg == "" {
				return fmt.Errorf("apt mode=cve requires package_name to be resolved before calling runPatch")
			}
			cmd = exec.Command("apt-get", "install", "-y", "--only-upgrade", pkg)
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
	if _, err := os.Stat("/var/run/reboot-required"); err == nil {
		return true
	}
	if path, err := exec.LookPath("needs-restarting"); err == nil && path != "" {
		cmd := exec.Command("needs-restarting", "-r")
		if err := cmd.Run(); err != nil {
			return true
		}
	}
	return false
}
