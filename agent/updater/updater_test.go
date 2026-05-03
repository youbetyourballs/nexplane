package updater_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"nexplane-agent/updater"
)

// helper: spin up a test server that serves a given version string at /downloads/version
func versionServer(t *testing.T, version string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/downloads/version" {
			fmt.Fprint(w, version)
			return
		}
		http.NotFound(w, r)
	}))
}

func TestCheckAndUpdate_AlreadyUpToDate(t *testing.T) {
	srv := versionServer(t, "0.1.0")
	defer srv.Close()

	updated, err := updater.CheckAndUpdate(context.Background(), srv.URL, "0.1.0")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if updated {
		t.Fatal("expected updated=false when versions match")
	}
}

func TestCheckAndUpdate_WindowsReturnsError(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows-only test")
	}
	srv := versionServer(t, "0.2.0")
	defer srv.Close()

	_, err := updater.CheckAndUpdate(context.Background(), srv.URL, "0.1.0")
	if err == nil {
		t.Fatal("expected error on Windows")
	}
	if !strings.Contains(err.Error(), "Windows") {
		t.Fatalf("expected Windows error message, got: %v", err)
	}
}

func TestCheckAndUpdate_VersionFetchError(t *testing.T) {
	// Point at a server that's already closed
	srv := versionServer(t, "0.2.0")
	srv.Close()

	_, err := updater.CheckAndUpdate(context.Background(), srv.URL, "0.1.0")
	if err == nil {
		t.Fatal("expected error when server is unreachable")
	}
}

func TestFetchVersion(t *testing.T) {
	srv := versionServer(t, "  0.2.0\n") // includes whitespace to test trimming
	defer srv.Close()

	version, err := updater.FetchVersion(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if version != "0.2.0" {
		t.Fatalf("expected '0.2.0', got %q", version)
	}
}

func TestDownloadFile(t *testing.T) {
	content := []byte("fake binary content")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(content)
	}))
	defer srv.Close()

	dest := filepath.Join(t.TempDir(), "downloaded")
	err := updater.DownloadFile(context.Background(), srv.URL+"/binary", dest)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got, _ := os.ReadFile(dest)
	if string(got) != string(content) {
		t.Fatalf("file content mismatch: got %q", got)
	}
}

func TestDownloadFile_HTTP404(t *testing.T) {
	srv := httptest.NewServer(http.NotFoundHandler())
	defer srv.Close()

	dest := filepath.Join(t.TempDir(), "downloaded")
	err := updater.DownloadFile(context.Background(), srv.URL+"/missing", dest)
	if err == nil {
		t.Fatal("expected error for HTTP 404")
	}
}

func TestVerifySHA256(t *testing.T) {
	data := []byte("binary content for sha256 test")
	h := sha256.Sum256(data)
	hashHex := hex.EncodeToString(h[:])

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// sha256sum output format: "<hash>  <filename>"
		fmt.Fprintf(w, "%s  nexplane-agent-linux-amd64-0.2.0\n", hashHex)
	}))
	defer srv.Close()

	tmpFile := filepath.Join(t.TempDir(), "binary")
	os.WriteFile(tmpFile, data, 0644)

	err := updater.VerifySHA256(context.Background(), srv.URL+"/binary.sha256", tmpFile)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestVerifySHA256_Mismatch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "aabbccdd00112233aabbccdd00112233aabbccdd00112233aabbccdd00112233  somefile\n")
	}))
	defer srv.Close()

	tmpFile := filepath.Join(t.TempDir(), "binary")
	os.WriteFile(tmpFile, []byte("different content"), 0644)

	err := updater.VerifySHA256(context.Background(), srv.URL+"/binary.sha256", tmpFile)
	if err == nil {
		t.Fatal("expected SHA256 mismatch error")
	}
	if !strings.Contains(err.Error(), "SHA256 mismatch") {
		t.Fatalf("unexpected error message: %v", err)
	}
}
