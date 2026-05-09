package containerizebuild

import (
	"fmt"
	"strings"
)

type AppProfile struct {
	Name           string   `json:"name"`
	Binary         string   `json:"binary"`
	SystemdUnit    string   `json:"systemd_unit"`
	ProcessUser    string   `json:"process_user"`
	ListeningPorts []Port   `json:"listening_ports"`
	ConfigFiles    []string `json:"config_files"`
	DataDirs       []string `json:"data_directories"`
	EnvVars        []string `json:"env_vars"`
	OSFamily       string   `json:"os_family"` // debian, rhel, alpine, unknown
}

type Port struct {
	Port     int    `json:"port"`
	Protocol string `json:"protocol"`
}

var baseImages = map[string]string{
	"debian":  "debian:bookworm-slim",
	"ubuntu":  "ubuntu:22.04",
	"rhel":    "redhat/ubi9-minimal",
	"centos":  "redhat/ubi9-minimal",
	"alpine":  "alpine:3.19",
	"unknown": "ubuntu:22.04",
}

func GenerateDockerfile(app AppProfile) (string, error) {
	base, ok := baseImages[app.OSFamily]
	if !ok {
		base = baseImages["unknown"]
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("FROM %s\n\n", base))

	if len(app.EnvVars) > 0 {
		for _, e := range app.EnvVars {
			sb.WriteString(fmt.Sprintf("ENV %s=\"\"\n", e))
		}
		sb.WriteString("\n")
	}

	sb.WriteString(fmt.Sprintf("COPY %s %s\n", app.Binary, app.Binary))
	sb.WriteString(fmt.Sprintf("RUN chmod +x %s\n\n", app.Binary))

	for _, cf := range app.ConfigFiles {
		sb.WriteString(fmt.Sprintf("COPY %s %s\n", cf, cf))
	}
	if len(app.ConfigFiles) > 0 {
		sb.WriteString("\n")
	}

	for _, d := range app.DataDirs {
		sb.WriteString(fmt.Sprintf("VOLUME [\"%s\"]\n", d))
	}
	if len(app.DataDirs) > 0 {
		sb.WriteString("\n")
	}

	for _, p := range app.ListeningPorts {
		sb.WriteString(fmt.Sprintf("EXPOSE %d\n", p.Port))
	}
	if len(app.ListeningPorts) > 0 {
		sb.WriteString("\n")
	}

	user := app.ProcessUser
	if user == "" || user == "root" {
		user = "nobody"
	}
	sb.WriteString(fmt.Sprintf("USER %s\n\n", user))
	sb.WriteString(fmt.Sprintf("CMD [\"%s\"]\n", app.Binary))

	return sb.String(), nil
}
