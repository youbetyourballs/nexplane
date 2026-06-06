//go:build linux

package installer

import (
	"fmt"
	"os"
	"text/template"
)

const unitPath = "/etc/systemd/system/nexplane-agent.service"

var unitTmpl = template.Must(template.New("unit").Parse(`[Unit]
Description=Nexplane Agent
After=network.target

[Service]
ExecStart={{.Binary}}
Environment="NP_CONTROL_PLANE={{.ControlPlane}}"
Environment="NP_SECRET={{.Secret}}"
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
`))

func installService(binaryPath, controlPlane, secret string) error {
	f, err := os.OpenFile(unitPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	if err != nil {
		return fmt.Errorf("writing unit file %s: %w", unitPath, err)
	}
	defer f.Close()

	if err := unitTmpl.Execute(f, struct {
		Binary       string
		ControlPlane string
		Secret       string
	}{binaryPath, controlPlane, secret}); err != nil {
		return fmt.Errorf("rendering unit file: %w", err)
	}

	if err := runCmd("systemctl", "daemon-reload"); err != nil {
		return fmt.Errorf("systemctl daemon-reload: %w", err)
	}
	if err := runCmd("systemctl", "enable", "--now", "nexplane-agent"); err != nil {
		return fmt.Errorf("systemctl enable: %w", err)
	}

	fmt.Fprintf(os.Stdout, "systemd unit installed: %s\n", unitPath)
	return nil
}
