//go:build linux

package linuxharden

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func seccompLearnExecute(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}
	profileName, _ := params["profile_name"].(string)
	if profileName == "" {
		profileName = "nexplane-learned"
	}
	outputPath := fmt.Sprintf("/etc/seccomp/%s.json", profileName)
	_ = os.MkdirAll("/etc/seccomp", 0755)

	// Enable SCMP_ACT_LOG via audit — write a permissive seccomp profile that logs
	logProfile := `{
  "defaultAction": "SCMP_ACT_LOG",
  "architectures": ["SCMP_ARCH_X86_64"],
  "syscalls": []
}`
	if err := os.WriteFile(outputPath+".log_phase", []byte(logProfile), 0644); err != nil {
		return nil, fmt.Errorf("write log profile: %w", err)
	}

	time.Sleep(time.Duration(duration) * time.Second)

	// Collect logged syscalls from audit log
	out, err := exec.Command("ausearch", "-m", "SECCOMP", "-ts", "recent", "--raw").Output()
	var syscalls []string
	if err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			if strings.Contains(line, "syscall=") {
				for _, field := range strings.Fields(line) {
					if strings.HasPrefix(field, "syscall=") {
						syscalls = append(syscalls, strings.TrimPrefix(field, "syscall="))
					}
				}
			}
		}
	}
	// Deduplicate
	seen := map[string]bool{}
	var uniq []string
	for _, s := range syscalls {
		if !seen[s] {
			seen[s] = true
			uniq = append(uniq, s)
		}
	}

	return map[string]any{
		"action":           "seccomp_learn",
		"profile_name":     profileName,
		"output_path":      outputPath,
		"duration_seconds": int(duration),
		"syscalls_seen":    uniq,
		"syscall_count":    len(uniq),
	}, nil
}

func seccompLearnRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "seccomp_learn_rollback", "status": "no_state_to_revert"}, nil
}
