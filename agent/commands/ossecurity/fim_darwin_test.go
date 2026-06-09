//go:build darwin

package ossecurity

import (
	"os"
	"path/filepath"
	"testing"
)

func TestFimExecuteOS_DarwinInit(t *testing.T) {
	tmpDir := t.TempDir()
	snapshotPath := filepath.Join(tmpDir, "fim-snapshot.json")
	origPath := darwinFIMSnapshotPath
	darwinFIMSnapshotPath = snapshotPath
	t.Cleanup(func() { darwinFIMSnapshotPath = origPath })

	testFile := filepath.Join(tmpDir, "test.txt")
	os.WriteFile(testFile, []byte("hello"), 0644)

	result, err := fimExecuteOS(map[string]any{
		"action":      "init",
		"watch_paths": []any{tmpDir},
	})
	if err != nil {
		t.Fatalf("fimExecuteOS init: %v", err)
	}
	if result["snapshot_path"] == nil {
		t.Fatal("expected snapshot_path")
	}
	if _, err := os.Stat(snapshotPath); err != nil {
		t.Fatalf("snapshot file not created: %v", err)
	}
}

func TestFimExecuteOS_DarwinCheck(t *testing.T) {
	tmpDir := t.TempDir()
	snapshotPath := filepath.Join(tmpDir, "fim-snapshot.json")
	origPath := darwinFIMSnapshotPath
	darwinFIMSnapshotPath = snapshotPath
	t.Cleanup(func() { darwinFIMSnapshotPath = origPath })

	testFile := filepath.Join(tmpDir, "test.txt")
	os.WriteFile(testFile, []byte("hello"), 0644)
	fimExecuteOS(map[string]any{"action": "init", "watch_paths": []any{tmpDir}})

	os.WriteFile(testFile, []byte("modified"), 0644)

	result, err := fimExecuteOS(map[string]any{
		"action":      "check",
		"watch_paths": []any{tmpDir},
	})
	if err != nil {
		t.Fatalf("fimExecuteOS check: %v", err)
	}
	violations, _ := result["violations"].(bool)
	if !violations {
		t.Fatal("expected violations=true after file modification")
	}
}

func TestFimRollbackOS_Darwin(t *testing.T) {
	result, err := fimRollbackOS(map[string]any{})
	if err != nil {
		t.Fatalf("fimRollbackOS: %v", err)
	}
	if result["rolled_back"] == true {
		t.Fatal("FIM should not have rolled_back=true (monitoring-only)")
	}
}
