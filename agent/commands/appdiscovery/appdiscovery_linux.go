//go:build linux

package appdiscovery

import (
	"crypto/md5"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
)

func discoverApplicationsOS(params map[string]any) ([]Application, error) {
	systemdApps := discoverSystemdServices()
	portMap := discoverListeningPorts()
	extraApps := discoverNonPackageBinaries(systemdApps)

	all := append(systemdApps, extraApps...)
	for i := range all {
		binName := filepath.Base(all[i].Binary)
		if ports, ok := portMap[binName]; ok {
			all[i].ListeningPorts = ports
		}
		// Stateful if it has any data directories regardless of port binding
		all[i].Stateful = len(all[i].DataDirectories) > 0
	}

	return all, nil
}

func discoverSystemdServices() []Application {
	out, err := exec.Command("systemctl", "list-units", "--type=service",
		"--state=running", "--no-legend", "--plain").Output()
	if err != nil {
		return nil
	}

	skipPrefixes := []string{
		"systemd-", "dbus", "NetworkManager", "sshd", "cron", "rsyslog",
		"snapd", "getty", "udev", "polkit", "accounts-daemon", "avahi",
	}

	var apps []Application
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 1 {
			continue
		}
		unitName := fields[0]

		skip := false
		for _, prefix := range skipPrefixes {
			if strings.HasPrefix(unitName, prefix) {
				skip = true
				break
			}
		}
		if skip {
			continue
		}

		app := extractServiceDetails(unitName)
		if app.Binary != "" {
			apps = append(apps, app)
		}
	}
	return apps
}

func extractServiceDetails(unit string) Application {
	out, err := exec.Command("systemctl", "show", unit,
		"--property=ExecStart,User,WorkingDirectory").Output()
	if err != nil {
		return Application{}
	}

	app := Application{
		ID:                     fmt.Sprintf("%x", md5.Sum([]byte(unit)))[:8],
		SystemdUnit:            unit,
		Name:                   strings.TrimSuffix(unit, ".service"),
		DataDirectories:        []string{},
		ConfigFiles:            []string{},
		EnvVars:                []string{},
		Dependencies:           []string{},
		ExternalDataStores:     []string{},
		ContainerizationStatus: "not_started",
	}

	for _, line := range strings.Split(string(out), "\n") {
		kv := strings.SplitN(line, "=", 2)
		if len(kv) != 2 {
			continue
		}
		key, val := kv[0], kv[1]
		switch key {
		case "ExecStart":
			if idx := strings.Index(val, "path="); idx >= 0 {
				rest := val[idx+5:]
				if end := strings.IndexAny(rest, " ;"); end > 0 {
					app.Binary = rest[:end]
				}
			}
		case "User":
			if val != "" && val != "root" {
				app.ProcessUser = val
			}
		case "WorkingDirectory":
			if val != "" && val != "/" && val != "/root" {
				app.DataDirectories = append(app.DataDirectories, val)
			}
		}
	}

	app.EstimatedDataSizeGB = estimateDirSizeGB(app.DataDirectories)

	confDir := "/etc/" + app.Name
	if entries, err := os.ReadDir(confDir); err == nil {
		for _, e := range entries {
			if !e.IsDir() {
				app.ConfigFiles = append(app.ConfigFiles, filepath.Join(confDir, e.Name()))
			}
		}
	}

	return app
}

func discoverListeningPorts() map[string][]PortBinding {
	out, err := exec.Command("ss", "-tlnp").Output()
	if err != nil {
		return map[string][]PortBinding{}
	}

	result := map[string][]PortBinding{}
	for _, line := range strings.Split(string(out), "\n")[1:] {
		fields := strings.Fields(line)
		if len(fields) < 5 {
			continue
		}
		localAddr := fields[3]
		processInfo := fields[len(fields)-1]

		port := 0
		if idx := strings.LastIndex(localAddr, ":"); idx >= 0 {
			port, _ = strconv.Atoi(localAddr[idx+1:])
		}
		if port == 0 {
			continue
		}

		binary := ""
		if start := strings.Index(processInfo, `(("`); start >= 0 {
			rest := processInfo[start+3:]
			if end := strings.Index(rest, `"`); end >= 0 {
				binary = rest[:end]
			}
		}
		if binary == "" {
			continue
		}

		result[binary] = append(result[binary], PortBinding{Port: port, Protocol: "tcp"})
	}
	return result
}

func discoverNonPackageBinaries(existing []Application) []Application {
	// Search these directories flat
	flatDirs := []string{"/usr/local/bin", "/usr/local/sbin"}
	// Search these directories one level deep (e.g. /opt/myapp/bin/myapp)
	deepDirs := []string{"/opt"}

	knownBinaries := map[string]bool{}
	for _, a := range existing {
		knownBinaries[filepath.Base(a.Binary)] = true
	}

	var apps []Application

	collectBinaries := func(dir string) {
		entries, err := os.ReadDir(dir)
		if err != nil {
			return
		}
		for _, e := range entries {
			if e.IsDir() || knownBinaries[e.Name()] {
				continue
			}
			fullPath := filepath.Join(dir, e.Name())
			info, err := e.Info()
			if err != nil {
				continue
			}
			if info.Mode()&0111 == 0 {
				continue
			}
			apps = append(apps, Application{
				ID:                     fmt.Sprintf("%x", md5.Sum([]byte(fullPath)))[:8],
				Name:                   e.Name(),
				Binary:                 fullPath,
				DataDirectories:        []string{},
				ConfigFiles:            []string{},
				EnvVars:                []string{},
				Dependencies:           []string{},
				ExternalDataStores:     []string{},
				ContainerizationStatus: "not_started",
			})
		}
	}

	for _, dir := range flatDirs {
		collectBinaries(dir)
	}

	// For deep dirs: check immediate subdirectories for bin/ folders
	for _, baseDir := range deepDirs {
		vendorEntries, err := os.ReadDir(baseDir)
		if err != nil {
			continue
		}
		for _, vendor := range vendorEntries {
			if !vendor.IsDir() {
				continue
			}
			// Check <vendor>/bin/ directly
			binDir := filepath.Join(baseDir, vendor.Name(), "bin")
			collectBinaries(binDir)
			// Also check the vendor dir itself for direct binaries
			collectBinaries(filepath.Join(baseDir, vendor.Name()))
		}
	}

	return apps
}

func estimateDirSizeGB(dirs []string) float64 {
	var totalBytes int64
	for _, dir := range dirs {
		filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
			if err == nil && !info.IsDir() {
				totalBytes += info.Size()
			}
			return nil
		})
	}
	return float64(totalBytes) / (1024 * 1024 * 1024)
}
