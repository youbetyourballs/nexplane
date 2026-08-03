package runcommand

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func RunCommand(params map[string]any) (map[string]any, error) {
	command, _ := params["command"].(string)
	if command == "" {
		return nil, fmt.Errorf("run_command: command is required")
	}
	timeoutSecs, _ := params["timeout"].(float64)
	if timeoutSecs <= 0 {
		timeoutSecs = 60
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutSecs)*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, "sh", "-c", command)
	// Inherit the process environment and overlay caller-supplied variables.
	cmd.Env = os.Environ()
	if envMap, ok := params["env"].(map[string]any); ok {
		for k, v := range envMap {
			if sv, ok := v.(string); ok {
				cmd.Env = append(cmd.Env, k+"="+sv)
			}
		}
	}
	out, err := cmd.CombinedOutput()
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			return nil, fmt.Errorf("run_command: exec failed: %w", err)
		}
	}
	return map[string]any{
		"output":    strings.TrimRight(string(out), "\n"),
		"exit_code": exitCode,
	}, nil
}
