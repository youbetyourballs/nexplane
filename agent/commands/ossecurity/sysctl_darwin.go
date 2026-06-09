//go:build darwin

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

var execCommandSysctl = exec.Command

var cisDarwinSysctl = map[string]string{
	"net.inet.ip.forwarding":     "0",
	"net.inet.ip.redirect":       "0",
	"net.inet6.ip6.forwarding":   "0",
	"net.inet.icmp.bmcastecho":   "0",
	"kern.sugid_coredump":        "0",
	"security.mac.proc_enforce":  "1",
	"security.mac.vnode_enforce": "1",
}

const darwinSysctlPlist = "/Library/LaunchDaemons/com.nexplane.sysctl.plist"

func sysctlExecuteOS(params map[string]any) (map[string]any, error) {
	settings := make(map[string]string)
	for k, v := range cisDarwinSysctl {
		settings[k] = v
	}
	if overrides, ok := params["settings"].(map[string]any); ok {
		for k, v := range overrides {
			settings[k] = fmt.Sprintf("%v", v)
		}
	}

	var snapLines []string
	for k := range settings {
		out, err := execCommandSysctl("sysctl", "-n", k).Output()
		if err == nil {
			snapLines = append(snapLines, k+": "+strings.TrimSpace(string(out)))
		}
	}
	snapshot := strings.Join(snapLines, "\n")

	applied := map[string]string{}
	for k, v := range settings {
		out, err := execCommandSysctl("sysctl", "-w", k+"="+v).CombinedOutput()
		if err != nil {
			applied[k] = "skipped: " + strings.TrimSpace(string(out))
			continue
		}
		applied[k] = v
	}

	writeDarwinSysctlPlist(settings)

	return map[string]any{
		"settings":   applied,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func sysctlRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	if snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	for _, line := range strings.Split(snapshot, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.SplitN(line, ": ", 2)
		if len(parts) == 2 {
			execCommandSysctl("sysctl", "-w", parts[0]+"="+parts[1]).Run()
		}
	}
	os.Remove(darwinSysctlPlist)
	return map[string]any{"rolled_back": true}, nil
}

func writeDarwinSysctlPlist(settings map[string]string) {
	var args []string
	for k, v := range settings {
		args = append(args, k+"="+v)
	}
	plistContent := `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.nexplane.sysctl</string>
  <key>ProgramArguments</key><array>
    <string>/usr/sbin/sysctl</string>
`
	for _, a := range args {
		plistContent += "    <string>" + a + "</string>\n"
	}
	plistContent += `  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>`
	os.WriteFile(darwinSysctlPlist, []byte(plistContent), 0644)
}
