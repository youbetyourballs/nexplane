package appdiscovery

import "fmt"

// Application represents a discovered application running on the host.
type Application struct {
	ID                     string        `json:"id"`
	Name                   string        `json:"name"`
	Binary                 string        `json:"binary"`
	SystemdUnit            string        `json:"systemd_unit"`
	ListeningPorts         []PortBinding `json:"listening_ports"`
	ConfigFiles            []string      `json:"config_files"`
	DataDirectories        []string      `json:"data_directories"`
	EstimatedDataSizeGB    float64       `json:"estimated_data_size_gb"`
	Stateful               bool          `json:"stateful"`
	ExternalDataStores     []string      `json:"external_data_stores"`
	ProcessUser            string        `json:"process_user"`
	EnvVars                []string      `json:"env_vars"`
	Dependencies           []string      `json:"dependencies"`
	ContainerizationStatus string        `json:"containerization_status"`
}

// PortBinding represents a port a process is listening on.
type PortBinding struct {
	Port     int    `json:"port"`
	Protocol string `json:"protocol"`
}

// DiscoverApplicationsExecute scans the host and returns discovered applications.
func DiscoverApplicationsExecute(params map[string]any) (map[string]any, error) {
	apps, err := discoverApplicationsOS(params)
	if err != nil {
		return nil, fmt.Errorf("discover_applications: %w", err)
	}

	result := make([]map[string]any, 0, len(apps))
	for _, app := range apps {
		result = append(result, map[string]any{
			"id":                      app.ID,
			"name":                    app.Name,
			"binary":                  app.Binary,
			"systemd_unit":            app.SystemdUnit,
			"listening_ports":         portBindingsToMaps(app.ListeningPorts),
			"config_files":            app.ConfigFiles,
			"data_directories":        app.DataDirectories,
			"estimated_data_size_gb":  app.EstimatedDataSizeGB,
			"stateful":                app.Stateful,
			"external_data_stores":    app.ExternalDataStores,
			"process_user":            app.ProcessUser,
			"env_vars":                app.EnvVars,
			"dependencies":            app.Dependencies,
			"containerization_status": "not_started",
		})
	}

	return map[string]any{
		"action":       "discover_applications",
		"applications": result,
	}, nil
}

func portBindingsToMaps(bindings []PortBinding) []map[string]any {
	out := make([]map[string]any, 0, len(bindings))
	for _, b := range bindings {
		out = append(out, map[string]any{"port": b.Port, "protocol": b.Protocol})
	}
	return out
}
