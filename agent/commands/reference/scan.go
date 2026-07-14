// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// Package reference implements the reference-scan agent subcommand. It walks
// configured paths (env files, config files, systemd units, docker-compose
// files) and emits a JSON hit list for every line that contains one of the
// caller-supplied search terms.
package reference

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Hit is a single line match in the unified reference-scan schema.
type Hit struct {
	Surface          string           `json:"surface"`
	Location         string           `json:"location"`
	MatchedTerm      string           `json:"matched_term"`
	Snippet          string           `json:"snippet"`
	ConsumerIdentity ConsumerIdentity `json:"consumer_identity"`
}

// ConsumerIdentity identifies the host that owns the scanned file.
type ConsumerIdentity struct {
	StableID        string         `json:"stable_id"`
	Hostname        string         `json:"hostname"`
	SurfaceMetadata map[string]any `json:"surface_metadata"`
}

// ScanOutput is written to stdout.
type ScanOutput struct {
	Hits        []Hit `json:"hits"`
	ScanSummary struct {
		Scanned int `json:"scanned"`
		Matched int `json:"matched"`
	} `json:"scan_summary"`
}

// Execute is the executor.CommandFunc entry point for the "reference-scan" agent job.
// Parameters expected: "terms" (comma-separated string), optional "asset_id".
func Execute(params map[string]any) (map[string]any, error) {
	termsRaw, _ := params["terms"].(string)
	assetID, _ := params["asset_id"].(string)

	var args []string
	if termsRaw != "" {
		args = append(args, "--terms", termsRaw)
	}
	if assetID != "" {
		args = append(args, "--asset-id", assetID)
	}

	// Capture stdout by redirecting os.Stdout temporarily.
	oldStdout := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		return nil, fmt.Errorf("pipe: %w", err)
	}
	os.Stdout = w

	runErr := Run(args)

	w.Close()
	os.Stdout = oldStdout

	var buf strings.Builder
	sc := bufio.NewScanner(r)
	for sc.Scan() {
		buf.WriteString(sc.Text())
	}
	r.Close()

	if runErr != nil {
		return nil, runErr
	}

	var out ScanOutput
	if err := json.Unmarshal([]byte(buf.String()), &out); err != nil {
		return nil, fmt.Errorf("parse scan output: %w", err)
	}

	// Convert to map[string]any for the executor framework.
	hits := make([]any, len(out.Hits))
	for i, h := range out.Hits {
		hits[i] = map[string]any{
			"surface":      h.Surface,
			"location":     h.Location,
			"matched_term": h.MatchedTerm,
			"snippet":      h.Snippet,
			"consumer_identity": map[string]any{
				"stable_id":        h.ConsumerIdentity.StableID,
				"hostname":         h.ConsumerIdentity.Hostname,
				"surface_metadata": h.ConsumerIdentity.SurfaceMetadata,
			},
		}
	}

	return map[string]any{
		"hits": hits,
		"scan_summary": map[string]any{
			"scanned": out.ScanSummary.Scanned,
			"matched": out.ScanSummary.Matched,
		},
	}, nil
}

// Run is the entry point for the reference-scan subcommand.
// args format (positional flags parsed manually for zero-dependency simplicity):
//
//	--terms <t1,t2>   comma-separated search terms (required)
//	--paths <p1,p2>   comma-separated directories / glob patterns (optional, uses defaults)
//	--extensions <e>  comma-separated extensions filter (ignored for built-in surfaces)
//	--asset-id <id>   Nexplane asset UUID used as stable_id (optional, falls back to hostname)
func Run(args []string) error {
	var terms []string
	assetID := ""

	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--terms":
			if i+1 < len(args) {
				i++
				for _, t := range strings.Split(args[i], ",") {
					t = strings.TrimSpace(t)
					if t != "" {
						terms = append(terms, t)
					}
				}
			}
		case "--asset-id":
			if i+1 < len(args) {
				i++
				assetID = args[i]
			}
		// --paths and --extensions are accepted but ignored; we use the canonical
		// surfaces defined in the task brief so the output schema is predictable.
		case "--paths", "--extensions":
			if i+1 < len(args) {
				i++
			}
		}
	}

	if len(terms) == 0 {
		return fmt.Errorf("--terms is required")
	}

	hostname, _ := os.Hostname()
	stableID := assetID
	if stableID == "" {
		stableID = hostname
	}

	// Collect all (path, fileType) pairs to scan.
	type candidate struct {
		path     string
		fileType string
	}

	var candidates []candidate

	// 1. Environment files
	envFiles := []string{
		"/etc/environment",
		os.ExpandEnv("$HOME/.env"),
		os.ExpandEnv("$HOME/.bashrc"),
	}
	profileGlobs, _ := filepath.Glob("/etc/profile.d/*.sh")
	for _, p := range profileGlobs {
		envFiles = append(envFiles, p)
	}
	for _, p := range envFiles {
		if fileExists(p) {
			candidates = append(candidates, candidate{p, "env_file"})
		}
	}

	// 2. Config files
	configGlobs := []string{
		"/etc/app*.conf",
		"/opt/**/*.conf",
		"/opt/**/*.yaml",
		"/opt/**/*.toml",
	}
	for _, pattern := range configGlobs {
		matched, _ := doubleStarGlob(pattern)
		for _, p := range matched {
			candidates = append(candidates, candidate{p, "config_file"})
		}
	}

	// 3. systemd unit files
	systemdGlobs := []string{
		"/etc/systemd/system/*.service",
		"/lib/systemd/system/*.service",
	}
	for _, pattern := range systemdGlobs {
		matched, _ := filepath.Glob(pattern)
		for _, p := range matched {
			candidates = append(candidates, candidate{p, "systemd_unit"})
		}
	}

	// 4. Docker compose files
	composeGlobs := []string{
		"/opt/**/docker-compose.yml",
		"/opt/**/docker-compose.yaml",
	}
	for _, pattern := range composeGlobs {
		matched, _ := doubleStarGlob(pattern)
		for _, p := range matched {
			candidates = append(candidates, candidate{p, "docker_compose"})
		}
	}

	// Deduplicate candidates by path.
	seen := map[string]bool{}
	var deduped []candidate
	for _, c := range candidates {
		if !seen[c.path] {
			seen[c.path] = true
			deduped = append(deduped, c)
		}
	}

	var out ScanOutput
	out.Hits = []Hit{} // ensure non-null JSON array

	for _, c := range deduped {
		hits, err := scanFile(c.path, c.fileType, terms, stableID, hostname)
		if err != nil {
			continue
		}
		out.ScanSummary.Scanned++
		out.Hits = append(out.Hits, hits...)
		out.ScanSummary.Matched += len(hits)
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	return enc.Encode(out)
}

// scanFile scans a single file and returns hits for any matching line.
func scanFile(path, fileType string, terms []string, stableID, hostname string) ([]Hit, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var hits []Hit
	scanner := bufio.NewScanner(f)
	lineNum := 0
	for scanner.Scan() {
		lineNum++
		line := scanner.Text()
		lower := strings.ToLower(line)
		for _, term := range terms {
			if strings.Contains(lower, strings.ToLower(term)) {
				hits = append(hits, Hit{
					Surface:     "host_file",
					Location:    fmt.Sprintf("%s:%d", path, lineNum),
					MatchedTerm: term,
					Snippet:     line,
					ConsumerIdentity: ConsumerIdentity{
						StableID: stableID,
						Hostname: hostname,
						SurfaceMetadata: map[string]any{
							"file_type": fileType,
						},
					},
				})
			}
		}
	}
	return hits, scanner.Err()
}

// fileExists returns true when path names a regular file.
func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

// doubleStarGlob handles patterns containing "**" by walking directories.
func doubleStarGlob(pattern string) ([]string, error) {
	if !strings.Contains(pattern, "**") {
		return filepath.Glob(pattern)
	}

	parts := strings.SplitN(pattern, "**", 2)
	root := strings.TrimRight(parts[0], "/")
	if root == "" {
		root = "/"
	}
	suffix := strings.TrimLeft(parts[1], "/")

	var matches []string
	_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info == nil || info.IsDir() {
			return nil
		}
		if suffix == "" {
			matches = append(matches, path)
			return nil
		}
		matched, _ := filepath.Match(suffix, filepath.Base(path))
		if matched {
			matches = append(matches, path)
		}
		return nil
	})
	return matches, nil
}
