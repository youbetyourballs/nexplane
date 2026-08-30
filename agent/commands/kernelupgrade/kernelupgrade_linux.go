//go:build linux

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package kernelupgrade

import (
	"bufio"
	"fmt"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

var validSvcName = regexp.MustCompile(`^[a-zA-Z0-9._@\-]+\.service$`)
var validCtrName = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._-]*$`)

// ─── helpers ────────────────────────────────────────────────────────────────

func detectPM() string {
	for _, pm := range []string{"apt-get", "dnf", "yum", "zypper"} {
		if path, err := exec.LookPath(pm); err == nil && path != "" {
			if pm == "apt-get" {
				return "apt"
			}
			return pm
		}
	}
	return "unknown"
}

func runningKernel() string {
	out, err := exec.Command("uname", "-r").Output()
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(out))
}

// diskFreeGB returns free gigabytes on the given mount. If _test_disk_free is
// set in params, it returns that value instead (used in tests).
func diskFreeGB(path string, params map[string]any) float64 {
	if v, ok := params["_test_disk_free"].(float64); ok {
		return v
	}
	out, err := exec.Command("df", "-BG", "--output=avail", path).Output()
	if err != nil {
		return 0
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	if len(lines) < 2 {
		return 0
	}
	val := strings.TrimSuffix(strings.TrimSpace(lines[1]), "G")
	var gb float64
	fmt.Sscanf(val, "%f", &gb)
	return gb
}

// installedKernels lists kernel packages sorted newest-first.
func installedKernels(pm string) []string {
	var out []byte
	var err error
	switch pm {
	case "apt":
		out, err = exec.Command("dpkg-query", "-W",
			"-f=${Version}\n",
			"linux-image-*").Output()
	case "dnf", "yum":
		out, err = exec.Command("rpm", "-q", "kernel",
			"--queryformat", "%{VERSION}-%{RELEASE}.%{ARCH}\n").Output()
	case "zypper":
		out, err = exec.Command("rpm", "-q", "kernel-default",
			"--queryformat", "%{VERSION}-%{RELEASE}.%{ARCH}\n").Output()
	}
	if err != nil || len(out) == 0 {
		return nil
	}
	var result []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		line = strings.TrimSpace(line)
		if line != "" && !strings.HasPrefix(line, "error") {
			result = append(result, line)
		}
	}
	return result
}

func dkmsModules() []string {
	out, err := exec.Command("dkms", "status").Output()
	if err != nil {
		return nil // dkms not installed — not an error
	}
	var modules []string
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line != "" {
			modules = append(modules, line)
		}
	}
	return modules
}

// grubReboot schedules a one-shot next-boot to the given GRUB entry.
// On Debian/Ubuntu: grub-reboot "entry"
// On RHEL/Rocky: grub2-reboot "entry"
func grubReboot(entry string) error {
	if strings.HasPrefix(entry, "--") {
		return fmt.Errorf("GRUB entry %q starts with '--' which would be interpreted as a flag", entry)
	}
	for _, bin := range []string{"grub-reboot", "grub2-reboot"} {
		if path, err := exec.LookPath(bin); err == nil && path != "" {
			out, err := exec.Command(bin, entry).CombinedOutput()
			if err != nil {
				return fmt.Errorf("%s %q failed: %w\n%s", bin, entry, err, out)
			}
			return nil
		}
	}
	return fmt.Errorf("neither grub-reboot nor grub2-reboot found — cannot set one-shot boot entry")
}

// grubSetDefault makes the given entry the permanent default kernel.
// Use ONLY for rollback — never for the new kernel (use grubReboot for that).
func grubSetDefault(entry string) error {
	if strings.HasPrefix(entry, "--") {
		return fmt.Errorf("GRUB entry %q starts with '--' which would be interpreted as a flag", entry)
	}
	for _, bin := range []string{"grub-set-default", "grub2-set-default"} {
		if path, err := exec.LookPath(bin); err == nil && path != "" {
			out, err := exec.Command(bin, entry).CombinedOutput()
			if err != nil {
				return fmt.Errorf("%s %q failed: %w\n%s", bin, entry, err, out)
			}
			return nil
		}
	}
	return fmt.Errorf("neither grub-set-default nor grub2-set-default found")
}

// updateGrubConfig regenerates the GRUB menu (must run after installing a kernel).
func updateGrubConfig() error {
	if path, err := exec.LookPath("update-grub"); err == nil && path != "" {
		out, err := exec.Command("update-grub").CombinedOutput()
		if err != nil {
			return fmt.Errorf("update-grub failed: %w\n%s", err, out)
		}
		return nil
	}
	// RHEL/Rocky path
	for _, cfg := range []string{"/boot/grub2/grub.cfg", "/boot/efi/EFI/redhat/grub.cfg"} {
		if _, err := exec.Command("test", "-f", cfg).Output(); err == nil {
			out, err2 := exec.Command("grub2-mkconfig", "-o", cfg).CombinedOutput()
			if err2 != nil {
				return fmt.Errorf("grub2-mkconfig -o %s failed: %w\n%s", cfg, err2, out)
			}
			return nil
		}
	}
	return fmt.Errorf("could not find update-grub or grub2-mkconfig")
}

// installKernel installs the specified kernel version via the system package manager.
func installKernel(pm, targetKernel string) error {
	var cmd *exec.Cmd
	switch pm {
	case "apt":
		pkg := targetKernel
		if !strings.HasPrefix(pkg, "linux-image-") {
			pkg = "linux-image-" + pkg
		}
		if out, err := exec.Command("apt-get", "update", "-q").CombinedOutput(); err != nil {
			return fmt.Errorf("apt-get update: %w\n%s", err, out)
		}
		cmd = exec.Command("apt-get", "install", "-y",
			"-o", "Dpkg::Options::=--force-confold",
			"-o", "Dpkg::Options::=--force-confdef",
			pkg, "linux-modules-"+strings.TrimPrefix(pkg, "linux-image-"))
	case "dnf":
		cmd = exec.Command("dnf", "install", "-y", "kernel-"+targetKernel)
	case "yum":
		cmd = exec.Command("yum", "install", "-y", "kernel-"+targetKernel)
	case "zypper":
		cmd = exec.Command("zypper", "--non-interactive", "install", "kernel-default-"+targetKernel)
	default:
		return fmt.Errorf("unsupported package manager: %s", pm)
	}
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("kernel install failed: %w\n%s", err, out)
	}
	return nil
}

// grubEntryForKernel returns the GRUB menu entry title for a kernel version.
func grubEntryForKernel(pm, kernelVersion string) string {
	switch pm {
	case "apt":
		out, err := exec.Command("grep", "-E", "submenu|menuentry",
			"/boot/grub/grub.cfg").Output()
		if err != nil {
			return kernelVersion
		}
		var submenu, entry string
		scanner := bufio.NewScanner(strings.NewReader(string(out)))
		for scanner.Scan() {
			line := scanner.Text()
			if strings.Contains(line, "submenu") {
				submenu = extractQuoted(line)
			} else if strings.Contains(line, "menuentry") && strings.Contains(line, kernelVersion) {
				entry = extractQuoted(line)
				break
			}
		}
		if submenu != "" && entry != "" {
			return submenu + ">" + entry
		}
		return kernelVersion
	default:
		// RHEL grub2-reboot accepts the kernel version string directly
		return kernelVersion
	}
}

func extractQuoted(s string) string {
	for _, q := range []byte{'"', '\''} {
		start := strings.IndexByte(s, q)
		if start < 0 {
			continue
		}
		end := strings.IndexByte(s[start+1:], q)
		if end >= 0 {
			return s[start+1 : start+1+end]
		}
	}
	return s
}

// ─── service/container baseline ─────────────────────────────────────────────

// runningServices returns systemd services currently in "active" state.
func runningServices() []string {
	out, err := exec.Command("systemctl", "list-units", "--type=service",
		"--state=active", "--no-legend", "--no-pager", "--plain").Output()
	if err != nil {
		return nil
	}
	var services []string
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) > 0 && strings.HasSuffix(fields[0], ".service") {
			svc := fields[0]
			if !strings.HasPrefix(svc, "systemd-") && !strings.HasPrefix(svc, "session-") {
				services = append(services, svc)
			}
		}
	}
	return services
}

// runningContainers returns names of Docker containers in "running" state.
func runningContainers() []string {
	if _, err := exec.LookPath("docker"); err != nil {
		return nil
	}
	out, err := exec.Command("docker", "ps", "--format={{.Names}}").Output()
	if err != nil {
		return nil
	}
	var containers []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line = strings.TrimSpace(line); line != "" {
			containers = append(containers, line)
		}
	}
	return containers
}

// ─── command implementations ─────────────────────────────────────────────────

func preflightOS(params map[string]any) (map[string]any, error) {
	targetKernel, _ := params["target_kernel"].(string)
	pm := detectPM()
	current := runningKernel()
	installed := installedKernels(pm)
	dkms := dkmsModules()
	free := diskFreeGB("/boot", params)

	// Capture application baseline so verify_services knows what to check post-reboot
	services := runningServices()
	containers := runningContainers()

	warnings := []string{}
	if len(dkms) > 0 {
		warnings = append(warnings,
			fmt.Sprintf("%d DKMS module(s) found — verify they support the target kernel: %v", len(dkms), dkms))
	}

	// 2 GB minimum free on /boot for kernel + initramfs
	const minBootGB = 2.0
	if free < minBootGB {
		return map[string]any{
			"status":         "blocked",
			"reason":         fmt.Sprintf("/boot has %.1f GB free — need at least %.0f GB for kernel install", free, minBootGB),
			"current_kernel": current,
			"disk_free_gb":   free,
		}, nil
	}
	if free < 4.0 {
		warnings = append(warnings, fmt.Sprintf("/boot has only %.1f GB free — 4+ GB recommended", free))
	}

	alreadyInstalled := false
	for _, k := range installed {
		if strings.Contains(k, targetKernel) {
			alreadyInstalled = true
			break
		}
	}

	return map[string]any{
		"status":             "ok",
		"current_kernel":     current,
		"installed_kernels":  installed,
		"target_kernel":      targetKernel,
		"already_installed":  alreadyInstalled,
		"package_manager":    pm,
		"dkms_modules":       dkms,
		"boot_disk_free_gb":  free,
		"running_services":   services,
		"running_containers": containers,
		"warnings":           warnings,
		"checked_at":         time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func executeOS(params map[string]any) (map[string]any, error) {
	targetKernel, _ := params["target_kernel"].(string)
	if targetKernel == "" {
		return nil, fmt.Errorf("target_kernel is required")
	}
	alreadyInstalled, _ := params["already_installed"].(bool)
	pm := detectPM()
	previousKernel := runningKernel()

	// Install kernel package unless already present
	if !alreadyInstalled {
		if err := installKernel(pm, targetKernel); err != nil {
			return nil, fmt.Errorf("kernel install: %w", err)
		}
	}

	// Regenerate GRUB menu so new kernel appears
	if err := updateGrubConfig(); err != nil {
		return nil, fmt.Errorf("grub config update: %w", err)
	}

	// Set new kernel for NEXT BOOT ONLY (one-shot — if anything goes wrong after reboot,
	// next subsequent reboot will automatically return to the old default kernel)
	entry := grubEntryForKernel(pm, targetKernel)
	if err := grubReboot(entry); err != nil {
		return nil, fmt.Errorf("grub-reboot: %w", err)
	}

	return map[string]any{
		"kernel_installed": targetKernel,
		"previous_kernel":  previousKernel,
		"grub_entry":       entry,
		"reboot_armed":     true, // grub-reboot set; caller must now trigger actual reboot
		"installed_at":     time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func verifyOS(params map[string]any) (map[string]any, error) {
	targetKernel, _ := params["target_kernel"].(string)
	current := runningKernel()

	versionMatch := targetKernel != "" && strings.Contains(current, targetKernel)

	return map[string]any{
		"verified":       versionMatch,
		"running_kernel": current,
		"target_kernel":  targetKernel,
		"verified_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	previousKernel, _ := params["previous_kernel"].(string)
	if previousKernel == "" {
		return nil, fmt.Errorf("previous_kernel is required for rollback")
	}
	pm := detectPM()

	// Make the old kernel the permanent default
	entry := grubEntryForKernel(pm, previousKernel)
	if err := grubSetDefault(entry); err != nil {
		return nil, fmt.Errorf("grub-set-default: %w", err)
	}
	if err := updateGrubConfig(); err != nil {
		return nil, fmt.Errorf("grub config update: %w", err)
	}

	// Schedule reboot (caller must confirm agent re-registration)
	out, err := exec.Command("shutdown", "-r", "+1", "Nexplane kernel rollback reboot").CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("shutdown -r: %w\n%s", err, out)
	}

	return map[string]any{
		"rolled_back":      true,
		"previous_kernel":  previousKernel,
		"grub_entry":       entry,
		"reboot_scheduled": true,
		"rolled_back_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

// verifyServicesOS checks that the systemd services and Docker containers that
// were recorded in preflight are still running after the kernel upgrade reboot.
// params["expected_services"] []string — service names from preflight
// params["expected_containers"] []string — container names from preflight
// params["restart_failed"] bool — if true, attempt systemctl start / docker start before failing
func verifyServicesOS(params map[string]any) (map[string]any, error) {
	expectedServices := toStringSlice(params["expected_services"])
	expectedContainers := toStringSlice(params["expected_containers"])
	restartFailed, _ := params["restart_failed"].(bool)

	failedServices := []string{}
	for _, svc := range expectedServices {
		if !validSvcName.MatchString(svc) {
			failedServices = append(failedServices, svc+" (invalid service name)")
			continue
		}
		if err := exec.Command("systemctl", "is-active", "--quiet", svc).Run(); err != nil {
			if restartFailed {
				exec.Command("systemctl", "start", svc).Run()
				if err2 := exec.Command("systemctl", "is-active", "--quiet", svc).Run(); err2 != nil {
					failedServices = append(failedServices, svc+" (start failed)")
				}
			} else {
				failedServices = append(failedServices, svc)
			}
		}
	}

	failedContainers := []string{}
	if _, err := exec.LookPath("docker"); err == nil {
		for _, ctr := range expectedContainers {
			if !validCtrName.MatchString(ctr) {
				failedContainers = append(failedContainers, ctr+" (invalid container name)")
				continue
			}
			out, err := exec.Command("docker", "inspect", "--format={{.State.Running}}", ctr).Output()
			if err != nil || strings.TrimSpace(string(out)) != "true" {
				if restartFailed {
					exec.Command("docker", "start", ctr).Run()
					out2, err2 := exec.Command("docker", "inspect", "--format={{.State.Running}}", ctr).Output()
					if err2 != nil || strings.TrimSpace(string(out2)) != "true" {
						failedContainers = append(failedContainers, ctr+" (start failed)")
					}
				} else {
					failedContainers = append(failedContainers, ctr)
				}
			}
		}
	}

	healthy := len(failedServices) == 0 && len(failedContainers) == 0
	return map[string]any{
		"services_healthy":    healthy,
		"expected_services":   expectedServices,
		"expected_containers": expectedContainers,
		"failed_services":     failedServices,
		"failed_containers":   failedContainers,
		"checked_at":          time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func toStringSlice(v any) []string {
	raw, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(raw))
	for _, item := range raw {
		if s, ok := item.(string); ok && s != "" {
			out = append(out, s)
		}
	}
	return out
}
