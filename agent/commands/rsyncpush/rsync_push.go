package rsyncpush

import (
	"encoding/base64"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func Execute(params map[string]any) (map[string]any, error) {
	destHost, _ := params["dest_host"].(string)
	destUser, _ := params["dest_user"].(string)
	if destUser == "" {
		destUser = "root"
	}
	destSSHKeyB64, _ := params["dest_ssh_key"].(string)
	pathsRaw, _ := params["paths"].([]any)
	excludesRaw, _ := params["excludes"].([]any)
	deleteFlag, _ := params["delete"].(bool)

	if destHost == "" || destSSHKeyB64 == "" || len(pathsRaw) == 0 {
		return nil, fmt.Errorf("rsync_push: dest_host, dest_ssh_key, and paths are required")
	}
	keyBytes, err := base64.StdEncoding.DecodeString(destSSHKeyB64)
	if err != nil {
		return nil, fmt.Errorf("rsync_push: failed to decode SSH key: %w", err)
	}
	tmpDir, err := os.MkdirTemp("", "nexplane-rsync-*")
	if err != nil {
		return nil, fmt.Errorf("rsync_push: failed to create temp dir: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	keyPath := filepath.Join(tmpDir, "rsync_key")
	if err := os.WriteFile(keyPath, keyBytes, 0600); err != nil {
		return nil, fmt.Errorf("rsync_push: failed to write key: %w", err)
	}
	args := []string{
		"-az", "--checksum", "--stats",
		"-e", fmt.Sprintf("ssh -i '%s' -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null", keyPath),
	}
	if deleteFlag {
		args = append(args, "--delete")
	}
	for _, ex := range excludesRaw {
		if s, ok := ex.(string); ok {
			args = append(args, "--exclude="+s)
		}
	}
	for _, p := range pathsRaw {
		if s, ok := p.(string); ok {
			args = append(args, s)
		}
	}
	args = append(args, fmt.Sprintf("%s@%s:", destUser, destHost))

	start := time.Now()
	out, err := exec.Command("rsync", args...).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("rsync_push failed: %w\noutput: %s", err, string(out))
	}

	var bytesTransferred, filesTransferred int64
	for _, line := range strings.Split(string(out), "\n") {
		if strings.HasPrefix(line, "Number of regular files transferred:") {
			parts := strings.Fields(line)
			if len(parts) > 0 {
				n, _ := strconv.ParseInt(strings.ReplaceAll(parts[len(parts)-1], ",", ""), 10, 64)
				filesTransferred = n
			}
		}
		if strings.HasPrefix(line, "Total transferred file size:") {
			parts := strings.Fields(line)
			if len(parts) >= 5 {
				n, _ := strconv.ParseInt(strings.ReplaceAll(parts[4], ",", ""), 10, 64)
				bytesTransferred = n
			}
		}
	}
	return map[string]any{
		"bytes_transferred": bytesTransferred,
		"files_transferred": filesTransferred,
		"duration_seconds":  time.Since(start).Seconds(),
		"output":            string(out),
	}, nil
}
