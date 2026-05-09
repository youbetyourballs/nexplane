package containerizebuild

import (
	"bytes"
	"fmt"
	"os/exec"
	"strings"
)

// BuildAndPush builds a Docker image from the provided dockerfile content and pushes it.
// If dryRun is true, no docker commands are executed and ("", nil) is returned.
// Returns the sha256 digest of the pushed image on success.
func BuildAndPush(imageName, dockerfile string, dryRun bool) (string, error) {
	if dryRun {
		return "", nil
	}

	// docker build --file - --tag imageName .
	buildCmd := exec.Command("docker", "build", "--file", "-", "--tag", imageName, ".")
	buildCmd.Stdin = strings.NewReader(dockerfile)
	var buildOut, buildErr bytes.Buffer
	buildCmd.Stdout = &buildOut
	buildCmd.Stderr = &buildErr

	if err := buildCmd.Run(); err != nil {
		return "", fmt.Errorf("docker build failed: %w\nstderr: %s", err, buildErr.String())
	}

	// docker push --quiet imageName
	pushCmd := exec.Command("docker", "push", "--quiet", imageName)
	var pushOut, pushErr bytes.Buffer
	pushCmd.Stdout = &pushOut
	pushCmd.Stderr = &pushErr

	if err := pushCmd.Run(); err != nil {
		return "", fmt.Errorf("docker push failed: %w\nstderr: %s", err, pushErr.String())
	}

	// Extract sha256 digest from push output
	digest := extractDigest(pushOut.String())
	return digest, nil
}

func extractDigest(output string) string {
	for _, line := range strings.Split(output, "\n") {
		if idx := strings.Index(line, "sha256:"); idx >= 0 {
			parts := strings.Fields(line[idx:])
			if len(parts) > 0 {
				return parts[0]
			}
		}
	}
	return ""
}
