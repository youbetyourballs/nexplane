//go:build linux

package forensics

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

const maxBundleSize = 2 * 1024 * 1024 * 1024 // 2 GB

func collectOS(ctx context.Context, cfg ForensicsConfig) ([]ArtifactEntry, bytes.Buffer, error) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	var artifacts []ArtifactEntry

	add := func(name string, data []byte) {
		if buf.Len() > maxBundleSize {
			return // cap bundle size
		}
		entry, err := NewArtifactEntry(name, bytes.NewReader(data))
		if err != nil {
			return
		}
		hdr := &tar.Header{
			Name:    name,
			Size:    int64(len(data)),
			Mode:    0600,
			ModTime: time.Now(),
		}
		tw.WriteHeader(hdr) //nolint:errcheck
		tw.Write(data)      //nolint:errcheck
		artifacts = append(artifacts, entry)
	}

	addFile := func(name, path string) {
		data, err := os.ReadFile(path)
		if err != nil {
			return // skip missing files silently
		}
		add(name, data)
	}

	addCmd := func(name string, args ...string) {
		out, err := exec.CommandContext(ctx, args[0], args[1:]...).Output()
		if err != nil {
			out = []byte(fmt.Sprintf("ERROR: %v\n", err))
		}
		add(name, out)
	}

	// Auth logs
	addFile("auth.log", "/var/log/auth.log")
	addFile("secure.log", "/var/log/secure")

	// Journal
	addCmd("journal.log", "journalctl", "-n", "50000", "--no-pager")

	// Auditd
	addFile("audit.log", "/var/log/audit/audit.log")

	// Network state
	addCmd("ss.txt", "ss", "-antp")
	addCmd("arp.txt", "arp", "-n")
	addCmd("routes.txt", "ip", "route")

	// Process list
	addCmd("ps.txt", "ps", "aux")

	// /proc summaries — walk /proc/<pid>/status for all numeric entries
	entries, _ := filepath.Glob("/proc/[0-9]*/status")
	var procSummary bytes.Buffer
	for _, path := range entries {
		data, _ := os.ReadFile(path)
		procSummary.Write(data)
		procSummary.WriteString("\n---\n")
	}
	add("proc_status_summary.txt", procSummary.Bytes())

	// Optional memory dump — limited VMA region summary
	if cfg.IncludeMemoryDump {
		addCmd("mem_maps_pid1.txt", "cat", "/proc/1/maps")
	}

	tw.Close() //nolint:errcheck
	gz.Close() //nolint:errcheck

	// Drain any excess beyond maxBundleSize is already handled inside add()
	_ = io.Discard
	return artifacts, buf, nil
}
