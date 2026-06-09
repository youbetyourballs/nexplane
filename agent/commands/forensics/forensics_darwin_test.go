//go:build darwin

package forensics

import (
	"bytes"
	"compress/gzip"
	"context"
	"io"
	"os/exec"
	"testing"
)

func TestCollectOS_Darwin(t *testing.T) {
	execCommandForensics = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "mock output for "+name)
	}
	t.Cleanup(func() { execCommandForensics = exec.Command })

	cfg := ForensicsConfig{}
	artifacts, buf, err := collectOS(context.Background(), cfg)
	if err != nil {
		t.Fatalf("collectOS: %v", err)
	}
	if len(artifacts) == 0 {
		t.Fatal("expected at least one artifact")
	}

	gz, err := gzip.NewReader(bytes.NewReader(buf.Bytes()))
	if err != nil {
		t.Fatalf("bundle not valid gzip: %v", err)
	}
	defer gz.Close()
	content, err := io.ReadAll(gz)
	if err != nil {
		t.Fatalf("reading gzip: %v", err)
	}
	if len(content) == 0 {
		t.Fatal("empty bundle content")
	}
}
