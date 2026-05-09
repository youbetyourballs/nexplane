package containerizebuild

import (
	"strings"
	"testing"
)

// TestGenerateDockerfile_Stateless tests a stateless app with debian base, port 8080, and env var.
func TestGenerateDockerfile_Stateless(t *testing.T) {
	app := AppProfile{
		Name:        "myapp",
		Binary:      "/usr/bin/myapp",
		ProcessUser: "myapp",
		ListeningPorts: []Port{
			{Port: 8080, Protocol: "tcp"},
		},
		EnvVars:  []string{"DATABASE_URL"},
		OSFamily: "debian",
	}

	df, err := GenerateDockerfile(app)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if !strings.Contains(df, "FROM debian:bookworm-slim") {
		t.Errorf("expected debian base image, got:\n%s", df)
	}
	if !strings.Contains(df, "EXPOSE 8080") {
		t.Errorf("expected EXPOSE 8080, got:\n%s", df)
	}
	if !strings.Contains(df, "USER myapp") {
		t.Errorf("expected USER myapp, got:\n%s", df)
	}
	if !strings.Contains(df, `ENV DATABASE_URL=""`) {
		t.Errorf("expected ENV DATABASE_URL, got:\n%s", df)
	}
}

// TestGenerateDockerfile_Stateful tests a stateful app with rhel base and VOLUME directive.
func TestGenerateDockerfile_Stateful(t *testing.T) {
	app := AppProfile{
		Name:        "mydb",
		Binary:      "/usr/bin/mydb",
		ProcessUser: "mydb",
		DataDirs:    []string{"/var/lib/mydb"},
		OSFamily:    "rhel",
	}

	df, err := GenerateDockerfile(app)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if !strings.Contains(df, "FROM redhat/ubi9-minimal") {
		t.Errorf("expected rhel base image, got:\n%s", df)
	}
	if !strings.Contains(df, `VOLUME ["/var/lib/mydb"]`) {
		t.Errorf("expected VOLUME directive, got:\n%s", df)
	}
}

// TestGenerateDockerfile_UnknownOS tests that unknown OS family falls back to ubuntu.
func TestGenerateDockerfile_UnknownOS(t *testing.T) {
	app := AppProfile{
		Name:     "someapp",
		Binary:   "/usr/bin/someapp",
		OSFamily: "freebsd",
	}

	df, err := GenerateDockerfile(app)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if !strings.Contains(df, "FROM ubuntu:22.04") {
		t.Errorf("expected ubuntu fallback, got:\n%s", df)
	}
}

// TestGenerateManifests_Stateless tests a stateless app: has Deployment + Service + ConfigMap, no PVC.
func TestGenerateManifests_Stateless(t *testing.T) {
	app := AppProfile{
		Name:    "webserver",
		Binary:  "/usr/bin/webserver",
		EnvVars: []string{"LOG_LEVEL"},
		ListeningPorts: []Port{
			{Port: 80, Protocol: "tcp"},
		},
	}

	ms, err := GenerateManifests(app, "registry.example.com/webserver:latest", "production", "200m", "256Mi")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if !strings.Contains(ms.Deployment, "kind: Deployment") {
		t.Errorf("expected Deployment manifest")
	}
	if !strings.Contains(ms.Service, "kind: Service") {
		t.Errorf("expected Service manifest")
	}
	if ms.PVC != "" {
		t.Errorf("expected no PVC for stateless app, got: %s", ms.PVC)
	}
	if !strings.Contains(ms.ConfigMap, "kind: ConfigMap") {
		t.Errorf("expected ConfigMap manifest")
	}
	if !strings.Contains(ms.ConfigMap, "LOG_LEVEL") {
		t.Errorf("expected LOG_LEVEL in ConfigMap")
	}
}

// TestGenerateManifests_Stateful tests that a stateful app (with DataDirs) gets a PVC.
func TestGenerateManifests_Stateful(t *testing.T) {
	app := AppProfile{
		Name:     "postgres",
		Binary:   "/usr/bin/postgres",
		DataDirs: []string{"/var/lib/postgresql/data"},
	}

	ms, err := GenerateManifests(app, "registry.example.com/postgres:latest", "default", "", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if ms.PVC == "" {
		t.Errorf("expected PVC for stateful app")
	}
	if !strings.Contains(ms.PVC, "kind: PersistentVolumeClaim") {
		t.Errorf("expected PVC manifest, got: %s", ms.PVC)
	}
}

// TestContainerizeBuildExecute_MissingApp tests that missing app_profile returns an error.
func TestContainerizeBuildExecute_MissingApp(t *testing.T) {
	_, err := ContainerizeBuildExecute(map[string]any{})
	if err == nil {
		t.Fatal("expected error for missing app_profile, got nil")
	}
	if !strings.Contains(err.Error(), "app_profile") {
		t.Errorf("expected error mentioning app_profile, got: %v", err)
	}
}

// TestContainerizeBuildExecute_DryRun tests dry_run=true: generates dockerfile/manifests, empty digest.
func TestContainerizeBuildExecute_DryRun(t *testing.T) {
	appProfile := map[string]any{
		"name":     "testapp",
		"binary":   "/usr/bin/testapp",
		"os_family": "debian",
		"listening_ports": []any{
			map[string]any{"port": float64(8080), "protocol": "tcp"},
		},
		"env_vars": []any{"API_KEY"},
	}

	result, err := ContainerizeBuildExecute(map[string]any{
		"app_profile": appProfile,
		"dry_run":     true,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if result["dockerfile"] == "" || result["dockerfile"] == nil {
		t.Errorf("expected dockerfile in result")
	}
	if result["manifests"] == nil {
		t.Errorf("expected manifests in result")
	}
	if result["image_digest"] != "" {
		t.Errorf("expected empty image_digest on dry_run, got: %v", result["image_digest"])
	}
	if result["dry_run"] != true {
		t.Errorf("expected dry_run=true in result")
	}
	manifests, ok := result["manifests"].(map[string]any)
	if !ok {
		t.Fatalf("expected manifests to be map[string]any")
	}
	if manifests["deployment"] == "" || manifests["deployment"] == nil {
		t.Errorf("expected deployment in manifests")
	}
	if manifests["service"] == "" || manifests["service"] == nil {
		t.Errorf("expected service in manifests")
	}
}
