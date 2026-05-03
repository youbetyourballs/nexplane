package updater

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"runtime"
	"strings"
	"syscall"
)

// CheckAndUpdate fetches the server version and, if different from currentVersion,
// downloads the versioned binary, verifies its SHA256, atomically replaces the
// running binary, and exec()s the new process. Returns (true, nil) if update
// succeeded — but the caller never sees this because exec() replaces the process.
// Returns (false, nil) if already up to date.
// Returns (false, err) if anything failed — caller should log and continue.
func CheckAndUpdate(ctx context.Context, controlPlaneURL, currentVersion string) (bool, error) {
	serverVersion, err := FetchVersion(ctx, controlPlaneURL)
	if err != nil {
		return false, fmt.Errorf("version check failed: %w", err)
	}
	if serverVersion == currentVersion {
		return false, nil
	}

	if runtime.GOOS == "windows" {
		return false, errors.New("self-update not supported on Windows — replace binary manually from " + controlPlaneURL + "/downloads/")
	}

	arch := runtime.GOARCH
	binaryName := fmt.Sprintf("nexplane-agent-linux-%s-%s", arch, serverVersion)

	execPath, err := os.Executable()
	if err != nil {
		return false, fmt.Errorf("cannot determine executable path: %w", err)
	}

	newPath := execPath + ".new"
	if err := DownloadFile(ctx, controlPlaneURL+"/downloads/"+binaryName, newPath); err != nil {
		return false, fmt.Errorf("download failed: %w", err)
	}

	if err := VerifySHA256(ctx, controlPlaneURL+"/downloads/"+binaryName+".sha256", newPath); err != nil {
		_ = os.Remove(newPath)
		return false, fmt.Errorf("checksum verification failed: %w", err)
	}

	if err := os.Chmod(newPath, 0755); err != nil {
		_ = os.Remove(newPath)
		return false, fmt.Errorf("chmod failed: %w", err)
	}

	_ = os.Rename(execPath, execPath+".old")

	if err := os.Rename(newPath, execPath); err != nil {
		_ = os.Rename(execPath+".old", execPath)
		return false, fmt.Errorf("binary swap failed: %w", err)
	}

	return true, syscall.Exec(execPath, os.Args, os.Environ())
}

// FetchVersion fetches the plain-text version string from controlPlaneURL/downloads/version.
// Exported for testing.
func FetchVersion(ctx context.Context, controlPlaneURL string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, controlPlaneURL+"/downloads/version", nil)
	if err != nil {
		return "", err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64))
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(body)), nil
}

// DownloadFile downloads the file at url to destPath. Exported for testing.
func DownloadFile(ctx context.Context, url, destPath string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d downloading %s", resp.StatusCode, url)
	}
	f, err := os.Create(destPath)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(f, resp.Body)
	return err
}

// VerifySHA256 fetches checksumURL (sha256sum format), computes the SHA256 of
// filePath, and returns an error if they don't match. Exported for testing.
func VerifySHA256(ctx context.Context, checksumURL, filePath string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, checksumURL, nil)
	if err != nil {
		return err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 256))
	if err != nil {
		return err
	}
	fields := strings.Fields(string(body))
	if len(fields) == 0 {
		return fmt.Errorf("empty checksum file at %s", checksumURL)
	}
	expectedHash := fields[0]

	f, err := os.Open(filePath)
	if err != nil {
		return err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return err
	}
	actualHash := hex.EncodeToString(h.Sum(nil))
	if actualHash != expectedHash {
		return fmt.Errorf("SHA256 mismatch: expected %s, got %s", expectedHash, actualHash)
	}
	return nil
}
