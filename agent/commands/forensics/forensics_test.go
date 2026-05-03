package forensics_test

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"nexplane-agent/commands/forensics"
)

func TestForensicsConfig_MissingUploadURL(t *testing.T) {
	cfg := forensics.ForensicsConfig{AssetID: "asset-1"}
	_, err := forensics.Collect(context.Background(), cfg)
	if err == nil || !strings.Contains(err.Error(), "upload_url") {
		t.Fatalf("expected upload_url error, got: %v", err)
	}
}

func TestForensicsConfig_MissingAssetID(t *testing.T) {
	cfg := forensics.ForensicsConfig{UploadURL: "https://s3.example.com/bundle"}
	_, err := forensics.Collect(context.Background(), cfg)
	if err == nil || !strings.Contains(err.Error(), "asset_id") {
		t.Fatalf("expected asset_id error, got: %v", err)
	}
}

func TestArtifactEntry_SHA256(t *testing.T) {
	content := []byte("test artifact content")
	entry, err := forensics.NewArtifactEntry("test.txt", bytes.NewReader(content))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if entry.Name != "test.txt" {
		t.Errorf("expected name test.txt, got %s", entry.Name)
	}
	if entry.SizeBytes != int64(len(content)) {
		t.Errorf("expected size %d, got %d", len(content), entry.SizeBytes)
	}
	if len(entry.SHA256) != 64 {
		t.Errorf("expected 64-char hex SHA256, got %q", entry.SHA256)
	}
}

func TestUploadBundle(t *testing.T) {
	var received bytes.Buffer
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPut {
			t.Errorf("expected PUT, got %s", r.Method)
		}
		io.Copy(&received, r.Body)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	// Build a minimal tar.gz
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	content := []byte("hello")
	tw.WriteHeader(&tar.Header{Name: "test.txt", Size: int64(len(content))})
	tw.Write(content)
	tw.Close()
	gz.Close()

	err := forensics.UploadBundle(context.Background(), srv.URL, bytes.NewReader(buf.Bytes()), int64(buf.Len()))
	if err != nil {
		t.Fatalf("upload failed: %v", err)
	}
	if received.Len() == 0 {
		t.Error("server received empty body")
	}
}

func TestUploadBundle_HTTP500(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	err := forensics.UploadBundle(context.Background(), srv.URL, bytes.NewReader([]byte("data")), 4)
	if err == nil || !strings.Contains(err.Error(), "500") {
		t.Fatalf("expected HTTP 500 error, got: %v", err)
	}
}

func TestForensicBundle_JSONRoundTrip(t *testing.T) {
	bundle := &forensics.ForensicBundle{
		BundleID:   "test-bundle-uuid",
		UploadedAt: "2026-05-03T00:00:00Z",
		Artifacts: []forensics.ArtifactEntry{
			{Name: "auth.log", SizeBytes: 1024, SHA256: strings.Repeat("a", 64)},
		},
	}
	b, _ := json.Marshal(bundle)
	var got forensics.ForensicBundle
	json.Unmarshal(b, &got)
	if got.BundleID != bundle.BundleID || len(got.Artifacts) != 1 {
		t.Fatalf("round-trip failed: %+v", got)
	}
}
