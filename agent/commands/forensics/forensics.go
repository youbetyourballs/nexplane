package forensics

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"time"
)

// ForensicsConfig is sent from the control plane as the step payload.
type ForensicsConfig struct {
	UploadURL         string `json:"upload_url"`
	AssetID           string `json:"asset_id"`
	IncludeMemoryDump bool   `json:"include_memory_dump"`
}

// ForensicBundle is the manifest returned to the control plane.
type ForensicBundle struct {
	BundleID   string          `json:"bundle_id"`
	UploadedAt string          `json:"uploaded_at"`
	Artifacts  []ArtifactEntry `json:"artifacts"`
}

// ArtifactEntry is the manifest entry for a single collected artifact.
type ArtifactEntry struct {
	Name      string `json:"name"`
	SizeBytes int64  `json:"size_bytes"`
	SHA256    string `json:"sha256"`
}

// NewArtifactEntry computes size and SHA256 from a reader.
// Exported for testing. The caller must not use the reader after calling this.
func NewArtifactEntry(name string, r io.Reader) (ArtifactEntry, error) {
	data, err := io.ReadAll(r)
	if err != nil {
		return ArtifactEntry{}, err
	}
	h := sha256.Sum256(data)
	return ArtifactEntry{
		Name:      name,
		SizeBytes: int64(len(data)),
		SHA256:    hex.EncodeToString(h[:]),
	}, nil
}

// UploadBundle uploads the tar.gz bundle to uploadURL via HTTP PUT.
// Exported for testing.
func UploadBundle(ctx context.Context, uploadURL string, body io.Reader, size int64) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPut, uploadURL, body)
	if err != nil {
		return err
	}
	req.ContentLength = size
	req.Header.Set("Content-Type", "application/gzip")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("upload returned HTTP %d", resp.StatusCode)
	}
	return nil
}

func (c ForensicsConfig) validate() error {
	if c.UploadURL == "" {
		return fmt.Errorf("upload_url is required")
	}
	if c.AssetID == "" {
		return fmt.Errorf("asset_id is required")
	}
	return nil
}

// Collect gathers all artifacts, assembles them into a tar.gz, uploads to
// UploadURL, and returns the manifest. BundleID must be assigned by the caller
// (control plane) after this call returns.
func Collect(ctx context.Context, cfg ForensicsConfig) (*ForensicBundle, error) {
	if err := cfg.validate(); err != nil {
		return nil, err
	}

	artifacts, archive, err := collectOS(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("artifact collection: %w", err)
	}

	size := int64(archive.Len())
	if err := UploadBundle(ctx, cfg.UploadURL, bytes.NewReader(archive.Bytes()), size); err != nil {
		return nil, fmt.Errorf("upload: %w", err)
	}

	return &ForensicBundle{
		UploadedAt: time.Now().UTC().Format(time.RFC3339),
		Artifacts:  artifacts,
	}, nil
}

// Execute is the CommandFunc adapter for the executor.
func Execute(params map[string]any) (map[string]any, error) {
	cfg := ForensicsConfig{}
	cfg.UploadURL, _ = params["upload_url"].(string)
	cfg.AssetID, _ = params["asset_id"].(string)
	cfg.IncludeMemoryDump, _ = params["include_memory_dump"].(bool)

	bundle, err := Collect(context.Background(), cfg)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"uploaded_at":    bundle.UploadedAt,
		"artifact_count": len(bundle.Artifacts),
		"artifacts":      bundle.Artifacts,
	}, nil
}
