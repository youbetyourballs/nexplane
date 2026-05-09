package containerizebuild

import (
	"encoding/json"
	"fmt"
)

// ContainerizeBuildExecute is the entry point for the containerize_build command.
// Expected params:
//   - app_profile (map or JSON) — required
//   - registry (string) — default "registry.example.com"
//   - namespace (string) — default "default"
//   - cpu_request (string) — default "100m"
//   - mem_request (string) — default "128Mi"
//   - dry_run (bool) — default false
func ContainerizeBuildExecute(params map[string]any) (map[string]any, error) {
	rawProfile, ok := params["app_profile"]
	if !ok || rawProfile == nil {
		return nil, fmt.Errorf("missing required parameter: app_profile")
	}

	var app AppProfile
	switch v := rawProfile.(type) {
	case map[string]any:
		// Re-encode to JSON then decode into AppProfile
		b, err := json.Marshal(v)
		if err != nil {
			return nil, fmt.Errorf("failed to marshal app_profile: %w", err)
		}
		if err := json.Unmarshal(b, &app); err != nil {
			return nil, fmt.Errorf("failed to parse app_profile: %w", err)
		}
	case AppProfile:
		app = v
	default:
		return nil, fmt.Errorf("app_profile must be a map or AppProfile struct")
	}

	registry := stringParam(params, "registry", "registry.example.com")
	namespace := stringParam(params, "namespace", "default")
	cpuRequest := stringParam(params, "cpu_request", "100m")
	memRequest := stringParam(params, "mem_request", "128Mi")
	dryRun := boolParam(params, "dry_run", false)

	imageName := fmt.Sprintf("%s/%s:latest", registry, sanitizeName(app.Name))

	dockerfile, err := GenerateDockerfile(app)
	if err != nil {
		return nil, fmt.Errorf("GenerateDockerfile: %w", err)
	}

	manifests, err := GenerateManifests(app, imageName, namespace, cpuRequest, memRequest)
	if err != nil {
		return nil, fmt.Errorf("GenerateManifests: %w", err)
	}

	digest, err := BuildAndPush(imageName, dockerfile, dryRun)
	if err != nil {
		return nil, fmt.Errorf("BuildAndPush: %w", err)
	}

	return map[string]any{
		"action":       "containerize_build",
		"app_name":     app.Name,
		"image_name":   imageName,
		"image_digest": digest,
		"dockerfile":   dockerfile,
		"manifests": map[string]any{
			"deployment": manifests.Deployment,
			"service":    manifests.Service,
			"pvc":        manifests.PVC,
			"config_map": manifests.ConfigMap,
		},
		"dry_run": dryRun,
	}, nil
}

func stringParam(params map[string]any, key, defaultVal string) string {
	if v, ok := params[key]; ok {
		if s, ok := v.(string); ok && s != "" {
			return s
		}
	}
	return defaultVal
}

func boolParam(params map[string]any, key string, defaultVal bool) bool {
	if v, ok := params[key]; ok {
		if b, ok := v.(bool); ok {
			return b
		}
	}
	return defaultVal
}
