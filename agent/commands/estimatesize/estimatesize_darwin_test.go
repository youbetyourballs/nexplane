//go:build darwin

package estimatesize

import (
	"os/exec"
	"testing"
)

func TestExecuteOS_Darwin(t *testing.T) {
	execCommandEstimateDarwin = func(name string, args ...string) *exec.Cmd {
		if name == "du" {
			return exec.Command("echo", "12345\t/etc")
		}
		return exec.Command("echo", "Filesystem 1024-blocks Used Available")
	}
	t.Cleanup(func() { execCommandEstimateDarwin = exec.Command })

	result, err := executeOS(map[string]any{"path": "/etc"})
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	size, _ := result["size_bytes"].(int64)
	if size != 12345*1024 {
		t.Fatalf("expected size_bytes=%d; got %d", 12345*1024, size)
	}
}
