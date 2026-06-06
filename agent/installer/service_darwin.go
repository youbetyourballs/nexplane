//go:build darwin

package installer

import (
	"fmt"
	"os"
	"text/template"
)

const plistPath = "/Library/LaunchDaemons/com.nexplane.agent.plist"

var plistTmpl = template.Must(template.New("plist").Parse(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>com.nexplane.agent</string>
	<key>ProgramArguments</key>
	<array>
		<string>{{.Binary}}</string>
	</array>
	<key>EnvironmentVariables</key>
	<dict>
		<key>NP_CONTROL_PLANE</key>
		<string>{{.ControlPlane}}</string>
		<key>NP_SECRET</key>
		<string>{{.Secret}}</string>
	</dict>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>StandardOutPath</key>
	<string>/var/log/nexplane-agent.log</string>
	<key>StandardErrorPath</key>
	<string>/var/log/nexplane-agent.log</string>
</dict>
</plist>
`))

func installService(binaryPath, controlPlane, secret string) error {
	f, err := os.OpenFile(plistPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	if err != nil {
		return fmt.Errorf("writing plist %s: %w", plistPath, err)
	}
	defer f.Close()

	if err := plistTmpl.Execute(f, struct {
		Binary       string
		ControlPlane string
		Secret       string
	}{binaryPath, controlPlane, secret}); err != nil {
		return fmt.Errorf("rendering plist: %w", err)
	}

	// Bootstrap the service (macOS 11+ preferred over launchctl load)
	if err := runCmd("launchctl", "bootstrap", "system", plistPath); err != nil {
		// Fall back to legacy load for older macOS
		if err2 := runCmd("launchctl", "load", plistPath); err2 != nil {
			return fmt.Errorf("launchctl bootstrap: %w; launchctl load: %v", err, err2)
		}
	}

	fmt.Fprintf(os.Stdout, "LaunchDaemon installed: %s\n", plistPath)
	return nil
}
