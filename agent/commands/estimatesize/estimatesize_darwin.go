//go:build darwin

package estimatesize

import (
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

var execCommandEstimateDarwin = exec.Command

func executeOS(params map[string]any) (map[string]any, error) {
	path, _ := params["path"].(string)
	if path == "" {
		path = "/"
	}

	out, err := execCommandEstimateDarwin("du", "-sk", path).Output()
	if err != nil {
		return nil, fmt.Errorf("du -sk %s: %w", path, err)
	}
	parts := strings.Fields(string(out))
	if len(parts) == 0 {
		return nil, fmt.Errorf("du -sk returned empty output")
	}
	sizeKB, _ := strconv.ParseInt(parts[0], 10, 64)

	dfOut, _ := execCommandEstimateDarwin("df", "-k", path).Output()

	return map[string]any{
		"path":         path,
		"size_bytes":   sizeKB * 1024,
		"df_output":    strings.TrimSpace(string(dfOut)),
		"collected_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
