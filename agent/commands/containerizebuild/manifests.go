package containerizebuild

import (
	"fmt"
	"regexp"
	"strings"
)

type ManifestSet struct {
	Deployment string
	Service    string
	PVC        string
	ConfigMap  string
}

var nonAlphanumRe = regexp.MustCompile(`[^a-z0-9]+`)

func sanitizeName(s string) string {
	lower := strings.ToLower(s)
	result := nonAlphanumRe.ReplaceAllString(lower, "-")
	result = strings.Trim(result, "-")
	return result
}

func GenerateManifests(app AppProfile, imageRef, namespace, cpuRequest, memRequest string) (ManifestSet, error) {
	name := sanitizeName(app.Name)

	if namespace == "" {
		namespace = "default"
	}
	if cpuRequest == "" {
		cpuRequest = "100m"
	}
	if memRequest == "" {
		memRequest = "128Mi"
	}

	// Build port list for Deployment and Service
	var containerPorts strings.Builder
	var servicePorts strings.Builder
	for i, p := range app.ListeningPorts {
		proto := strings.ToUpper(p.Protocol)
		if proto == "" {
			proto = "TCP"
		}
		containerPorts.WriteString(fmt.Sprintf("        - containerPort: %d\n          protocol: %s\n", p.Port, proto))
		servicePorts.WriteString(fmt.Sprintf("  - port: %d\n    targetPort: %d\n    protocol: %s\n", p.Port, p.Port, proto))
		_ = i
	}

	// Deployment
	deployment := fmt.Sprintf(`apiVersion: apps/v1
kind: Deployment
metadata:
  name: %s
  namespace: %s
  labels:
    app: %s
spec:
  replicas: 1
  selector:
    matchLabels:
      app: %s
  template:
    metadata:
      labels:
        app: %s
    spec:
      containers:
      - name: %s
        image: %s
        resources:
          requests:
            cpu: %s
            memory: %s
%s`, name, namespace, name, name, name, name, imageRef, cpuRequest, memRequest, formatContainerPorts(app.ListeningPorts))

	// Service
	service := fmt.Sprintf(`apiVersion: v1
kind: Service
metadata:
  name: %s
  namespace: %s
  labels:
    app: %s
spec:
  selector:
    app: %s
  ports:
%s`, name, namespace, name, name, formatServicePorts(app.ListeningPorts))

	// PVC — only if DataDirs present
	var pvc string
	if len(app.DataDirs) > 0 {
		pvc = fmt.Sprintf(`apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: %s-data
  namespace: %s
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
`, name, namespace)
	}

	// ConfigMap — from env vars
	var configMapData strings.Builder
	for _, e := range app.EnvVars {
		configMapData.WriteString(fmt.Sprintf("  %s: \"\"\n", e))
	}
	configMap := fmt.Sprintf(`apiVersion: v1
kind: ConfigMap
metadata:
  name: %s-config
  namespace: %s
data:
%s`, name, namespace, configMapData.String())

	return ManifestSet{
		Deployment: deployment,
		Service:    service,
		PVC:        pvc,
		ConfigMap:  configMap,
	}, nil
}

func formatContainerPorts(ports []Port) string {
	if len(ports) == 0 {
		return ""
	}
	var sb strings.Builder
	sb.WriteString("        ports:\n")
	for _, p := range ports {
		proto := strings.ToUpper(p.Protocol)
		if proto == "" {
			proto = "TCP"
		}
		sb.WriteString(fmt.Sprintf("        - containerPort: %d\n          protocol: %s\n", p.Port, proto))
	}
	return sb.String()
}

func formatServicePorts(ports []Port) string {
	if len(ports) == 0 {
		return "  - port: 80\n    targetPort: 80\n"
	}
	var sb strings.Builder
	for _, p := range ports {
		proto := strings.ToUpper(p.Protocol)
		if proto == "" {
			proto = "TCP"
		}
		sb.WriteString(fmt.Sprintf("  - port: %d\n    targetPort: %d\n    protocol: %s\n", p.Port, p.Port, proto))
	}
	return sb.String()
}
