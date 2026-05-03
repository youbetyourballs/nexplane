//go:build windows

package forensics

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"fmt"
	"os/exec"
	"time"
)

func collectOS(ctx context.Context, cfg ForensicsConfig) ([]ArtifactEntry, bytes.Buffer, error) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	var artifacts []ArtifactEntry

	add := func(name string, data []byte) {
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

	addCmd := func(name string, args ...string) {
		out, err := exec.CommandContext(ctx, args[0], args[1:]...).Output()
		if err != nil {
			out = []byte(fmt.Sprintf("ERROR: %v\n", err))
		}
		add(name, out)
	}

	// Event logs
	addCmd("system_events.xml", "wevtutil", "qe", "System", "/count:10000", "/format:XML")
	addCmd("security_events.xml", "wevtutil", "qe", "Security", "/count:10000", "/format:XML")

	// Process list
	addCmd("tasklist.csv", "tasklist", "/v", "/fo", "CSV")

	// Network state
	addCmd("netstat.txt", "netstat", "-ano")
	addCmd("arp.txt", "arp", "-a")
	addCmd("routes.txt", "route", "print")

	// Optional memory dump via procdump if available
	if cfg.IncludeMemoryDump {
		if out, err := exec.CommandContext(ctx, "where", "procdump64.exe").Output(); err == nil && len(out) > 0 {
			addCmd("memdump_system.dmp", "procdump64.exe", "-ma", "-accepteula", "4") // PID 4 = System
		} else {
			add("memdump_skipped.txt", []byte("procdump64.exe not found on PATH — memory dump skipped\n"))
		}
	}

	tw.Close() //nolint:errcheck
	gz.Close() //nolint:errcheck

	return artifacts, buf, nil
}
