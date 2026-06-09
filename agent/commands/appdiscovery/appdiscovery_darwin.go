//go:build darwin

package appdiscovery

import (
	"crypto/md5"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
)

var execCommandAppDiscovery = exec.Command

func discoverApplicationsOS(_ map[string]any) ([]Application, error) {
	services := discoverLaunchdServices()
	portMap := discoverDarwinListeningPorts()

	for i := range services {
		name := services[i].Name
		if ports, ok := portMap[name]; ok {
			services[i].ListeningPorts = ports
		}
		services[i].Stateful = len(services[i].DataDirectories) > 0
	}

	return services, nil
}

func discoverLaunchdServices() []Application {
	out, err := execCommandAppDiscovery("launchctl", "list").Output()
	if err != nil {
		return nil
	}

	var apps []Application
	lines := strings.Split(string(out), "\n")
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) < 3 || fields[0] == "-" {
			continue
		}
		label := fields[2]
		app := Application{
			ID:                     fmt.Sprintf("%x", md5.Sum([]byte(label)))[:8],
			Name:                   labelToName(label),
			Binary:                 label,
			DataDirectories:        []string{},
			ConfigFiles:            []string{},
			EnvVars:                []string{},
			Dependencies:           []string{},
			ExternalDataStores:     []string{},
			ContainerizationStatus: "not_started",
		}
		// Check if pid field (fields[0]) is non-zero to determine status
		if fields[0] != "-" && fields[0] != "0" {
			app.ProcessUser = "" // launchctl doesn't report user in list output
		}
		apps = append(apps, app)
	}
	return apps
}

func labelToName(label string) string {
	parts := strings.Split(label, ".")
	if len(parts) > 0 {
		return parts[len(parts)-1]
	}
	return label
}

func discoverDarwinListeningPorts() map[string][]PortBinding {
	portMap := map[string][]PortBinding{}
	out, err := execCommandAppDiscovery("lsof", "-nP", "-iTCP", "-sTCP:LISTEN").Output()
	if err != nil {
		return portMap
	}
	for _, line := range strings.Split(string(out), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 9 {
			continue
		}
		cmd := fields[0]
		nameField := fields[len(fields)-1]
		if idx := strings.LastIndex(nameField, ":"); idx != -1 {
			portStr := nameField[idx+1:]
			if port, err := strconv.Atoi(portStr); err == nil {
				portMap[cmd] = append(portMap[cmd], PortBinding{Port: port, Protocol: "tcp"})
			}
		}
	}
	return portMap
}
