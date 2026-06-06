package installer

import (
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
)

// Run handles the `nexplane-agent install` subcommand.
// It copies the binary to the system path and installs a service that runs
// the agent on boot with the given control plane URL and secret.
func Run(args []string) error {
	fs := flag.NewFlagSet("nexplane-agent install", flag.ContinueOnError)
	controlPlane := fs.String("control-plane", "", "Base URL of the Nexplane control plane (required)")
	secret := fs.String("secret", "", "Shared HMAC secret for agent authentication (required)")
	fs.Bool("non-interactive", false, "Suppress prompts (no-op, accepted for compatibility)")

	if err := fs.Parse(args); err != nil {
		return err
	}

	if *controlPlane == "" {
		if v := os.Getenv("NP_CONTROL_PLANE"); v != "" {
			*controlPlane = v
		} else {
			return fmt.Errorf("--control-plane / NP_CONTROL_PLANE is required")
		}
	}
	if *secret == "" {
		if v := os.Getenv("NP_SECRET"); v != "" {
			*secret = v
		} else {
			return fmt.Errorf("--secret / NP_SECRET is required")
		}
	}

	binaryDest, err := installBinary()
	if err != nil {
		return fmt.Errorf("installing binary: %w", err)
	}
	fmt.Fprintf(os.Stdout, "Binary installed to %s\n", binaryDest)

	if err := installService(binaryDest, *controlPlane, *secret); err != nil {
		return fmt.Errorf("installing service: %w", err)
	}

	fmt.Fprintf(os.Stdout, "Nexplane agent installed (os=%s)\n", runtime.GOOS)
	return nil
}

// installBinary copies the running executable to /usr/local/bin/nexplane-agent.
func installBinary() (string, error) {
	dest := "/usr/local/bin/nexplane-agent"

	src, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("resolving executable path: %w", err)
	}

	if err := os.MkdirAll(filepath.Dir(dest), 0755); err != nil {
		return "", err
	}

	in, err := os.Open(src)
	if err != nil {
		return "", err
	}
	defer in.Close()

	// Write to a temp file first so the copy is atomic.
	tmp := dest + ".tmp"
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0755)
	if err != nil {
		return "", err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		os.Remove(tmp)
		return "", err
	}
	if err := out.Close(); err != nil {
		os.Remove(tmp)
		return "", err
	}
	if err := os.Rename(tmp, dest); err != nil {
		os.Remove(tmp)
		return "", err
	}
	return dest, nil
}
